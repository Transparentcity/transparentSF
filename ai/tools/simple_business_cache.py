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
import json
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Any
from datetime import datetime
import os
from dotenv import load_dotenv
from ai.tools.data_fetcher import fetch_data_from_api
from ai.tools.db_utils import get_pooled_connection

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
        
        # Addresses with unit/apartment numbers using hyphen or underscore
        # (e.g., "2443 FILLMORE ST 380-2710", "2443 FILLMORE ST 380_2266")
        # This pattern indicates unit/apartment numbers, not street-level addresses
        if re.search(r'\s\d+[-_]\d+', s):
            return True
        
        return False
    
    def get_db_connection(self):
        """Get database connection from pool"""
        # Use connection pool to avoid connection exhaustion and locks
        return get_pooled_connection()
    
    def _get_db_connection_raw(self):
        """Get raw database connection (fallback if pool fails)"""
        return psycopg2.connect(self.db_url)
    
    def create_tables(self):
        """Create cache tables if they don't exist"""
        logger.info("Creating/updating cache tables...")
        try:
            logger.info("  Connecting to database (using connection pool)...")
            with get_pooled_connection() as conn:
                logger.info("  ✅ Database connection established")
                with conn.cursor() as cur:
                    logger.info(f"  Creating {self.business_table} table...")
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
                    zoning_district TEXT,
                    raw_data JSONB,
                    fetched_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(certificate_number, full_business_address)
                )
                """)
                    logger.info(f"  ✅ {self.business_table} table ready")
                
                    # Create indexes for fast lookups - check existence first to avoid hanging
                    logger.info("  Creating business table indexes (this may take a moment if table is large)...")
                    index_names = [
                        ('idx_business_address', 'full_business_address'),
                        ('idx_business_normalized_address', 'normalized_address'),
                        ('idx_business_cert', 'certificate_number'),
                        ('idx_business_district', 'supervisor_district'),
                        ('idx_business_street_level', 'is_street_level'),
                        ('idx_business_has_tax_filing', 'has_commercial_tax_filing'),
                        ('idx_business_ownership_name', 'ownership_name'),
                        ('idx_business_ttxid', 'ttxid'),
                    ]
                    
                    for idx_name, col_name in index_names:
                        try:
                            # Check if index already exists
                            cur.execute("""
                                SELECT EXISTS (
                                    SELECT 1 FROM pg_indexes 
                                    WHERE tablename = %s AND indexname = %s
                                )
                            """, (self.business_table, idx_name))
                            if cur.fetchone()[0]:
                                logger.debug(f"    Index {idx_name} already exists, skipping")
                                continue
                            
                            logger.info(f"    Creating index {idx_name}...")
                            cur.execute(f"""
                                CREATE INDEX {idx_name} 
                                ON {self.business_table}({col_name})
                            """)
                            logger.info(f"    ✅ Index {idx_name} created")
                        except Exception as e:
                            logger.warning(f"    ⚠️  Could not create index {idx_name}: {e}")
                
                    # Check if zoning_district column exists, add if missing (for existing tables)
                    logger.info("  Checking for zoning_district column...")
                    cur.execute(f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = '{self.business_table}' 
                        AND column_name = 'zoning_district'
                    """)
                    if not cur.fetchone():
                        logger.info("  Adding zoning_district column to existing table (this may take a moment)...")
                        cur.execute(f"""
                            ALTER TABLE {self.business_table} 
                            ADD COLUMN zoning_district TEXT
                        """)
                        conn.commit()
                        logger.info("  ✅ Added zoning_district column")
                    else:
                        logger.info("  ✅ zoning_district column already exists")
                    
                    # Create zoning_district index if it doesn't exist
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_indexes 
                            WHERE tablename = %s AND indexname = 'idx_business_zoning_district'
                        )
                    """, (self.business_table,))
                    if not cur.fetchone()[0]:
                        logger.info("  Creating idx_business_zoning_district index...")
                        cur.execute(f"""
                            CREATE INDEX idx_business_zoning_district 
                            ON {self.business_table}(zoning_district)
                        """)
                        logger.info("  ✅ Created idx_business_zoning_district index")
                
                    # Enable PostGIS extension for spatial operations
                    logger.info("  Checking PostGIS extension...")
                    try:
                        cur.execute("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_extension WHERE extname = 'postgis'
                            )
                        """)
                        postgis_exists = cur.fetchone()[0]
                        
                        if postgis_exists:
                            logger.info("  ✅ PostGIS extension already enabled")
                        else:
                            logger.info("  Attempting to enable PostGIS extension (requires superuser privileges)...")
                            cur.execute("""
                                CREATE EXTENSION IF NOT EXISTS postgis;
                            """)
                            logger.info("  ✅ PostGIS extension enabled")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Could not enable PostGIS extension (may require superuser): {e}")
                        # Continue anyway - table creation will fail if PostGIS is truly needed
                
                    # Zoning polygons cache - stores zoning district multipolygons
                    logger.info("  Creating zoning_polygons_cache table...")
                    try:
                        cur.execute("""
                        CREATE TABLE IF NOT EXISTS zoning_polygons_cache (
                            id SERIAL PRIMARY KEY,
                            zoning TEXT NOT NULL,
                            geometry GEOMETRY(MULTIPOLYGON, 4326),
                            raw_data JSONB,
                            fetched_at TIMESTAMP DEFAULT NOW()
                        )
                        """)
                        logger.info("  ✅ Created zoning_polygons_cache table")
                    except Exception as e:
                        # If PostGIS isn't available, create table without geometry column
                        logger.warning(f"  ⚠️  Could not create with geometry (PostGIS may not be available): {e}")
                        logger.info("  Attempting to create zoning_polygons_cache without geometry column...")
                        try:
                            cur.execute("""
                            CREATE TABLE IF NOT EXISTS zoning_polygons_cache (
                                id SERIAL PRIMARY KEY,
                                zoning TEXT NOT NULL,
                                geometry_data JSONB,
                                raw_data JSONB,
                                fetched_at TIMESTAMP DEFAULT NOW()
                            )
                            """)
                            logger.info("  ✅ Created zoning_polygons_cache table (without PostGIS geometry)")
                        except Exception as e2:
                            logger.error(f"  ❌ Failed to create zoning_polygons_cache table: {e2}")
                            raise
                    
                    # Create indexes for zoning table
                    logger.info("  Creating indexes for zoning_polygons_cache...")
                    try:
                        cur.execute("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_indexes 
                                WHERE tablename = 'zoning_polygons_cache' AND indexname = 'idx_zoning_polygons_zoning'
                            )
                        """)
                        if not cur.fetchone()[0]:
                            cur.execute("""
                            CREATE INDEX idx_zoning_polygons_zoning 
                            ON zoning_polygons_cache(zoning)
                            """)
                            logger.info("  ✅ Created idx_zoning_polygons_zoning index")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Could not create zoning index: {e}")
                    
                    try:
                        # Check if geometry column exists before creating spatial index
                        cur.execute("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'zoning_polygons_cache' 
                            AND column_name = 'geometry'
                        """)
                        if cur.fetchone():
                            cur.execute("""
                                SELECT EXISTS (
                                    SELECT 1 FROM pg_indexes 
                                    WHERE tablename = 'zoning_polygons_cache' AND indexname = 'idx_zoning_polygons_geometry'
                                )
                            """)
                            if not cur.fetchone()[0]:
                                logger.info("  Creating spatial index on zoning_polygons_cache (this may take a moment)...")
                                cur.execute("""
                                CREATE INDEX idx_zoning_polygons_geometry 
                                ON zoning_polygons_cache USING GIST(geometry)
                                """)
                                logger.info("  ✅ Created spatial index on zoning_polygons_cache")
                        else:
                            logger.info("  ℹ️  Skipping spatial index (geometry column not available)")
                    except Exception as e:
                        logger.warning(f"  ⚠️  Could not create spatial index: {e}")
                    
                    # Commercial tax cache - mirrors DataSF commercial tax dataset
                    # No UNIQUE constraint here because LIN can be NULL
                    # Deduplication is handled in Python before insert
                    logger.info(f"  Creating {self.tax_table} table...")
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
                    logger.info(f"  ✅ {self.tax_table} table ready")
                
                    # Create indexes for tax table
                    logger.info("  Creating tax table indexes...")
                    tax_indexes = [
                        ('idx_tax_address', 'address'),
                        ('idx_tax_normalized_address', 'normalized_address'),
                        ('idx_tax_ban', 'ban'),
                        ('idx_tax_district', 'supervisor_district'),
                        ('idx_tax_lin', 'lin'),
                        ('idx_tax_normalized_linaddress', 'normalized_linaddress'),
                    ]
                    
                    for idx_name, col_name in tax_indexes:
                        try:
                            cur.execute("""
                                SELECT EXISTS (
                                    SELECT 1 FROM pg_indexes 
                                    WHERE tablename = %s AND indexname = %s
                                )
                            """, (self.tax_table, idx_name))
                            if cur.fetchone()[0]:
                                continue
                            
                            cur.execute(f"""
                                CREATE INDEX {idx_name} 
                                ON {self.tax_table}({col_name})
                            """)
                            logger.debug(f"    ✅ Created {idx_name}")
                        except Exception as e:
                            logger.warning(f"    ⚠️  Could not create index {idx_name}: {e}")
                    
                    conn.commit()
                    logger.info("✅ Cache tables created/updated successfully")
        except Exception as e:
            logger.error(f"❌ Error creating tables: {e}", exc_info=True)
            raise
    
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
    
    def fetch_zoning_data(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch zoning district data from DataSF"""
        logger.info("Fetching zoning district data from DataSF...")
        
        # Fetch zoning data from endpoint 3i4a-hu95
        # The geometry field contains MULTIPOLYGON data
        query = """
        SELECT 
            zoning,
            the_geom
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        query_object = {
            "endpoint": "3i4a-hu95",  # Zoning Map - Zoning Districts
            "query": query
        }
        
        result = fetch_data_from_api(query_object)
        data = result.get('data', []) if isinstance(result, dict) else result
        logger.info(f"Fetched {len(data):,} zoning district records")
        return data
    
    def _delete_in_batches(self, cur, conn, table_name: str, total_count: int, batch_size: int = 10000):
        """Delete records in batches to avoid long-running transactions"""
        logger.info(f"  Deleting {total_count:,} records in batches of {batch_size:,}...")
        
        # Ensure we start with a clean transaction
        try:
            conn.rollback()
        except:
            pass
        
        deleted = 0
        batch_num = 0
        
        while deleted < total_count:
            batch_num += 1
            batch_start = time.time()
            
            try:
                # Each batch is its own transaction
                cur.execute(f"""
                    DELETE FROM {table_name}
                    WHERE id IN (
                        SELECT id FROM {table_name}
                        ORDER BY id
                        LIMIT {batch_size}
                    )
                """)
                batch_deleted = cur.rowcount
                
                if batch_deleted > 0:
                    conn.commit()  # Commit this batch
                    deleted += batch_deleted
                    
                    batch_elapsed = time.time() - batch_start
                    progress_pct = (deleted / total_count * 100) if total_count > 0 else 0
                    rate = batch_deleted / batch_elapsed if batch_elapsed > 0 else 0
                    
                    logger.info(f"    Batch {batch_num}: Deleted {deleted:,}/{total_count:,} ({progress_pct:.1f}%) "
                               f"in {batch_elapsed:.1f}s ({rate:.0f} rec/s)...")
                else:
                    # No more records to delete
                    logger.info(f"    No more records found. Total deleted: {deleted:,}")
                    break
                    
            except Exception as e:
                # Rollback this batch and continue
                try:
                    conn.rollback()
                except:
                    pass
                
                error_msg = str(e).lower()
                if 'aborted' in error_msg:
                    logger.error(f"  Transaction aborted. Starting fresh...")
                    # Try to get a fresh connection/transaction
                    try:
                        conn.rollback()
                        time.sleep(1)  # Brief pause before retry
                        continue
                    except:
                        logger.error(f"  ❌ Could not recover from aborted transaction")
                        raise
                else:
                    logger.error(f"  ❌ Error in batch delete {batch_num}: {e}")
                    raise
        
        logger.info(f"  ✅ Successfully deleted {deleted:,} records in {batch_num} batches")
    
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
        logger.info("  Clearing existing business cache data...")
        clear_start = time.time()
        
        try:
            logger.info("  Getting database connection from pool...")
            with get_pooled_connection() as conn:
                logger.info("  ✅ Connection obtained from pool")
                # Set a longer statement timeout for this operation (5 minutes)
                with conn.cursor() as cur:
                    try:
                        cur.execute("SET statement_timeout = '5min'")
                    except:
                        pass  # Some databases may not support this
                    
                    # First check how many records exist
                    # Use a read-only transaction and set query timeout
                    logger.info("  Checking existing record count...")
                    try:
                        cur.execute("SET statement_timeout = '30s'")  # 30 second timeout for COUNT
                    except:
                        pass
                    
                    # Use a fast count query with READ COMMITTED isolation to avoid locks
                    cur.execute(f"""
                        SELECT COUNT(*) FROM {self.business_table}
                    """)
                    existing_count = cur.fetchone()[0]
                    logger.info(f"  Found {existing_count:,} existing records to delete...")
                    
                    if existing_count > 0:
                        # Try TRUNCATE first - it's much faster than DELETE
                        logger.info("  Attempting TRUNCATE (fastest method)...")
                        try:
                            # Check if there are any foreign key constraints
                            cur.execute("""
                                SELECT COUNT(*) FROM information_schema.table_constraints 
                                WHERE constraint_type = 'FOREIGN KEY' 
                                AND table_name = %s
                            """, (self.business_table,))
                            fk_count = cur.fetchone()[0]
                            
                            if fk_count > 0:
                                logger.info(f"  Found {fk_count} foreign key constraint(s), using TRUNCATE CASCADE...")
                                cur.execute(f"TRUNCATE TABLE {self.business_table} RESTART IDENTITY CASCADE")
                            else:
                                cur.execute(f"TRUNCATE TABLE {self.business_table} RESTART IDENTITY")
                            
                            conn.commit()
                            clear_elapsed = time.time() - clear_start
                            logger.info(f"  ✅ Truncated table successfully in {clear_elapsed:.1f}s (cleared {existing_count:,} records)")
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.warning(f"  TRUNCATE failed: {e}")
                            
                            # If transaction was aborted (timeout), rollback first
                            if 'aborted' in error_msg or 'timeout' in error_msg or 'querycanceled' in error_msg:
                                logger.info("  Rolling back aborted transaction...")
                                try:
                                    conn.rollback()
                                except:
                                    pass
                            
                            # If it's a lock/timeout issue, wait a bit and retry once in a fresh transaction
                            if 'lock' in error_msg or 'timeout' in error_msg or 'querycanceled' in error_msg:
                                logger.info("  Waiting 5 seconds for locks to clear, then retrying TRUNCATE...")
                                time.sleep(5)
                                try:
                                    # Start fresh transaction after rollback
                                    cur.execute(f"TRUNCATE TABLE {self.business_table} RESTART IDENTITY CASCADE")
                                    conn.commit()
                                    clear_elapsed = time.time() - clear_start
                                    logger.info(f"  ✅ Truncated on retry in {clear_elapsed:.1f}s")
                                except Exception as e2:
                                    logger.warning(f"  Retry also failed: {e2}")
                                    # Rollback the failed retry
                                    try:
                                        conn.rollback()
                                    except:
                                        pass
                                    logger.info("  Falling back to DELETE in batches (this may take several minutes)...")
                                    # Fall back to DELETE in batches - need fresh transaction
                                    self._delete_in_batches(cur, conn, self.business_table, existing_count)
                            else:
                                # For other errors, try DELETE as fallback
                                # Ensure transaction is clean
                                try:
                                    conn.rollback()
                                except:
                                    pass
                                logger.info("  Falling back to DELETE in batches (this may take several minutes)...")
                                self._delete_in_batches(cur, conn, self.business_table, existing_count)
                    else:
                        logger.info("  ✅ No existing records to clear")
                    
        except Exception as e:
            logger.error(f"  ❌ Error clearing table: {e}", exc_info=True)
            raise
        
        clear_elapsed = time.time() - clear_start
        logger.info(f"  ✅ Clearing complete in {clear_elapsed:.1f}s")
        
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
            neighborhoods_analysis_boundaries, zoning_district, raw_data
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
            location_lat = EXCLUDED.location_lat,
            location_lon = EXCLUDED.location_lon,
            fetched_at = NOW()
        """
        
        successful_inserts = 0
        errors = 0
        total_batches = (len(deduped_data) + self.batch_size - 1) // self.batch_size
        
        # Process in batches with individual transactions
        logger.info(f"  Starting batch inserts ({total_batches} batches of up to {self.batch_size:,} records each)...")
        batch_start_time = time.time()
        
        for batch_num in range(total_batches):
            batch_item_start = time.time()
            start_idx = batch_num * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(deduped_data))
            batch = deduped_data[start_idx:end_idx]
            
            progress_pct = ((batch_num + 1) / total_batches * 100) if total_batches > 0 else 0
            logger.info(f"  Batch {batch_num + 1}/{total_batches} ({progress_pct:.1f}%): Processing {len(batch):,} records...")
            
            try:
                # Each batch gets its own transaction
                with get_pooled_connection() as conn:
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
                                None,  # zoning_district - will be updated later via spatial matching
                                psycopg2.extras.Json(record)  # Store raw data as JSONB
                            )
                            batch_values.append(values)
                        
                        # Execute batch insert
                        insert_start = time.time()
                        psycopg2.extras.execute_values(
                            cur, insert_sql, batch_values, page_size=self.batch_size
                        )
                        conn.commit()  # Commit this batch
                        insert_elapsed = time.time() - insert_start
                        successful_inserts += len(batch)
                        
                        batch_elapsed = time.time() - batch_item_start
                        elapsed_total = time.time() - batch_start_time
                        rate = len(batch) / batch_elapsed if batch_elapsed > 0 else 0
                        
                        logger.info(f"    ✅ Batch {batch_num + 1} complete: {len(batch):,} records in {batch_elapsed:.1f}s "
                                   f"({rate:.0f} rec/s) | Total: {successful_inserts:,}/{len(deduped_data):,} "
                                   f"in {elapsed_total:.1f}s")
                        
            except Exception as e:
                logger.error(f"  ❌ Error in batch {batch_num + 1}: {e}", exc_info=True)
                errors += len(batch)
                # Transaction auto-rolls back, continue with next batch
        
        total_elapsed = time.time() - batch_start_time
        logger.info(f"✅ Business data storage complete: {successful_inserts:,} records ({errors} errors) in {total_elapsed:.1f}s")
        
        return {
            'total': len(deduped_data),
            'successful': successful_inserts,
            'errors': errors
        }
    
    def store_tax_data(self, tax_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Store raw commercial tax data in cache"""
        logger.info(f"Storing {len(tax_data):,} tax records...")
        store_start = time.time()
        
        # Deduplicate: Use (lin, year) when available, otherwise (parcelnumber, year)
        logger.info("  Deduplicating tax records...")
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
        logger.info(f"  ✅ Deduplicated to {len(deduped_data):,} unique records (from {len(tax_data):,})")
        logger.info(f"    - With LIN: {sum(1 for r in deduped_data if r.get('lin'))}")
        logger.info(f"    - Without LIN (using parcel): {sum(1 for r in deduped_data if not r.get('lin'))}")
        
        logger.info("  Clearing existing tax cache data...")
        clear_tax_start = time.time()
        try:
            with get_pooled_connection() as conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute("SET statement_timeout = '5min'")
                    except:
                        pass
                    
                    # Check existing count with timeout
                    try:
                        cur.execute("SET statement_timeout = '30s'")
                    except:
                        pass
                    
                    cur.execute(f"SELECT COUNT(*) FROM {self.tax_table}")
                    existing_count = cur.fetchone()[0]
                    logger.info(f"  Found {existing_count:,} existing tax records to delete...")
                    
                    if existing_count > 0:
                        try:
                            cur.execute(f"TRUNCATE TABLE {self.tax_table} RESTART IDENTITY CASCADE")
                            conn.commit()
                            clear_tax_elapsed = time.time() - clear_tax_start
                            logger.info(f"  ✅ Truncated tax table in {clear_tax_elapsed:.1f}s")
                        except Exception as e:
                            logger.warning(f"  TRUNCATE failed, using DELETE: {e}")
                            self._delete_in_batches(cur, conn, self.tax_table, existing_count)
                    else:
                        logger.info("  ✅ No existing tax records to clear")
        except Exception as e:
            logger.error(f"  ❌ Error clearing tax table: {e}", exc_info=True)
            raise
        
        # Prepare batch insert (no ON CONFLICT since we handle deduplication in Python)
        with get_pooled_connection() as conn:
            with conn.cursor() as cur:
                insert_sql = f"""
                INSERT INTO {self.tax_table} (
                    lin, linaddress, normalized_linaddress, ban, entity, address, normalized_address, 
                    filed, vacancy_status, year, assessor_parcel_number, block, lot, supervisor_district, raw_data
                ) VALUES %s
                """
                
                successful_inserts = 0
                errors = 0
                total_batches = (len(deduped_data) + self.batch_size - 1) // self.batch_size
                
                logger.info(f"  Starting batch inserts ({total_batches} batches)...")
                tax_batch_start = time.time()
                
                # Process in batches
                for batch_num in range(total_batches):
                    batch_item_start = time.time()
                    start_idx = batch_num * self.batch_size
                    end_idx = min(start_idx + self.batch_size, len(deduped_data))
                    batch = deduped_data[start_idx:end_idx]
                    
                    progress_pct = ((batch_num + 1) / total_batches * 100) if total_batches > 0 else 0
                    logger.info(f"  Batch {batch_num + 1}/{total_batches} ({progress_pct:.1f}%): Processing {len(batch):,} records...")
                    
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
                        
                        batch_elapsed = time.time() - batch_item_start
                        elapsed_total = time.time() - tax_batch_start
                        rate = len(batch) / batch_elapsed if batch_elapsed > 0 else 0
                        
                        logger.info(f"    ✅ Batch {batch_num + 1} complete: {len(batch):,} records in {batch_elapsed:.1f}s "
                                   f"({rate:.0f} rec/s) | Total: {successful_inserts:,}/{len(deduped_data):,}")
                        
                    except Exception as e:
                        logger.error(f"  ❌ Error in batch {batch_num + 1}: {e}", exc_info=True)
                        errors += len(batch)
                
                conn.commit()
                total_elapsed = time.time() - store_start
                logger.info(f"✅ Tax data storage complete: {successful_inserts:,} records ({errors} errors) in {total_elapsed:.1f}s")
                
                return {
                    'total': len(tax_data),
                    'successful': successful_inserts,
                    'errors': errors
                }
    
    def update_tax_filing_flags(self) -> int:
        """
        Update has_commercial_tax_filing flags by joining business and tax caches.
        Uses multiple matching strategies: LIN/ttxid, BAN+address, and address matching.
        Optimized to use separate UPDATE statements for better index usage.
        Returns the number of businesses updated.
        """
        logger.info("Updating has_commercial_tax_filing flags...")
        
        with get_pooled_connection() as conn:
            with conn.cursor() as cur:
                # Set a longer statement timeout for this operation (10 minutes)
                try:
                    cur.execute("SET statement_timeout = '10min'")
                except Exception:
                    pass  # Some databases may not support this
                
                # First, reset ALL flags to False
                logger.info("  Step 1: Resetting all flags to False...")
                cur.execute(f"""
                    UPDATE {self.business_table}
                    SET has_commercial_tax_filing = FALSE
                """)
                logger.info(f"  ✅ Reset all flags to False")
                conn.commit()
                
                total_updated = 0
                
                # Break down the complex OR query into separate UPDATEs for better optimization
                # Each UPDATE can use its specific index more efficiently
                
                # Step 2: Match 1 - LIN/ttxid match (most precise, fastest)
                logger.info("  Step 2: Matching by LIN/ttxid...")
                cur.execute(f"""
                    UPDATE {self.business_table} b
                    SET has_commercial_tax_filing = TRUE
                    FROM {self.tax_table} t
                    WHERE b.ttxid = t.lin 
                    AND b.ttxid IS NOT NULL 
                    AND t.lin IS NOT NULL
                    AND b.has_commercial_tax_filing = FALSE
                """)
                count1 = cur.rowcount
                total_updated += count1
                logger.info(f"  ✅ Matched {count1:,} by LIN/ttxid")
                conn.commit()
                
                # Step 3: Match 2 - BAN/certificate_number match WITH address verification
                logger.info("  Step 3: Matching by BAN + address...")
                cur.execute(f"""
                    UPDATE {self.business_table} b
                    SET has_commercial_tax_filing = TRUE
                    FROM {self.tax_table} t
                    WHERE b.certificate_number = t.ban 
                    AND b.certificate_number IS NOT NULL 
                    AND t.ban IS NOT NULL
                    AND (
                        b.normalized_address = t.normalized_address
                        OR b.normalized_address = t.normalized_linaddress
                    )
                    AND b.has_commercial_tax_filing = FALSE
                """)
                count2 = cur.rowcount
                total_updated += count2
                logger.info(f"  ✅ Matched {count2:,} by BAN + address")
                conn.commit()
                
                # Step 4: Match 3a - Address match via parcelsitusaddress
                logger.info("  Step 4: Matching by normalized address...")
                cur.execute(f"""
                    UPDATE {self.business_table} b
                    SET has_commercial_tax_filing = TRUE
                    FROM {self.tax_table} t
                    WHERE b.normalized_address = t.normalized_address 
                    AND b.normalized_address IS NOT NULL 
                    AND t.normalized_address IS NOT NULL
                    AND b.has_commercial_tax_filing = FALSE
                """)
                count3a = cur.rowcount
                total_updated += count3a
                logger.info(f"  ✅ Matched {count3a:,} by normalized address")
                conn.commit()
                
                # Step 5: Match 3b - Address match via linaddress
                logger.info("  Step 5: Matching by normalized linaddress...")
                cur.execute(f"""
                    UPDATE {self.business_table} b
                    SET has_commercial_tax_filing = TRUE
                    FROM {self.tax_table} t
                    WHERE b.normalized_address = t.normalized_linaddress 
                    AND b.normalized_address IS NOT NULL 
                    AND t.normalized_linaddress IS NOT NULL
                    AND b.has_commercial_tax_filing = FALSE
                """)
                count3b = cur.rowcount
                total_updated += count3b
                logger.info(f"  ✅ Matched {count3b:,} by normalized linaddress")
                conn.commit()
                
                logger.info(f"✅ Total: Set {total_updated:,} businesses to have tax filing flags")
                logger.info(f"  Breakdown: LIN/ttxid={count1:,}, BAN+address={count2:,}, "
                          f"norm_address={count3a:,}, norm_linaddress={count3b:,}")
                
                # Reset timeout
                try:
                    cur.execute("RESET statement_timeout")
                except Exception:
                    pass
                
                return total_updated
    
    def store_zoning_data(self, zoning_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Store zoning polygon data in cache"""
        logger.info(f"Storing {len(zoning_data):,} zoning district records...")
        
        with get_pooled_connection() as conn:
            with conn.cursor() as cur:
                # Check if table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'zoning_polygons_cache'
                    );
                """)
                table_exists = cur.fetchone()[0]
                
                if not table_exists:
                    logger.error("zoning_polygons_cache table does not exist. Please ensure PostGIS is enabled and run create_tables() first.")
                    return {
                        'total': len(zoning_data),
                        'successful': 0,
                        'errors': len(zoning_data)
                    }
                
                # Clear existing data
                try:
                    cur.execute("DELETE FROM zoning_polygons_cache")
                    logger.info("Cleared existing zoning cache data")
                except Exception as e:
                    logger.warning(f"Could not clear existing zoning cache: {e}")
                
                successful_inserts = 0
                errors = 0
                
                # Process records
                batch_values = []
                for record in zoning_data:
                    zoning = record.get('zoning')
                    the_geom = record.get('the_geom')
                    
                    if not zoning:
                        continue
                    
                    # Convert geometry to PostGIS format
                    # the_geom can be a GeoJSON object, string, or WKT/POINT string
                    if the_geom:
                        try:
                            # Handle different geometry formats from DataSF
                            if isinstance(the_geom, str):
                                # Try to parse as JSON first (GeoJSON)
                                try:
                                    geom_data = json.loads(the_geom)
                                except json.JSONDecodeError:
                                    # Might be WKT format like "POINT(...)" or "MULTIPOLYGON(...)"
                                    # Store as-is and use ST_GeomFromText in SQL
                                    geom_data = the_geom
                            else:
                                # Already a dict/object (GeoJSON)
                                geom_data = the_geom
                            
                            values = (
                                zoning,
                                geom_data,  # Will be converted to PostGIS geometry in SQL
                                psycopg2.extras.Json(record)  # Store raw data
                            )
                            batch_values.append(values)
                            
                        except Exception as e:
                            logger.warning(f"Error parsing geometry for zoning {zoning}: {e}")
                            continue
                    else:
                        # No geometry - skip this record
                        logger.warning(f"No geometry found for zoning {zoning}")
                        continue
                
                # Insert in batches using execute_values
                total_batches = (len(batch_values) + self.batch_size - 1) // self.batch_size
                for batch_num in range(total_batches):
                    start_idx = batch_num * self.batch_size
                    end_idx = min(start_idx + self.batch_size, len(batch_values))
                    batch = batch_values[start_idx:end_idx]
                    
                    logger.info(f"Processing zoning batch {batch_num + 1}/{total_batches} ({len(batch):,} records)")
                    
                    try:
                        # Check if geometry column exists (PostGIS table) or geometry_data (fallback table)
                        cur.execute("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'zoning_polygons_cache' 
                            AND column_name IN ('geometry', 'geometry_data')
                        """)
                        geom_columns = [row[0] for row in cur.fetchall()]
                        has_postgis_geom = 'geometry' in geom_columns
                        has_json_geom = 'geometry_data' in geom_columns
                        
                        # For each record, convert geometry to appropriate format
                        for zoning, geom_data, raw_data in batch:
                            try:
                                insert_success = False
                                if has_postgis_geom:
                                    # Use PostGIS geometry column
                                    if isinstance(geom_data, dict):
                                        # GeoJSON format - convert to JSON string for ST_GeomFromGeoJSON
                                        geom_json_str = json.dumps(geom_data)
                                        cur.execute("""
                                            INSERT INTO zoning_polygons_cache (zoning, geometry, raw_data)
                                            VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                                        """, (zoning, geom_json_str, raw_data))
                                        insert_success = True
                                    elif isinstance(geom_data, str):
                                        # Could be WKT format - try ST_GeomFromText
                                        if geom_data.startswith('MULTIPOLYGON') or geom_data.startswith('POLYGON'):
                                            cur.execute("""
                                                INSERT INTO zoning_polygons_cache (zoning, geometry, raw_data)
                                                VALUES (%s, ST_SetSRID(ST_GeomFromText(%s), 4326), %s)
                                            """, (zoning, geom_data, raw_data))
                                            insert_success = True
                                        else:
                                            # Try as GeoJSON string
                                            cur.execute("""
                                                INSERT INTO zoning_polygons_cache (zoning, geometry, raw_data)
                                                VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                                            """, (zoning, geom_data, raw_data))
                                            insert_success = True
                                    else:
                                        logger.warning(f"Unexpected geometry format for zoning {zoning}: {type(geom_data)}")
                                elif has_json_geom:
                                    # Use JSONB geometry_data column (fallback)
                                    if isinstance(geom_data, dict):
                                        cur.execute("""
                                            INSERT INTO zoning_polygons_cache (zoning, geometry_data, raw_data)
                                            VALUES (%s, %s, %s)
                                        """, (zoning, psycopg2.extras.Json(geom_data), raw_data))
                                        insert_success = True
                                    elif isinstance(geom_data, str):
                                        try:
                                            geom_dict = json.loads(geom_data)
                                            cur.execute("""
                                                INSERT INTO zoning_polygons_cache (zoning, geometry_data, raw_data)
                                                VALUES (%s, %s, %s)
                                            """, (zoning, psycopg2.extras.Json(geom_dict), raw_data))
                                            insert_success = True
                                        except json.JSONDecodeError:
                                            logger.warning(f"Could not parse geometry string for zoning {zoning}")
                                else:
                                    logger.error("zoning_polygons_cache table has neither geometry nor geometry_data column")
                                
                                if insert_success:
                                    successful_inserts += 1
                                
                            except Exception as e:
                                logger.warning(f"Error inserting zoning {zoning}: {e}")
                                errors += 1
                    except Exception as e:
                        logger.error(f"Error in zoning batch {batch_num + 1}: {e}")
                        errors += len(batch)
                
                conn.commit()
                logger.info(f"Stored {successful_inserts:,} zoning records ({errors} errors)")
                
                return {
                    'total': len(zoning_data),
                    'successful': successful_inserts,
                    'errors': errors
                }
    
    def update_zoning_districts(self, reset_existing: bool = True) -> int:
        """
        Update zoning_district column by matching business lat/lon to zoning polygons.
        Uses PostGIS spatial functions for point-in-polygon matching.
        
        Args:
            reset_existing: If True, reset all existing zoning_district values to NULL first.
                          If False, only update businesses that already have NULL zoning_district.
                          Default True for full refresh, False for incremental updates.
        
        Returns:
            Number of businesses updated.
        """
        logger.info("Updating zoning_district for businesses using spatial matching...")
        
        with get_pooled_connection() as conn:
            with conn.cursor() as cur:
                # Check if geometry column exists (PostGIS)
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'zoning_polygons_cache' 
                    AND column_name = 'geometry'
                """)
                has_postgis_geom = cur.fetchone() is not None
                
                if not has_postgis_geom:
                    logger.warning("PostGIS geometry column not found in zoning_polygons_cache. Spatial matching requires PostGIS.")
                    return 0
                
                # Optionally reset ALL zoning_district to NULL
                if reset_existing:
                    logger.info("Resetting all zoning_district values to NULL (this may take a few minutes for large tables)...")
                    cur.execute(f"""
                        UPDATE {self.business_table}
                        SET zoning_district = NULL
                        WHERE zoning_district IS NOT NULL
                    """)
                    reset_count = cur.rowcount
                    logger.info(f"Reset {reset_count:,} zoning_district values to NULL")
                    conn.commit()
                else:
                    logger.info("Skipping reset - only updating businesses with NULL zoning_district")
                
                # Count businesses that need zoning matching
                logger.info("Counting businesses with coordinates...")
                cur.execute(f"""
                    SELECT COUNT(*) FROM {self.business_table}
                    WHERE location_lat IS NOT NULL 
                    AND location_lon IS NOT NULL
                """)
                total_to_match = cur.fetchone()[0]
                logger.info(f"Found {total_to_match:,} businesses with coordinates to match")
                
                # Diagnostic: Count total businesses vs those with coordinates
                cur.execute(f"SELECT COUNT(*) FROM {self.business_table}")
                total_businesses = cur.fetchone()[0]
                businesses_without_coords = total_businesses - total_to_match
                logger.info(f"Total businesses in cache: {total_businesses:,}")
                logger.info(f"Businesses without coordinates: {businesses_without_coords:,} ({businesses_without_coords/total_businesses*100:.1f}%)")
                
                # Diagnostic: Check for invalid coordinates (0,0 or out of bounds)
                cur.execute(f"""
                    SELECT COUNT(*) FROM {self.business_table}
                    WHERE location_lat IS NOT NULL 
                    AND location_lon IS NOT NULL
                    AND (location_lat = 0 AND location_lon = 0
                         OR location_lat < -90 OR location_lat > 90
                         OR location_lon < -180 OR location_lon > 180
                         OR ABS(location_lat) < 0.001 OR ABS(location_lon) < 0.001)
                """)
                invalid_coords = cur.fetchone()[0]
                if invalid_coords > 0:
                    logger.warning(f"Found {invalid_coords:,} businesses with invalid coordinates (0,0 or out of bounds)")
                
                # Diagnostic: Check if coordinates are in SF area (rough bounds)
                cur.execute(f"""
                    SELECT COUNT(*) FROM {self.business_table}
                    WHERE location_lat IS NOT NULL 
                    AND location_lon IS NOT NULL
                    AND location_lat BETWEEN 37.7 AND 37.9
                    AND location_lon BETWEEN -122.6 AND -122.3
                """)
                in_sf_bounds = cur.fetchone()[0]
                out_of_bounds = total_to_match - in_sf_bounds
                if out_of_bounds > 0:
                    logger.warning(f"Found {out_of_bounds:,} businesses with coordinates outside SF bounds (lat 37.7-37.9, lon -122.6 to -122.3)")
                
                # Count zoning polygons
                logger.info("Counting zoning polygons...")
                cur.execute("SELECT COUNT(*) FROM zoning_polygons_cache")
                zoning_count = cur.fetchone()[0]
                logger.info(f"Found {zoning_count:,} zoning polygons to match against")
                
                # Diagnostic: Check for NULL zoning values in polygons
                cur.execute("""
                    SELECT COUNT(*) FROM zoning_polygons_cache
                    WHERE zoning IS NULL OR zoning = ''
                """)
                null_zoning_count = cur.fetchone()[0]
                if null_zoning_count > 0:
                    logger.warning(f"Found {null_zoning_count:,} zoning polygons with NULL or empty zoning values (these won't match businesses)")
                
                # Diagnostic: Check geometry validity
                cur.execute("""
                    SELECT COUNT(*) FROM zoning_polygons_cache
                    WHERE geometry IS NULL OR ST_IsValid(geometry) = FALSE
                """)
                invalid_geometry = cur.fetchone()[0]
                if invalid_geometry > 0:
                    logger.warning(f"Found {invalid_geometry:,} zoning polygons with NULL or invalid geometry")
                
                # Verify spatial index exists (critical for performance)
                logger.info("Checking for spatial index on zoning_polygons_cache...")
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_indexes 
                        WHERE tablename = 'zoning_polygons_cache' 
                        AND indexname = 'idx_zoning_polygons_geometry'
                    )
                """)
                has_index = cur.fetchone()[0]
                if has_index:
                    logger.info("✅ Spatial index found - queries should be fast")
                else:
                    logger.warning("⚠️  No spatial index found - queries may be very slow! Consider creating one.")
                
                if total_to_match == 0:
                    logger.info("No businesses with coordinates to match")
                    return 0
                
                if zoning_count == 0:
                    logger.warning("No zoning polygons in cache. Fetch zoning data first.")
                    return 0
                
                # Use batched JOINs for better progress tracking and to avoid timeouts
                # This leverages the spatial index efficiently while processing in manageable chunks
                logger.info("Starting spatial matching with efficient batched JOINs...")
                start_time = time.time()
                
                # Get initial count of businesses to process
                cur.execute(f"""
                    SELECT COUNT(*) FROM {self.business_table}
                    WHERE location_lat IS NOT NULL 
                    AND location_lon IS NOT NULL
                    AND zoning_district IS NULL
                    AND location_lat BETWEEN 37.6 AND 38.0
                    AND location_lon BETWEEN -122.7 AND -122.2
                """)
                initial_remaining = cur.fetchone()[0]
                remaining = initial_remaining
                logger.info(f"Found {initial_remaining:,} businesses within SF bounds to process")
                
                if initial_remaining == 0:
                    logger.info("No businesses to process")
                    return 0
                
                # Process in batches: first select IDs, then JOIN just those businesses
                # Track the last processed ID to avoid reprocessing businesses
                batch_size = 5000  # Smaller batches for spatial joins
                updated_count = 0
                iteration = 0
                max_iterations = (initial_remaining // batch_size) + 50
                last_processed_id = 0  # Track where we left off
                
                # Set timeout per batch (5 minutes should be enough for 5k records)
                cur.execute("SET statement_timeout = '5min'")
                
                try:
                    while remaining > 0 and iteration < max_iterations:
                        iteration += 1
                        batch_start = time.time()
                        
                        # Step 1: Get a batch of business IDs to process (fast - just index scan)
                        # Use last_processed_id to ensure we don't reprocess businesses
                        # This ensures monotonic progress and avoids decreasing match rates
                        cur.execute(f"""
                            SELECT id FROM {self.business_table}
                            WHERE location_lat IS NOT NULL 
                            AND location_lon IS NOT NULL
                            AND zoning_district IS NULL
                            AND location_lat BETWEEN 37.6 AND 38.0
                            AND location_lon BETWEEN -122.7 AND -122.2
                            AND id > %s
                            ORDER BY id
                            LIMIT {batch_size}
                        """, (last_processed_id,))
                        batch_ids = [row[0] for row in cur.fetchall()]
                        
                        if not batch_ids:
                            logger.info("No more businesses to process")
                            break
                        
                        # Update last_processed_id to the maximum ID in this batch
                        last_processed_id = max(batch_ids)
                        
                        logger.info(f"Processing batch {iteration}: {len(batch_ids):,} businesses (IDs {min(batch_ids)}-{max(batch_ids)}, remaining: {remaining:,})...")
                        
                        try:
                            # Step 2: JOIN only these specific businesses with zoning polygons
                            # This is much faster because we're only doing spatial checks on 5k businesses
                            cur.execute(f"""
                                UPDATE {self.business_table} b
                                SET zoning_district = z.zoning
                                FROM (
                                    SELECT DISTINCT ON (b2.id)
                                        b2.id,
                                        z2.zoning
                                    FROM {self.business_table} b2
                                    INNER JOIN zoning_polygons_cache z2
                                        ON ST_Contains(
                                            z2.geometry,
                                            ST_SetSRID(ST_MakePoint(b2.location_lon, b2.location_lat), 4326)
                                        )
                                    WHERE b2.id = ANY(%s)
                                        AND b2.location_lat IS NOT NULL
                                        AND b2.location_lon IS NOT NULL
                                        AND b2.zoning_district IS NULL
                                        AND z2.zoning IS NOT NULL
                                        AND z2.zoning != ''
                                    ORDER BY b2.id, z2.zoning
                                ) z
                                WHERE b.id = z.id
                            """, (batch_ids,))
                            
                            batch_updated = cur.rowcount
                            updated_count += batch_updated
                            conn.commit()
                            
                            # Recalculate remaining
                            cur.execute(f"""
                                SELECT COUNT(*) FROM {self.business_table}
                                WHERE location_lat IS NOT NULL 
                                AND location_lon IS NOT NULL
                                AND zoning_district IS NULL
                                AND location_lat BETWEEN 37.6 AND 38.0
                                AND location_lon BETWEEN -122.7 AND -122.2
                            """)
                            remaining = cur.fetchone()[0]
                            
                            batch_elapsed = time.time() - batch_start
                            elapsed_total = time.time() - start_time
                            processed = initial_remaining - remaining
                            progress_pct = (processed / initial_remaining * 100) if initial_remaining > 0 else 0
                            rate = batch_updated / batch_elapsed if batch_elapsed > 0 else 0
                            
                            logger.info(f"  ✅ Batch {iteration} complete: {batch_updated:,} matched in {batch_elapsed:.1f}s ({rate:.0f} rec/s) | "
                                       f"Progress: {processed:,}/{initial_remaining:,} ({progress_pct:.1f}%) | "
                                       f"Remaining: {remaining:,} | Total elapsed: {elapsed_total/60:.1f} min")
                            
                            if batch_updated == 0:
                                logger.warning(f"  No matches in batch {iteration} - likely all remaining businesses are outside polygons")
                                if remaining == 0:
                                    break
                                
                        except Exception as query_error:
                            logger.error(f"Error executing batch {iteration}: {query_error}", exc_info=True)
                            conn.rollback()  # Rollback the failed batch
                            # Reset timeout and try smaller batch or break
                            try:
                                cur.execute("RESET statement_timeout")
                            except:
                                pass
                            # If it's a timeout, we could retry with smaller batch, but for now just stop
                            if 'timeout' in str(query_error).lower() or 'canceled' in str(query_error).lower():
                                logger.warning(f"Timeout in batch {iteration} - stopping. Partial results: {updated_count:,} businesses matched")
                                break
                            raise
                    
                    elapsed = time.time() - start_time
                    final_remaining = remaining
                    total_matched = initial_remaining - final_remaining
                    match_rate = (total_matched / initial_remaining * 100) if initial_remaining > 0 else 0
                    
                    logger.info(f"✅ Spatial matching complete: {total_matched:,} businesses matched in {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
                    logger.info(f"   Processing rate: {total_matched/elapsed:.0f} businesses/second")
                    logger.info(f"   Match rate: {match_rate:.1f}% ({total_matched:,}/{initial_remaining:,} businesses with coordinates)")
                    
                    if final_remaining > 0:
                        logger.info(f"   Remaining unmatched: {final_remaining:,} businesses (likely outside all polygons)")
                    
                    # Reset timeout
                    try:
                        cur.execute("RESET statement_timeout")
                    except:
                        pass
                    
                    return total_matched
                    
                except Exception as e:
                    logger.error(f"Error in spatial matching: {e}", exc_info=True)
                    try:
                        conn.rollback()
                        cur.execute("RESET statement_timeout")
                    except:
                        pass
                    logger.info(f"Partial results: {updated_count:,} businesses matched before error")
                    raise
    
    def refresh_cache(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Refresh both business and tax caches"""
        logger.info("=== Starting Simple Cache Refresh ===")
        start_time = time.time()
        
        try:
            # Create tables
            logger.info("Step 1/7: Creating cache tables...")
            self.create_tables()
            logger.info("✅ Tables ready")
            
            # Fetch and store business data
            logger.info("Step 2/7: Fetching business data from DataSF...")
            business_data = self.fetch_business_data(limit)
            logger.info("Step 3/7: Storing business data...")
            business_stats = self.store_business_data(business_data)
            logger.info(f"✅ Business data stored: {business_stats.get('successful', 0):,} records")
            
            # Fetch and store tax data
            logger.info("Step 4/7: Fetching tax data from DataSF...")
            tax_data = self.fetch_tax_data(limit)
            logger.info("Step 5/7: Storing tax data...")
            tax_stats = self.store_tax_data(tax_data)
            logger.info(f"✅ Tax data stored: {tax_stats.get('successful', 0):,} records")
            
            # Fetch and store zoning data
            try:
                logger.info("Fetching zoning district data...")
                zoning_data = self.fetch_zoning_data(limit)
                logger.info(f"Fetched {len(zoning_data):,} zoning records, storing...")
                zoning_stats = self.store_zoning_data(zoning_data)
                logger.info(f"Zoning data: fetched {len(zoning_data):,} records, stored {zoning_stats.get('successful', 0):,}")
            except Exception as e:
                logger.error(f"Error fetching/storing zoning data: {e}", exc_info=True)
                zoning_stats = {'total': 0, 'successful': 0, 'errors': 0}
            
            # Update has_commercial_tax_filing flags
            tax_filing_count = self.update_tax_filing_flags()
            
            # Update zoning_district using spatial matching
            try:
                logger.info("Starting zoning district spatial matching...")
                zoning_match_count = self.update_zoning_districts()
                logger.info(f"Zoning matching: updated {zoning_match_count:,} businesses with zoning districts")
            except Exception as e:
                logger.error(f"Error updating zoning districts: {e}", exc_info=True)
                zoning_match_count = 0
            
            elapsed_time = time.time() - start_time
            
            result = {
                'status': 'success',
                'business_cache': business_stats,
                'tax_cache': tax_stats,
                'zoning_cache': zoning_stats,
                'tax_filing_matches': tax_filing_count,
                'zoning_district_matches': zoning_match_count,
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
    
    def refresh_zoning_only(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Update only zoning data without refreshing business/tax caches.
        Useful for testing or updating zoning without the overhead of full cache refresh.
        """
        logger.info("=== Starting Zoning-Only Update ===")
        start_time = time.time()
        
        try:
            # Ensure tables exist
            logger.info("Step 1/3: Ensuring cache tables exist...")
            self.create_tables()
            logger.info("✅ Tables ready")
            
            # Fetch and store zoning data
            try:
                logger.info("Step 2/3: Fetching zoning district data from DataSF...")
                zoning_data = self.fetch_zoning_data(limit)
                logger.info(f"Fetched {len(zoning_data):,} zoning records")
                
                logger.info("Storing zoning data...")
                zoning_stats = self.store_zoning_data(zoning_data)
                logger.info(f"✅ Zoning data stored: {zoning_stats.get('successful', 0):,} records "
                          f"({zoning_stats.get('errors', 0)} errors)")
            except Exception as e:
                logger.error(f"Error fetching/storing zoning data: {e}", exc_info=True)
                zoning_stats = {'total': 0, 'successful': 0, 'errors': 0}
            
            # Update zoning_district using spatial matching
            try:
                logger.info("Step 3/3: Starting zoning district spatial matching...")
                zoning_match_count = self.update_zoning_districts()
                logger.info(f"✅ Zoning matching: updated {zoning_match_count:,} businesses with zoning districts")
            except Exception as e:
                logger.error(f"Error updating zoning districts: {e}", exc_info=True)
                zoning_match_count = 0
            
            elapsed_time = time.time() - start_time
            
            result = {
                'status': 'success',
                'zoning_cache': zoning_stats,
                'zoning_district_matches': zoning_match_count,
                'elapsed_time': elapsed_time
            }
            
            logger.info(f"=== Zoning-Only Update Complete ({elapsed_time:.2f}s) ===")
            return result
            
        except Exception as e:
            logger.error(f"Zoning update failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'elapsed_time': time.time() - start_time
            }
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with get_pooled_connection() as conn:
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
                
                # Zoning cache stats
                zoning_count = 0
                zoning_last_updated = None
                businesses_with_zoning = 0
                try:
                    cur.execute("SELECT COUNT(*) FROM zoning_polygons_cache")
                    zoning_count = cur.fetchone()[0]
                    
                    cur.execute("SELECT MAX(fetched_at) FROM zoning_polygons_cache")
                    zoning_last_updated_row = cur.fetchone()[0]
                    if zoning_last_updated_row:
                        zoning_last_updated = zoning_last_updated_row
                    
                    # Count businesses with zoning districts assigned
                    cur.execute(f"""
                        SELECT COUNT(*) FROM {self.business_table} 
                        WHERE zoning_district IS NOT NULL
                    """)
                    businesses_with_zoning = cur.fetchone()[0]
                except Exception as e:
                    logger.warning(f"Could not get zoning stats (table may not exist): {e}")
                
                return {
                    'business_registrations': {
                        'count': business_count,
                        'last_updated': business_last_updated.isoformat() if business_last_updated else None
                    },
                    'commercial_tax': {
                        'count': tax_count,
                        'last_updated': tax_last_updated.isoformat() if tax_last_updated else None
                    },
                    'zoning_polygons': {
                        'count': zoning_count,
                        'last_updated': zoning_last_updated.isoformat() if zoning_last_updated else None
                    },
                    'businesses_with_zoning': businesses_with_zoning
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

