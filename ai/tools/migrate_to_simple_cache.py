"""
Migration Script: Add Normalized Address Columns
This script adds normalized_address columns to both cache tables
and populates them for better join performance.
"""

import logging
import psycopg2
import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Find the .env file in the ai directory (parent of tools)
script_dir = Path(__file__).resolve().parent
ai_dir = script_dir.parent
env_path = ai_dir / '.env'

if env_path.exists():
    load_dotenv(env_path)
    print(f"Loaded environment from: {env_path}")
else:
    print(f"Warning: .env file not found at {env_path}")
    # Try loading from current directory as fallback
    load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_building_address(full_address):
    """Extract building address without unit letters/numbers"""
    if not full_address:
        return None

    # Convert to uppercase for consistent processing
    address = full_address.upper()

    # Remove common unit patterns
    address = re.sub(r'(\d+)\s+([A-Z](?:\s+[A-Z])*\s+)([A-Z]+)', r'\1 \3', address)
    address = re.sub(r'(\d+)\s+([A-Z])\s+([A-Z]+)', r'\1 \3', address)
    address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#[A-Z]\s*$', r'\1', address)
    address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#\s*\d+\s*$', r'\1', address)
    address = re.sub(r'\s+[A-Z](?:\s+[A-Z])*\s*$', '', address)
    address = re.sub(r'\s+\d+[A-Z]?\s*$', '', address)
    address = re.sub(r'\s+[A-Z]\s*$', '', address)
    address = re.sub(r'\s+#\s*\d+\s*$', '', address)
    address = re.sub(r'\s+#[A-Z]\s*$', '', address)
    address = re.sub(r'\s+(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLOOR)\s*[A-Z0-9]+\s*$', '', address, flags=re.IGNORECASE)
    address = re.sub(r'\s+', ' ', address)
    address = re.sub(r'\s*[,;]+\s*$', '', address)
    address = re.sub(r'\s*&\s*$', '', address)
    address = re.sub(r'\s+AND\s*$', '', address)
    address = re.sub(r'\b([A-Z])\s+([A-Z\s]+)\s+\1\b', r'\1 \2', address)

    return address.strip()


def is_upper_floor_address(full_address: str) -> bool:
    """Return True if the address indicates an upper-floor/unit (non-storefront)."""
    if not full_address:
        return False
    
    s = full_address.upper()
    
    # Indicators of units/floors
    if re.search(r'#\s*[0-9]+', s):
        return True
    if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
        return True
    if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
        return True
    if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
        return True
    
    trailing_number_match = re.search(r'\s([0-9]+)\s*$', s)
    if trailing_number_match:
        trailing_number = int(trailing_number_match.group(1))
        if trailing_number >= 200:
            return True
    
    if re.search(r'\s[0-9]+[A-Z]\s*$', s):
        return True
    
    return False


