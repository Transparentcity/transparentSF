"""
Business Cache Processor

This module pre-processes all business data from DataSF and stores it in the local database
for fast searching and display. This eliminates the need to query DataSF API for every search.
"""

import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import json
from collections import defaultdict
import psycopg2.errors

from ai.tools.data_fetcher import fetch_data_from_api
from ai.tools.db_utils import execute_with_connection

# Configure logging
logger = logging.getLogger(__name__)

class BusinessCacheProcessor:
    """Handles caching of business data for fast searching"""
    
    def __init__(self):
        self.cache_table = "business_cache"
        self.tax_table = "commercial_tax_cache"
    
    def create_cache_tables(self):
        """Create tables for caching business data"""
        logger.info("Creating business cache tables...")
        
        # Business cache table
        business_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.cache_table} (
            id SERIAL PRIMARY KEY,
            certificate_number VARCHAR(50),
            dba_name VARCHAR(500),
            naic_code_description TEXT,
            lic_code_description TEXT,
            business_corridor VARCHAR(200),
            supervisor_district VARCHAR(10),
            full_business_address TEXT,
            distinct_address TEXT,
            building_address TEXT,
            location_lat FLOAT,
            location_lon FLOAT,
            dba_start_date DATE,
            location_start_date DATE,
            dba_end_date DATE,
            location_end_date DATE,
            administratively_closed BOOLEAN DEFAULT FALSE,
            status VARCHAR(20),
            is_street_level BOOLEAN DEFAULT TRUE,
            business_count INTEGER DEFAULT 1,
            streetfront_open_count INTEGER DEFAULT 0,
            streetfront_closed_count INTEGER DEFAULT 0,
            total_streetfront_addresses INTEGER DEFAULT 0,
            upper_floor_open_count INTEGER DEFAULT 0,
            upper_floor_closed_count INTEGER DEFAULT 0,
            total_upper_floor_addresses INTEGER DEFAULT 0,
            individual_addresses_count INTEGER DEFAULT 1,
            addresses_data JSONB,
            has_commercial_tax_filing BOOLEAN DEFAULT FALSE,
            tax_filing_addresses TEXT[],
            vacancy_status VARCHAR(20) DEFAULT 'unfiled',
            tax_filing_year INTEGER,
            tax_filing_ban VARCHAR(50),
            tax_filing_entity VARCHAR(500),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(certificate_number, full_business_address)
        );
        """
        
        # Create minimal indexes for fast searching
        indexes_sql = [
            f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_dba_name ON {self.cache_table} (dba_name);",
            f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_certificate ON {self.cache_table} (certificate_number);",
            f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_created_at ON {self.cache_table} (created_at);",
        ]
        
        def create_tables(conn):
            cursor = conn.cursor()
            logger.info("Creating business table...")
            cursor.execute(business_table_sql)
            logger.info("✅ Business table created")
            
            # Add created_at column if it doesn't exist (for existing tables)
            try:
                cursor.execute(f"""
                    ALTER TABLE {self.cache_table} 
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("✅ Added created_at column if not exists")
            except Exception as e:
                logger.warning(f"Could not add created_at column: {e}")
            
            logger.info("Creating indexes...")
            for i, index_sql in enumerate(indexes_sql):
                try:
                    logger.info(f"Creating index {i+1}/{len(indexes_sql)}...")
                    cursor.execute(index_sql)
                    logger.info(f"✅ Index {i+1} created")
                except Exception as e:
                    logger.warning(f"Failed to create index {i+1}: {e}")
                    continue
            logger.info("✅ All indexes processed")
            
            # Don't commit here - let execute_with_connection handle it
            logger.info("✅ Transaction prepared")
            cursor.close()
        
        logger.info("Executing table creation with database connection...")
        execute_with_connection(create_tables)
        logger.info("Business cache tables created successfully")
    
    def fetch_all_business_data(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch all business data from DataSF API"""
        logger.info("Fetching all business data from DataSF API...")
        
        query = """
        SELECT 
            location,
            dba_name,
            naic_code_description,
            lic_code_description,
            business_corridor,
            supervisor_district,
            dba_start_date,
            location_start_date,
            dba_end_date,
            location_end_date,
            administratively_closed,
            full_business_address,
            certificate_number
        WHERE location IS NOT NULL
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        query_object = {
            "endpoint": "g8m3-pdis",
            "query": query
        }
        
        result = fetch_data_from_api(query_object)
        data = result.get('data', []) if isinstance(result, dict) else result
        logger.info(f"Fetched {len(data)} business records")
        return data
    
    def normalize_address(self, full_address: str) -> str:
        """Normalize address for consistent matching - same logic as vacancy_analysis.py"""
        if not full_address:
            return ""
        
        import re
        
        # Convert to uppercase for consistent processing
        address = full_address.upper()
        
        # Remove common unit patterns more comprehensively
        # Apply patterns in order of specificity (most specific first)
        
        # Pattern 1: Handle unit letters that appear after street number (like "2139 A POLK ST")
        address = re.sub(r'(\d+)\s+([A-Z](?:\s+[A-Z])*\s+)([A-Z]+)', r'\1 \3', address)
        
        # Pattern 1b: Handle # unit letters in the middle (like "2139 POLK ST #C")
        address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#[A-Z]\s*$', r'\1', address)
        # Pattern 1c: Handle # unit numbers in the middle/end with optional space (like "123 MAIN ST # 301")
        address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#\s*\d+\s*$', r'\1', address)
        
        # Pattern 2: Handle complex unit combinations at the end (multiple letters, separators)
        address = re.sub(r'\s+[A-Z](?:\s+[A-Z])*\s*$', '', address)  # A B C, A B, A patterns at end
        
        # Pattern 3: Remove numbered units with letters at the end (1A, 2B, etc.)
        address = re.sub(r'\s+\d+[A-Z]?\s*$', '', address)
        
        # Pattern 4: Remove simple unit letters at the end (A, B, C, etc.)
        address = re.sub(r'\s+[A-Z]\s*$', '', address)
        
        # Pattern 5: Remove numbered units (#1, # 2, etc.) and unit letters with # (#A, #B, #C)
        address = re.sub(r'\s+#\s*\d+\s*$', '', address)
        address = re.sub(r'\s+#[A-Z]\s*$', '', address)  # Remove #A, #B, #C at end
        
        # Pattern 6: Remove apartment/suite/unit indicators with numbers/letters
        address = re.sub(r'\s+(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLOOR)\s*[A-Z0-9]+\s*$', '', address, flags=re.IGNORECASE)
        
        # Pattern 6: Clean up any remaining artifacts and normalize whitespace
        address = re.sub(r'\s+', ' ', address)  # Normalize whitespace
        address = re.sub(r'\s*[,;]+\s*$', '', address)  # Remove trailing commas/semicolons
        address = re.sub(r'\s*&\s*$', '', address)  # Remove trailing &
        address = re.sub(r'\s+AND\s*$', '', address)  # Remove trailing AND
        
        # Pattern 7: Handle repeated unit letters (like "2139 B POLK ST B")
        address = re.sub(r'\b([A-Z])\s+([A-Z\s]+)\s+\1\b', r'\1 \2', address)  # Remove duplicate unit letters
        
        return address.strip()
    
    def fetch_commercial_tax_filing_data(self) -> Dict[str, Dict]:
        """Fetch and process commercial tax filing data for fast matching"""
        logger.info("Fetching commercial tax filing data for address matching...")
        
        tax_query = """
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
        
        query_object = {
            "endpoint": "rzkk-54yv",
            "query": tax_query
        }
        
        result = fetch_data_from_api(query_object)
        tax_data = result.get('data', []) if isinstance(result, dict) else result
        
        if not tax_data:
            logger.warning("Failed to fetch commercial tax filing data")
            return {}
        
        # Process tax filing data: normalize addresses and track most recent filing per address
        address_to_tax_data = {}
        
        for tax_record in tax_data:
            # Get addresses from both fields
            parcel_address = tax_record.get('parcelsitusaddress', '').strip()
            lin_address = tax_record.get('linaddress', '').strip()
            
            # Normalize both addresses
            normalized_parcel = self.normalize_address(parcel_address) if parcel_address else ""
            normalized_lin = self.normalize_address(lin_address) if lin_address else ""
            
            # Parse tax year for comparison
            tax_year = tax_record.get('taxyear', '')
            try:
                year_int = int(tax_year) if tax_year else 0
            except (ValueError, TypeError):
                year_int = 0
            
            # Process each normalized address
            for normalized_addr in [normalized_parcel, normalized_lin]:
                if not normalized_addr:
                    continue
                
                # Check if this is the most recent filing for this address
                if normalized_addr not in address_to_tax_data or year_int > address_to_tax_data[normalized_addr].get('year', 0):
                    # Determine vacancy status
                    vacant = tax_record.get('vacant', '').lower()
                    if vacant == 'yes':
                        vacancy_status = 'yes'
                    elif vacant == 'no':
                        vacancy_status = 'no'
                    else:
                        vacancy_status = 'unfiled'
                    
                    address_to_tax_data[normalized_addr] = {
                        'year': year_int,
                        'vacancy_status': vacancy_status,
                        'filed': tax_record.get('filed', ''),
                        'rate': tax_record.get('rate', ''),
                        'filertype': tax_record.get('filertype', ''),
                        'entity': tax_record.get('entity', ''),
                        'ban': tax_record.get('ban', ''),
                        'parcelnumber': tax_record.get('parcelnumber', ''),
                        'original_parcel_address': parcel_address,
                        'original_lin_address': lin_address
                    }
        
        logger.info(f"Processed {len(address_to_tax_data)} unique normalized addresses from commercial tax filing data")
        return address_to_tax_data
    
    def process_business_data(self, business_data: List[Dict[str, Any]], tax_filing_data: Dict[str, Dict] = None) -> List[Dict[str, Any]]:
        """Process and group business data by address with tax filing matching"""
        logger.info(f"Processing {len(business_data)} business records...")
        
        if tax_filing_data is None:
            tax_filing_data = {}
        
        # Group by address
        address_groups = defaultdict(list)
        for record in business_data:
            address = record.get('full_business_address', '').strip()
            if address:
                address_groups[address].append(record)
        
        processed_data = []
        for address, records in address_groups.items():
            # Process each address group
            processed_record = self._process_address_group(address, records, tax_filing_data)
            processed_data.append(processed_record)
        
        logger.info(f"Processed {len(processed_data)} building groups from {len(business_data)} business records")
        return processed_data
    
    def _process_address_group(self, address: str, records: List[Dict[str, Any]], tax_filing_data: Dict[str, Dict] = None) -> Dict[str, Any]:
        """Process a group of business records for the same address"""
        # Use the first record as the base
        base_record = records[0]
        
        # Process each individual address within the building
        addresses_data = []
        streetfront_open_count = 0
        streetfront_closed_count = 0
        total_business_count = 0

        for record in records:
            total_business_count += 1
            
            # Determine if this is a street-level address using robust detection
            is_street_level = not self._is_upper_floor_address(address)
            
            # Get status of this business based on dates
            dba_start = self._parse_date(record.get('dba_start_date'))
            location_start = self._parse_date(record.get('location_start_date'))
            dba_end = self._parse_date(record.get('dba_end_date'))
            location_end = self._parse_date(record.get('location_end_date'))

            all_open_dates = []
            all_close_dates = []
            if dba_start:
                all_open_dates.append(dba_start)
            if location_start:
                all_open_dates.append(location_start)
            if dba_end:
                all_close_dates.append(dba_end)
            if location_end:
                all_close_dates.append(location_end)

            most_recent_open_date = max(all_open_dates) if all_open_dates else None
            most_recent_close_date = max(all_close_dates) if all_close_dates else None

            # Determine individual address status
            if not most_recent_close_date:
                individual_status = 'Open'
            elif not most_recent_open_date:
                individual_status = 'Closed'
            elif most_recent_close_date > most_recent_open_date:
                individual_status = 'Closed'
            else:
                individual_status = 'Open'

            # Count streetfront addresses
            if is_street_level:
                if individual_status == 'Open':
                    streetfront_open_count += 1
                else:
                    streetfront_closed_count += 1

            # Add to addresses_data
            processed_address = record.copy()
            processed_address['is_street_level'] = is_street_level
            processed_address['status'] = individual_status
            processed_address['open_date'] = most_recent_open_date.isoformat() if most_recent_open_date else None
            processed_address['close_date'] = most_recent_close_date.isoformat() if most_recent_close_date else None
            addresses_data.append(processed_address)
        
        # Determine overall building status based on streetfront addresses
        if streetfront_closed_count > 0 and streetfront_open_count == 0:
            overall_status = 'Closed'
        elif streetfront_open_count > 0:
            overall_status = 'Open'
        else:
            overall_status = 'Unknown'
        
        # Compute upper-floor counts from addresses_data
        upper_floor_open_count = sum(1 for a in addresses_data if a.get('is_street_level') is False and a.get('status') == 'Open')
        upper_floor_closed_count = sum(1 for a in addresses_data if a.get('is_street_level') is False and a.get('status') == 'Closed')
        total_upper_floor_addresses = upper_floor_open_count + upper_floor_closed_count
        
        # Create processed record
        processed_record = {
            'certificate_number': base_record.get('certificate_number'),
            'dba_name': base_record.get('dba_name'),
            'naic_code_description': base_record.get('naic_code_description'),
            'lic_code_description': base_record.get('lic_code_description'),
            'business_corridor': base_record.get('business_corridor'),
            'supervisor_district': base_record.get('supervisor_district'),
            'full_business_address': address,
            'distinct_address': address,
            'building_address': address,
            'location_lat': self._parse_location(base_record.get('location', {}), 'latitude'),
            'location_lon': self._parse_location(base_record.get('location', {}), 'longitude'),
            'dba_start_date': self._parse_date(base_record.get('dba_start_date')),
            'location_start_date': self._parse_date(base_record.get('location_start_date')),
            'dba_end_date': self._parse_date(base_record.get('dba_end_date')),
            'location_end_date': self._parse_date(base_record.get('location_end_date')),
            'administratively_closed': base_record.get('administratively_closed', False),
            'status': overall_status,
            'is_street_level': True,  # Building level - at least one address is street level
            'business_count': total_business_count,
            'streetfront_open_count': streetfront_open_count,
            'streetfront_closed_count': streetfront_closed_count,
            'total_streetfront_addresses': streetfront_open_count + streetfront_closed_count,
            'upper_floor_open_count': upper_floor_open_count,
            'upper_floor_closed_count': upper_floor_closed_count,
            'total_upper_floor_addresses': total_upper_floor_addresses,
            'individual_addresses_count': len(addresses_data),
            'addresses_data': addresses_data
        }
        
        # Add tax filing matching logic using normalized addresses
        if tax_filing_data:
            # Normalize the business address for matching
            normalized_address = self.normalize_address(address)
            
            # Check if normalized address matches any tax filing data
            if normalized_address in tax_filing_data:
                tax_info = tax_filing_data[normalized_address]
                processed_record['has_commercial_tax_filing'] = True
                processed_record['tax_filing_addresses'] = [normalized_address]
                processed_record['vacancy_status'] = tax_info.get('vacancy_status', 'unfiled')
                processed_record['tax_filing_year'] = tax_info.get('year', 0)
                processed_record['tax_filing_ban'] = tax_info.get('ban', '')
                processed_record['tax_filing_entity'] = tax_info.get('entity', '')
            else:
                processed_record['has_commercial_tax_filing'] = False
                processed_record['tax_filing_addresses'] = []
                processed_record['vacancy_status'] = 'unfiled'
                processed_record['tax_filing_year'] = 0
                processed_record['tax_filing_ban'] = ''
                processed_record['tax_filing_entity'] = ''
        else:
            processed_record['has_commercial_tax_filing'] = False
            processed_record['tax_filing_addresses'] = []
            processed_record['vacancy_status'] = 'unfiled'
            processed_record['tax_filing_year'] = 0
            processed_record['tax_filing_ban'] = ''
            processed_record['tax_filing_entity'] = ''
        
        return processed_record
    
    def _parse_location(self, location: Dict[str, Any], coord_type: str) -> Optional[float]:
        """Parse location coordinates"""
        if not location or not isinstance(location, dict):
            return None
        
        # Handle GeoJSON Point format with coordinates array [longitude, latitude]
        if 'coordinates' in location and isinstance(location['coordinates'], list) and len(location['coordinates']) >= 2:
            if coord_type == 'latitude':
                return float(location['coordinates'][1])  # latitude is second element
            elif coord_type == 'longitude':
                return float(location['coordinates'][0])  # longitude is first element
        
        # Fallback to individual latitude/longitude keys
        coord = location.get(coord_type)
        if coord is None:
            return None
        
        try:
            return float(coord)
        except (ValueError, TypeError):
            return None
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string to datetime object"""
        if not date_str:
            return None
        try:
            from datetime import datetime
            return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
        except:
            return None
    
    def _is_upper_floor_address(self, full_address: str) -> bool:
        """Return True if the address indicates an upper-floor/unit (non-storefront)."""
        if not full_address:
            return False
        import re
        s = full_address.upper()
        # Indicators of units/floors: #digits (allow optional space), APT/UNIT/STE/SUITE/RM/ROOM, FL/FLOOR with optional digits, ordinals
        if re.search(r'#\s*[0-9]+', s):
            return True
        if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
            return True
        if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
            return True
        if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
            return True
        # Trailing numeric token (e.g., "945 TARAVAL ST 1045")
        # But exclude 100s addresses which are typically ground floor (e.g., "2001 UNION ST 107")
        trailing_number_match = re.search(r'\s([0-9]+)\s*$', s)
        if trailing_number_match:
            trailing_number = int(trailing_number_match.group(1))
            # Only consider it upper floor if it's 200 or higher (100s are typically ground floor)
            if trailing_number >= 200:
                return True
        # Addresses ending with letter (e.g., "101 LOMBARD ST 23W", "101 LOMBARD ST 409W")
        if re.search(r'\s[0-9]+[A-Z]\s*$', s):
            return True
        return False
    
    def store_business_cache(self, processed_data: List[Dict[str, Any]]):
        """Store processed business data in the database"""
        logger.info(f"Storing {len(processed_data)} processed business records in database...")
        
        # Clear and insert data in a single transaction
        def clear_and_insert_data(conn):
            cursor = conn.cursor()
            
            # Clear existing data first (only if table exists)
            try:
                cursor.execute(f"DELETE FROM {self.cache_table}")
                logger.info("Cleared existing business cache data")
            except psycopg2.errors.UndefinedTable:
                logger.info("Table doesn't exist yet, skipping delete")
            except Exception as e:
                logger.warning(f"Could not clear table: {e}")
            
            # Insert all data
            successful_inserts = 0
            duplicate_skips = 0
            errors = 0
            
            for i, record in enumerate(processed_data):
                try:
                    # Convert administratively_closed to boolean
                    admin_closed = record.get('administratively_closed', False)
                    if isinstance(admin_closed, str):
                        admin_closed = admin_closed.lower() in ['true', '1', 'yes', 'y', 't']
                    elif admin_closed is None:
                        admin_closed = False
                    
                    insert_sql = f"""
                    INSERT INTO {self.cache_table} (
                        certificate_number, dba_name, naic_code_description, lic_code_description,
                        business_corridor, supervisor_district, full_business_address, distinct_address,
                        building_address, location_lat, location_lon, dba_start_date, location_start_date,
                        dba_end_date, location_end_date, administratively_closed, status, is_street_level,
                        business_count, streetfront_open_count, streetfront_closed_count,
                        total_streetfront_addresses, upper_floor_open_count, upper_floor_closed_count,
                        total_upper_floor_addresses, individual_addresses_count, addresses_data,
                        has_commercial_tax_filing, tax_filing_addresses, vacancy_status, tax_filing_year,
                        tax_filing_ban, tax_filing_entity
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """
                    
                    cursor.execute(insert_sql, (
                        record.get('certificate_number'),
                        record.get('dba_name'),
                        record.get('naic_code_description'),
                        record.get('lic_code_description'),
                        record.get('business_corridor'),
                        record.get('supervisor_district'),
                        record.get('full_business_address'),
                        record.get('distinct_address'),
                        record.get('building_address'),
                        record.get('location_lat'),
                        record.get('location_lon'),
                        record.get('dba_start_date'),
                        record.get('location_start_date'),
                        record.get('dba_end_date'),
                        record.get('location_end_date'),
                        admin_closed,
                        record.get('status'),
                        True,  # is_street_level
                        record.get('business_count'),
                        record.get('streetfront_open_count'),
                        record.get('streetfront_closed_count'),
                        record.get('total_streetfront_addresses'),
                        record.get('upper_floor_open_count'),
                        record.get('upper_floor_closed_count'),
                        record.get('total_upper_floor_addresses'),
                        record.get('individual_addresses_count'),
                        json.dumps(record.get('addresses_data', [])),
                        record.get('has_commercial_tax_filing', False),
                        record.get('tax_filing_addresses', []),
                        record.get('vacancy_status', 'unfiled'),
                        record.get('tax_filing_year', 0),
                        record.get('tax_filing_ban', ''),
                        record.get('tax_filing_entity', '')
                    ))
                    successful_inserts += 1
                    
                except psycopg2.errors.UniqueViolation as e:
                    duplicate_skips += 1
                    if duplicate_skips <= 5:
                        logger.debug(f"Skipping duplicate certificate_number: {record.get('certificate_number')} - {e}")
                    continue
                except Exception as e:
                    errors += 1
                    if errors <= 10:  # Show more errors to catch the first one
                        logger.error(f"Error inserting record {i+1} ({record.get('certificate_number')}): {e}")
                        logger.error(f"Error type: {type(e).__name__}")
                        if errors == 1:  # Log the first error in detail
                            logger.error(f"FIRST ERROR DETAILS:")
                            logger.error(f"  Record index: {i+1}")
                            logger.error(f"  Certificate: {record.get('certificate_number')}")
                            logger.error(f"  DBA Name: {record.get('dba_name')}")
                            logger.error(f"  Address: {record.get('full_business_address')}")
                            logger.error(f"  Status: {record.get('status')}")
                            logger.error(f"  Location: {record.get('location_lat')}, {record.get('location_lon')}")
                            logger.error(f"  Business count: {record.get('business_count')}")
                            logger.error(f"  Addresses data length: {len(record.get('addresses_data', []))}")
                    continue
            
            cursor.close()
            return {
                'successful_inserts': successful_inserts,
                'duplicate_skips': duplicate_skips,
                'errors': errors
            }
        
        result = execute_with_connection(clear_and_insert_data)
        
        if result.get('status') == 'success':
            stats = result.get('result', {})
            logger.info(f"Successfully inserted {stats.get('successful_inserts', 0)} records, skipped {stats.get('duplicate_skips', 0)} duplicates, {stats.get('errors', 0)} errors")
        else:
            logger.error(f"Failed to store business cache: {result.get('message', 'Unknown error')}")
        
        logger.info("Business cache stored successfully")
    
    def create_and_populate_cache(self, processed_data: List[Dict[str, Any]]):
        """Create tables and populate with data in a single transaction"""
        def create_and_populate(conn):
            cursor = conn.cursor()
            
            # Create business cache table
            business_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {self.cache_table} (
                id SERIAL PRIMARY KEY,
                certificate_number VARCHAR(50),
                dba_name VARCHAR(500),
                naic_code_description TEXT,
                lic_code_description TEXT,
                business_corridor VARCHAR(200),
                supervisor_district VARCHAR(10),
                full_business_address TEXT,
                distinct_address TEXT,
                building_address TEXT,
                location_lat FLOAT,
                location_lon FLOAT,
                dba_start_date DATE,
                location_start_date DATE,
                dba_end_date DATE,
                location_end_date DATE,
                administratively_closed BOOLEAN DEFAULT FALSE,
                status VARCHAR(20),
                is_street_level BOOLEAN DEFAULT TRUE,
                business_count INTEGER DEFAULT 1,
                streetfront_open_count INTEGER DEFAULT 0,
                streetfront_closed_count INTEGER DEFAULT 0,
                total_streetfront_addresses INTEGER DEFAULT 0,
                upper_floor_open_count INTEGER DEFAULT 0,
                upper_floor_closed_count INTEGER DEFAULT 0,
                total_upper_floor_addresses INTEGER DEFAULT 0,
                individual_addresses_count INTEGER DEFAULT 1,
                addresses_data JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            
            cursor.execute(business_table_sql)
            logger.info("✅ Business table created")
            
            # Add created_at column if it doesn't exist (for existing tables)
            try:
                cursor.execute(f"""
                    ALTER TABLE {self.cache_table} 
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("✅ Added created_at column if not exists")
            except Exception as e:
                logger.warning(f"Could not add created_at column: {e}")
            
            # Drop the old single-column unique constraint if it exists
            try:
                cursor.execute(f"""
                    ALTER TABLE {self.cache_table} 
                    DROP CONSTRAINT IF EXISTS business_cache_certificate_number_key
                """)
                logger.info("✅ Dropped old single-column unique constraint")
            except Exception as e:
                logger.warning(f"Could not drop old constraint: {e}")
            
            # Add the new composite unique constraint (only if it doesn't exist)
            try:
                cursor.execute(f"""
                    DO $$ 
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint 
                            WHERE conname = 'business_cache_certificate_address_unique'
                        ) THEN
                            ALTER TABLE {self.cache_table} 
                            ADD CONSTRAINT business_cache_certificate_address_unique 
                            UNIQUE (certificate_number, full_business_address);
                        END IF;
                    END $$;
                """)
                logger.info("✅ Added composite unique constraint")
            except Exception as e:
                logger.warning(f"Could not add composite constraint: {e}")
            
            # Create indexes
            indexes_sql = [
                f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_dba_name ON {self.cache_table} (dba_name);",
                f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_certificate ON {self.cache_table} (certificate_number);",
                f"CREATE INDEX IF NOT EXISTS idx_{self.cache_table}_created_at ON {self.cache_table} (created_at);",
            ]
            
            for i, index_sql in enumerate(indexes_sql):
                try:
                    cursor.execute(index_sql)
                    logger.info(f"✅ Index {i+1} created")
                except Exception as e:
                    logger.warning(f"Failed to create index {i+1}: {e}")
            
            # Clear existing data
            try:
                cursor.execute(f"DELETE FROM {self.cache_table}")
                logger.info("Cleared existing business cache data")
            except psycopg2.errors.UndefinedTable:
                logger.info("Table doesn't exist yet, skipping delete")
            except Exception as e:
                logger.warning(f"Could not clear table: {e}")
            
            # Insert all data
            successful_inserts = 0
            duplicate_skips = 0
            errors = 0
            
            for i, record in enumerate(processed_data):
                try:
                    # Convert administratively_closed to boolean
                    admin_closed = record.get('administratively_closed', False)
                    if isinstance(admin_closed, str):
                        admin_closed = admin_closed.lower() in ['true', '1', 'yes', 'y', 't']
                    elif admin_closed is None:
                        admin_closed = False
                    
                    insert_sql = f"""
                    INSERT INTO {self.cache_table} (
                        certificate_number, dba_name, naic_code_description, lic_code_description,
                        business_corridor, supervisor_district, full_business_address, distinct_address,
                        building_address, location_lat, location_lon, dba_start_date, location_start_date,
                        dba_end_date, location_end_date, administratively_closed, status, is_street_level,
                        business_count, streetfront_open_count, streetfront_closed_count,
                        total_streetfront_addresses, upper_floor_open_count, upper_floor_closed_count,
                        total_upper_floor_addresses, individual_addresses_count, addresses_data,
                        has_commercial_tax_filing, tax_filing_addresses, vacancy_status, tax_filing_year,
                        tax_filing_ban, tax_filing_entity
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """
                    
                    cursor.execute(insert_sql, (
                        record.get('certificate_number'),
                        record.get('dba_name'),
                        record.get('naic_code_description'),
                        record.get('lic_code_description'),
                        record.get('business_corridor'),
                        record.get('supervisor_district'),
                        record.get('full_business_address'),
                        record.get('distinct_address'),
                        record.get('building_address'),
                        record.get('location_lat'),
                        record.get('location_lon'),
                        record.get('dba_start_date'),
                        record.get('location_start_date'),
                        record.get('dba_end_date'),
                        record.get('location_end_date'),
                        admin_closed,
                        record.get('status'),
                        True,  # is_street_level
                        record.get('business_count'),
                        record.get('streetfront_open_count'),
                        record.get('streetfront_closed_count'),
                        record.get('total_streetfront_addresses'),
                        record.get('upper_floor_open_count'),
                        record.get('upper_floor_closed_count'),
                        record.get('total_upper_floor_addresses'),
                        record.get('individual_addresses_count'),
                        json.dumps(record.get('addresses_data', [])),
                        record.get('has_commercial_tax_filing', False),
                        record.get('tax_filing_addresses', []),
                        record.get('vacancy_status', 'unfiled'),
                        record.get('tax_filing_year', 0),
                        record.get('tax_filing_ban', ''),
                        record.get('tax_filing_entity', '')
                    ))
                    successful_inserts += 1
                    
                except psycopg2.errors.UniqueViolation as e:
                    duplicate_skips += 1
                    if duplicate_skips <= 5:
                        logger.debug(f"Skipping duplicate certificate_number: {record.get('certificate_number')} - {e}")
                    continue
                except Exception as e:
                    errors += 1
                    if errors <= 10:  # Show more errors to catch the first one
                        logger.error(f"Error inserting record {i+1} ({record.get('certificate_number')}): {e}")
                        logger.error(f"Error type: {type(e).__name__}")
                        if errors == 1:  # Log the first error in detail
                            logger.error(f"FIRST ERROR DETAILS:")
                            logger.error(f"  Record index: {i+1}")
                            logger.error(f"  Certificate: {record.get('certificate_number')}")
                            logger.error(f"  DBA Name: {record.get('dba_name')}")
                            logger.error(f"  Address: {record.get('full_business_address')}")
                            logger.error(f"  Status: {record.get('status')}")
                            logger.error(f"  Location: {record.get('location_lat')}, {record.get('location_lon')}")
                            logger.error(f"  Business count: {record.get('business_count')}")
                            logger.error(f"  Addresses data length: {len(record.get('addresses_data', []))}")
                    continue
            
            cursor.close()
            return {
                'successful_inserts': successful_inserts,
                'duplicate_skips': duplicate_skips,
                'errors': errors
            }
        
        result = execute_with_connection(create_and_populate)
        
        if result.get('status') == 'success':
            stats = result.get('result', {})
            logger.info(f"Successfully inserted {stats.get('successful_inserts', 0)} records, skipped {stats.get('duplicate_skips', 0)} duplicates, {stats.get('errors', 0)} errors")
        else:
            logger.error(f"Failed to create and populate cache: {result.get('message', 'Unknown error')}")
    
    def refresh_cache(self, limit: Optional[int] = None):
        """Refresh the entire business cache"""
        logger.info(f"Starting business cache refresh with limit: {limit}")
        start_time = datetime.now()
        
        try:
            # Fetch and process business data first
            logger.info("Step 1: Fetching business data...")
            business_data = self.fetch_all_business_data(limit)
            logger.info(f"✅ Fetched {len(business_data)} business records")
            
            logger.info("Step 2: Fetching and processing commercial tax filing data...")
            tax_filing_data = self.fetch_commercial_tax_filing_data()
            logger.info(f"✅ Processed {len(tax_filing_data)} tax filing addresses with vacancy status")
            
            logger.info("Step 3: Processing business data with tax filing matching...")
            processed_data = self.process_business_data(business_data, tax_filing_data)
            logger.info(f"✅ Processed {len(processed_data)} business records")
            
            # Create tables and store data in a single transaction
            logger.info("Step 4: Creating tables and storing data...")
            self.create_and_populate_cache(processed_data)
            logger.info("✅ Data stored successfully")
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"🎉 Business cache refresh completed in {duration:.2f} seconds")
            logger.info(f"Stored {len(processed_data)} records")
            
            return {
                'status': 'success',
                'business_records': len(processed_data),
                'tax_records': 0,
                'duration_seconds': duration
            }
            
        except Exception as e:
            logger.error(f"Error refreshing business cache: {str(e)}")
            raise e
    
    def add_to_cache(self, limit: Optional[int] = None):
        """Add more data to existing business cache without clearing"""
        logger.info(f"Adding {limit} more records to business cache...")
        start_time = datetime.now()
        
        try:
            # Fetch and process business data first
            logger.info("Step 1: Fetching business data...")
            business_data = self.fetch_all_business_data(limit)
            logger.info(f"✅ Fetched {len(business_data)} business records")
            
            logger.info("Step 2: Processing business data...")
            processed_data = self.process_business_data(business_data)
            logger.info(f"✅ Processed {len(processed_data)} business records")
            
            # Store data without clearing existing cache
            logger.info("Step 3: Adding data to existing cache...")
            self.add_business_cache(processed_data)
            logger.info("✅ Data added successfully")
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"🎉 Business cache addition completed in {duration:.2f} seconds")
            logger.info(f"Added {len(processed_data)} records")
            
            return {
                'status': 'success',
                'business_records': len(processed_data),
                'tax_records': 0,
                'duration_seconds': duration
            }
            
        except Exception as e:
            logger.error(f"Error adding to business cache: {str(e)}")
            raise e
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the cached data"""
        def get_stats(conn):
            cursor = conn.cursor()
            
            # Get total business records
            cursor.execute(f"SELECT COUNT(*) FROM {self.cache_table}")
            total_count = cursor.fetchone()[0]
            
            # Get open businesses count
            cursor.execute(f"SELECT COUNT(*) FROM {self.cache_table} WHERE status = 'Occupied'")
            open_count = cursor.fetchone()[0]
            
            # Get closed businesses count  
            cursor.execute(f"SELECT COUNT(*) FROM {self.cache_table} WHERE status = 'Vacant'")
            closed_count = cursor.fetchone()[0]
            
            # Get last updated timestamp
            cursor.execute(f"SELECT MAX(processed_at) FROM {self.cache_table}")
            last_updated = cursor.fetchone()[0]
            
            cursor.close()
            
            return {
                'business_records': total_count,
                'open_businesses': open_count,
                'closed_businesses': closed_count,
                'tax_records': 0,  # Tax records are now part of business_cache
                'last_updated': last_updated.isoformat() if last_updated else None
            }
        
        try:
            result = execute_with_connection(get_stats)
            if result.get('status') == 'success':
                return result.get('result', {})
            else:
                logger.error(f"Database operation failed: {result.get('message', 'Unknown error')}")
                return {
                    'business_records': 0,
                    'open_businesses': 0,
                    'closed_businesses': 0,
                    'tax_records': 0,
                    'last_updated': None
                }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {
                'business_records': 0,
                'open_businesses': 0,
                'closed_businesses': 0,
                'tax_records': 0,
                'last_updated': None
            }
