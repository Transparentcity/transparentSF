#!/usr/bin/env python3
"""
Test script to examine business data at 2139 Polk St
"""

import sys
import os
sys.path.append('ai')

from tools.data_fetcher import fetch_data_from_api

def test_polk_street():
    """Test query for 2139 Polk St businesses"""
    
    query = """
    SELECT 
        location,
        dba_name,
        naic_code_description,
        lic_code_description,
        business_corridor,
        supervisor_district,
        dba_start_date,
        location_start_date,
        dba_end_date,
        location_end_date,
        administratively_closed,
        full_business_address
    WHERE full_business_address LIKE '%2139 POLK%'
    ORDER BY dba_start_date DESC
    LIMIT 20
    """
    
    print("Querying businesses at 2139 Polk St...")
    
    result = fetch_data_from_api({
        'endpoint': 'g8m3-pdis',
        'query': query
    })
    
    if result and 'data' in result:
        print(f"Found {len(result['data'])} records:")
        print("-" * 80)
        
        for i, business in enumerate(result['data']):
            print(f"\nRecord {i+1}:")
            print(f"  Business Name: {business.get('dba_name', 'N/A')}")
            print(f"  License Description: '{business.get('lic_code_description', 'N/A')}'")
            print(f"  NAICS Description: {business.get('naic_code_description', 'N/A')}")
            print(f"  Address: {business.get('full_business_address', 'N/A')}")
            print(f"  Start Date: {business.get('dba_start_date', 'N/A')}")
            print(f"  End Date: {business.get('dba_end_date', 'N/A')}")
            print(f"  Location Start: {business.get('location_start_date', 'N/A')}")
            print(f"  Location End: {business.get('location_end_date', 'N/A')}")
            print(f"  Admin Closed: {business.get('administratively_closed', 'N/A')}")
            print(f"  Business Corridor: {business.get('business_corridor', 'N/A')}")
            
    else:
        print("No data found or error occurred")
        print(f"Result: {result}")

if __name__ == "__main__":
    test_polk_street()

