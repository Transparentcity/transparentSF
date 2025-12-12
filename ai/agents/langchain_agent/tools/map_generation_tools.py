"""
Map Generation Tools for LangChain Agent
========================================

This module provides tools for the LangChain explainer agent to generate maps
using the existing TransparentSF map generation system.
"""

import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
import json
import pandas as pd

# Add the parent directory to sys.path for absolute imports
current_dir = Path(__file__).parent
ai_dir = current_dir.parent.parent
sys.path.insert(0, str(ai_dir))

logger = logging.getLogger(__name__)

def _calculate_delta_data(current_df: pd.DataFrame, previous_df: pd.DataFrame, 
                         group_field: str, value_field: str = 'value') -> List[Dict[str, Any]]:
    """
    Calculate delta data for delta maps by comparing current and previous period data.
    
    Args:
        current_df: DataFrame with current period data
        previous_df: DataFrame with previous period data  
        group_field: Field to group by (e.g., 'supervisor_district', 'analysis_neighborhood')
        value_field: Field containing the values to compare
        
    Returns:
        List of dictionaries with enhanced delta data structure
    """
    logger.info(f"Calculating delta data for {group_field} using {value_field}")
    
    try:
        # Ensure value field is numeric
        current_df[value_field] = pd.to_numeric(current_df[value_field], errors='coerce')
        previous_df[value_field] = pd.to_numeric(previous_df[value_field], errors='coerce')
        
        # Group and aggregate data
        current_grouped = current_df.groupby(group_field)[value_field].sum().reset_index()
        previous_grouped = previous_df.groupby(group_field)[value_field].sum().reset_index()
        
        # Merge the data to calculate percent change
        merged_data = pd.merge(
            current_grouped, 
            previous_grouped,
            on=group_field, 
            suffixes=('_current', '_previous')
        )
        
        # Output key must match what the map viewer expects:
        # - supervisor_district / police_district: "district"
        # - analysis_neighborhood: "neighborhood"
        output_key = "neighborhood" if group_field == "analysis_neighborhood" else "district"

        delta_data: List[Dict[str, Any]] = []
        for _, row in merged_data.iterrows():
            if pd.isna(row[group_field]):
                continue
                
            # Normalize the key value based on map type.
            # For districts, force an integer-like string ("1".."11") when possible.
            # For neighborhoods, preserve the neighborhood name as-is.
            raw_key_value = row[group_field]
            if output_key == "district":
                if pd.api.types.is_numeric_dtype(type(raw_key_value)) or (
                    isinstance(raw_key_value, str)
                    and raw_key_value.replace(".", "", 1).isdigit()
                ):
                    try:
                        key_value = str(int(float(raw_key_value)))
                    except Exception:
                        key_value = str(raw_key_value)
                else:
                    key_value = str(raw_key_value)
            else:
                key_value = str(raw_key_value)
            
            current_value = row[f"{value_field}_current"]
            previous_value = row[f"{value_field}_previous"]
            
            # Calculate absolute delta and percent change
            delta = current_value - previous_value
            if previous_value != 0:
                percent_change = ((current_value - previous_value) / previous_value)
            else:
                percent_change = 0 if current_value == 0 else 1.0
            
            # Create enhanced data structure for delta maps
            delta_data.append({
                output_key: key_value,
                "current_value": current_value,
                "previous_value": previous_value,
                "delta": delta,
                "percent_change": percent_change,
                "value": percent_change  # For backward compatibility and coloring
            })
            
        logger.info(f"Calculated delta data for {len(delta_data)} districts")
        return delta_data
        
    except Exception as e:
        logger.error(f"Error calculating delta data: {e}")
        return []

