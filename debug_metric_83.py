#!/usr/bin/env python3
"""Debug script to compare metric 83 (Licensed Restaurants) with working metrics."""

import sys
sys.path.insert(0, '/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai')

import os
from dotenv import load_dotenv
from tools.db_utils import get_pooled_connection
import psycopg2.extras

# Load environment from ai/.env
load_dotenv('/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai/.env')

def compare_metrics():
    """Compare metric 83 with working retail metrics."""
    
    print("=" * 80)
    print("Comparing Metric 83 (Licensed Restaurants) with Working Metrics")
    print("=" * 80)
    print()
    
    try:
        with get_pooled_connection() as connection:
            cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            # Query for metric 83 and similar retail/restaurant metrics
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
                   OR metric_key LIKE '%retail%' 
                   OR metric_key LIKE '%restaurant%'
                   OR metric_name ILIKE '%retail%'
                   OR metric_name ILIKE '%restaurant%'
                   OR metric_name ILIKE '%business%'
                ORDER BY id
                LIMIT 15
            """)
            
            metrics = cursor.fetchall()
            
            if not metrics:
                print("❌ No metrics found!")
                return
            
            print(f"Found {len(metrics)} metrics\n")
            
            # Focus on metric 83 first
            metric_83 = None
            for metric in metrics:
                if metric['id'] == 83:
                    metric_83 = metric
                    break
            
            if metric_83:
                print("\n" + "=" * 80)
                print(f"METRIC 83: {metric_83['metric_name']}")
                print("=" * 80)
                print(f"ID: {metric_83['id']}")
                print(f"Key: {metric_83['metric_key']}")
                print(f"Endpoint: {metric_83['endpoint']}")
                print(f"Active: {metric_83['is_active']}")
                print(f"Location Fields: {metric_83['location_fields']}")
                print("\nYTD Query:")
                print("-" * 80)
                print(metric_83['ytd_query'] if metric_83['ytd_query'] else "NO YTD QUERY")
                print("\nMetric Query:")
                print("-" * 80)
                print(metric_83['metric_query'] if metric_83['metric_query'] else "NO METRIC QUERY")
            else:
                print("❌ Metric 83 not found!")
            
            # Now show comparison metrics
            print("\n\n" + "=" * 80)
            print("COMPARISON METRICS (Working Examples)")
            print("=" * 80)
            
            for metric in metrics:
                if metric['id'] != 83:
                    print(f"\n--- Metric {metric['id']}: {metric['metric_name']} ---")
                    print(f"Key: {metric['metric_key']}")
                    print(f"Endpoint: {metric['endpoint']}")
                    print(f"Active: {metric['is_active']}")
                    print(f"Location Fields: {metric['location_fields']}")
                    
                    if metric['ytd_query']:
                        print("\nYTD Query:")
                        print("-" * 40)
                        # Show first 300 characters
                        ytd = metric['ytd_query']
                        if len(ytd) > 300:
                            print(ytd[:300] + "...")
                        else:
                            print(ytd)
                    else:
                        print("\nYTD Query: NONE")
            
            cursor.close()
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    compare_metrics()

