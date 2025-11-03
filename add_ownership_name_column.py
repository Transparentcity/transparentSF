#!/usr/bin/env python3
"""
Migration script to add ownership_name column to business_registrations_cache table.

This script:
1. Adds ownership_name column to existing cache table
2. Creates an index for fast searches
3. Does NOT repopulate data (you'll need to refresh the cache to get ownership names)
"""

import psycopg2
import os
from dotenv import load_dotenv
from pathlib import Path

def main():
    # Load environment variables
    env_path = Path('ai/.env')
    load_dotenv(env_path)
    
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("❌ ERROR: DATABASE_URL not set in environment")
        return False
    
    print("🔧 Adding ownership_name column to business_registrations_cache")
    print("="*80)
    
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'business_registrations_cache' 
            AND column_name = 'ownership_name'
        """)
        
        if cursor.fetchone():
            print("✅ ownership_name column already exists")
        else:
            print("📝 Adding ownership_name column...")
            cursor.execute("""
                ALTER TABLE business_registrations_cache 
                ADD COLUMN ownership_name TEXT
            """)
            conn.commit()
            print("✅ Added ownership_name column")
        
        # Create index
        print("📝 Creating index on ownership_name...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_business_ownership_name 
            ON business_registrations_cache(ownership_name)
        """)
        conn.commit()
        print("✅ Created index idx_business_ownership_name")
        
        # Check stats
        cursor.execute("SELECT COUNT(*) FROM business_registrations_cache")
        total_records = cursor.fetchone()[0]
        
        print()
        print("="*80)
        print(f"✅ Migration complete!")
        print(f"   Total records in cache: {total_records:,}")
        print()
        print("⚠️  IMPORTANT: Ownership names are NULL for all existing records.")
        print("   To populate ownership_name data, you need to refresh the cache:")
        print()
        print("   python -m ai.tools.simple_business_cache")
        print()
        print("   Or refresh from the admin interface.")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)





