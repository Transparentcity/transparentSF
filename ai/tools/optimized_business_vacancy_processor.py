"""
Optimized Business-Vacancy Data Processor

This module processes business registration data and combines it with commercial vacancy tax data
using only address matching for maximum speed and simplicity.

Key Features:
- Single-pass address matching (no BAN matching)
- Pre-loaded vacancy tax data for fast lookups
- Optimized address matching algorithms
- Minimal API calls and data processing
- Fast batch processing
"""

import logging
import time
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
import json
import os
from dotenv import load_dotenv
from ai.tools.data_fetcher import fetch_data_from_api

# Load environment variables
load_dotenv('ai/.env')

logger = logging.getLogger(__name__)

@dataclass
class BusinessVacancyRecord:
    """Structured business record with vacancy tax information"""
    # Business registration data
    certificate_number: str
    dba_name: str
    full_business_address: str
    city: str
    state: str
    business_zip: str
    dba_start_date: Optional[str]
    dba_end_date: Optional[str]
    location_start_date: Optional[str]
    location_end_date: Optional[str]
    administratively_closed: bool
    naic_code: Optional[str]
    naic_code_description: Optional[str]
    lic: Optional[str]
    lic_code_description: Optional[str]
    business_corridor: Optional[str]
    supervisor_district: Optional[str]
    neighborhoods_analysis_boundaries: Optional[str]
    location: Optional[Dict[str, Any]]
    
    # Vacancy tax data
    parcelnumber: Optional[str]
    parcelsitusaddress: Optional[str]
    taxyear: Optional[int]
    filed: Optional[str]
    vacant: Optional[str]
    rate: Optional[float]
    filertype: Optional[str]
    entity: Optional[str]
    lin: Optional[str]
    linaddress: Optional[str]
    
    # Coordinates
    latitude: float
    longitude: float
    
    # Metadata
    match_method: str  # 'address_match'
    created_at: str

