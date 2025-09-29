"""
True Real-Time Vacancy Analysis Route

This is an exact replica of the old working real-time vacancy analysis code.
It should produce exactly the same results as the original working version.
"""

import logging
import os
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, Optional, List
import json
from collections import defaultdict
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Templates will be set by main app
templates = None

def set_templates(templates_instance):
    """Set templates instance from main app"""
    global templates
    templates = templates_instance

@router.get("/vacancy")
async def vacancy_page(request: Request):
    """Serve the true real-time vacancy analysis page"""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    # Get Mapbox token from environment
    mapbox_token = os.getenv('MAPBOX_ACCESS_TOKEN')
    if not mapbox_token:
        logger.error("MAPBOX_ACCESS_TOKEN not found in environment")
        raise HTTPException(status_code=500, detail="Mapbox token not configured")
    
    return templates.TemplateResponse("vacancy_analysis.html", {
        "request": request,
        "mapbox_token": mapbox_token
    })

@router.get("/api/vacancy/data")
async def get_vacancy_data(
    industry_type: Optional[str] = Query(None),  # NAICS code description
    license_type: Optional[str] = Query(None),   # License code description
    business_corridor: Optional[str] = Query(None),
    corridor_only: bool = False,
    district: Optional[str] = Query(None),
    limit: Optional[int] = Query(None)  # Add limit parameter for testing
):
    """Get true real-time vacancy analysis data - exact copy of old working code"""
    try:
        # Build the SOQL query for DataSF API - EXACT COPY from old code
        base_query = """
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
        WHERE location IS NOT NULL
        """
        
        # Add filters - EXACT COPY from old code
        if industry_type:
            base_query += f" AND naic_code_description LIKE '%{industry_type}%'"
        
        if license_type:
            base_query += f" AND lic_code_description LIKE '%{license_type}%'"
        
        if business_corridor:
            base_query += f" AND business_corridor LIKE '%{business_corridor}%'"
        
        if corridor_only:
            base_query += " AND business_corridor IS NOT NULL AND business_corridor != ''"
        
        if district:
            base_query += f" AND supervisor_district = '{district}'"
        
        # Add limit - EXACT COPY from old code
        if limit:
            base_query += f" LIMIT {limit}"
        else:
            base_query += " LIMIT 50000"
        
        # Fetch data from DataSF API - EXACT COPY from old code
        from ai.tools.data_fetcher import fetch_data_from_api
        
        query_object = {
            'endpoint': 'g8m3-pdis',
            'query': base_query
        }
        
        result = fetch_data_from_api(query_object)
        
        if not result or 'data' not in result:
            error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
            raise HTTPException(status_code=500, detail=f"Failed to fetch data from DataSF: {error_msg}")
        
        raw_data = result['data']
        
        # Group data by address and process vacancy status per address - EXACT COPY from old code
        def parse_date(date_str):
            if not date_str:
                return None
            try:
                return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
            except:
                return None
        
        def parse_location_coordinates(location_data):
            """Extract coordinates from location field - EXACT COPY from old code"""
            coordinates = [0, 0]  # Default coordinates
            
            if location_data:
                if isinstance(location_data, dict):
                    # If it's already a dict with coordinates
                    if 'coordinates' in location_data:
                        coordinates = location_data['coordinates']
                    elif 'longitude' in location_data and 'latitude' in location_data:
                        coordinates = [location_data['longitude'], location_data['latitude']]
                elif isinstance(location_data, str) and location_data.startswith('POINT'):
                    try:
                        # Extract coordinates from "POINT (-122.435385968 37.637676996)"
                        coords_str = location_data.replace('POINT (', '').replace(')', '')
                        lon, lat = coords_str.split()
                        coordinates = [float(lon), float(lat)]
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse coordinates from '{location_data}': {e}")
                else:
                    logger.warning(f"Unexpected location data format: {type(location_data)} - {location_data}")
            
            return coordinates
        
        # Group businesses by address - EXACT COPY from old code
        address_groups = defaultdict(list)
        
        for record in raw_data:
            # Use full_business_address as the grouping key, fall back to coordinates
            address_key = record.get('full_business_address')
            if not address_key:
                # Fall back to coordinates for grouping
                coordinates = parse_location_coordinates(record.get('location'))
                address_key = f"{coordinates[0]:.6f},{coordinates[1]:.6f}"
            
            address_groups[address_key].append(record)
        
        processed_data = []
        
        # Process each address group - EXACT COPY from old code
        for address_key, businesses in address_groups.items():
            # Find all open and close dates for this address
            all_open_dates = []
            all_close_dates = []
            
            # Collect all business names at this address
            business_names = []
            
            # Use the first business for location and address info
            primary_business = businesses[0]
            coordinates = parse_location_coordinates(primary_business.get('location'))
            
            for business in businesses:
                business_names.append(business.get('dba_name', 'Unknown Business'))
                
                # Collect all dates from this business
                dba_start = parse_date(business.get('dba_start_date'))
                location_start = parse_date(business.get('location_start_date'))
                dba_end = parse_date(business.get('dba_end_date'))
                location_end = parse_date(business.get('location_end_date'))
                
                # Add valid dates to collections
                if dba_start:
                    all_open_dates.append(dba_start)
                if location_start:
                    all_open_dates.append(location_start)
                if dba_end:
                    all_close_dates.append(dba_end)
                if location_end:
                    all_close_dates.append(location_end)
            
            # Find most recent dates across all businesses at this address
            most_recent_open_date = max(all_open_dates) if all_open_dates else None
            most_recent_close_date = max(all_close_dates) if all_close_dates else None
            
            # Determine address status: Open if most recent activity was an opening - EXACT COPY from old code
            if not most_recent_close_date:
                status = 'Open'
            elif not most_recent_open_date:
                status = 'Closed'  
            elif most_recent_close_date > most_recent_open_date:
                status = 'Closed'
            else:
                status = 'Open'
            
            # Create a representative business name (primary or combined) - EXACT COPY from old code
            if len(business_names) == 1:
                display_name = business_names[0]
            else:
                display_name = f"{business_names[0]} (+{len(business_names)-1} others)"
            
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': display_name,
                'naic_code_description': primary_business.get('naic_code_description'),
                'lic_code_description': primary_business.get('lic_code_description'),
                'business_corridor': primary_business.get('business_corridor'),
                'supervisor_district': primary_business.get('supervisor_district'),
                'most_recent_open_date': most_recent_open_date.isoformat() if most_recent_open_date else None,
                'most_recent_close_date': most_recent_close_date.isoformat() if most_recent_close_date else None,
                'status': status,
                'full_business_address': primary_business.get('full_business_address'),
                'business_count': len(businesses),
                'all_business_names': business_names,
                # Add properties that the JavaScript expects
                'total_addresses': 1,
                'open_addresses': 1 if status == 'Open' else 0,
                'closed_addresses': 1 if status == 'Closed' else 0,
                'street_level_addresses': 1,
                'upper_floor_addresses': 0,
                'total_businesses': len(businesses),
                'addresses_data': [{
                    'address': primary_business.get('full_business_address'),
                    'status': status,
                    'is_street_level': True,
                    'business_count': len(businesses),
                    'businesses': businesses,
                    'business_names': business_names,
                    'currently_open_businesses': len(businesses) if status == 'Open' else 0,
                    'currently_closed_businesses': len(businesses) if status == 'Closed' else 0
                }]
            }
            
            processed_data.append(processed_record)
        
        # Calculate summary statistics - EXACT COPY from old code
        total_locations = len(processed_data)
        open_locations = len([d for d in processed_data if d['status'] == 'Open'])
        closed_locations = len([d for d in processed_data if d['status'] == 'Closed'])
        vacancy_rate = (closed_locations / total_locations * 100) if total_locations > 0 else 0
        
        # Group by district for district-level analysis - EXACT COPY from old code
        district_stats = {}
        for record in processed_data:
            district = record['supervisor_district'] or 'Unknown'
            if district not in district_stats:
                district_stats[district] = {'total': 0, 'open': 0, 'closed': 0}
            
            district_stats[district]['total'] += 1
            if record['status'] == 'Open':
                district_stats[district]['open'] += 1
            else:
                district_stats[district]['closed'] += 1
        
        # Calculate vacancy rates by district - EXACT COPY from old code
        for district in district_stats:
            stats = district_stats[district]
            stats['vacancy_rate'] = (stats['closed'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        return JSONResponse({
            "status": "success",
            "data": processed_data,
            "summary": {
                "total_locations": total_locations,
                "open_locations": open_locations,
                "closed_locations": closed_locations,
                "vacancy_rate": round(vacancy_rate, 2)
            },
            "district_stats": district_stats,
            "filters_applied": {
                "industry_type": industry_type,
                "license_type": license_type,
                "business_corridor": business_corridor,
                "corridor_only": corridor_only,
                "district": district
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_true_realtime_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting true real-time vacancy data: {str(e)}")

@router.get("/api/vacancy/filters")
async def get_filter_options():
    """Get available filter options for the true real-time vacancy analysis - EXACT COPY from old code"""
    try:
        from ai.tools.data_fetcher import fetch_data_from_api
        
        # Get unique industry types (NAICS codes) - EXACT COPY from old code
        industry_query = """
        SELECT DISTINCT naic_code_description 
        WHERE naic_code_description IS NOT NULL 
        AND naic_code_description != ''
        ORDER BY naic_code_description
        LIMIT 100
        """
        
        industry_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': industry_query
        })
        
        industry_types = []
        if industry_result and 'data' in industry_result:
            industry_types = [row['naic_code_description'] for row in industry_result['data']]
        
        # Get unique license types - EXACT COPY from old code
        license_query = """
        SELECT DISTINCT lic_code_description 
        WHERE lic_code_description IS NOT NULL 
        AND lic_code_description != ''
        ORDER BY lic_code_description
        LIMIT 100
        """
        
        license_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': license_query
        })
        
        license_types = []
        if license_result and 'data' in license_result:
            license_types = [row['lic_code_description'] for row in license_result['data']]
        
        # Get unique business corridors - EXACT COPY from old code
        corridor_query = """
        SELECT DISTINCT business_corridor 
        WHERE business_corridor IS NOT NULL 
        AND business_corridor != ''
        ORDER BY business_corridor
        LIMIT 100
        """
        
        corridor_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': corridor_query
        })
        
        business_corridors = []
        if corridor_result and 'data' in corridor_result:
            business_corridors = [row['business_corridor'] for row in corridor_result['data']]
        
        # Get districts - EXACT COPY from old code
        district_query = """
        SELECT DISTINCT supervisor_district 
        WHERE supervisor_district IS NOT NULL 
        AND supervisor_district != ''
        ORDER BY supervisor_district
        LIMIT 20
        """
        
        district_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': district_query
        })
        
        districts = []
        if district_result and 'data' in district_result:
            districts = [str(row['supervisor_district']) for row in district_result['data']]
        
        return JSONResponse({
            "status": "success",
            "filters": {
                "industry_types": industry_types,
                "license_types": license_types,
                "business_corridors": business_corridors,
                "districts": districts
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_true_realtime_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting true real-time filter options: {str(e)}")
