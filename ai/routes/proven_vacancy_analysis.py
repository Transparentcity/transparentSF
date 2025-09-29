"""
Proven Vacancy Analysis Route

This route uses the proven vacancy database table built from the working
real-time logic. It provides the same interface as the old real-time
approach but with better performance through database queries.

Based on the proven logic that was accurately placing buildings and floors.
"""

import logging
import os
from fastapi import APIRouter, Request, HTTPException, Query
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

@router.get("/proven-vacancy")
async def proven_vacancy_analysis_page(request: Request):
    """Serve the proven vacancy analysis page"""
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

@router.get("/api/proven-vacancy/data")
async def get_proven_vacancy_data(
    industry_type: Optional[str] = Query(None),  # NAICS code description
    license_type: Optional[str] = Query(None),   # License code description
    business_corridor: Optional[str] = Query(None),
    corridor_only: bool = False,
    district: Optional[str] = Query(None)
):
    """Get proven vacancy analysis data with filters using database queries"""
    try:
        # Get database connection
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise HTTPException(status_code=500, detail="Database not configured")
        
        conn = psycopg2.connect(db_url)
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Build the query using the proven_vacancy table
                base_query = """
                SELECT 
                    address_key,
                    full_business_address,
                    latitude,
                    longitude,
                    dba_name,
                    naic_code_description,
                    lic_code_description,
                    business_corridor,
                    supervisor_district,
                    most_recent_open_date,
                    most_recent_close_date,
                    status,
                    business_count,
                    all_business_names
                FROM proven_vacancy
                WHERE latitude != 0 AND longitude != 0
                """
                
                params = []
                
                # Add filters
                if industry_type:
                    base_query += " AND naic_code_description ILIKE %s"
                    params.append(f"%{industry_type}%")
                
                if license_type:
                    base_query += " AND lic_code_description ILIKE %s"
                    params.append(f"%{license_type}%")
                
                if business_corridor:
                    base_query += " AND business_corridor ILIKE %s"
                    params.append(f"%{business_corridor}%")
                
                if corridor_only:
                    base_query += " AND business_corridor IS NOT NULL AND business_corridor != ''"
                
                if district:
                    base_query += " AND supervisor_district = %s"
                    params.append(district)
                
                # Add limit for performance
                base_query += " LIMIT 10000"
                
                # Execute query
                cur.execute(base_query, params)
                raw_data = cur.fetchall()
                
                logger.info(f"Retrieved {len(raw_data)} records from proven_vacancy table")
                
                # Process data to match the expected format
                processed_data = []
                
                for record in raw_data:
                    # Parse all_business_names from JSON
                    all_business_names = []
                    if record['all_business_names']:
                        try:
                            all_business_names = json.loads(record['all_business_names'])
                        except (json.JSONDecodeError, TypeError):
                            all_business_names = [record['dba_name']]
                    
                    processed_record = {
                        'location': {
                            'type': 'Point',
                            'coordinates': [record['longitude'], record['latitude']]
                        },
                        'dba_name': record['dba_name'],
                        'naic_code_description': record['naic_code_description'],
                        'lic_code_description': record['lic_code_description'],
                        'business_corridor': record['business_corridor'],
                        'supervisor_district': record['supervisor_district'],
                        'most_recent_open_date': record['most_recent_open_date'].isoformat() if record['most_recent_open_date'] else None,
                        'most_recent_close_date': record['most_recent_close_date'].isoformat() if record['most_recent_close_date'] else None,
                        'status': record['status'],
                        'full_business_address': record['full_business_address'],
                        'business_count': record['business_count'],
                        'all_business_names': all_business_names
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
        
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error in get_proven_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting proven vacancy data: {str(e)}")

@router.get("/api/proven-vacancy/filters")
async def get_proven_filter_options():
    """Get available filter options for the proven vacancy analysis"""
    try:
        # Get database connection
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise HTTPException(status_code=500, detail="Database not configured")
        
        conn = psycopg2.connect(db_url)
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get unique industry types (NAICS codes)
                cur.execute("""
                    SELECT DISTINCT naic_code_description 
                    FROM proven_vacancy
                    WHERE naic_code_description IS NOT NULL 
                    AND naic_code_description != ''
                    ORDER BY naic_code_description
                    LIMIT 100
                """)
                industry_types = [row['naic_code_description'] for row in cur.fetchall()]
                
                # Get unique license types
                cur.execute("""
                    SELECT DISTINCT lic_code_description 
                    FROM proven_vacancy
                    WHERE lic_code_description IS NOT NULL 
                    AND lic_code_description != ''
                    ORDER BY lic_code_description
                    LIMIT 100
                """)
                license_types = [row['lic_code_description'] for row in cur.fetchall()]
                
                # Get unique business corridors
                cur.execute("""
                    SELECT DISTINCT business_corridor 
                    FROM proven_vacancy
                    WHERE business_corridor IS NOT NULL 
                    AND business_corridor != ''
                    ORDER BY business_corridor
                    LIMIT 100
                """)
                business_corridors = [row['business_corridor'] for row in cur.fetchall()]
                
                # Get districts
                cur.execute("""
                    SELECT DISTINCT supervisor_district 
                    FROM proven_vacancy
                    WHERE supervisor_district IS NOT NULL 
                    AND supervisor_district != ''
                    ORDER BY supervisor_district
                    LIMIT 20
                """)
                districts = [str(row['supervisor_district']) for row in cur.fetchall()]
                
                return JSONResponse({
                    "status": "success",
                    "filters": {
                        "industry_types": industry_types,
                        "license_types": license_types,
                        "business_corridors": business_corridors,
                        "districts": districts
                    }
                })
        
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error in get_proven_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting proven filter options: {str(e)}")

@router.get("/api/proven-vacancy/stats")
async def get_proven_vacancy_stats():
    """Get statistics about the proven vacancy table"""
    try:
        # Get database connection
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise HTTPException(status_code=500, detail="Database not configured")
        
        conn = psycopg2.connect(db_url)
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get total count
                cur.execute("SELECT COUNT(*) FROM proven_vacancy")
                total_count = cur.fetchone()['count']
                
                # Get status counts
                cur.execute("SELECT status, COUNT(*) FROM proven_vacancy GROUP BY status")
                status_counts = {row['status']: row['count'] for row in cur.fetchall()}
                
                # Get district counts
                cur.execute("""
                    SELECT supervisor_district, COUNT(*) 
                    FROM proven_vacancy 
                    WHERE supervisor_district IS NOT NULL 
                    GROUP BY supervisor_district 
                    ORDER BY COUNT(*) DESC 
                    LIMIT 10
                """)
                district_counts = {row['supervisor_district']: row['count'] for row in cur.fetchall()}
                
                # Get corridor counts
                cur.execute("""
                    SELECT business_corridor, COUNT(*) 
                    FROM proven_vacancy 
                    WHERE business_corridor IS NOT NULL AND business_corridor != ''
                    GROUP BY business_corridor 
                    ORDER BY COUNT(*) DESC 
                    LIMIT 10
                """)
                corridor_counts = {row['business_corridor']: row['count'] for row in cur.fetchall()}
                
                # Get update info
                cur.execute("""
                    SELECT 
                        MIN(created_at) as first_created,
                        MAX(updated_at) as last_updated
                    FROM proven_vacancy
                """)
                update_info = cur.fetchone()
                
                return JSONResponse({
                    "status": "success",
                    "stats": {
                        "total_locations": total_count,
                        "status_breakdown": status_counts,
                        "top_districts": district_counts,
                        "top_corridors": corridor_counts,
                        "last_updated": update_info['last_updated'].isoformat() if update_info['last_updated'] else None,
                        "first_created": update_info['first_created'].isoformat() if update_info['first_created'] else None
                    }
                })
        
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error getting proven vacancy stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting proven vacancy stats: {str(e)}")


