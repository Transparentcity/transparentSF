#!/usr/bin/env python3
"""
Migration script to add LIN/ttxid matching fields to cache tables.

This adds:
- ttxid column to business_registrations_cache
- lin, linaddress, normalized_linaddress, and filed columns to commercial_tax_cache
- Updates UNIQUE constraint to use (lin, year) instead of (entity, address, year)
- Creates indexes for new columns
"""

import psycopg2
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_path = Path('ai/.env')
load_dotenv(env_path)

db_url = os.getenv('DATABASE_URL')

def main():
    print("="*80)
    print("LIN/ttxid Fields Migration")
    print("="*80)
    
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    try:
        # Step 1: Add ttxid to business_registrations_cache
        print("\n1. Adding ttxid column to business_registrations_cache...")
        cursor.execute("""
            ALTER TABLE business_registrations_cache 
            ADD COLUMN IF NOT EXISTS ttxid TEXT
        """)
        conn.commit()
        print("   ✅ Added ttxid column")
        
        # Step 2: Create index on ttxid
        print("\n2. Creating index on ttxid...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_business_ttxid 
            ON business_registrations_cache(ttxid)
        """)
        conn.commit()
        print("   ✅ Created idx_business_ttxid")
        
        # Step 3: Drop and recreate commercial_tax_cache with new schema
        print("\n3. Backing up and recreating commercial_tax_cache...")
        
        # Create backup table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS commercial_tax_cache_backup AS 
            SELECT * FROM commercial_tax_cache
        """)
        conn.commit()
        print("   ✅ Created backup table")
        
        # Drop old table
        cursor.execute("DROP TABLE IF EXISTS commercial_tax_cache CASCADE")
        conn.commit()
        print("   ✅ Dropped old table")
        
        # Create new table with updated schema
        cursor.execute("""
            CREATE TABLE commercial_tax_cache (
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
                fetched_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(lin, year)
            )
        """)
        conn.commit()
        print("   ✅ Created new table with lin, linaddress, normalized_linaddress, and filed columns")
        
        # Step 4: Create indexes
        print("\n4. Creating indexes on new columns...")
        
        indexes = [
            ("idx_tax_lin", "lin"),
            ("idx_tax_linaddress", "linaddress"),
            ("idx_tax_normalized_linaddress", "normalized_linaddress"),
            ("idx_tax_ban", "ban"),
            ("idx_tax_address", "address"),
            ("idx_tax_normalized_address", "normalized_address"),
            ("idx_tax_district", "supervisor_district"),
        ]
        
        for idx_name, col_name in indexes:
            cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS {idx_name} 
                ON commercial_tax_cache({col_name})
            """)
            print(f"   ✅ Created {idx_name}")
        
        conn.commit()
        
        # Step 5: Check current record counts
        print("\n5. Current record counts:")
        cursor.execute("SELECT COUNT(*) FROM business_registrations_cache")
        business_count = cursor.fetchone()[0]
        print(f"   Business registrations: {business_count:,}")
        
        cursor.execute("SELECT COUNT(*) FROM commercial_tax_cache_backup")
        tax_count = cursor.fetchone()[0]
        print(f"   Commercial tax (backup): {tax_count:,}")
        
        print("\n" + "="*80)
        print("✅ Migration completed successfully!")
        print("="*80)
        print("\nNext steps:")
        print("1. Run: python -m ai.tools.simple_business_cache")
        print("   This will re-fetch all data with the new lin/ttxid fields")
        print("\n2. The update_tax_filing_flags will now use 3 matching strategies:")
        print("   - LIN/ttxid match (most precise)")
        print("   - BAN/certificate_number match")
        print("   - Address match (parcelsitusaddress OR linaddress)")
        print("\n3. You can drop the backup table after verifying the new cache:")
        print("   DROP TABLE commercial_tax_cache_backup;")
        
    except Exception as e:
        print(f"\n❌ Error during migration: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()

