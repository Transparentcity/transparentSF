#!/usr/bin/env python3
"""Fix metric 83 YTD query to match working retail metrics."""

import sys
sys.path.insert(0, '/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai')

import os
from dotenv import load_dotenv
from tools.db_utils import get_pooled_connection
import psycopg2.extras

# Load environment from ai/.env
load_dotenv('/Users/simongoldman/Documents/TransparentSF_from_git/transparentSF/ai/.env')

# The current metric tracks "open restaurants" which is a point-in-time count
# For a true YTD comparison like retail metrics, we have two options:
#
# OPTION 1: Track NEW restaurant openings (like retail openings metric 12)
# - This shows trend of new restaurants opening each day
# - Comparable to other opening metrics
#
# OPTION 2: Keep the "currently open" concept but fix the query structure
# - Use dynamic dates instead of hardcoded 2024/2025
# - Still tracks point-in-time "how many are open" but updates automatically

def show_proposed_fixes():
    """Show the current query and proposed fixes."""
    
    print("=" * 80)
    print("FIXING METRIC 83: Licensed Restaurants YTD Query")
    print("=" * 80)
    print()
    
    print("CURRENT QUERY (BROKEN):")
    print("-" * 80)
    print("""
SELECT date_trunc_ym('2025-01-01') as date, COUNT(*) as value, supervisor_district 
WHERE (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
AND administratively_closed IS NULL 
AND location_start_date <= '2025-01-01' 
...
UNION ALL [repeats 23 more times with hardcoded dates]

PROBLEMS:
  ❌ Hardcoded dates (2025-01-01, 2024-01-01, etc.)
  ❌ 24 UNION ALL statements (one per month)
  ❌ Monthly snapshots, not daily YTD data
  ❌ Will be wrong in 2026 without manual update
    """)
    
    print("\n" + "=" * 80)
    print("OPTION 1: Track NEW Restaurant Openings (Recommended)")
    print("=" * 80)
    print("""
This matches the pattern of working retail metrics (#12, #14, etc.)
Shows the daily trend of new restaurants opening.

NEW YTD QUERY:
""")
    
    option1_ytd = """SELECT date_trunc_ymd(CASE WHEN dba_start_date >= last_year_start THEN dba_start_date ELSE location_start_date END) as date, 
COUNT(*) as value, 
supervisor_district
WHERE ((dba_start_date >= last_year_start AND dba_start_date <= current_date) 
       OR (location_start_date >= last_year_start AND location_start_date <= current_date AND dba_start_date IS NULL))
AND (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%')
AND administratively_closed IS NULL
GROUP BY date, supervisor_district
ORDER BY date"""
    
    print(option1_ytd)
    
    print("""
NEW METRIC QUERY:
""")
    
    option1_metric = """SELECT 'New Licensed Restaurants' as label, 
CURRENT_DATE as max_date,
COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) 
               OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL))
     THEN 1 END) as this_year,
COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) 
               OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL))
     THEN 1 END) as last_year,
(COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) 
               OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL))
     THEN 1 END) - 
 COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) 
               OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL))
     THEN 1 END)) as delta,
((COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= this_year_start AND dba_start_date <= this_year_end) 
               OR (location_start_date >= this_year_start AND location_start_date <= this_year_end AND dba_start_date IS NULL))
     THEN 1 END) - 
  COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) 
               OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL))
     THEN 1 END)) * 100.0 / 
 NULLIF(COUNT(CASE WHEN (lic LIKE '%H24%' OR lic LIKE '%H25%' OR lic LIKE '%H26%') 
          AND administratively_closed IS NULL 
          AND ((dba_start_date >= last_year_start AND dba_start_date <= last_year_end) 
               OR (location_start_date >= last_year_start AND location_start_date <= last_year_end AND dba_start_date IS NULL))
     THEN 1 END), 0)) as perc_diff,
supervisor_district
GROUP BY supervisor_district"""
    
    print(option1_metric)
    
    print("""
ADVANTAGES:
  ✅ Dynamic dates (last_year_start, current_date)
  ✅ Single query, no UNION ALL
  ✅ Daily granularity for trend analysis
  ✅ Matches pattern of working retail metrics
  ✅ Will work in 2026, 2027, etc. automatically
    """)
    
    print("\n" + "=" * 80)
    print("OPTION 2: Keep 'Currently Open' Concept with Monthly Snapshots")
    print("=" * 80)
    print("""
This keeps the monthly snapshot approach but fixes the date handling.
Shows "how many restaurants were open on the 1st of each month".

Note: This is NOT a true YTD metric - it's a point-in-time count.
The metric name should be changed to reflect this if you choose this option.
    """)
    
    return option1_ytd, option1_metric

def apply_fix(option1_ytd, option1_metric):
    """Apply the fix to metric 83."""
    
    print("\n" + "=" * 80)
    print("APPLYING FIX TO METRIC 83")
    print("=" * 80)
    
    try:
        with get_pooled_connection() as connection:
            cursor = connection.cursor()
            
            # Update the metric with Option 1 (recommended)
            cursor.execute("""
                UPDATE metrics
                SET 
                    metric_name = '🍴 New Licensed Restaurants',
                    ytd_query = %s,
                    metric_query = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 83
            """, (option1_ytd, option1_metric))
            
            connection.commit()
            
            print("\n✅ SUCCESS! Metric 83 has been updated.")
            print("\nChanges:")
            print("  - Metric name: '🍴 Licensed Restaurants' → '🍴 New Licensed Restaurants'")
            print("  - YTD query: Fixed to track daily new restaurant openings")
            print("  - Metric query: Fixed to use dynamic date placeholders")
            print("\nThe metric now:")
            print("  ✅ Uses dynamic dates (will work in future years)")
            print("  ✅ Tracks daily new restaurant openings")
            print("  ✅ Matches the pattern of working retail metrics")
            print("  ✅ Supports district-level data")
            
            cursor.close()
            
    except Exception as e:
        print(f"\n❌ ERROR applying fix: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("\n")
    option1_ytd, option1_metric = show_proposed_fixes()
    
    print("\n" + "=" * 80)
    response = input("\nApply Option 1 (Recommended) to metric 83? (yes/no): ")
    
    if response.lower() in ['yes', 'y']:
        apply_fix(option1_ytd, option1_metric)
    else:
        print("\nFix not applied. You can:")
        print("  1. Run this script again to apply Option 1")
        print("  2. Manually update metric 83 in the database")
        print("  3. Use the chat interface to modify the metric")

