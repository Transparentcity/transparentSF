"""
Reverse Business-Vacancy Data Processor

This module starts with taxable commercial spaces (~18K records) and finds matching businesses,
which is much more efficient than processing 348K+ business records.

Approach:
1. Fetch all taxable commercial spaces (~18K records)
2. Fetch all business registrations once and index by BAN and address
3. For each taxable space, find all matching businesses
4. Create combined records

This is ~20x more efficient than the forward approach.
"""

import logging
import time
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
import json
import os
import re
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
    match_method: str
    created_at: str

class ReverseVacancyProcessor:
    """Efficient processor that starts with taxable spaces and finds matching businesses"""
    
    def __init__(self, db_connection_string: str = None):
        self.db_connection_string = db_connection_string or os.getenv('DATABASE_URL')
        if not self.db_connection_string:
            raise ValueError("Database connection string not provided")
        
        self.batch_size = 1000
        self.total_processed = 0
        self.total_errors = 0
        self.ban_matches = 0
        self.address_matches = 0
        self.no_matches = 0
        
        # Indexes for fast lookups
        self.business_ban_index = {}      # BAN -> [business records]
        self.business_address_index = {}  # normalized_address -> [business records]
    
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_connection_string)
    
    def create_table_if_not_exists(self):
        """Create the business_vacancy table if it doesn't exist"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Create the table (same schema as before)
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
    
    def normalize_address_for_indexing(self, address: str) -> str:
        """Normalize address for fast lookup (same logic as before)"""
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
            r'\s+[A-Z0-9]\s*$'       # Single letter/number at end
        ]
        
        for pattern in unit_patterns:
            normalized = re.sub(pattern, '', normalized)
        
        # Clean up spaces and periods
        normalized = normalized.replace('.', '').strip()
        normalized = ' '.join(normalized.split())
        
        return normalized
    
    def fetch_all_business_data(self, use_cached: bool = False) -> List[Dict[str, Any]]:
        """Fetch all business registration data once"""
        if use_cached:
            return self.get_cached_business_data()
        
        logger.info("Fetching all business registration data from API")
        
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
        
        try:
            result = fetch_data_from_api({
                'endpoint': 'g8m3-pdis',
                'query': query
            })
            
            if not result or 'data' not in result:
                raise Exception(f"Failed to fetch business data: {result.get('error', 'Unknown error')}")
            
            logger.info(f"Fetched {len(result['data'])} business records from DataSF")
            return result['data']
            
        except Exception as e:
            logger.warning(f"API fetch failed: {e}")
            logger.info("Falling back to cached business data from database")
            return self.get_cached_business_data()
    
    def get_cached_business_data(self) -> List[Dict[str, Any]]:
        """Get business data from existing database records"""
        logger.info("Using cached business data from database")
        try:
            with self.get_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
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
                        ORDER BY certificate_number
                    """)
                    
                    records = cur.fetchall()
                    business_data = [dict(record) for record in records]
                    logger.info(f"Retrieved {len(business_data)} cached business records")
                    return business_data
                    
        except Exception as e:
            logger.error(f"Could not fetch cached business data: {e}")
            raise Exception("No business data available - both API and database failed")
    
    def fetch_all_vacancy_data(self) -> List[Dict[str, Any]]:
        """Fetch all commercial vacancy tax data"""
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
        
        result = fetch_data_from_api({
            'endpoint': 'rzkk-54yv',
            'query': query
        })
        
        if not result or 'data' not in result:
            raise Exception(f"Failed to fetch vacancy data: {result.get('error', 'Unknown error')}")
        
        logger.info(f"Fetched {len(result['data'])} vacancy tax records from DataSF")
        return result['data']
    
    def build_business_indexes(self, business_data: List[Dict[str, Any]]):
        """Build indexes for fast business lookup"""
        logger.info("Building business lookup indexes")
        
        self.business_ban_index = {}
        self.business_address_index = {}
        
        for business in business_data:
            cert_num = business.get('certificate_number')
            if cert_num and str(cert_num).strip():
                cert_key = str(cert_num).strip()
                if cert_key not in self.business_ban_index:
                    self.business_ban_index[cert_key] = []
                self.business_ban_index[cert_key].append(business)
            
            # Build address index
            address = business.get('full_business_address', '')
            if address:
                normalized = self.normalize_address_for_indexing(address)
                if normalized not in self.business_address_index:
                    self.business_address_index[normalized] = []
                self.business_address_index[normalized].append(business)
        
        logger.info(f"Built business BAN index with {len(self.business_ban_index)} unique certificate numbers")
        logger.info(f"Built business address index with {len(self.business_address_index)} unique addresses")
    
    def find_matching_businesses(self, vacancy_record: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
        """Find all businesses matching a vacancy record
        
        Returns: List of (business_record, match_method) tuples
        """
        matches = []
        
        # Stage 1: Try BAN matching
        ban = vacancy_record.get('ban')
        if ban and str(ban).strip():
            ban_key = str(ban).strip()
            if ban_key in self.business_ban_index:
                for business in self.business_ban_index[ban_key]:
                    matches.append((business, 'ban_match'))
        
        # Stage 2: Try address matching (only if no BAN matches to avoid duplicates)
        if not matches:
            address = vacancy_record.get('parcelsitusaddress', '')
            if address:
                normalized = self.normalize_address_for_indexing(address)
                if normalized in self.business_address_index:
                    for business in self.business_address_index[normalized]:
                        matches.append((business, 'address_match'))
        
        return matches
    
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
            logger.debug(f"Could not parse coordinates from {location_data}: {e}")
        
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
    
    def process_vacancy_records(self, vacancy_data: List[Dict[str, Any]]) -> List[BusinessVacancyRecord]:
        """Process vacancy records and find matching businesses"""
        logger.info(f"Processing {len(vacancy_data)} vacancy records")
        
        all_records = []
        
        for vacancy in vacancy_data:
            try:
                # Find matching businesses for this vacancy record
                business_matches = self.find_matching_businesses(vacancy)
                
                if business_matches:
                    # Create a record for each matching business
                    for business, match_method in business_matches:
                        # Parse coordinates (prefer business location, fallback to vacancy)
                        lat, lon = self.parse_location_coordinates(business.get('location'))
                        if lat == 0.0 and lon == 0.0:
                            # Fallback to vacancy coordinates
                            try:
                                lat = float(vacancy.get('latitude', 0.0))
                                lon = float(vacancy.get('longitude', 0.0))
                            except (ValueError, TypeError):
                                lat, lon = 0.0, 0.0
                        
                        record = BusinessVacancyRecord(
                            # Business data
                            certificate_number=business.get('certificate_number', ''),
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
                            
                            # Vacancy data
                            parcelnumber=vacancy.get('parcelnumber'),
                            parcelsitusaddress=vacancy.get('parcelsitusaddress'),
                            taxyear=vacancy.get('taxyear'),
                            filed=vacancy.get('filed'),
                            vacant=vacancy.get('vacant'),
                            rate=vacancy.get('rate'),
                            filertype=vacancy.get('filertype'),
                            entity=vacancy.get('entity'),
                            lin=vacancy.get('lin'),
                            linaddress=vacancy.get('linaddress'),
                            
                            # Coordinates
                            latitude=lat,
                            longitude=lon,
                            
                            # Metadata
                            match_method=match_method,
                            created_at=time.strftime('%Y-%m-%d %H:%M:%S')
                        )
                        
                        all_records.append(record)
                        
                        # Track statistics
                        if match_method == 'ban_match':
                            self.ban_matches += 1
                        elif match_method == 'address_match':
                            self.address_matches += 1
                
            except Exception as e:
                logger.warning(f"Error processing vacancy record: {e}")
                self.total_errors += 1
        
        logger.info(f"Created {len(all_records)} business-vacancy records")
        logger.info(f"Match statistics: {self.ban_matches} BAN matches, {self.address_matches} address matches")
        return all_records
    
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
                        dba_start_date, dba_end_date, location_start_date, location_end_date,
                        administratively_closed, naic_code, naic_code_description, lic, lic_code_description,
                        business_corridor, supervisor_district, neighborhoods_analysis_boundaries, location,
                        parcelnumber, parcelsitusaddress, taxyear, filed, vacant, rate,
                        filertype, entity, lin, linaddress, latitude, longitude, match_method
                    ) VALUES %s
                    """
                    
                    # Prepare data for batch insert
                    records_data = []
                    for record in records:
                        records_data.append((
                            record.certificate_number, record.dba_name, record.full_business_address,
                            record.city, record.state, record.business_zip,
                            record.dba_start_date, record.dba_end_date, record.location_start_date, record.location_end_date,
                            record.administratively_closed, record.naic_code, record.naic_code_description,
                            record.lic, record.lic_code_description, record.business_corridor,
                            record.supervisor_district, record.neighborhoods_analysis_boundaries,
                            json.dumps(record.location) if record.location else None,
                            record.parcelnumber, record.parcelsitusaddress, record.taxyear,
                            record.filed, record.vacant, record.rate, record.filertype,
                            record.entity, record.lin, record.linaddress,
                            record.latitude, record.longitude, record.match_method
                        ))
                    
                    # Execute batch insert
                    psycopg2.extras.execute_values(cur, insert_sql, records_data)
                    conn.commit()
                    
                    logger.info(f"Inserted {len(records)} business-vacancy records")
                    
        except Exception as e:
            logger.error(f"Error inserting business-vacancy records: {e}")
            raise
    
    def process_all_data(self, clear_existing: bool = True, use_cached_business: bool = False):
        """Process all data using the reverse (efficient) approach"""
        start_time = time.time()
        
        try:
            # Create table if not exists
            self.create_table_if_not_exists()
            
            # Clear existing data if requested
            if clear_existing:
                self.clear_existing_data()
            
            # Fetch all data
            logger.info("Step 1: Fetching all business registration data...")
            business_data = self.fetch_all_business_data(use_cached=use_cached_business)
            
            logger.info("Step 2: Fetching all vacancy tax data...")
            vacancy_data = self.fetch_all_vacancy_data()
            
            # Build business indexes for fast lookup
            logger.info("Step 3: Building business lookup indexes...")
            self.build_business_indexes(business_data)
            
            # Process vacancy records (much faster - only ~18K iterations)
            logger.info("Step 4: Processing vacancy records and finding matches...")
            matched_records = self.process_vacancy_records(vacancy_data)
            
            # Insert in batches
            logger.info("Step 5: Inserting matched records...")
            for i in range(0, len(matched_records), self.batch_size):
                batch = matched_records[i:i + self.batch_size]
                self.insert_business_vacancy_records(batch)
                self.total_processed += len(batch)
            
            # Final statistics
            end_time = time.time()
            duration = end_time - start_time
            
            logger.info(f"Processing complete: {len(matched_records)} records processed in {duration:.2f} seconds")
            logger.info(f"BAN matches: {self.ban_matches}")
            logger.info(f"Address matches: {self.address_matches}")
            logger.info(f"Total errors: {self.total_errors}")
            logger.info(f"Average processing rate: {len(matched_records)/duration:.2f} records/second")
            
        except Exception as e:
            logger.error(f"Error in process_all_data: {e}")
            raise

def main():
    """Main function for testing"""
    processor = ReverseVacancyProcessor()
    processor.process_all_data(clear_existing=True)

if __name__ == "__main__":
    main()
