"""
Dual Map Routes for TransparentSF
=================================

This module provides routes for displaying two maps with different colored layers
on the same map view, allowing for comparison between different datasets.
"""

import os
import logging
import json
import psycopg2.extras
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

# Import centralized database utilities
from tools.db_utils import get_postgres_connection, execute_with_connection

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None

def set_templates(template_instance: Jinja2Templates):
    """Set templates instance from main app."""
    global templates
    templates = template_instance

@router.get("/dual-map-selector")
async def dual_map_selector(request: Request):
    """Serve the dual map selector page where users can choose two maps to compare."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    try:
        # Get available maps from database
        conn = get_postgres_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get recent maps
        cursor.execute("""
            SELECT id, title, type, created_at, metric_id
            FROM maps 
            WHERE active = TRUE
            ORDER BY created_at DESC 
            LIMIT 50
        """)
        maps = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Convert to list of dictionaries
        maps_list = []
        for map_record in maps:
            map_data = dict(map_record)
            # Format the created_at date
            if map_data['created_at']:
                map_data['created_at'] = map_data['created_at'].strftime('%Y-%m-%d %H:%M')
            maps_list.append(map_data)
        
        return templates.TemplateResponse("dual_map_selector.html", {
            "request": request,
            "maps": maps_list
        })
        
    except Exception as e:
        logger.error(f"Error loading dual map selector: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error loading map selector: {str(e)}")

@router.get("/dual-map")
async def dual_map_page(
    request: Request, 
    map1_id: str = Query(..., description="First map ID"),
    map2_id: str = Query(..., description="Second map ID")
):
    """Serve the dual map comparison page."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    try:
        # Get both maps from database
        conn = get_postgres_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get first map
        cursor.execute("SELECT * FROM maps WHERE id = %s", [map1_id])
        map1_data = cursor.fetchone()
        
        # Get second map
        cursor.execute("SELECT * FROM maps WHERE id = %s", [map2_id])
        map2_data = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not map1_data:
            raise HTTPException(status_code=404, detail=f"Map 1 (ID: {map1_id}) not found")
        
        if not map2_data:
            raise HTTPException(status_code=404, detail=f"Map 2 (ID: {map2_id}) not found")
        
        # Parse metadata for both maps
        def parse_map_data(map_data):
            # Parse metadata if it's a string
            if isinstance(map_data['metadata'], str):
                try:
                    metadata = json.loads(map_data['metadata'])
                except:
                    metadata = {}
            else:
                metadata = map_data['metadata'] or {}
            
            # Parse location_data if it's a string
            if isinstance(map_data['location_data'], str):
                try:
                    location_data = json.loads(map_data['location_data'])
                except:
                    location_data = []
            else:
                location_data = map_data['location_data'] or []
            
            # Handle different data formats
            if isinstance(location_data, dict) and 'data' in location_data:
                # Old format: {'data': [...]}
                location_data = location_data['data']
            elif not isinstance(location_data, list):
                # Ensure location_data is always a list
                location_data = []
            
            return {
                'id': map_data['id'],
                'title': map_data['title'],
                'type': map_data['type'],
                'metadata': metadata,
                'location_data': location_data,
                'created_at': map_data['created_at'],
                'updated_at': map_data['updated_at']
            }
        
        map1_parsed = parse_map_data(map1_data)
        map2_parsed = parse_map_data(map2_data)
        
        # Convert datetime objects to strings for JSON serialization
        def serialize_dates(data):
            if isinstance(data, dict):
                result = {}
                for key, value in data.items():
                    if hasattr(value, 'isoformat'):  # datetime object
                        result[key] = value.isoformat()
                    elif isinstance(value, (dict, list)):
                        result[key] = serialize_dates(value)
                    else:
                        result[key] = value
                return result
            elif isinstance(data, list):
                return [serialize_dates(item) for item in data]
            else:
                return data
        
        map1_serialized = serialize_dates(map1_parsed)
        map2_serialized = serialize_dates(map2_parsed)
        
        # Get Mapbox token
        mapbox_token = os.getenv("MAPBOX_ACCESS_TOKEN", "")
        
        return templates.TemplateResponse("dual_map.html", {
            "request": request,
            "map1": map1_serialized,
            "map2": map2_serialized,
            "config": {
                "MAPBOX_ACCESS_TOKEN": mapbox_token
            }
        })
        
    except Exception as e:
        logger.error(f"Error loading dual map comparison for maps {map1_id} and {map2_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error loading maps: {str(e)}")

@router.get("/api/dual-map-data")
async def get_dual_map_data(
    map1_id: str = Query(..., description="First map ID"),
    map2_id: str = Query(..., description="Second map ID")
):
    """Get data for both maps in a format suitable for dual map rendering."""
    try:
        # Get both maps from database
        conn = get_postgres_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get first map
        cursor.execute("SELECT * FROM maps WHERE id = %s", [map1_id])
        map1_data = cursor.fetchone()
        
        # Get second map
        cursor.execute("SELECT * FROM maps WHERE id = %s", [map2_id])
        map2_data = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not map1_data or not map2_data:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "One or both maps not found"}
            )
        
        def process_map_data(map_data, layer_name, default_color):
            """Process map data and add layer-specific properties."""
            # Parse location_data
            if isinstance(map_data['location_data'], str):
                try:
                    location_data = json.loads(map_data['location_data'])
                except:
                    location_data = []
            else:
                location_data = map_data['location_data'] or []
            
            # Handle different data formats
            if isinstance(location_data, dict) and 'data' in location_data:
                location_data = location_data['data']
            elif not isinstance(location_data, list):
                location_data = []
            
            # Add layer-specific properties to each point
            processed_data = []
            for item in location_data:
                processed_item = item.copy()
                processed_item['layer'] = layer_name
                processed_item['layer_color'] = default_color
                # Override color if not set or use layer default
                if not processed_item.get('color'):
                    processed_item['color'] = default_color
                processed_data.append(processed_item)
            
            return {
                'id': map_data['id'],
                'title': map_data['title'],
                'type': map_data['type'],
                'location_data': processed_data,
                'layer_name': layer_name
            }
        
        # Process both maps with different default colors using brand palette
        map1_processed = process_map_data(map1_data, 'layer1', '#ad35fa')  # Brand bright purple
        map2_processed = process_map_data(map2_data, 'layer2', '#FF6B5A')  # Brand warm coral
        
        return JSONResponse(content={
            "status": "success",
            "map1": map1_processed,
            "map2": map2_processed
        })
        
    except Exception as e:
        logger.error(f"Error getting dual map data: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Error retrieving map data: {str(e)}"}
        )
