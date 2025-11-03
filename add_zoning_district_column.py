#!/usr/bin/env python3
"""
Migration script to add zoning_district column to business_registrations_cache table.

This script:
1. Adds zoning_district column to existing cache table (if it doesn't exist)
2. Creates an index for fast filtering
3. Does NOT populate zoning data (you'll need to refresh the cache to get zoning districts)
4. Also sets up PostGIS extension and zoning_polygons_cache table
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
    
    print("🔧 Adding zoning_district column and setting up zoning cache")
    print("="*80)
    
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # 1. Enable PostGIS extension
        print("\n1. Enabling PostGIS extension...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        conn.commit()
        print("   ✅ PostGIS extension enabled")
        
        # 2. Check if zoning_district column already exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'business_registrations_cache' 
            AND column_name = 'zoning_district'
        """)
        
        if cursor.fetchone():
            print("\n2. ✅ zoning_district column already exists")
        else:
            print("\n2. 📝 Adding zoning_district column...")
            cursor.execute("""
                ALTER TABLE business_registrations_cache 
                ADD COLUMN zoning_district TEXT
            """)
            conn.commit()
            print("   ✅ Added zoning_district column")
        
        # 3. Create index on zoning_district
        print("\n3. 📝 Creating index on zoning_district...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_business_zoning_district 
            ON business_registrations_cache(zoning_district)
        """)
        conn.commit()
        print("   ✅ Created index idx_business_zoning_district")
        
        # 4. Create zoning_polygons_cache table
        print("\n4. 📝 Creating zoning_polygons_cache table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS zoning_polygons_cache (
                id SERIAL PRIMARY KEY,
                zoning TEXT NOT NULL,
                geometry GEOMETRY(MULTIPOLYGON, 4326),
                raw_data JSONB,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        print("   ✅ Created zoning_polygons_cache table")
        
        # 5. Create indexes for zoning_polygons_cache
        print("\n5. 📝 Creating indexes for zoning_polygons_cache...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_zoning_polygons_zoning 
            ON zoning_polygons_cache(zoning)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_zoning_polygons_geometry 
            ON zoning_polygons_cache USING GIST(geometry)
        """)
        conn.commit()
        print("   ✅ Created indexes for zoning_polygons_cache")
        
        # Check stats
        cursor.execute("SELECT COUNT(*) FROM business_registrations_cache")
        total_records = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM business_registrations_cache WHERE zoning_district IS NOT NULL")
        records_with_zoning = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM zoning_polygons_cache")
        zoning_polygons_count = cursor.fetchone()[0]
        
        print()
        print("="*80)
        print(f"✅ Migration complete!")
        print(f"   Total business records: {total_records:,}")
        print(f"   Records with zoning_district: {records_with_zoning:,}")
        print(f"   Zoning polygons cached: {zoning_polygons_count:,}")
        print()
        print("⚠️  IMPORTANT: zoning_district is NULL for all existing records.")
        print("   To populate zoning data, you need to refresh the cache:")
        print()
        print("   - Via API: POST /api/vacancy/refresh-cache")
        print("   - Via script: python -m ai.tools.simple_business_cache")
        print()
        print("   The cache refresh will:")
        print("   1. Fetch zoning polygons from DataSF (endpoint 3i4a-hu95)")
        print("   2. Store them in zoning_polygons_cache")
        print("   3. Match business lat/lon to polygons using PostGIS")
        print("   4. Populate zoning_district column")
        
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


