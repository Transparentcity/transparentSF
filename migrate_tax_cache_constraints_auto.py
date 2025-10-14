#!/usr/bin/env python3
"""
Automated Migration Script: Update Commercial Tax Cache
========================================================

This is an automated version (no prompts) for running via command line.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment from ai/.env
env_path = Path(__file__).parent / 'ai' / '.env'
if not env_path.exists():
    env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

def run_migration():
    """Run the migration to update commercial tax cache constraints"""
    
    print("="*80)
    print("Commercial Tax Cache Migration (AUTOMATED)")
    print("="*80)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Connect to database
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("❌ ERROR: DATABASE_URL not found in environment")
        sys.exit(1)
    
    print(f"📡 Connecting to database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    try:
        # Get current stats
        print("\n📊 Current Statistics:")
        print("-" * 80)
        
        cur.execute("SELECT COUNT(*) FROM commercial_tax_cache")
        current_count = cur.fetchone()[0]
        print(f"   Current tax records in cache: {current_count:,}")
        
        # Drop the table
        print("\n🗑️  Dropping commercial_tax_cache table...")
        cur.execute("DROP TABLE IF EXISTS commercial_tax_cache CASCADE")
        conn.commit()
        print("   ✅ Table dropped")
        
        # Recreate with new constraint
        print("\n🔨 Creating new commercial_tax_cache table...")
        cur.execute("""
            CREATE TABLE commercial_tax_cache (
                id SERIAL PRIMARY KEY,
                ban TEXT,
                entity TEXT,
                address TEXT,
                normalized_address TEXT,
                vacancy_status TEXT,
                year INTEGER,
                assessor_parcel_number TEXT,
                block TEXT,
                lot TEXT,
                supervisor_district TEXT,
                raw_data JSONB,
                fetched_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(entity, address, year)
            )
        """)
        conn.commit()
        print("   ✅ Table created with new constraint: UNIQUE(entity, address, year)")
        
        # Recreate indexes
        print("\n📑 Creating indexes...")
        indexes = [
            ("idx_tax_address", "address"),
            ("idx_tax_normalized_address", "normalized_address"),
            ("idx_tax_ban", "ban"),
            ("idx_tax_district", "supervisor_district"),
        ]
        
        for idx_name, column in indexes:
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS {idx_name} 
                ON commercial_tax_cache({column})
            """)
            print(f"   ✅ Created index: {idx_name}")
        
        conn.commit()
        
        print("\n✅ Migration Complete!")
        print("="*80)
        
    except Exception as e:
        print(f"\n❌ ERROR during migration: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
        print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)

if __name__ == "__main__":
    run_migration()