def _create_previous_period_query(query: str, period_type: str = "month") -> str:
    """
    Create a query for the previous period by modifying date conditions.
    
    Args:
        query: Original query for current period
        period_type: Type of period comparison ("month", "year", "quarter", "ytd", "custom")
        
    Returns:
        Modified query for previous period
    """
    import re
    from datetime import datetime, timedelta
    
    # Handle YTD (Year-to-Date) comparisons - subtract 1 year from hardcoded dates
    if period_type == "ytd":
        # Pattern to match YYYY-MM-DD dates and subtract 1 year (both single and double quotes)
        def subtract_year_single(match):
            year = int(match.group(1))
            month = match.group(2)
            day = match.group(3)
            return f"'{year-1}-{month}-{day}'"
        
        def subtract_year_double(match):
            year = int(match.group(1))
            month = match.group(2)
            day = match.group(3)
            return f'"{year-1}-{month}-{day}"'
        
        # Replace all YYYY-MM-DD patterns with (YYYY-1)-MM-DD (both quote types)
        previous_query = re.sub(r"'(\d{4})-(\d{2})-(\d{2})'", subtract_year_single, query)
        previous_query = re.sub(r'"(\d{4})-(\d{2})-(\d{2})"', subtract_year_double, previous_query)
        logger.info(f"Created YTD previous period query: {previous_query}")
        return previous_query
    
    # Handle hardcoded date ranges for month/year/quarter comparisons
    if period_type in ["month", "year", "quarter"]:
        # Pattern to match hardcoded date ranges like "field >= '2025-09-01' AND field <= '2025-09-30'"
        def create_modified_range(match):
            start_year = int(match.group(2))
            start_month = int(match.group(3))
            start_day = int(match.group(4))
            field_name_end = match.group(5)  # Capture end field name
            end_year = int(match.group(6))
            end_month = int(match.group(7))
            end_day = int(match.group(8))
            
            # Create datetime objects
            start_date = datetime(start_year, start_month, start_day)
            end_date = datetime(end_year, end_month, end_day)
            
            # Calculate previous period based on period_type
            if period_type == "month":
                # Subtract 1 month
                if start_month == 1:
                    prev_start_date = datetime(start_year - 1, 12, start_day)
                else:
                    prev_start_date = datetime(start_year, start_month - 1, start_day)
                
                if end_month == 1:
                    prev_end_date = datetime(end_year - 1, 12, end_day)
                else:
                    prev_end_date = datetime(end_year, end_month - 1, end_day)
                    
            elif period_type == "year":
                # Subtract 1 year
                prev_start_date = datetime(start_year - 1, start_month, start_day)
                prev_end_date = datetime(end_year - 1, end_month, end_day)
                
            elif period_type == "quarter":
                # Subtract 3 months
                prev_start_date = start_date - timedelta(days=90)  # Approximate
                prev_end_date = end_date - timedelta(days=90)      # Approximate
            
            # Format back to YYYY-MM-DD
            prev_start_str = prev_start_date.strftime("%Y-%m-%d")
            prev_end_str = prev_end_date.strftime("%Y-%m-%d")
            
            return f"{match.group(1)} >= '{prev_start_str}' AND {field_name_end} <= '{prev_end_str}'"
        
        pattern = r"(\w+)\s*>=\s*'(\d{4})-(\d{2})-(\d{2})'\s*AND\s*(\w+)\s*<=\s*'(\d{4})-(\d{2})-(\d{2})'"
        
        previous_query = re.sub(pattern, create_modified_range, query, flags=re.IGNORECASE)
        
        if previous_query != query:
            logger.info(f"Created {period_type} previous period query: {previous_query}")
            return previous_query
    
    # Handle custom period type - return original query (user handles manually)
    if period_type == "custom":
        logger.info("Custom period type - returning original query")
        return query
    
    # Define period intervals for CURRENT_DATE modifications
    period_intervals = {
        "month": "INTERVAL '1 month'",
        "year": "INTERVAL '1 year'", 
        "quarter": "INTERVAL '3 months'"
    }
    
    interval = period_intervals.get(period_type, "INTERVAL '1 month'")
    
    # Replace CURRENT_DATE with CURRENT_DATE - INTERVAL for previous period
    # Handle various date field patterns
    patterns = [
        (r'date_trunc_ym\((\w+)\)\s*=\s*date_trunc_ym\(CURRENT_DATE\)', 
         f'date_trunc_ym(\\1) = date_trunc_ym(CURRENT_DATE - {interval})'),
        (r'(\w+)\s*>=\s*CURRENT_DATE', 
         f'\\1 >= CURRENT_DATE - {interval}'),
        (r'(\w+)\s*<=\s*CURRENT_DATE', 
         f'\\1 <= CURRENT_DATE - {interval}'),
    ]
    
    previous_query = query
    for pattern, replacement in patterns:
        previous_query = re.sub(pattern, replacement, previous_query, flags=re.IGNORECASE)
    
    # Check if query was actually modified
    if previous_query == query:
        logger.error("⚠️  DELTA MAP ERROR: Query was NOT modified for previous period!")
        logger.error(f"Original query: {query}")
        logger.error("This will result in identical current/previous values (often a white map)")
        logger.error("Query must contain one of these patterns:")
        logger.error("  - date_trunc_ym(field) = date_trunc_ym(CURRENT_DATE)")
        logger.error("  - field >= CURRENT_DATE")
        logger.error("  - field >= 'YYYY-MM-DD' AND field <= 'YYYY-MM-DD'")
        raise ValueError(
            "Delta map requires a query with a recognizable date filter so the "
            "system can fetch the previous period automatically."
        )
    else:
        logger.info(f"✅ Successfully created previous period query: {previous_query}")
    
    return previous_query

