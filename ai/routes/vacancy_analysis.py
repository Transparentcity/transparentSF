"""
Vacancy Analysis Route

This route provides a custom map interface for analyzing business vacancy rates
based on the business registration dataset. It shows open/closed status of businesses
and calculates vacancy rates with filtering by license type and business corridors.
"""

import logging
import os
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from typing import Dict, Any, Optional, List
import json

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

@router.get("/vacancy-analysis")
async def vacancy_analysis_page(request: Request):
    """Serve the vacancy analysis page"""
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

@router.get("/api/vacancy-analysis/data")
async def get_vacancy_data(
    industry_type: Optional[str] = None,  # NAICS code description
    license_type: Optional[str] = None,   # License code description
    business_corridor: Optional[str] = None,
    corridor_only: bool = False,
    district: Optional[str] = None
):
    """Get vacancy analysis data with filters"""
    try:
        # Build the SOQL query for DataSF API
        # This query determines the status of each business location
        # by comparing the most recent open vs close dates
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
        
        # Add filters
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
        
        # Add limit - increased for heatmap aggregation
        base_query += " LIMIT 50000"
        
        # Fetch data from DataSF API
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
        
        # Group data by address and process vacancy status per address
        from collections import defaultdict
        from datetime import datetime
        
        def parse_date(date_str):
            if not date_str:
                return None
            try:
                return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
            except:
                return None
        
        def parse_location_coordinates(location_data):
            """Extract coordinates from location field"""
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
        
        # Group businesses by address
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
        
        # Process each address group
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
            
            # Determine address status: Open if most recent activity was an opening
            if not most_recent_close_date:
                status = 'Open'
            elif not most_recent_open_date:
                status = 'Closed'  
            elif most_recent_close_date > most_recent_open_date:
                status = 'Closed'
            else:
                status = 'Open'
            
            # Create a representative business name (primary or combined)
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
                'all_business_names': business_names
            }
            
            processed_data.append(processed_record)
        
        # Calculate summary statistics
        total_locations = len(processed_data)
        open_locations = len([d for d in processed_data if d['status'] == 'Open'])
        closed_locations = len([d for d in processed_data if d['status'] == 'Closed'])
        vacancy_rate = (closed_locations / total_locations * 100) if total_locations > 0 else 0
        
        # Group by district for district-level analysis
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
        
        # Calculate vacancy rates by district
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
        logger.error(f"Error in get_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting vacancy data: {str(e)}")

@router.get("/api/vacancy-analysis/filters")
async def get_filter_options():
    """Get available filter options for the vacancy analysis"""
    try:
        from ai.tools.data_fetcher import fetch_data_from_api
        
        # Get unique industry types (NAICS codes)
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
        
        # Get unique license types
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
        
        # Get unique business corridors
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
        
        # Get districts
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
        logger.error(f"Error in get_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting filter options: {str(e)}")