def add_normalized_address_columns():
    """Add normalized_address and flag columns to cache tables"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set")
    
    logger.info("Starting migration to add normalized address and flags...")
    
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            # 1. Add normalized_address column to business_registrations_cache
            logger.info("Adding normalized_address column to business_registrations_cache...")
            cur.execute("""
                ALTER TABLE business_registrations_cache 
                ADD COLUMN IF NOT EXISTS normalized_address TEXT
            """)
            conn.commit()
            
            # 2. Add is_street_level flag column
            logger.info("Adding is_street_level column to business_registrations_cache...")
            cur.execute("""
                ALTER TABLE business_registrations_cache 
                ADD COLUMN IF NOT EXISTS is_street_level BOOLEAN DEFAULT TRUE
            """)
            conn.commit()
            
            # 3. Add has_commercial_tax_filing flag column
            logger.info("Adding has_commercial_tax_filing column to business_registrations_cache...")
            cur.execute("""
                ALTER TABLE business_registrations_cache 
                ADD COLUMN IF NOT EXISTS has_commercial_tax_filing BOOLEAN DEFAULT FALSE
            """)
            conn.commit()
            
            # 4. Add normalized_address column to commercial_tax_cache
            logger.info("Adding normalized_address column to commercial_tax_cache...")
            cur.execute("""
                ALTER TABLE commercial_tax_cache 
                ADD COLUMN IF NOT EXISTS normalized_address TEXT
            """)
            conn.commit()
            
            # 5. Populate normalized_address and is_street_level for business_registrations_cache
            logger.info("Populating normalized addresses and street level flags for business registrations...")
            cur.execute("SELECT id, full_business_address FROM business_registrations_cache")
            rows = cur.fetchall()
            
            update_count = 0
            for row_id, full_address in rows:
                normalized = extract_building_address(full_address)
                is_street_level = not is_upper_floor_address(full_address)
                
                if normalized:
                    cur.execute("""
                        UPDATE business_registrations_cache 
                        SET normalized_address = %s,
                            is_street_level = %s
                        WHERE id = %s
                    """, (normalized, is_street_level, row_id))
                    update_count += 1
                    
                    if update_count % 1000 == 0:
                        logger.info(f"Processed {update_count:,} business addresses...")
                        conn.commit()
            
            conn.commit()
            logger.info(f"Updated {update_count:,} business registrations with normalized addresses and flags")
            
            # 4. Populate normalized_address for commercial_tax_cache
            logger.info("Populating normalized addresses for commercial tax cache...")
            cur.execute("SELECT id, address FROM commercial_tax_cache")
            rows = cur.fetchall()
            
            update_count = 0
            for row_id, address in rows:
                normalized = extract_building_address(address)
                if normalized:
                    cur.execute("""
                        UPDATE commercial_tax_cache 
                        SET normalized_address = %s 
                        WHERE id = %s
                    """, (normalized, row_id))
                    update_count += 1
                    
                    if update_count % 1000 == 0:
                        logger.info(f"Processed {update_count:,} tax addresses...")
                        conn.commit()
            
            conn.commit()
            logger.info(f"Updated {update_count:,} tax records with normalized addresses")
            
            # 7. Update has_commercial_tax_filing flags by joining tables
            logger.info("Updating has_commercial_tax_filing flags...")
            cur.execute("""
                UPDATE business_registrations_cache b
                SET has_commercial_tax_filing = TRUE
                FROM commercial_tax_cache t
                WHERE b.normalized_address = t.normalized_address
                AND b.normalized_address IS NOT NULL
                AND t.normalized_address IS NOT NULL
            """)
            tax_filing_matches = cur.rowcount
            conn.commit()
            logger.info(f"Marked {tax_filing_matches:,} businesses as having commercial tax filings")
            
            # 8. Create indexes on normalized_address and flag columns for fast queries
            logger.info("Creating indexes on normalized addresses and flags...")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_business_normalized_address 
                ON business_registrations_cache(normalized_address)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_business_street_level 
                ON business_registrations_cache(is_street_level)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_business_has_tax_filing 
                ON business_registrations_cache(has_commercial_tax_filing)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tax_normalized_address 
                ON commercial_tax_cache(normalized_address)
            """)
            conn.commit()
            
            logger.info("Migration complete! Normalized address columns and flags added and indexed.")
            
            # 9. Show statistics
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(normalized_address) as with_normalized,
                    SUM(CASE WHEN is_street_level THEN 1 ELSE 0 END) as street_level,
                    SUM(CASE WHEN has_commercial_tax_filing THEN 1 ELSE 0 END) as with_tax_filing
                FROM business_registrations_cache
            """)
            biz_stats = cur.fetchone()
            
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(normalized_address) as with_normalized
                FROM commercial_tax_cache
            """)
            tax_stats = cur.fetchone()
            
            logger.info(f"""
            === Migration Statistics ===
            Business Registrations:
              Total: {biz_stats[0]:,}
              With normalized address: {biz_stats[1]:,}
              Street level: {biz_stats[2]:,} ({biz_stats[2]/biz_stats[0]*100:.1f}%)
              With tax filing: {biz_stats[3]:,} ({biz_stats[3]/biz_stats[0]*100:.1f}%)
            
            Commercial Tax Records:
              Total: {tax_stats[0]:,}
              With normalized address: {tax_stats[1]:,}
            
            Join Success Rate: {biz_stats[3]/biz_stats[0]*100:.1f}% of businesses have matching tax filings
            """)


if __name__ == "__main__":
    add_normalized_address_columns()

