#!/usr/bin/env python3
"""Find metric 83 and show metrics around that ID range."""

import sys
sys.path.insert(0, '/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai')

import os
from dotenv import load_dotenv
from tools.db_utils import get_pooled_connection
import psycopg2.extras

# Load environment from ai/.env
load_dotenv('/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai/.env')

def find_metric_83():
    """Find metric 83 and nearby metrics."""
    
    print("=" * 80)
    print("Searching for Metric 83")
    print("=" * 80)
    print()
    
    try:
        with get_pooled_connection() as connection:
            cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            # First, check if metric 83 exists at all
            cursor.execute("SELECT COUNT(*) as count FROM metrics WHERE id = 83")
            result = cursor.fetchone()
            print(f"Metrics with ID 83: {result['count']}")
            print()
            
            # Get metric 83 specifically
            cursor.execute("""
                SELECT 
                    id,
                    metric_name,
                    metric_key,
                    endpoint,
                    ytd_query,
                    metric_query,
                    location_fields,
                    is_active
                FROM metrics
                WHERE id = 83
            """)
            
            metric_83 = cursor.fetchone()
            
            if metric_83:
                print("\n" + "=" * 80)
                print(f"FOUND METRIC 83: {metric_83['metric_name']}")
                print("=" * 80)
                print(f"ID: {metric_83['id']}")
                print(f"Key: {metric_83['metric_key']}")
                print(f"Endpoint: {metric_83['endpoint']}")
                print(f"Active: {metric_83['is_active']}")
                print(f"Location Fields: {metric_83['location_fields']}")
                print("\nYTD Query:")
                print("-" * 80)
                if metric_83['ytd_query']:
                    print(metric_83['ytd_query'])
                else:
                    print("NO YTD QUERY")
                print("\nMetric Query:")
                print("-" * 80)
                if metric_83['metric_query']:
                    print(metric_83['metric_query'])
                else:
                    print("NO METRIC QUERY")
            else:
                print("❌ Metric 83 NOT FOUND in database!")
                print("\nLet's check metrics around ID 83...")
            
            # Show metrics around ID 83 (75-90)
            print("\n\n" + "=" * 80)
            print("METRICS IN RANGE 75-90")
            print("=" * 80)
            
            cursor.execute("""
                SELECT 
                    id,
                    metric_name,
                    metric_key,
                    endpoint,
                    is_active,
                    CASE 
                        WHEN ytd_query IS NULL THEN 'NO YTD QUERY'
                        WHEN LENGTH(ytd_query) < 50 THEN ytd_query
                        ELSE LEFT(ytd_query, 100) || '...'
                    END as ytd_preview
                FROM metrics
                WHERE id BETWEEN 75 AND 90
                ORDER BY id
            """)
            
            nearby_metrics = cursor.fetchall()
            
            for metric in nearby_metrics:
                marker = ">>> " if metric['id'] == 83 else "    "
                print(f"\n{marker}Metric {metric['id']}: {metric['metric_name']}")
                print(f"{marker}  Key: {metric['metric_key']}")
                print(f"{marker}  Endpoint: {metric['endpoint']}")
                print(f"{marker}  Active: {metric['is_active']}")
                print(f"{marker}  YTD: {metric['ytd_preview']}")
            
            # Also search for any restaurant-related metrics
            print("\n\n" + "=" * 80)
            print("RESTAURANT-RELATED METRICS")
            print("=" * 80)
            
            cursor.execute("""
                SELECT 
                    id,
                    metric_name,
                    metric_key,
                    endpoint,
                    is_active
                FROM metrics
                WHERE metric_name ILIKE '%restaurant%' 
                   OR metric_key ILIKE '%restaurant%'
                ORDER BY id
            """)
            
            restaurant_metrics = cursor.fetchall()
            
            if restaurant_metrics:
                for metric in restaurant_metrics:
                    print(f"\nMetric {metric['id']}: {metric['metric_name']}")
                    print(f"  Key: {metric['metric_key']}")
                    print(f"  Active: {metric['is_active']}")
            else:
                print("No restaurant-related metrics found!")
            
            cursor.close()
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    find_metric_83()

