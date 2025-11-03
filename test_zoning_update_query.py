#!/usr/bin/env python3
"""
Test the actual UPDATE query used in update_zoning_districts()
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv('ai/.env')

def test_update_query():
    """Test the UPDATE query on a specific address"""
    db_url = os.getenv('DATABASE_URL')
    conn = psycopg2.connect(db_url)
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # First, check the current state
        print("BEFORE UPDATE:")
        cur.execute("""
            SELECT id, full_business_address, location_lat, location_lon, zoning_district
            FROM business_registrations_cache
            WHERE UPPER(full_business_address) LIKE '%2139 POLK%'
            LIMIT 3
        """)
        before = cur.fetchall()
        for row in before:
            print(f"  ID {row['id']}: {row['full_business_address']} -> zoning={row['zoning_district']}")
        
        # Test the UPDATE query on just one ID
        test_id = before[0]['id'] if before else None
        if not test_id:
            print("No test record found")
            return
        
        print(f"\nTesting UPDATE on ID {test_id}...")
        
        # This is the exact query from update_zoning_districts()
        cur.execute("""
            UPDATE business_registrations_cache b
            SET zoning_district = (
                SELECT z.zoning
                FROM zoning_polygons_cache z
                WHERE ST_Contains(
                    z.geometry,
                    ST_SetSRID(ST_MakePoint(b.location_lon, b.location_lat), 4326)
                )
                LIMIT 1
            )
            WHERE b.id = %s
            AND b.zoning_district IS NULL
            AND EXISTS (
                SELECT 1
                FROM zoning_polygons_cache z
                WHERE ST_Contains(
                    z.geometry,
                    ST_SetSRID(ST_MakePoint(b.location_lon, b.location_lat), 4326)
                )
                AND z.zoning IS NOT NULL
            )
            RETURNING b.id, b.full_business_address, b.zoning_district
        """, (test_id,))
        
        result = cur.fetchall()
        
        if result:
            print(f"\n✅ UPDATE SUCCESSFUL:")
            for row in result:
                print(f"  ID {row['id']}: {row['full_business_address']} -> zoning={row['zoning_district']}")
            conn.commit()
        else:
            print(f"\n❌ UPDATE FAILED - No rows updated")
            print("Checking why...")
            
            # Debug: Check if EXISTS clause is true
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1
                    FROM zoning_polygons_cache z
                    WHERE ST_Contains(
                        z.geometry,
                        ST_SetSRID(ST_MakePoint(
                            (SELECT location_lon FROM business_registrations_cache WHERE id = %s),
                            (SELECT location_lat FROM business_registrations_cache WHERE id = %s)
                        ), 4326)
                    )
                    AND z.zoning IS NOT NULL
                ) as exists_match
            """, (test_id, test_id))
            
            exists_result = cur.fetchone()
            print(f"  EXISTS clause result: {exists_result['exists_match']}")
            
            # Check if zoning_district is already set
            cur.execute("""
                SELECT zoning_district IS NULL as is_null_zoning
                FROM business_registrations_cache
                WHERE id = %s
            """, (test_id,))
            null_check = cur.fetchone()
            print(f"  zoning_district IS NULL: {null_check['is_null_zoning']}")
        
        # Check the state after
        print("\nAFTER UPDATE:")
        cur.execute("""
            SELECT id, full_business_address, location_lat, location_lon, zoning_district
            FROM business_registrations_cache
            WHERE id = %s
        """, (test_id,))
        after = cur.fetchone()
        if after:
            print(f"  ID {after['id']}: {after['full_business_address']} -> zoning={after['zoning_district']}")

if __name__ == "__main__":
    test_update_query()


