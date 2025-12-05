import os
import requests
import json
import logging
from dotenv import load_dotenv
from pathlib import Path
import psycopg2
import psycopg2.extras

# Configure logging
logger = logging.getLogger(__name__)
# Remove explicit logging level setting - will use what's in .env
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Load environment variables
# Determine the project root based on the script's location
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent.parent  # Moves up two levels: tools -> ai -> project_root
ai_dir = script_dir.parent  # This should be the 'ai' directory

possible_env_paths = [
    ai_dir / '.env',          # Check ai/.env first
    project_root / '.env',    # Then check project_root/.env (original logic)
    Path.home() / '.env'      # Finally, check home directory
]

loaded_env = False
for env_path in possible_env_paths:
    if env_path.exists():
        logger.info(f"Loading environment variables from: {env_path}")
        load_dotenv(dotenv_path=env_path)
        loaded_env = True
        break

if not loaded_env:
    logger.warning("No .env file found in project root or home directory. Relying on environment variables being set.")

DATAWRAPPER_API_KEY = os.getenv("DATAWRAPPER_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")  # Default if not set
DW_API_BASE_URL = "https://api.datawrapper.de/v3"

if not DATAWRAPPER_API_KEY:
    logger.error("DATAWRAPPER_API_KEY not found in environment variables. Script cannot function.")

def _make_dw_request(method, endpoint, headers=None, data=None, json_payload=None):
    """Helper function to make requests to Datawrapper API."""
    if not DATAWRAPPER_API_KEY:
        logger.error("Datawrapper API key is not configured.")
        return None

    url = f"{DW_API_BASE_URL}{endpoint}"
    
    default_headers = {
        "Authorization": f"Bearer {DATAWRAPPER_API_KEY}"
    }
    if headers:
        default_headers.update(headers)

    try:
        response = requests.request(method, url, headers=default_headers, data=data, json=json_payload)
        response.raise_for_status()
        
        if response.content:
            try:
                return response.json()
            except json.JSONDecodeError:
                logger.info(f"Response from {method} {url} was not JSON, returning raw content.")
                return response.text
        return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
    return None

def get_db_connection():
    """Get a connection to the PostgreSQL database using centralized db_utils."""
    from ai.tools.db_utils import get_postgres_connection
    return get_postgres_connection()

def get_district_shapes(district_type="supervisor", district_ids=None):
    """
    Fetch district shape data from the city's API endpoint.
    
    Args:
        district_type: Type of district ("supervisor" or "police")
        district_ids: List of district IDs to fetch (optional, will fetch all if None)
        
    Returns:
        Dictionary mapping district IDs to GeoJSON polygon data
    """
    logger.info(f"Fetching {district_type} district shapes")
    
    try:
        # Set up endpoint ID and field name based on district type
        if district_type.lower() == "supervisor":
            endpoint_id = "f2zs-jevy"  # Supervisor districts endpoint (2022)
            id_field = "sup_dist"
        elif district_type.lower() == "police":
            endpoint_id = "wkhw-cjsf"  # Police districts endpoint
            id_field = "district"
        else:
            logger.error(f"Unsupported district type: {district_type}")
            return None
        
        # Import the set_dataset function
        from tools.data_fetcher import set_dataset
        
        # Build the SoQL query - polygon is the column that contains the geometry data
        query = "SELECT *"  # Don't explicitly include 'polygon' as it's already part of *
        if district_ids:
            # Format list of district IDs for query
            district_filter = " OR ".join([f"{id_field}='{dist_id}'" for dist_id in district_ids])
            query += f" WHERE {district_filter}"
        
        # Create context variables dictionary
        context_variables = {}
        
        # Use set_dataset to fetch the data
        result = set_dataset(context_variables, endpoint=endpoint_id, query=query)
        
        if isinstance(result, dict) and "error" in result:
            logger.error(f"Error fetching district shapes: {result['error']}")
            return None
        
        # Check if we got data
        dataset = context_variables.get("dataset")
        if dataset is None or dataset.empty:
            logger.error("No district data retrieved")
            return None
        
        # Process the data into a dictionary mapping district IDs to GeoJSON
        district_shapes = {}
        for _, row in dataset.iterrows():
            district_id = str(row.get(id_field))
            if not district_id:
                logger.warning(f"Missing district ID in data row")
                continue
                
            # Extract the geometry data - check polygon column
            if "polygon" in row and row["polygon"]:
                geometry_data = row["polygon"]
                # Parse the geometry data if it's a string
                geometry = json.loads(geometry_data) if isinstance(geometry_data, str) else geometry_data
                district_shapes[district_id] = geometry
            else:
                logger.warning(f"No polygon data found for district {district_id}")
        
        return district_shapes
    
    except Exception as e:
        logger.error(f"Error fetching district shapes: {str(e)}")
        return None

