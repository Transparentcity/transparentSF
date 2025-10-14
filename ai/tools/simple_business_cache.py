"""
Simple Business Cache Processor

This module provides a simple, efficient cache that mirrors DataSF tables.
No processing, no joining - just raw data storage for fast querying.

Architecture:
1. Fetch raw data from DataSF APIs
2. Store in PostgreSQL tables as-is
3. All processing/joining happens in vacancy_analysis.py
"""

import logging
import time
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Any
from datetime import datetime
import os
from dotenv import load_dotenv
from ai.tools.data_fetcher import fetch_data_from_api

# Load environment variables
load_dotenv('ai/.env')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SimpleBusinessCache:
    """Simple cache that mirrors DataSF business and tax data"""
    
    def __init__(self, db_url: Optional[str] = None, batch_size: int = 1000):
        """Initialize cache processor"""
        self.db_url = db_url or os.getenv('DATABASE_URL')
        self.batch_size = batch_size
        self.business_table = 'business_registrations_cache'
        self.tax_table = 'commercial_tax_cache'
        
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
    
    def extract_building_address(self, full_address):
        """Extract building address without unit letters/numbers for normalized joins"""
        if not full_address:
            return None

        import re

        # Convert to uppercase for consistent processing
        address = full_address.upper()

        # Remove common unit patterns more comprehensively
        # Apply patterns in order of specificity (most specific first)

        # Pattern 1: Handle unit letters that appear after street number (like "2139 A POLK ST")
        address = re.sub(r'(\d+)\s+([A-Z](?:\s+[A-Z])*\s+)([A-Z]+)', r'\1 \3', address)
        
        # Pattern 1a: Handle single unit letter after street number (like "2139 B POLK ST")
        address = re.sub(r'(\d+)\s+([A-Z])\s+([A-Z]+)', r'\1 \3', address)

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
    
    def is_upper_floor_address(self, full_address: str) -> bool:
        """
        Return True if the address indicates an upper-floor/unit (non-storefront).
        This helps identify street-level commercial spaces vs upper floor units.
        """
        if not full_address:
            return False
        
        import re
        s = full_address.upper()
        
        # Indicators of units/floors: #digits, APT/UNIT/STE/SUITE/RM/ROOM, FL/FLOOR
        if re.search(r'#\s*[0-9]+', s):
            return True
        if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
            return True
        if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
            return True
        if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
            return True
        
        # Trailing numeric token (e.g., "945 TARAVAL ST 1045")
        # But exclude 100s addresses which are typically ground floor
        trailing_number_match = re.search(r'\s([0-9]+)\s*$', s)
        if trailing_number_match:
            trailing_number = int(trailing_number_match.group(1))
            # Only consider it upper floor if it's 200 or higher
            if trailing_number >= 200:
                return True
        
        # Addresses ending with letter+number combo (e.g., "101 LOMBARD ST 23W")
        if re.search(r'\s[0-9]+[A-Z]\s*$', s):
            return True
        
        return False
    
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_url)
    
    def create_tables(self):
        """Create cache tables if they don't exist"""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Business registrations cache - mirrors DataSF g8m3-pdis dataset
                cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.business_table} (
                    id SERIAL PRIMARY KEY,
                    certificate_number TEXT,
                    ttxid TEXT,
                    dba_name TEXT,
                    ownership_name TEXT,
                    full_business_address TEXT,
                    normalized_address TEXT,
                    is_street_level BOOLEAN DEFAULT TRUE,
                    has_commercial_tax_filing BOOLEAN DEFAULT FALSE,
                    dba_start_date TIMESTAMP,
                    dba_end_date TIMESTAMP,
                    location_start_date TIMESTAMP,
                    location_end_date TIMESTAMP,
                    administratively_closed BOOLEAN,
                    naic_code_description TEXT,
                    lic_code_description TEXT,
                    business_corridor TEXT,
                    supervisor_district TEXT,
                    location_lat DOUBLE PRECISION,
                    location_lon DOUBLE PRECISION,
                    mailing_address TEXT,
                    city TEXT,
                    state TEXT,
                    zipcode TEXT,
                    business_account_number TEXT,
                    parking_tax BOOLEAN,
                    transient_occupancy_tax BOOLEAN,
                    lic_code TEXT,
                    naic_code TEXT,
                    neighborhoods_analysis_boundaries TEXT,
                    raw_data JSONB,
                    fetched_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(certificate_number, full_business_address)
                )
                """)
                
                # Create indexes for fast lookups
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_address 
                ON {self.business_table}(full_business_address)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_normalized_address 
                ON {self.business_table}(normalized_address)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_cert 
                ON {self.business_table}(certificate_number)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_district 
                ON {self.business_table}(supervisor_district)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_street_level 
                ON {self.business_table}(is_street_level)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_has_tax_filing 
                ON {self.business_table}(has_commercial_tax_filing)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_ownership_name 
                ON {self.business_table}(ownership_name)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_business_ttxid 
                ON {self.business_table}(ttxid)
                """)
                
                # Commercial tax cache - mirrors DataSF commercial tax dataset
                # No UNIQUE constraint here because LIN can be NULL
                # Deduplication is handled in Python before insert
                cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.tax_table} (
                    id SERIAL PRIMARY KEY,
                    lin TEXT,
                    linaddress TEXT,
                    normalized_linaddress TEXT,
                    ban TEXT,
                    entity TEXT,
                    address TEXT,
                    normalized_address TEXT,
                    filed TEXT,
                    vacancy_status TEXT,
                    year INTEGER,
                    assessor_parcel_number TEXT,
                    block TEXT,
                    lot TEXT,
                    supervisor_district TEXT,
                    raw_data JSONB,
                    fetched_at TIMESTAMP DEFAULT NOW()
                )
                """)
                
                # Create indexes for fast lookups
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_address 
                ON {self.tax_table}(address)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_normalized_address 
                ON {self.tax_table}(normalized_address)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_ban 
                ON {self.tax_table}(ban)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_district 
                ON {self.tax_table}(supervisor_district)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_lin 
                ON {self.tax_table}(lin)
                """)
                
                cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_tax_normalized_linaddress 
                ON {self.tax_table}(normalized_linaddress)
                """)
                
                conn.commit()
                logger.info("Cache tables created successfully")
    
    def fetch_business_data(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch raw business registration data from DataSF"""
        logger.info("Fetching business registration data from DataSF...")
        
        # Use proper SoQL SELECT query - only include fields that actually exist
        query = """
        SELECT 
            certificate_number,
            ttxid,
            ownership_name,
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
            location,
            mailing_address_1,
            parking_tax,
            transient_occupancy_tax
        WHERE certificate_number IS NOT NULL
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        query_object = {
            "endpoint": "g8m3-pdis",
            "query": query
        }
        
        result = fetch_data_from_api(query_object)
        data = result.get('data', []) if isinstance(result, dict) else result
        logger.info(f"Fetched {len(data):,} business records")
        return data
    
    def fetch_tax_data(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch raw commercial tax data from DataSF"""
        logger.info("Fetching commercial tax data from DataSF...")
        
        # Use proper SoQL SELECT query for commercial tax filings
        # Fetch ALL records, not just those with BAN
        query = """
        SELECT 
            lin,
            linaddress,
            ban,
            entity,
            parcelsitusaddress as address,
            filed,
            vacant as vacancy_status,
            taxyear as year,
            parcelnumber as assessor_parcel_number,
            block,
            lot,
            supervisor_district
        ORDER BY taxyear DESC
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        query_object = {
            "endpoint": "rzkk-54yv",  # Commercial tax dataset
            "query": query
        }
        
        result = fetch_data_from_api(query_object)
        data = result.get('data', []) if isinstance(result, dict) else result
        logger.info(f"Fetched {len(data):,} commercial tax records")
        return data
    
    def _parse_timestamp(self, date_str: str) -> Optional[datetime]:
        """Parse date string to datetime"""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None
    
    def store_business_data(self, business_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Store raw business data in cache"""
        logger.info(f"Storing {len(business_data):,} business records...")
        
        # Deduplicate: Keep only one record per certificate_number + address combination
        seen = {}
        deduped_data = []
        for record in business_data:
            cert = record.get('certificate_number')
            address = record.get('full_business_address')
            key = (cert, address)
            
            if key not in seen:
                seen[key] = record
                deduped_data.append(record)
        
        if len(deduped_data) < len(business_data):
            logger.info(f"Deduplicated from {len(business_data):,} to {len(deduped_data):,} unique certificate+address combinations")
        
        # Clear existing data first (separate transaction)
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.business_table}")
                conn.commit()
                logger.info("Cleared existing business cache data")
        
        # Prepare batch insert SQL
        insert_sql = f"""
        INSERT INTO {self.business_table} (
            certificate_number, ttxid, dba_name, ownership_name, full_business_address, normalized_address,
            is_street_level, has_commercial_tax_filing,
            dba_start_date, dba_end_date, location_start_date, location_end_date,
            administratively_closed, naic_code_description, lic_code_description,
            business_corridor, supervisor_district, location_lat, location_lon,
            mailing_address, city, state, zipcode, business_account_number,
            parking_tax, transient_occupancy_tax, lic_code, naic_code,
            neighborhoods_analysis_boundaries, raw_data
        ) VALUES %s
        ON CONFLICT (certificate_number, full_business_address) DO UPDATE SET
            ttxid = EXCLUDED.ttxid,
            dba_name = EXCLUDED.dba_name,
            ownership_name = EXCLUDED.ownership_name,
            normalized_address = EXCLUDED.normalized_address,
            is_street_level = EXCLUDED.is_street_level,
            dba_start_date = EXCLUDED.dba_start_date,
            dba_end_date = EXCLUDED.dba_end_date,
            location_start_date = EXCLUDED.location_start_date,
            location_end_date = EXCLUDED.location_end_date,
            administratively_closed = EXCLUDED.administratively_closed,
            fetched_at = NOW()
        """
        
        successful_inserts = 0
        errors = 0
        total_batches = (len(deduped_data) + self.batch_size - 1) // self.batch_size
        
        # Process in batches with individual transactions
        for batch_num in range(total_batches):
            start_idx = batch_num * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(deduped_data))
            batch = deduped_data[start_idx:end_idx]
            
            logger.info(f"Processing batch {batch_num + 1}/{total_batches} ({len(batch):,} records)")
            
            try:
                # Each batch gets its own transaction
                with self.get_db_connection() as conn:
                    with conn.cursor() as cur:
                        # Prepare batch data
                        batch_values = []
                        for record in batch:
                            # Extract location coordinates from GeoJSON if available
                            lat = None
                            lon = None
                            if 'location' in record and record['location']:
                                location = record['location']
                                if isinstance(location, dict) and 'coordinates' in location:
                                    coords = location['coordinates']
                                    if len(coords) >= 2:
                                        lon = float(coords[0])
                                        lat = float(coords[1])
                            
                            # Parse administratively_closed - can be boolean or string
                            admin_closed = record.get('administratively_closed', False)
                            if isinstance(admin_closed, str):
                                admin_closed = admin_closed.strip().lower() in ('true', 'yes', '***administratively closed')
                            elif admin_closed is None:
                                admin_closed = False
                            
                            # Normalize address for joins
                            full_address = record.get('full_business_address')
                            normalized_address = self.extract_building_address(full_address)
                            
                            # Determine if this is a street-level location (not upper floor)
                            is_street_level = not self.is_upper_floor_address(full_address)
                            
                            values = (
                                record.get('certificate_number'),
                                record.get('ttxid'),
                                record.get('dba_name'),
                                record.get('ownership_name'),
                                full_address,
                                normalized_address,
                                is_street_level,  # Computed flag
                                False,  # has_commercial_tax_filing - will be updated later
                                self._parse_timestamp(record.get('dba_start_date')),
                                self._parse_timestamp(record.get('dba_end_date')),
                                self._parse_timestamp(record.get('location_start_date')),
                                self._parse_timestamp(record.get('location_end_date')),
                                admin_closed,
                                record.get('naic_code_description'),
                                record.get('lic_code_description'),
                                record.get('business_corridor'),
                                record.get('supervisor_district'),
                                lat,
                                lon,
                                record.get('mailing_address_1'),
                                record.get('city'),
                                record.get('state'),
                                record.get('business_zip'),
                                None,  # business_account_number (not in dataset)
                                record.get('parking_tax', False),
                                record.get('transient_occupancy_tax', False),
                                record.get('lic'),
                                record.get('naic_code'),
                                record.get('neighborhoods_analysis_boundaries'),
                                psycopg2.extras.Json(record)  # Store raw data as JSONB
                            )
                            batch_values.append(values)
                        
                        # Execute batch insert
                        psycopg2.extras.execute_values(
                            cur, insert_sql, batch_values, page_size=self.batch_size
                        )
                        conn.commit()  # Commit this batch
                        successful_inserts += len(batch)
                        
            except Exception as e:
                logger.error(f"Error in batch {batch_num + 1}: {e}")
                errors += len(batch)
                # Transaction auto-rolls back, continue with next batch
        
        logger.info(f"Stored {successful_inserts:,} business records ({errors} errors)")
        
        return {
            'total': len(deduped_data),
            'successful': successful_inserts,
            'errors': errors
        }
    
    def store_tax_data(self, tax_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Store raw commercial tax data in cache"""
        logger.info(f"Storing {len(tax_data):,} tax records...")
        
        # Deduplicate: Use (lin, year) when available, otherwise (parcelnumber, year)
        seen = {}
        for record in tax_data:
            lin = record.get('lin')
            year = int(record.get('year', 0)) if record.get('year') else 0
            
            # Choose deduplication key based on available fields
            if lin:
                # Preferred: LIN + year (most precise)
                key = ('lin', lin, year)
            else:
                # Fallback: parcelnumber + year (each parcel has one filing per year)
                parcel = record.get('assessor_parcel_number', '')
                key = ('parcel', parcel, year)
            
            # Keep first occurrence of each unique combination
            if key not in seen:
                seen[key] = record
        
        deduped_data = list(seen.values())
        logger.info(f"Deduplicated to {len(deduped_data):,} unique records (from {len(tax_data):,})")
        logger.info(f"  - With LIN: {sum(1 for r in deduped_data if r.get('lin'))}")
        logger.info(f"  - Without LIN (using parcel): {sum(1 for r in deduped_data if not r.get('lin'))}")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Clear existing data
                cur.execute(f"DELETE FROM {self.tax_table}")
                logger.info("Cleared existing tax cache data")
                
                # Prepare batch insert (no ON CONFLICT since we handle deduplication in Python)
                insert_sql = f"""
                INSERT INTO {self.tax_table} (
                    lin, linaddress, normalized_linaddress, ban, entity, address, normalized_address, 
                    filed, vacancy_status, year, assessor_parcel_number, block, lot, supervisor_district, raw_data
                ) VALUES %s
                """
                
                successful_inserts = 0
                errors = 0
                total_batches = (len(deduped_data) + self.batch_size - 1) // self.batch_size
                
                # Process in batches
                for batch_num in range(total_batches):
                    start_idx = batch_num * self.batch_size
                    end_idx = min(start_idx + self.batch_size, len(deduped_data))
                    batch = deduped_data[start_idx:end_idx]
                    
                    logger.info(f"Processing batch {batch_num + 1}/{total_batches} ({len(batch):,} records)")
                    
                    try:
                        # Prepare batch data
                        batch_values = []
                        for record in batch:
                            # Normalize addresses for joins (both parcelsitusaddress and linaddress)
                            address = record.get('address')
                            normalized_address = self.extract_building_address(address)
                            
                            linaddress = record.get('linaddress')
                            normalized_linaddress = self.extract_building_address(linaddress) if linaddress else None
                            
                            values = (
                                record.get('lin'),
                                linaddress,
                                normalized_linaddress,
                                record.get('ban'),
                                record.get('entity'),
                                address,
                                normalized_address,
                                record.get('filed'),
                                record.get('vacancy_status'),
                                int(record.get('year', 0)) if record.get('year') else None,
                                record.get('assessor_parcel_number'),
                                record.get('block'),
                                record.get('lot'),
                                record.get('supervisor_district'),
                                psycopg2.extras.Json(record)  # Store raw data as JSONB
                            )
                            batch_values.append(values)
                        
                        # Execute batch insert
                        psycopg2.extras.execute_values(
                            cur, insert_sql, batch_values, page_size=self.batch_size
                        )
                        successful_inserts += len(batch)
                        
                    except Exception as e:
                        logger.error(f"Error in batch {batch_num + 1}: {e}")
                        errors += len(batch)
                
                conn.commit()
                logger.info(f"Stored {successful_inserts:,} tax records ({errors} errors)")
                
                return {
                    'total': len(tax_data),
                    'successful': successful_inserts,
                    'errors': errors
                }
    
    def update_tax_filing_flags(self) -> int:
        """
        Update has_commercial_tax_filing flags by joining business and tax caches.
        Uses multiple matching strategies: LIN/ttxid, BAN+address, and address matching.
        Returns the number of businesses updated.
        """
        logger.info("Updating has_commercial_tax_filing flags...")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # First, reset ALL flags to False
                cur.execute(f"""
                    UPDATE {self.business_table}
                    SET has_commercial_tax_filing = FALSE
                """)
                logger.info(f"Reset all flags to False")
                
                # Then, set to TRUE only for businesses that have matching tax filings using any of:
                # 1. LIN (ttxid) match
                # 2. BAN (certificate_number) match WITH address verification
                # 3. Address match (parcelsitusaddress OR linaddress)
                cur.execute(f"""
                    UPDATE {self.business_table} b
                    SET has_commercial_tax_filing = TRUE
                    FROM {self.tax_table} t
                    WHERE (
                        -- Match 1: LIN/ttxid match (most precise)
                        (b.ttxid = t.lin AND b.ttxid IS NOT NULL AND t.lin IS NOT NULL)
                        OR
                        -- Match 2: BAN/certificate_number match WITH address verification
                        -- Only match BAN if addresses also align (prevents false matches for property managers)
                        (
                            b.certificate_number = t.ban 
                            AND b.certificate_number IS NOT NULL 
                            AND t.ban IS NOT NULL
                            AND (
                                b.normalized_address = t.normalized_address
                                OR b.normalized_address = t.normalized_linaddress
                            )
                        )
                        OR
                        -- Match 3a: Address match via parcelsitusaddress
                        (b.normalized_address = t.normalized_address AND b.normalized_address IS NOT NULL AND t.normalized_address IS NOT NULL)
                        OR
                        -- Match 3b: Address match via linaddress
                        (b.normalized_address = t.normalized_linaddress AND b.normalized_address IS NOT NULL AND t.normalized_linaddress IS NOT NULL)
                    )
                """)
                updated_count = cur.rowcount
                conn.commit()
                
                logger.info(f"Set {updated_count:,} businesses to have tax filing flags")
                logger.info(f"  - Matched by LIN/ttxid, BAN+address, or address")
                return updated_count
    
    def refresh_cache(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Refresh both business and tax caches"""
        logger.info("=== Starting Simple Cache Refresh ===")
        start_time = time.time()
        
        try:
            # Create tables
            self.create_tables()
            
            # Fetch and store business data
            business_data = self.fetch_business_data(limit)
            business_stats = self.store_business_data(business_data)
            
            # Fetch and store tax data
            tax_data = self.fetch_tax_data(limit)
            tax_stats = self.store_tax_data(tax_data)
            
            # Update has_commercial_tax_filing flags
            tax_filing_count = self.update_tax_filing_flags()
            
            elapsed_time = time.time() - start_time
            
            result = {
                'status': 'success',
                'business_cache': business_stats,
                'tax_cache': tax_stats,
                'tax_filing_matches': tax_filing_count,
                'elapsed_time': elapsed_time
            }
            
            logger.info(f"=== Cache Refresh Complete ({elapsed_time:.2f}s) ===")
            return result
            
        except Exception as e:
            logger.error(f"Cache refresh failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'elapsed_time': time.time() - start_time
            }
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Business cache stats
                cur.execute(f"SELECT COUNT(*) FROM {self.business_table}")
                business_count = cur.fetchone()[0]
                
                cur.execute(f"SELECT MAX(fetched_at) FROM {self.business_table}")
                business_last_updated = cur.fetchone()[0]
                
                # Tax cache stats
                cur.execute(f"SELECT COUNT(*) FROM {self.tax_table}")
                tax_count = cur.fetchone()[0]
                
                cur.execute(f"SELECT MAX(fetched_at) FROM {self.tax_table}")
                tax_last_updated = cur.fetchone()[0]
                
                return {
                    'business_registrations': {
                        'count': business_count,
                        'last_updated': business_last_updated.isoformat() if business_last_updated else None
                    },
                    'commercial_tax': {
                        'count': tax_count,
                        'last_updated': tax_last_updated.isoformat() if tax_last_updated else None
                    }
                }


if __name__ == "__main__":
    import sys
    
    cache = SimpleBusinessCache()
    
    if len(sys.argv) > 1 and sys.argv[1] == 'stats':
        stats = cache.get_cache_stats()
        print("\n=== Cache Statistics ===")
        print(f"Business Registrations: {stats['business_registrations']['count']:,}")
        print(f"Last Updated: {stats['business_registrations']['last_updated']}")
        print(f"\nCommercial Tax Records: {stats['commercial_tax']['count']:,}")
        print(f"Last Updated: {stats['commercial_tax']['last_updated']}")
    else:
        # Default: refresh cache
        limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
        result = cache.refresh_cache(limit)
        print(f"\n{result}")