class OptimizedBusinessVacancyProcessor:
    """Optimized processor using only address matching"""
    
    def __init__(self, db_connection_string: str = None):
        """Initialize the processor with database connection"""
        self.db_connection_string = db_connection_string or os.getenv('DATABASE_URL')
        if not self.db_connection_string:
            raise ValueError("Database connection string not provided")
        
        self.batch_size = 1000
        self.total_processed = 0
        self.total_errors = 0
        self.ban_matches = 0
        self.address_matches = 0
        self.no_matches = 0
        self.vacancy_data = []  # Pre-loaded vacancy data
        self.vacancy_address_index = {}  # Fast address lookup
        self.vacancy_ban_index = {}  # Fast BAN lookup
    
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_connection_string)
    
    def create_table_if_not_exists(self):
        """Create the business_vacancy table if it doesn't exist"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Create the table
                    create_sql = """
                    CREATE TABLE IF NOT EXISTS business_vacancy (
                        id SERIAL PRIMARY KEY,
                        -- Business registration data
                        certificate_number TEXT,
                        dba_name TEXT,
                        full_business_address TEXT,
                        city TEXT,
                        state TEXT,
                        business_zip TEXT,
                        dba_start_date TIMESTAMP,
                        dba_end_date TIMESTAMP,
                        location_start_date TIMESTAMP,
                        location_end_date TIMESTAMP,
                        administratively_closed BOOLEAN,
                        naic_code TEXT,
                        naic_code_description TEXT,
                        lic TEXT,
                        lic_code_description TEXT,
                        business_corridor TEXT,
                        supervisor_district TEXT,
                        neighborhoods_analysis_boundaries TEXT,
                        location JSONB,
                        
                        -- Vacancy tax data
                        parcelnumber TEXT,
                        parcelsitusaddress TEXT,
                        taxyear INTEGER,
                        filed TEXT,
                        vacant TEXT,
                        rate FLOAT,
                        filertype TEXT,
                        entity TEXT,
                        lin TEXT,
                        linaddress TEXT,
                        
                        -- Coordinates
                        latitude FLOAT,
                        longitude FLOAT,
                        
                        -- Metadata
                        match_method TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                    cur.execute(create_sql)
                    
                    # Create indexes
                    indexes = [
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_certificate ON business_vacancy (certificate_number);",
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_parcel ON business_vacancy (parcelnumber);",
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_lat_lon ON business_vacancy (latitude, longitude);",
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_district ON business_vacancy (supervisor_district);",
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_corridor ON business_vacancy (business_corridor);",
                        "CREATE INDEX IF NOT EXISTS idx_business_vacancy_match_method ON business_vacancy (match_method);"
                    ]
                    
                    for index_sql in indexes:
                        cur.execute(index_sql)
                    
                    conn.commit()
                    logger.info("Business-vacancy table created/verified successfully")
        except Exception as e:
            logger.error(f"Error creating table: {e}")
            raise
    
    def clear_existing_data(self):
        """Clear existing data from the table"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM business_vacancy")
                    conn.commit()
                    logger.info("Cleared existing business-vacancy data")
        except Exception as e:
            logger.error(f"Error clearing existing data: {e}")
            raise
    
    def parse_location_coordinates(self, location_data: Any) -> Tuple[float, float]:
        """Extract coordinates from location field"""
        if not location_data:
            return 0.0, 0.0
        
        try:
            if isinstance(location_data, dict):
                if 'coordinates' in location_data:
                    coords = location_data['coordinates']
                    return float(coords[1]), float(coords[0])  # lat, lon
                elif 'longitude' in location_data and 'latitude' in location_data:
                    return float(location_data['latitude']), float(location_data['longitude'])
            elif isinstance(location_data, str) and location_data.startswith('POINT'):
                # Extract coordinates from "POINT (-122.435385968 37.637676996)"
                coords_str = location_data.replace('POINT (', '').replace(')', '')
                lon, lat = coords_str.split()
                return float(lat), float(lon)
        except (ValueError, IndexError, TypeError) as e:
            logger.warning(f"Failed to parse coordinates from '{location_data}': {e}")
        
        return 0.0, 0.0
    
    def parse_boolean(self, value: Any) -> bool:
        """Parse boolean value from various formats"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ['true', 'yes', '1', 't', 'y']
        if isinstance(value, (int, float)):
            return bool(value)
        return False
    
    def fetch_business_data(self, limit: int = None) -> List[Dict[str, Any]]:
        """Fetch business registration data from DataSF API"""
        logger.info(f"Fetching business registration data (limit: {limit or 'unlimited'})")
        
        # Build query
        query = """
        SELECT 
            certificate_number,
            dba_name,
            full_business_address,
            city,
            state,
            business_zip,
            dba_start_date,
            dba_end_date,
            location_start_date,
            location_end_date,
            administratively_closed,
            naic_code,
            naic_code_description,
            lic,
            lic_code_description,
            business_corridor,
            supervisor_district,
            neighborhoods_analysis_boundaries,
            location
        WHERE certificate_number IS NOT NULL
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        # Fetch data
        result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': query
        })
        
        if not result or 'data' not in result:
            raise Exception(f"Failed to fetch business data: {result.get('error', 'Unknown error')}")
        
        logger.info(f"Fetched {len(result['data'])} business records from DataSF")
        return result['data']
    
    def get_sample_business_data_from_db(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get sample business data from existing database records for testing"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Get sample records from the existing business_vacancy table
                    cur.execute(f"""
                        SELECT DISTINCT
                            certificate_number,
                            dba_name,
                            full_business_address,
                            city,
                            state,
                            business_zip,
                            dba_start_date,
                            dba_end_date,
                            location_start_date,
                            location_end_date,
                            administratively_closed,
                            naic_code,
                            naic_code_description,
                            lic,
                            lic_code_description,
                            business_corridor,
                            supervisor_district,
                            neighborhoods_analysis_boundaries,
                            location
                        FROM business_vacancy
                        WHERE certificate_number IS NOT NULL
                        LIMIT {limit}
                    """)
                    
                    records = cur.fetchall()
                    # Convert to list of dicts
                    return [dict(record) for record in records]
                    
        except Exception as e:
            logger.warning(f"Could not fetch sample data from DB: {e}")
            logger.info("Falling back to API fetch")
            return self.fetch_business_data(limit)
    
    def fetch_all_vacancy_data(self) -> List[Dict[str, Any]]:
        """Fetch all commercial vacancy tax data with retry logic and batching"""
        logger.info("Fetching all commercial vacancy tax data")
        
        query = """
        SELECT 
            ban,
            parcelnumber,
            parcelsitusaddress,
            taxyear,
            filed,
            vacant,
            rate,
            filertype,
            entity,
            lin,
            linaddress,
            longitude,
            latitude,
            analysis_neighborhood,
            supervisor_district
        """
        
        # Try with retry logic and timeout handling
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = fetch_data_from_api({
                    'endpoint': 'rzkk-54yv',
                    'query': query
                })
                
                if result and 'data' in result:
                    logger.info(f"Fetched {len(result['data'])} vacancy tax records on attempt {attempt + 1}")
                    return result['data']
                else:
                    logger.warning(f"No data returned on attempt {attempt + 1}: {result}")
                    
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    import time
                    wait_time = (attempt + 1) * 5  # 5, 10, 15 seconds
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    # Last attempt failed, try with a smaller dataset
                    logger.warning("All attempts failed, trying with limited dataset")
                    try:
                        limited_query = query + " LIMIT 10000"
                        result = fetch_data_from_api({
                            'endpoint': 'rzkk-54yv',
                            'query': limited_query
                        })
                        
                        if result and 'data' in result:
                            logger.info(f"Fetched {len(result['data'])} vacancy tax records with limit")
                            return result['data']
                    except Exception as limit_error:
                        logger.error(f"Limited query also failed: {limit_error}")
        
        # If all else fails, return empty list and log warning
        logger.warning("Could not fetch vacancy data, proceeding without vacancy matching")
        return []
    
    def build_indexes(self, vacancy_data: List[Dict[str, Any]]):
        """Build fast lookup indexes for BAN and address matching"""
        logger.info("Building BAN and address lookup indexes")
        
        self.vacancy_ban_index = {}
        self.vacancy_address_index = {}
        
        for vacancy in vacancy_data:
            # Build BAN index
            ban = vacancy.get('ban', '')
            if ban and str(ban).strip():
                ban_key = str(ban).strip()
                if ban_key not in self.vacancy_ban_index:
                    self.vacancy_ban_index[ban_key] = []
                self.vacancy_ban_index[ban_key].append(vacancy)
            
            # Build address index
            address = vacancy.get('parcelsitusaddress', '')
            if address:
                # Normalize address for indexing
                normalized = self.normalize_address_for_indexing(address)
                if normalized not in self.vacancy_address_index:
                    self.vacancy_address_index[normalized] = []
                self.vacancy_address_index[normalized].append(vacancy)
        
        logger.info(f"Built BAN index with {len(self.vacancy_ban_index)} unique BANs")
        logger.info(f"Built address index with {len(self.vacancy_address_index)} unique addresses")
    
    def normalize_address_for_indexing(self, address: str) -> str:
        """Normalize address for fast lookup"""
        if not address:
            return ""
        
        # Convert to uppercase and strip
        normalized = address.upper().strip()
        
        # Remove common suffixes
        suffixes_to_remove = [', SAN FRANCISCO, CA', ', SF, CA', ', CALIFORNIA']
        for suffix in suffixes_to_remove:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)].strip()
        
        # Standardize street types
        street_replacements = {
            ' STREET': ' ST', ' AVENUE': ' AVE', ' BOULEVARD': ' BLVD',
            ' DRIVE': ' DR', ' ROAD': ' RD', ' LANE': ' LN',
            ' PLACE': ' PL', ' COURT': ' CT', ' CIRCLE': ' CIR',
            ' WAY': ' WAY', ' SQUARE': ' SQ', ' TERRACE': ' TER',
            ' PARKWAY': ' PKWY'
        }
        
        for full_form, abbrev in street_replacements.items():
            normalized = normalized.replace(full_form, abbrev)
        
        # Remove unit/apartment/suite designations for better matching
        # This allows "123 Main St #5" to match "123 Main St"
        unit_patterns = [
            r'\s+#\w+.*$',           # #3, #A, #123A, etc.
            r'\s+APT\s+\w+.*$',      # APT B, APT 5, etc.
            r'\s+APARTMENT\s+\w+.*$', # APARTMENT B, etc.
            r'\s+UNIT\s+\w+.*$',     # UNIT 5, UNIT A, etc.
            r'\s+STE\s+\w+.*$',      # STE 200, etc.
            r'\s+SUITE\s+\w+.*$',    # SUITE 200, etc.
            r'\s+RM\s+\w+.*$',       # RM 5, ROOM 5, etc.
            r'\s+ROOM\s+\w+.*$',     # ROOM 5, etc.
            r'\s+FL\s+\w+.*$',       # FL 2, FLOOR 2, etc.
            r'\s+FLOOR\s+\w+.*$',    # FLOOR 2, etc.
            r'\s+[A-Z0-9]\s*$'       # Single letter/number at end (like "2139 A POLK ST")
        ]
        
        import re
        for pattern in unit_patterns:
            normalized = re.sub(pattern, '', normalized)
        
        # Clean up spaces and periods
        normalized = normalized.replace('.', '').strip()
        normalized = ' '.join(normalized.split())
        
        return normalized
    
    def find_best_vacancy_match(self, certificate_number: str, business_address: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """Find the best matching vacancy record using two-stage matching
        
        Returns: (vacancy_record, match_method)
        """
        # Stage 1: Try BAN matching first (most accurate)
        if certificate_number and str(certificate_number).strip():
            cert_key = str(certificate_number).strip()
            if cert_key in self.vacancy_ban_index:
                candidates = self.vacancy_ban_index[cert_key]
                if candidates:
                    return candidates[0], 'ban_match'
        
        # Stage 2: Try address matching for records not matched by BAN
        if business_address:
            address_match = self.find_best_address_match(business_address)
            if address_match:
                return address_match, 'address_match'
        
        return None, 'no_match'
    
    def find_best_address_match(self, business_address: str) -> Optional[Dict[str, Any]]:
        """Find the best matching vacancy record for a business address (improved version)"""
        if not business_address:
            return None
        
        # Normalize business address
        business_normalized = self.normalize_address_for_indexing(business_address)
        
        # Try exact match first
        if business_normalized in self.vacancy_address_index:
            candidates = self.vacancy_address_index[business_normalized]
            if candidates:
                return candidates[0]  # Return first match
        
        # Try partial matches - street number + street name only (removed overly permissive matching)
        business_parts = business_normalized.split()
        if len(business_parts) >= 2:
            # Try street number + street name
            street_key = f"{business_parts[0]} {business_parts[1]}"
            
            for indexed_address, candidates in self.vacancy_address_index.items():
                if indexed_address.startswith(street_key):
                    return candidates[0]
        
        # REMOVED: Overly permissive street number-only matching
        # This was causing too many false matches
        
        return None
    
    def process_business_records(self, business_data: List[Dict[str, Any]]) -> List[BusinessVacancyRecord]:
        """Process all business records and match with vacancy data"""
        logger.info(f"Processing {len(business_data)} business records")
        
        matched_records = []
        
        for business in business_data:
            try:
                cert_number = business.get('certificate_number')
                if not cert_number:
                    continue
                
                # Find matching vacancy data using two-stage matching
                business_address = business.get('full_business_address', '')
                vacancy_match, match_method = self.find_best_vacancy_match(cert_number, business_address)
                
                # Parse coordinates
                lat, lon = self.parse_location_coordinates(business.get('location'))
                
                # Create combined record
                record = BusinessVacancyRecord(
                    # Business data
                    certificate_number=cert_number,
                    dba_name=business.get('dba_name', ''),
                    full_business_address=business.get('full_business_address', ''),
                    city=business.get('city', ''),
                    state=business.get('state', ''),
                    business_zip=business.get('business_zip', ''),
                    dba_start_date=business.get('dba_start_date'),
                    dba_end_date=business.get('dba_end_date'),
                    location_start_date=business.get('location_start_date'),
                    location_end_date=business.get('location_end_date'),
                    administratively_closed=self.parse_boolean(business.get('administratively_closed', False)),
                    naic_code=business.get('naic_code'),
                    naic_code_description=business.get('naic_code_description'),
                    lic=business.get('lic'),
                    lic_code_description=business.get('lic_code_description'),
                    business_corridor=business.get('business_corridor'),
                    supervisor_district=business.get('supervisor_district'),
                    neighborhoods_analysis_boundaries=business.get('neighborhoods_analysis_boundaries'),
                    location=business.get('location'),
                    
                    # Vacancy data (if matched)
                    parcelnumber=vacancy_match.get('parcelnumber') if vacancy_match else None,
                    parcelsitusaddress=vacancy_match.get('parcelsitusaddress') if vacancy_match else None,
                    taxyear=vacancy_match.get('taxyear') if vacancy_match else None,
                    filed=vacancy_match.get('filed') if vacancy_match else None,
                    vacant=vacancy_match.get('vacant') if vacancy_match else None,
                    rate=vacancy_match.get('rate') if vacancy_match else None,
                    filertype=vacancy_match.get('filertype') if vacancy_match else None,
                    entity=vacancy_match.get('entity') if vacancy_match else None,
                    lin=vacancy_match.get('lin') if vacancy_match else None,
                    linaddress=vacancy_match.get('linaddress') if vacancy_match else None,
                    
                    # Coordinates
                    latitude=lat if lat != 0.0 else (vacancy_match.get('latitude', 0.0) if vacancy_match else 0.0),
                    longitude=lon if lon != 0.0 else (vacancy_match.get('longitude', 0.0) if vacancy_match else 0.0),
                    
                    # Metadata
                    match_method=match_method,
                    created_at=time.strftime('%Y-%m-%d %H:%M:%S')
                )
                
                matched_records.append(record)
                
                # Track match statistics
                if match_method == 'ban_match':
                    self.ban_matches += 1
                elif match_method == 'address_match':
                    self.address_matches += 1
                else:
                    self.no_matches += 1
                
            except Exception as e:
                logger.warning(f"Error processing business record {business.get('certificate_number', 'unknown')}: {e}")
                self.total_errors += 1
        
        logger.info(f"Processed {len(matched_records)} business records")
        logger.info(f"Match statistics: {self.ban_matches} BAN matches, {self.address_matches} address matches, {self.no_matches} no matches")
        return matched_records
    
    def insert_business_vacancy_records(self, records: List[BusinessVacancyRecord]):
        """Insert business-vacancy records into the database"""
        if not records:
            return
        
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Prepare insert statement
                    insert_sql = """
                    INSERT INTO business_vacancy (
                        certificate_number, dba_name, full_business_address, city, state, business_zip,
                        dba_start_date, dba_end_date, location_start_date, location_end_date, administratively_closed,
                        naic_code, naic_code_description, lic, lic_code_description, business_corridor,
                        supervisor_district, neighborhoods_analysis_boundaries, location,
                        parcelnumber, parcelsitusaddress, taxyear, filed, vacant, rate, filertype,
                        entity, lin, linaddress, latitude, longitude, match_method
                    ) VALUES (
                        %(certificate_number)s, %(dba_name)s, %(full_business_address)s, %(city)s, %(state)s, %(business_zip)s,
                        %(dba_start_date)s, %(dba_end_date)s, %(location_start_date)s, %(location_end_date)s, %(administratively_closed)s,
                        %(naic_code)s, %(naic_code_description)s, %(lic)s, %(lic_code_description)s, %(business_corridor)s,
                        %(supervisor_district)s, %(neighborhoods_analysis_boundaries)s, %(location)s,
                        %(parcelnumber)s, %(parcelsitusaddress)s, %(taxyear)s, %(filed)s, %(vacant)s, %(rate)s, %(filertype)s,
                        %(entity)s, %(lin)s, %(linaddress)s, %(latitude)s, %(longitude)s, %(match_method)s
                    )
                    """
                    
                    # Convert records to dictionaries for insertion
                    records_data = []
                    for record in records:
                        records_data.append({
                            'certificate_number': record.certificate_number,
                            'dba_name': record.dba_name,
                            'full_business_address': record.full_business_address,
                            'city': record.city,
                            'state': record.state,
                            'business_zip': record.business_zip,
                            'dba_start_date': record.dba_start_date,
                            'dba_end_date': record.dba_end_date,
                            'location_start_date': record.location_start_date,
                            'location_end_date': record.location_end_date,
                            'administratively_closed': record.administratively_closed,
                            'naic_code': record.naic_code,
                            'naic_code_description': record.naic_code_description,
                            'lic': record.lic,
                            'lic_code_description': record.lic_code_description,
                            'business_corridor': record.business_corridor,
                            'supervisor_district': record.supervisor_district,
                            'neighborhoods_analysis_boundaries': record.neighborhoods_analysis_boundaries,
                            'location': json.dumps(record.location) if record.location else None,
                            'parcelnumber': record.parcelnumber,
                            'parcelsitusaddress': record.parcelsitusaddress,
                            'taxyear': record.taxyear,
                            'filed': record.filed,
                            'vacant': record.vacant,
                            'rate': record.rate,
                            'filertype': record.filertype,
                            'entity': record.entity,
                            'lin': record.lin,
                            'linaddress': record.linaddress,
                            'latitude': record.latitude,
                            'longitude': record.longitude,
                            'match_method': record.match_method
                        })
                    
                    # Execute batch insert
                    psycopg2.extras.execute_batch(cur, insert_sql, records_data)
                    conn.commit()
                    
                    logger.info(f"Inserted {len(records)} business-vacancy records")
                    
        except Exception as e:
            logger.error(f"Error inserting business-vacancy records: {e}")
            raise
    
    def process_all_data(self, limit: int = None, clear_existing: bool = True, 
                         skip_vacancy_fetch: bool = False, skip_business_fetch: bool = False):
        """Process all business data and populate the table using optimized address matching
        
        Args:
            limit: Limit number of business records to process
            clear_existing: Clear existing data before processing
            skip_vacancy_fetch: Skip fetching vacancy data (reuse existing)
            skip_business_fetch: Skip fetching business data (reuse existing) 
        """
        start_time = time.time()
        
        try:
            # Create table if not exists
            self.create_table_if_not_exists()
            
            # Clear existing data if requested
            if clear_existing:
                self.clear_existing_data()
            
            # Fetch all vacancy data once (unless skipping)
            if not skip_vacancy_fetch:
                try:
                    self.vacancy_data = self.fetch_all_vacancy_data()
                    logger.info(f"Successfully fetched {len(self.vacancy_data)} vacancy records")
                except Exception as e:
                    logger.error(f"Failed to fetch vacancy data: {e}")
                    logger.info("Proceeding without vacancy data matching")
                    self.vacancy_data = []
            else:
                logger.info(f"Skipping vacancy data fetch - using existing {len(self.vacancy_data)} records")
            
            # Build BAN and address indexes
            self.build_indexes(self.vacancy_data)
            
            # Fetch business data (unless skipping)
            if not skip_business_fetch:
                business_data = self.fetch_business_data(limit)
            else:
                logger.info("Skipping business data fetch - using cached data")
                # For testing, we can use a small subset from the database
                business_data = self.get_sample_business_data_from_db(limit or 1000)
            
            # Process business records
            matched_records = self.process_business_records(business_data)
            
            # Insert in batches
            for i in range(0, len(matched_records), self.batch_size):
                batch = matched_records[i:i + self.batch_size]
                self.insert_business_vacancy_records(batch)
                self.total_processed += len(batch)
                
                # Log progress
                if i % 1000 == 0:
                    logger.info(f"Processed {i}/{len(matched_records)} records")
            
            # Final statistics
            total_time = time.time() - start_time
            logger.info(f"Processing complete: {self.total_processed} records processed in {total_time:.2f} seconds")
            logger.info(f"Address matches: {self.address_matches}")
            logger.info(f"Average processing rate: {self.total_processed / total_time:.2f} records/second")
            logger.info(f"Total errors: {self.total_errors}")
            
            return {
                'total_processed': self.total_processed,
                'address_matches': self.address_matches,
                'total_errors': self.total_errors,
                'processing_time': total_time,
                'records_per_second': self.total_processed / total_time if total_time > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error processing business-vacancy data: {e}")
            raise

def main():
    """Main function for command-line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Process business-vacancy data using optimized address matching')
    parser.add_argument('--limit', type=int, help='Limit number of records to process')
    parser.add_argument('--no-clear', action='store_true', help='Don\'t clear existing data')
    parser.add_argument('--db-url', help='Database connection string')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Process data
    processor = OptimizedBusinessVacancyProcessor(args.db_url)
    result = processor.process_all_data(
        limit=args.limit,
        clear_existing=not args.no_clear
    )
    
    print(f"Processing complete: {result}")

if __name__ == '__main__':
    main()