def create_datawrapper_map(map_id):
    """
    Creates a Datawrapper map for the specified map ID using copy-from-template approach.
    
    Args:
        map_id: The ID of the map to generate
        
    Returns:
        The public URL of the existing or newly created Datawrapper map, or None if failed
    """
    import uuid
    
    logger.info(f"Creating Datawrapper map for map_id: {map_id}")
    
    if not DATAWRAPPER_API_KEY:
        logger.error("Cannot create map: DATAWRAPPER_API_KEY is not set.")
        return None
    
    # Get default template chart IDs
    DEFAULT_DISTRICT_CHART = os.getenv("DATAWRAPPER_REFERENCE_CHART", "j5vON")
    DEFAULT_SYMBOL_CHART = "K8LoR"
    
    try:
        # Fetch map data from database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Query the map
        cursor.execute("SELECT * FROM maps WHERE id = %s", (map_id,))
        map_record = cursor.fetchone()
        
        if not map_record:
            logger.error(f"Map with ID {map_id} not found in database")
            cursor.close()
            conn.close()
            return None
        
        # Convert to dictionary and process JSON fields
        map_data = dict(map_record)
        
        map_data["location_data"] = json.loads(map_data["location_data"]) if isinstance(map_data["location_data"], str) else map_data["location_data"]
        map_data["metadata"] = json.loads(map_data["metadata"]) if map_data["metadata"] and isinstance(map_data["metadata"], str) else map_data["metadata"] or {}
        
        # Check if map already has a DataWrapper URL in metadata
        existing_dw_url = map_data["metadata"].get("dw_url")
        if existing_dw_url:
            logger.info(f"Map {map_id} already has DataWrapper URL in metadata: {existing_dw_url}")
            cursor.close()
            conn.close()
            return existing_dw_url
        
        # Check if published_url contains a datawrapper URL (legacy format)
        # If so, we'll create a new one and store it in metadata properly
        if map_data.get("published_url") and "datawrapper" in map_data["published_url"]:
            logger.info(f"Map {map_id} has legacy DataWrapper URL in published_url: {map_data['published_url']}")
            logger.info(f"Will create new DataWrapper version and store in metadata")
        
        # Proceed with creating a new Datawrapper map
        logger.info(f"Creating new Datawrapper chart for map {map_id} using copy-from-template")
        
        map_title = map_data["title"]
        map_type = map_data["type"]
        location_data = map_data["location_data"]
        metadata = map_data["metadata"]
        
        logger.info(f"Processing map: {map_title}, type: {map_type}")
        
        # Check if this is a delta map (change map)
        is_delta_map = metadata.get("map_type") == "delta"
        logger.info(f"Map is delta map: {is_delta_map}")
        
        # Convert location_data to CSV format if needed
        csv_data = None
        if isinstance(location_data, dict) and location_data.get("type") == "csv":
            csv_data = location_data.get("csv_data", "").strip()
            if not csv_data:
                logger.error("location_data csv_data is empty – cannot create map")
                cursor.close()
                conn.close()
                return None
        elif isinstance(location_data, list):
            # Convert list to CSV format
            logger.info(f"Converting list location_data to CSV format for {map_type} map")
            
            # Determine the key field based on map type
            key_field = "neighborhood" if map_type == "analysis_neighborhood" else "district"
            
            # Check if this has change data
            sample_item = location_data[0] if location_data else {}
            has_change_data = any(key in sample_item for key in ['current_value', 'previous_value', 'delta', 'percent_change'])
            
            if has_change_data and is_delta_map:
                # Delta map CSV format
                csv_data = f"{key_field},value,current_value,previous_value,delta,percent_change\n"
                for item in location_data:
                    key_value = item.get(key_field, '')
                    current = item.get('current_value', item.get('value', 0))
                    previous = item.get('previous_value', 0)
                    delta = item.get('delta', current - previous)
                    percent_change = item.get('percent_change', 0)
                    
                    # For delta maps, the value column should be percent_change * 100
                    value_for_coloring = max(min(round(percent_change * 100), 100), -100)
                    
                    # Apply greendirection logic
                    greendirection = metadata.get("greendirection", "up")
                    if greendirection == "down":
                        value_for_coloring = -value_for_coloring
                    
                    csv_data += f"{key_value},{value_for_coloring},{current},{previous},{delta},{percent_change}\n"
            else:
                # Regular density map CSV format
                csv_data = f"{key_field},value\n"
                for item in location_data:
                    if isinstance(item, dict) and key_field in item and "value" in item:
                        csv_data += f"{item[key_field]},{item['value']}\n"
                    else:
                        logger.warning(f"Skipping invalid {key_field} data item: {item}")
            
            logger.info(f"Converted to CSV format: {csv_data[:200]}...")
        else:
            logger.error(f"Unsupported location_data format: {type(location_data)}")
            cursor.close()
            conn.close()
            return None
        
        # Determine which template to use
        if map_type == "symbol":
            ref_chart_id = DEFAULT_SYMBOL_CHART
        elif map_type in ["supervisor_district", "police_district", "analysis_neighborhood"]:
            ref_chart_id = DEFAULT_DISTRICT_CHART
        else:
            logger.error(f"Unsupported map type for copy-from-template: {map_type}")
            cursor.close()
            conn.close()
            return None
        
        logger.info(f"Using template chart ID: {ref_chart_id} for map type: {map_type}")
        
        # Step 1: Copy the reference chart
        logger.info(f"Cloning chart from reference ID: {ref_chart_id}")
        copy_response = _make_dw_request(
            "POST",
            f"/charts/{ref_chart_id}/copy"
        )
        if not copy_response or "id" not in copy_response:
            logger.error(f"Failed to copy reference chart {ref_chart_id}")
            cursor.close()
            conn.close()
            return None
        
        chart_id = copy_response["id"]
        logger.info(f"Successfully copied chart. New chart ID: {chart_id}")
        
        # Step 2: Update the title
        _make_dw_request(
            "PATCH",
            f"/charts/{chart_id}",
            json_payload={"title": map_title}
        )
        logger.info(f"Updated title for chart ID {chart_id} to: {map_title}")
        
        # Step 3: Upload CSV data
        _make_dw_request(
            "PUT",
            f"/charts/{chart_id}/data",
            headers={"Content-Type": "text/csv"},
            data=csv_data
        )
        logger.info(f"Uploaded CSV data to chart ID {chart_id}")
        
        # Step 4: Apply custom styling for district maps (using logic from generate_map.py)
        if map_type in ["supervisor_district", "police_district", "analysis_neighborhood"]:
            # Get metric info from metadata
            metric_info = metadata.get('metric_info')
            item_noun = metric_info.get('item_noun', 'Items') if metric_info else 'Items'
            greendirection = metric_info.get('greendirection', 'up') if metric_info else metadata.get('greendirection', 'up')
            
            # Base configuration for choropleth maps
            base_config = {
                "basemap": "custom_upload",
                "basemapFilename": "districts_geojson.json" if map_type in ["supervisor_district", "police_district"] else "analysis_neighborhoods_geojson.json",
                "basemapProjection": "geoAzimuthalEqualArea",
                "map-key-attr": "district" if map_type in ["supervisor_district", "police_district"] else "neighborhood",
                "map-type-set": True,
                "chart-type-set": True,
                "zoomable": True,
                "map-align": "center",
                "map-padding": 0,
                "hide-region-borders": True,
                "hide-empty-regions": False,
                "basemapRegions": "all",
                "max-map-height": 650,
                "min-label-zoom": 1,
                "zoom-button-pos": "br",
                "map-label-format": "0,0.[00]",
                "avoid-label-overlap": True,
                "mapViewCropPadding": 10,
                "basemapShowExtraOptions": False
            }
            
            styling_payload = {
                "metadata": {
                    "visualize": {},
                    "describe": {
                        "intro": metadata.get("description", ""),
                        "source-name": "DataSF",
                        "source-url": metadata.get("executed_url", ""),
                        "byline": "Chart: TransparentSF"
                    },
                    "publish": {
                        "autoDarkMode": True
                    }
                }
            }
            
            if is_delta_map:
                # Delta map styling with choropleth colors
                if greendirection == 'down':
                    colors = [
                        {"color": "#00dca6", "position": 0},      # Strong Green (for -100%)
                        {"color": "#a7e9d8", "position": 0.25},   # Light Green
                        {"color": "#eeeeee", "position": 0.5},    # Neutral Gray (for 0%)
                        {"color": "#f87171", "position": 0.75},   # Light Red
                        {"color": "#dc2626", "position": 1.0}     # Strong Red (for +100%)
                    ]
                else:
                    colors = [
                        {"color": "#dc2626", "position": 0},      # Strong Red (for -100%)
                        {"color": "#f87171", "position": 0.25},   # Light Red
                        {"color": "#eeeeee", "position": 0.5},    # Neutral Gray (for 0%)
                        {"color": "#a7e9d8", "position": 0.75},   # Light Teal/Green
                        {"color": "#00dca6", "position": 1.0}     # Strong Teal/Green (for +100%)
                    ]
                
                styling_payload["metadata"]["visualize"] = {
                    **base_config,
                    "colorscale": {
                        "mode": "continuous",
                        "stops": "equidistant",
                        "colors": colors,
                        "palette": 0,
                        "stopCount": 5,
                        "interpolation": "equidistant",
                        "min": -100,
                        "max": 100,
                        "domain": [-100, 100],
                        "rangeMin": -100,
                        "rangeMax": 100,
                        "rangeCenter": 0,
                        "customStops": []
                    },
                    "legends": {
                        "color": {
                            "size": 170,
                            "title": f"CHANGE IN {item_noun.upper()}",
                            "labels": "ranges",
                            "enabled": True,
                            "offsetX": 0,
                            "offsetY": 0,
                            "reverse": False,
                            "labelMax": "100%",
                            "labelMin": "-100%",
                            "position": "above",
                            "interactive": True,
                            "labelCenter": "medium",
                            "labelFormat": "0'%'",
                            "orientation": "horizontal",
                            "titleEnabled": False,
                            "customLabels": []
                        }
                    },
                    "tooltip": {
                        "body": f"Current: {{{{current_value}}}} {item_noun}<br>Previous: {{{{previous_value}}}} {item_noun}<br>Change: {{{{delta}}}} {item_noun}<br>% Change: {{{{value}}}}%",
                        "title": "District {{ district }}",
                        "sticky": True,
                        "enabled": True
                    }
                }
            else:
                # Regular density map styling
                styling_payload["metadata"]["visualize"] = {
                    **base_config,
                    "colorscale": {
                        "mode": "continuous",
                        "stops": "equidistant",
                        "colors": [
                            {"color": "#E9D8FA", "position": 0},
                            {"color": "#ad35fa", "position": 1.0}
                        ],
                        "palette": 0,
                        "stopCount": 2,
                        "interpolation": "linear"
                    },
                    "legends": {
                        "color": {
                            "size": 170,
                            "title": f"NUMBER OF {item_noun.upper()}",
                            "labels": "ranges",
                            "enabled": True,
                            "offsetX": 0,
                            "offsetY": 0,
                            "reverse": False,
                            "labelMax": "",
                            "labelMin": "",
                            "position": "above",
                            "interactive": True,
                            "labelCenter": "medium",
                            "labelFormat": "0,0.[00]",
                            "orientation": "horizontal",
                            "titleEnabled": False
                        }
                    },
                    "tooltip": {
                        "body": f"{{{{value}}}} {item_noun}",
                        "title": "District {{ district }}",
                        "sticky": True,
                        "enabled": True
                    }
                }
            
            # Apply the styling
            _make_dw_request(
                "PATCH",
                f"/charts/{chart_id}",
                json_payload=styling_payload
            )
            logger.info(f"Applied custom choropleth styling to chart {chart_id} (delta={is_delta_map})")
        
        # Step 5: Publish the chart
        logger.info(f"Publishing chart {chart_id}")
        _make_dw_request(
            "POST",
            f"/charts/{chart_id}/publish"
        )
        logger.info(f"Chart {chart_id} published successfully")
        
        edit_url = f"https://app.datawrapper.de/edit/{chart_id}"
        public_url = f"https://datawrapper.dwcdn.net/{chart_id}/"
        
        # Update database with Datawrapper chart URLs
        if chart_id:
            # Get the published URL
            get_url = f"https://api.datawrapper.de/v3/charts/{chart_id}"
            get_headers = {
                "Authorization": f"Bearer {DATAWRAPPER_API_KEY}"
            }
            
            get_response = requests.get(get_url, headers=get_headers)
            get_response.raise_for_status()
            chart_info = get_response.json()
            
            if "publicUrl" in chart_info:
                public_url = chart_info["publicUrl"]
                
                # Update the map record in the database
                # Store DataWrapper URL in metadata instead of overwriting published_url
                # Get current metadata
                cursor.execute("SELECT metadata FROM maps WHERE id = %s", (map_id,))
                current_metadata = cursor.fetchone()
                metadata_dict = current_metadata[0] if current_metadata and current_metadata[0] else {}
                
                # Add dw_url to metadata
                metadata_dict['dw_url'] = public_url
                metadata_dict['dw_chart_id'] = chart_id
                metadata_dict['dw_edit_url'] = edit_url
                
                # Update metadata and chart_id, but NOT published_url
                cursor.execute(
                    "UPDATE maps SET metadata = %s, chart_id = %s WHERE id = %s",
                    (json.dumps(metadata_dict), chart_id, map_id)
                )
                conn.commit()
                
                logger.info(f"Map {map_id} updated with DataWrapper URL in metadata: {public_url} and chart_id: {chart_id}")
                cursor.close()
                conn.close()
                
                return public_url
            else:
                logger.error("Failed to get public URL")
                cursor.close()
                conn.close()
                return None
        else:
            logger.error("Failed to create chart_id")
            cursor.close()
            conn.close()
            return None
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Error creating Datawrapper map: {str(e)}")
        return None

if __name__ == "__main__":
    # Example Usage:
    # Set up logging for testing
    logging.basicConfig(level=logging.INFO)
    
    # Test fetching district shapes
    district_shapes = get_district_shapes("supervisor")
    if district_shapes:
        logger.info(f"Successfully fetched shapes for {len(district_shapes)} supervisor districts")
        
    # To test map creation, you need a valid map ID from the database
    # Uncomment and update with a valid map ID to test
    # map_url = create_datawrapper_map("your-map-id-here")
    # if map_url:
    #     logger.info(f"Map created and published at: {map_url}") 