def generate_map_tool(context_variables: Dict[str, Any], map_title: str, map_type: str, 
                      map_metadata: Optional[Dict[str, Any]] = None, 
                      series_field: Optional[str] = None, 
                      color_palette: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a map visualization for geographic data using the TransparentSF map generation system.
    
    Args:
        context_variables: Context variables for the current analysis
        map_title: Descriptive title for the map
        map_type: Type of map to create. Must be one of:
            * "supervisor_district" - Map showing data by San Francisco supervisor district (1-11)
            * "police_district" - Map showing data by San Francisco police district
            * "analysis_neighborhood" - Map showing data by San Francisco analysis neighborhood
            * "intersection" - Map showing points at specific street intersections
            * "point" - Map showing points at specific lat/long coordinates
            * "address" - Map showing points at specific addresses (will be geocoded automatically)
            * "symbol" - Scaled-symbol map for points. Use this when you want marker size to represent a value
        map_metadata: Dictionary with additional information about the map
            * For change/delta maps, use: {"map_type": "delta", "description": "Change from previous period"}
            * For basic density maps, use: {"description": "Current values by district"}
            * For point/address/intersection maps, you can specify view settings:
              {"description": "Description", "zoom_level": 12, "center_lat": 37.7749, "center_lon": -122.4194}
        series_field: Optional field name for grouping markers into different colored series (only for point/address/intersection maps)
        color_palette: Optional color palette for series maps. Options:
            * "categorical" - Different colors for each series (default)
            * "status" - Green, Amber, Red, Blue, Purple for status data
            * "priority" - Red, Orange, Yellow, Green, Blue for priority levels
            * "sequential" - Graduated colors for sequential data
            * Custom list of hex colors: ["#FF0000", "#00FF00", "#0000FF"]
    
    Returns:
        Dictionary with map_id and URLs for editing and viewing the map
        
    Example:
        generate_map_tool(
            context_variables={},
            map_title="Crime Incidents by District",
            map_type="supervisor_district",
            map_metadata={"description": "Monthly crime incidents by supervisor district"}
        )
    """
    logger.info("=== Starting generate_map_tool ===")
    logger.info(f"Map title: {map_title}")
    logger.info(f"Map type: {map_type}")
    logger.info(f"Map metadata: {map_metadata}")
    logger.info(f"Series field: {series_field}")
    logger.info(f"Color palette: {color_palette}")
    
    try:
        # Import the map generation function from the main tools directory
        from ai.tools.generate_map import generate_map
        
        # Call the existing generate_map function
        result = generate_map(
            context_variables=context_variables,
            map_title=map_title,
            map_type=map_type,
            map_metadata=map_metadata or {},
            series_field=series_field,
            color_palette=color_palette
        )
        
        if result and "map_id" in result:
            logger.info(f"Map generated successfully with ID: {result['map_id']}")
            return {
                'status': 'success',
                'map_id': result['map_id'],
                'edit_url': result.get('edit_url'),
                'publish_url': result.get('publish_url'),
                'message': f'Map "{map_title}" created successfully'
            }
        else:
            logger.error("Map generation failed - no map_id returned")
            return {
                'status': 'error',
                'error': 'Map generation failed - no map_id returned',
                'result': result
            }
            
    except Exception as e:
        logger.exception(f"Error in generate_map_tool: {str(e)}")
        return {
            'status': 'error',
            'error': f'Failed to generate map: {str(e)}'
        }

def get_map_by_id_tool(map_id: int) -> Dict[str, Any]:
    """
    Retrieve a previously created map by ID.
    
    Args:
        map_id: The ID of the map to retrieve
        
    Returns:
        Dictionary with map details including URLs and metadata
    """
    logger.info("=== Starting get_map_by_id_tool ===")
    logger.info(f"Map ID: {map_id}")
    
    try:
        # Import the function from the main tools directory
        from ai.tools.generate_map import get_map_by_id
        
        result = get_map_by_id({}, map_id)
        
        if result and "map_id" in result:
            logger.info(f"Map {map_id} retrieved successfully")
            return {
                'status': 'success',
                'map': result
            }
        else:
            logger.warning(f"Map {map_id} not found or invalid")
            return {
                'status': 'error',
                'error': f'Map {map_id} not found or invalid'
            }
            
    except Exception as e:
        logger.exception(f"Error in get_map_by_id_tool: {str(e)}")
        return {
            'status': 'error',
            'error': f'Failed to retrieve map: {str(e)}'
        }

def get_recent_maps_tool(limit: int = 10, map_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Get a list of recently created maps.
    
    Args:
        limit: Maximum number of maps to return (default: 10)
        map_type: Optional filter by map type (e.g., "supervisor_district", "point")
        
    Returns:
        Dictionary with list of recent maps
    """
    logger.info("=== Starting get_recent_maps_tool ===")
    logger.info(f"Limit: {limit}")
    logger.info(f"Map type filter: {map_type}")
    
    try:
        # Import the function from the main tools directory
        from ai.tools.generate_map import get_recent_maps
        
        result = get_recent_maps({}, limit, map_type)
        
        if result and "maps" in result:
            logger.info(f"Retrieved {len(result['maps'])} recent maps")
            return {
                'status': 'success',
                'maps': result['maps'],
                'total_count': len(result['maps'])
            }
        else:
            logger.warning("No recent maps found")
            return {
                'status': 'success',
                'maps': [],
                'total_count': 0
            }
            
    except Exception as e:
        logger.exception(f"Error in get_recent_maps_tool: {str(e)}")
        return {
            'status': 'error',
            'error': f'Failed to retrieve recent maps: {str(e)}'
        }

def generate_map_with_query_tool(endpoint: str, query: str, map_title: str, map_type: str, 
                                 map_metadata: Optional[Dict[str, Any]] = None, 
                                 series_field: Optional[str] = None, 
                                 color_palette: Optional[str] = None,
                                 metric_id: Optional[str] = None,
                                 period_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate a map by querying DataSF and creating a map visualization in one step.
    
    This tool combines data fetching and map generation to work around LangChain tool
    limitations where tools cannot share state through context_variables.
    
    **DELTA MAP SUPPORT**: When map_metadata contains {"map_type": "delta"}, the tool automatically:
    1. Fetches current period data using your query
    2. Fetches previous period data by modifying date conditions
    3. Calculates deltas and percent changes in Python
    4. Creates enhanced data structure with current_value, previous_value, delta, percent_change
    5. Applies proper green/red coloring based on the metric's greendirection
    
    Args:
        endpoint: The dataset identifier WITHOUT the .json extension (e.g., 'ubvf-ztfx')
        query: The complete SoQL query string using standard SQL syntax
            **For delta maps**: Use simple queries for current period data only
            **Example**: "SELECT supervisor_district, COUNT(*) as value WHERE date_trunc_ym(report_datetime) = date_trunc_ym(CURRENT_DATE) GROUP BY supervisor_district"
        map_title: Descriptive title for the map
        map_type: Type of map to create. Must be one of:
            * "supervisor_district" - Map showing data by San Francisco supervisor district (1-11)
            * "police_district" - Map showing data by San Francisco police district
            * "analysis_neighborhood" - Map showing data by San Francisco analysis neighborhood
            * "intersection" - Map showing points at specific street intersections
            * "point" - Map showing points at specific lat/long coordinates
            * "address" - Map showing points at specific addresses (will be geocoded automatically)
            * "symbol" - Scaled-symbol map for points. Use this when you want marker size to represent a value
        map_metadata: Dictionary with additional information about the map
            * **For change/delta maps**: {"map_type": "delta", "description": "Change from previous period"}
            * **For basic density maps**: {"description": "Current values by district"}
            * **For point/address/intersection maps**: {"description": "Description", "zoom_level": 12, "center_lat": 37.7749, "center_lon": -122.4194}
        series_field: Optional field name for grouping markers into different colored series (only for point/address/intersection maps)
        color_palette: Optional color palette for series maps. Options:
            * "categorical" - Different colors for each series (default)
            * "status" - Green, Amber, Red, Blue, Purple for status data
            * "priority" - Red, Orange, Yellow, Green, Blue for priority levels
            * "sequential" - Graduated colors for sequential data
            * Custom list of hex colors: ["#FF0000", "#00FF00", "#0000FF"]
        metric_id: Optional metric ID to associate this map with (for proper categorization in metric_control.html)
        period_type: Optional period type for delta maps. Options:
            * "month" - Compare current month vs previous month (default)
            * "year" - Compare current year vs previous year  
            * "quarter" - Compare current quarter vs previous quarter
            * "ytd" - Compare year-to-date vs same period previous year
            * "custom" - Use custom date logic (requires manual query modification)
    
    Returns:
        Dictionary with map_id and URLs for editing and viewing the map
        
    Examples:
        # Regular density map
        generate_map_with_query_tool(
            endpoint="wg3w-h783",
            query="SELECT supervisor_district, COUNT(*) as value WHERE date_trunc_ym(report_datetime) = date_trunc_ym(CURRENT_DATE) GROUP BY supervisor_district",
            map_title="Crime Incidents by District",
            map_type="supervisor_district",
            map_metadata={"description": "Monthly crime incidents by supervisor district"},
            metric_id="23"
        )
        
        # Delta map (shows green/red changes)
        generate_map_with_query_tool(
            endpoint="wg3w-h783",
            query="SELECT supervisor_district, COUNT(*) as value WHERE date_trunc_ym(report_datetime) = date_trunc_ym(CURRENT_DATE) GROUP BY supervisor_district",
            map_title="Crime Incidents Change by District",
            map_type="supervisor_district",
            map_metadata={"map_type": "delta", "description": "Month-over-month change in crime incidents"},
            metric_id="23",
            period_type="month"
        )
        
        # YTD delta map (year-over-year comparison)
        generate_map_with_query_tool(
            endpoint="wg3w-h783",
            query="SELECT supervisor_district, COUNT(*) as value WHERE incident_datetime >= '2025-01-01' AND incident_datetime <= '2025-09-30' GROUP BY supervisor_district",
            map_title="Property Crime YTD Change (2025 vs 2024)",
            map_type="supervisor_district",
            map_metadata={"map_type": "delta", "description": "Year-over-year change in property crime"},
            metric_id="3",
            period_type="ytd"
        )
    """
    logger.info("=== Starting generate_map_with_query_tool ===")
    logger.info(f"Endpoint: {endpoint}")
    logger.info(f"Query: {query}")
    logger.info(f"Map title: {map_title}")
    logger.info(f"Map type: {map_type}")
    
    try:
        # Step 1: Validate parameters
        if not endpoint:
            return {
                'status': 'error',
                'error': 'Endpoint is required'
            }
        if not query:
            return {
                'status': 'error',
                'error': 'Query is required'
            }
        if not map_title:
            return {
                'status': 'error',
                'error': 'Map title is required'
            }
        if not map_type:
            return {
                'status': 'error',
                'error': 'Map type is required'
            }
            
        # Step 2: Check if this is a delta map
        is_delta_map = map_metadata and map_metadata.get("map_type") == "delta"
        logger.info(f"Is delta map: {is_delta_map}")
        
        # Step 3: Fetch data from DataSF
        from ai.tools.data_fetcher import fetch_data_from_api
        import pandas as pd
        
        # Clean up endpoint - ensure it ends with .json
        if not endpoint.endswith('.json'):
            endpoint = f"{endpoint}.json"
            logger.info(f"Added .json to endpoint: {endpoint}")
        
        # CONTEXT WINDOW PROTECTION: Add automatic LIMIT if not present for map generation
        query_lower = query.lower()
        if 'limit' not in query_lower:
            # Maps can handle more data than text analysis, but still need limits
            MAP_DEFAULT_LIMIT = 10000
            query = f"{query} LIMIT {MAP_DEFAULT_LIMIT}"
            logger.info(f"Added automatic LIMIT {MAP_DEFAULT_LIMIT} for map generation")
        else:
            # Check if existing limit is too high for context
            import re
            limit_match = re.search(r'limit\s+(\d+)', query_lower)
            if limit_match:
                existing_limit = int(limit_match.group(1))
                MAP_MAX_SAFE_LIMIT = 10000  # Higher limit for maps
                if existing_limit > MAP_MAX_SAFE_LIMIT:
                    query = re.sub(r'limit\s+\d+', f'LIMIT {MAP_MAX_SAFE_LIMIT}', query, flags=re.IGNORECASE)
                    logger.warning(f"Reduced LIMIT from {existing_limit} to {MAP_MAX_SAFE_LIMIT} for map generation")
        
        # Fetch current period data
        query_object = {'endpoint': endpoint, 'query': query}
        result = fetch_data_from_api(query_object)
        logger.info(f"API result status: {'success' if result and 'data' in result else 'error'}")
        
        if not result or 'data' not in result:
            error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
            return {
                'status': 'error',
                'error': f'Failed to fetch data: {error_msg}',
                'queryURL': result.get('queryURL') if result else None
            }
            
        data = result['data']
        if not data:
            return {
                'status': 'error',
                'error': 'No data returned from the API',
                'queryURL': result.get('queryURL')
            }
            
        # Convert to DataFrame
        df = pd.DataFrame(data)
        logger.info(f"Dataset loaded with shape: {df.shape}")
        
        # Step 4: Handle delta maps - fetch previous period data and calculate deltas
        if is_delta_map:
            logger.info("Processing delta map - fetching previous period data")
            
            # Determine the group field based on map type
            group_field = "analysis_neighborhood" if map_type == "analysis_neighborhood" else "supervisor_district"
            
            # Create previous period query using explicit period_type
            period_type_to_use = period_type or "month"  # Default to month if not specified
            try:
                previous_query = _create_previous_period_query(query, period_type_to_use)
            except ValueError as e:
                return {
                    "status": "error",
                    "error": str(e),
                    "queryURL": result.get("queryURL"),
                }
            
            # Fetch previous period data
            previous_query_object = {'endpoint': endpoint, 'query': previous_query}
            previous_result = fetch_data_from_api(previous_query_object)
            
            if previous_result and 'data' in previous_result and previous_result['data']:
                previous_df = pd.DataFrame(previous_result['data'])
                logger.info(f"Previous period dataset loaded with shape: {previous_df.shape}")
                
                # Calculate delta data
                delta_data = _calculate_delta_data(df, previous_df, group_field, 'value')
                
                if delta_data:
                    logger.info(f"Successfully calculated delta data for {len(delta_data)} districts")
                    
                    # Create context_variables with delta data
                    context_variables = {
                        'dataset': pd.DataFrame(delta_data),  # Convert delta_data to DataFrame
                        'queryURL': result.get('queryURL'),
                        'delta_data': delta_data  # Also store as list for map generation
                    }
                    
                    # For delta maps, we need to pass the delta_data directly as location_data
                    # to preserve the proper structure for Datawrapper conversion
                    location_data_to_use = delta_data
                    
                    # Add greendirection to map_metadata if we have a metric_id
                    if metric_id:
                        try:
                            from ai.tools.generate_map import get_db_connection
                            import psycopg2.extras
                            
                            conn = get_db_connection()
                            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                            cursor.execute('SELECT greendirection FROM metrics WHERE id = %s', (metric_id,))
                            metric = cursor.fetchone()
                            if metric and metric['greendirection']:
                                map_metadata['greendirection'] = metric['greendirection']
                                logger.info(f"Added greendirection '{metric['greendirection']}' to map metadata")
                            cursor.close()
                            conn.close()
                        except Exception as e:
                            logger.warning(f"Could not fetch greendirection for metric {metric_id}: {e}")
                else:
                    logger.warning("No delta data calculated - falling back to current period data")
                    context_variables = {
                        'dataset': df,
                        'queryURL': result.get('queryURL')
                    }
                    location_data_to_use = "from_context"
            else:
                logger.warning("Failed to fetch previous period data - falling back to current period data")
                context_variables = {
                    'dataset': df,
                    'queryURL': result.get('queryURL')
                }
                location_data_to_use = "from_context"
        else:
            # Step 3: Create context_variables with the dataset (regular map)
            context_variables = {
                'dataset': df,
                'queryURL': result.get('queryURL')
            }
            location_data_to_use = "from_context"
        
        # Step 5: Generate the map using mapbox (only mapbox supported)
        from ai.tools.generate_map import generate_mapbox_map
        
        map_result = generate_mapbox_map(
            context_variables=context_variables,
            map_title=map_title,
            map_type=map_type,
            location_data=location_data_to_use,  # Use delta_data directly for delta maps, or "from_context" for regular maps
            map_metadata=map_metadata or {},
            metric_id=metric_id,
            series_field=series_field,
            color_palette=color_palette,
            series_info=None,
            preview_mode=False
        )
        
        if map_result and "map_id" in map_result:
            logger.info(f"Map generated successfully with ID: {map_result['map_id']}")
            return {
                'status': 'success',
                'map_id': map_result['map_id'],
                'edit_url': map_result.get('edit_url'),
                'publish_url': map_result.get('publish_url'),
                'view_url': map_result.get('view_url'),
                'message': f'Map "{map_title}" created successfully with data from {endpoint}',
                'queryURL': result.get('queryURL'),
                'data_shape': df.shape,
                'data_points': map_result.get('data_points', 0)
            }
        elif map_result and "error" in map_result:
            logger.error(f"Map generation failed: {map_result['error']}")
            return {
                'status': 'error',
                'error': f'Map generation failed: {map_result["error"]}',
                'queryURL': result.get('queryURL')
            }
        else:
            logger.error("Map generation failed - unexpected result format")
            return {
                'status': 'error',
                'error': 'Map generation failed - unexpected result format',
                'result': map_result
            }
            
    except Exception as e:
        logger.exception(f"Error in generate_map_with_query_tool: {str(e)}")
        return {
            'status': 'error',
            'error': f'Failed to generate map: {str(e)}'
        }
