#!/usr/bin/env python3
"""Apply the fix to metric 83 YTD query automatically."""

import sys
sys.path.insert(0, '/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai')

import os
from dotenv import load_dotenv
from tools.db_utils import get_pooled_connection

# Load environment from ai/.env
load_dotenv('/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai/.env')

# Fixed YTD query - tracks new restaurant openings (like retail metrics)
FIXED_YTD_QUERY = """SELECT date_trunc_ymd(CASE WHEN dba_start_date >= last_year_start THEN dba_start_date ELSE location_start_date END) as date, COUNT(*) as value, supervisor_district WHERE ((dba_start_date >= last_year_start AND dba_start_date <= current_date) OR (location_start_date >= last_year_start AND location_start_date <= current_date AND dba_start_date IS NULL)) AND (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL GROUP BY date, supervisor_district ORDER BY date"""

# Fixed metric query - year-over-year comparison of new restaurant openings
FIXED_METRIC_QUERY = """SELECT 'New Licensed Restaurants' as label, CURRENT_DATE as max_date, COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL)) THEN 1 END) as this_year, COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL)) THEN 1 END) as last_year, (COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL)) THEN 1 END) - COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL)) THEN 1 END)) as delta, ((COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL)) THEN 1 END) - COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL)) THEN 1 END)) * 100.0 / NULLIF(COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') AND administratively_closed IS NULL AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL)) THEN 1 END), 0)) as perc_diff, supervisor_district GROUP BY supervisor_district"""

def apply_fix():
    """Apply the fix to metric 83."""
    
    print("=" * 80)
    print("APPLYING FIX TO METRIC 83: Licensed Restaurants")
    print("=" * 80)
    print()
    
    try:
        with get_pooled_connection() as connection:
            cursor = connection.cursor()
            
            print("Updating metric 83 in database...")
            
            # Update the metric with the fixed queries
            cursor.execute("""
                UPDATE metrics
                SET 
                    metric_name = '🍴 New Licensed Restaurants',
                    ytd_query = %s,
                    metric_query = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 83
                RETURNING id, metric_name
            """, (FIXED_YTD_QUERY, FIXED_METRIC_QUERY))
            
            result = cursor.fetchone()
            connection.commit()
            
            if result:
                print("\n✅ SUCCESS! Metric 83 has been updated.")
                print(f"\nMetric ID: {result[0]}")
                print(f"New Name: {result[1]}")
                print("\nChanges applied:")
                print("  ✅ Metric name updated to '🍴 New Licensed Restaurants'")
                print("  ✅ YTD query fixed to use dynamic date placeholders")
                print("  ✅ Metric query fixed to track new restaurant openings")
                print("  ✅ Now matches the pattern of working retail metrics")
                print("\nThe metric now tracks:")
                print("  - Daily new restaurant openings (H24, H25, H26 licenses)")
                print("  - Year-over-year comparisons")
                print("  - District-level data")
                print("  - Uses dynamic dates (will work in 2026+)")
                print("\nNext steps:")
                print("  1. Review the metric in the dashboard")
                print("  2. Regenerate charts if needed")
                print("  3. See METRIC_83_FIX_SUMMARY.md for full details")
            else:
                print("\n⚠️  No rows were updated. Metric 83 may not exist.")
            
            cursor.close()
            
    except Exception as e:
        print(f"\n❌ ERROR applying fix: {e}")
        import traceback
        traceback.print_exc()
        print("\nYou can:")
        print("  1. Check database connection")
        print("  2. Verify metric 83 exists: SELECT * FROM metrics WHERE id = 83")
        print("  3. See METRIC_83_FIX_SUMMARY.md for manual SQL update")

if __name__ == "__main__":
    apply_fix()

