# data_fetcher.py
import requests
import re
from urllib.parse import urljoin
import pandas as pd
import logging
import json
from dateutil.relativedelta import relativedelta
import time
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Create a logger for this module
logger = logging.getLogger(__name__)
# Add these lines after the imports
logging.basicConfig(
    level=logging.INFO,  # Changed from DEBUG to INFO
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # This will output to console
    ]
)

def create_session_with_retry():
    """
    Creates a requests session with retry logic and proper timeout handling.
    """
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,  # Total number of retries
        backoff_factor=2,  # Exponential backoff: 1, 2, 4 seconds
        status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry
        allowed_methods=["HEAD", "GET", "OPTIONS"]  # Methods to retry
    )
    
    # Mount the adapter for both HTTP and HTTPS
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

def clean_query_string(query):
    """
    Cleans the query string by removing unnecessary whitespace and line breaks.
    """
    if not isinstance(query, str):
        logger.error(f"Query is not a string. Type: {type(query)}, Value: {query}")
        return str(query)
    cleaned = re.sub(r'\s+', ' ', query.replace('\n', ' ')).strip()
    logger.debug("Cleaned query string: %s", cleaned)
    return cleaned

def fetch_data_from_api(query_object):
    logger.info("Starting fetch_data_from_api with query_object: %s", json.dumps(query_object, indent=2))
    base_url = "https://data.sfgov.org/resource/"
    all_data = []
    limit = 5000
    offset = 0

    endpoint = query_object.get('endpoint')
    query = query_object.get('query')
    
    if not endpoint:
        logger.error("Missing endpoint in query_object")
        return {'error': 'Endpoint is required'}
    if not query:
        logger.error("Missing query in query_object")
        return {'error': 'Query is required'}

    logger.info(f"Processing endpoint: {endpoint}")
    logger.info(f"Initial query string: {query}")
    
    cleaned_query = clean_query_string(query)
    logger.info(f"After clean_query_string: {cleaned_query}")
    
    # Remove any $query= prefix if it exists
    if cleaned_query.startswith('$query='):
        cleaned_query = cleaned_query[7:]
        logger.info("Removed $query= prefix")
    if cleaned_query.startswith('query='):
        cleaned_query = cleaned_query[6:]
        logger.info("Removed query= prefix")
    
    logger.info(f"Final cleaned query: {cleaned_query}")

    has_limit = "limit" in cleaned_query.lower()
    url = urljoin(base_url, f"{endpoint if endpoint.endswith('.json') else endpoint + '.json'}")
    logger.info(f"Full API URL: {url}")
    
    # Don't wrap the query in $query= here, just pass it directly
    params = {"$query": cleaned_query}
    logger.info(f"Request parameters: {json.dumps(params, indent=2)}")

    headers = {
        'Accept': 'application/json',
        'X-App-Token': os.getenv('SOCRATA_APP_TOKEN', '')  # Add Socrata app token for better rate limits
    }
    logger.info(f"Request headers: {json.dumps(headers, indent=2)}")

    # Create session with retry logic
    session = create_session_with_retry()
    
    has_more_data = True
    while has_more_data:
        if not has_limit:
            paginated_query = f"{cleaned_query} LIMIT {limit} OFFSET {offset}"
            params["$query"] = paginated_query
        else:
            # If there's already a LIMIT in the query, we need to handle large limits by pagination
            # Extract the limit from the query and break it into smaller chunks
            import re
            limit_match = re.search(r'LIMIT\s+(\d+)', cleaned_query, re.IGNORECASE)
            if limit_match:
                total_limit = int(limit_match.group(1))
                if total_limit > 5000:  # DataSF API max limit per request
                    # Remove the LIMIT from the query and handle pagination manually
                    query_without_limit = re.sub(r'\s+LIMIT\s+\d+', '', cleaned_query, flags=re.IGNORECASE)
                    paginated_query = f"{query_without_limit} LIMIT {min(5000, total_limit - offset)} OFFSET {offset}"
                    params["$query"] = paginated_query
                else:
                    # Use the original query as-is
                    params["$query"] = cleaned_query
            else:
                # Use the original query as-is
                params["$query"] = cleaned_query
        logger.debug("URL being requested: %s, params: %s", url, params)
        try:
            # Use session with retry logic and increased timeout
            response = session.get(url, params=params, headers=headers, timeout=300)  # Increased timeout to 300 seconds (5 minutes)
            logger.debug("Response Status Code: %s", response.status_code)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                logger.exception(
                    "Failed to decode JSON response. Status Code: %s, Response Content: %s",
                    response.status_code,
                    response.text[:200]
                )
                # Construct the full URL with query parameters for the queryURL
                full_url = f"{url}?$query={requests.utils.quote(cleaned_query)}"
                return {'error': 'Failed to decode JSON response from the API.', 'queryURL': full_url}
            all_data.extend(data)
            logger.info("Fetched %d records in current batch.", len(data))

            # Check if we should continue pagination
            if has_limit:
                # Check if we've reached the total limit or if we got fewer records than requested
                limit_match = re.search(r'LIMIT\s+(\d+)', cleaned_query, re.IGNORECASE)
                if limit_match:
                    total_limit = int(limit_match.group(1))
                    if len(all_data) >= total_limit or len(data) < min(5000, total_limit - offset):
                        has_more_data = False
                        logger.debug("Reached total limit or end of data; ending pagination.")
                    else:
                        offset += min(5000, total_limit - offset)
                        logger.debug("Proceeding to next offset: %d", offset)
                else:
                    # Original logic for queries with LIMIT but no large limit handling
                    if len(data) < limit:
                        has_more_data = False
                        logger.debug("No more data to fetch; ending pagination.")
                    else:
                        offset += limit
                        logger.debug("Proceeding to next offset: %d", offset)
            else:
                # Original logic for queries without LIMIT
                if len(data) < limit:
                    has_more_data = False
                    logger.debug("No more data to fetch; ending pagination.")
                    logger.info("url: %s", url)
                else:
                    offset += limit
                    logger.debug("Proceeding to next offset: %d", offset)
        except requests.exceptions.ReadTimeout as timeout_err:
            # Log the actual query URL for debugging
            full_url = f"{url}?$query={requests.utils.quote(params.get('$query', cleaned_query))}"
            logger.error(f"Query timed out after 300s. URL: {full_url}")
            logger.error(f"Query: {params.get('$query', 'N/A')}")
            logger.error(f"Timeout error: {timeout_err}")
            return {'error': f'Query timed out after 300 seconds: {str(timeout_err)}', 'queryURL': full_url}
        except requests.exceptions.ConnectionError as conn_err:
            full_url = f"{url}?$query={requests.utils.quote(params.get('$query', cleaned_query))}"
            logger.error(f"Connection error. URL: {full_url}")
            logger.error(f"Query: {params.get('$query', 'N/A')}")
            logger.error(f"Connection error: {conn_err}")
            return {'error': f'Connection error: {str(conn_err)}', 'queryURL': full_url}
        except requests.HTTPError as http_err:
            error_content = ''
            try:
                # Attempt to extract the error message from the response JSON
                error_json = response.json()
                error_content = error_json.get('message', response.text[:200])
            except ValueError:
                # If response is not JSON, use the text content
                error_content = response.text[:200]
            logger.exception(
                "HTTP error occurred: %s. Response Content: %s",
                http_err,
                error_content
            )
            # Construct the full URL with query parameters for the queryURL
            full_url = f"{url}?$query={requests.utils.quote(params.get('$query', cleaned_query))}"
            return {'error': error_content, 'queryURL': full_url}
        except Exception as err:
            full_url = f"{url}?$query={requests.utils.quote(params.get('$query', cleaned_query))}"
            logger.exception("An error occurred: %s", err)
            logger.error(f"Query URL: {full_url}")
            return {'error': str(err), 'queryURL': full_url}

    logger.debug("Finished fetching data. Total records retrieved: %d", len(all_data))
    
    # Construct the full URL with query parameters for the queryURL
    full_url = f"{url}?$query={requests.utils.quote(cleaned_query)}"
    if not has_limit:
        full_url = f"{url}?$query={requests.utils.quote(cleaned_query)} LIMIT {limit} OFFSET 0"
    
    return {
        'data': all_data,
        'queryURL': full_url
    }

def set_dataset(context_variables, *args, **kwargs):
    """
    Fetches data from the API and sets it in the context variables.
    
    Args:
        context_variables: Dictionary to store the dataset
        endpoint: The dataset identifier (e.g., 'ubvf-ztfx')
        query: The complete SoQL query string
        
    The function can be called in two ways:
    1. With positional arguments:
       set_dataset(context_variables, "dataset-id", query="your-soql-query")
    2. With keyword arguments:
       set_dataset(context_variables, endpoint="dataset-id", query="your-soql-query")
    3. With nested kwargs (agent style):
       set_dataset(context_variables, args="{}", kwargs={"endpoint": "x", "query": "y"})
        
    Returns:
        Dictionary with status and optional error message
    """
    logger.info("=== Starting set_dataset ===")
    logger.info(f"Args received: {args}")
    logger.info(f"Kwargs received: {json.dumps(kwargs, indent=2)}")
    logger.info(f"Context variables keys: {list(context_variables.keys())}")

    try:
        # Handle nested kwargs structure (agent style)
        if 'kwargs' in kwargs and isinstance(kwargs['kwargs'], dict):
            inner_kwargs = kwargs['kwargs']
            endpoint = inner_kwargs.get('endpoint')
            query = inner_kwargs.get('query')
        else:
            # Handle direct kwargs or positional args
            endpoint = args[0] if args else kwargs.get('endpoint')
            query = kwargs.get('query')
        
        # Validate required parameters
        if not endpoint:
            logger.error("Missing endpoint parameter")
            return {'error': 'Endpoint is required', 'queryURL': None}
        if not query:
            logger.error("Missing query parameter")
            return {'error': 'Query is required', 'queryURL': None}
            
        # Clean up endpoint - ensure it ends with .json
        if not endpoint.endswith('.json'):
            endpoint = f"{endpoint}.json"
            logger.info(f"Added .json to endpoint: {endpoint}")

        logger.info(f"Final parameters - Endpoint: {endpoint}, Query: {query}")
        query_object = {'endpoint': endpoint, 'query': query}
        
        result = fetch_data_from_api(query_object)
        logger.info(f"API result status: {'success' if 'data' in result else 'error'}")
        
        if result and 'data' in result:
            data = result['data']
            if data:
                df = pd.DataFrame(data)
                context_variables['dataset'] = df
                # Store the query URL in context variables if available
                if 'queryURL' in result:
                    context_variables['executed_query_url'] = result['queryURL']
                    logger.info(f"Stored executed_query_url in context: {result['queryURL']}")
                logger.info(f"Dataset successfully created with shape: {df.shape}")
                return {'status': 'success', 'queryURL': result.get('queryURL')}
            else:
                logger.warning("API returned empty data")
                return {'error': 'No data returned from the API', 'queryURL': result.get('queryURL')}
        elif 'error' in result:
            logger.error(f"API returned error: {result['error']}")
            return {'error': result['error'], 'queryURL': result.get('queryURL')}
        else:
            logger.error("Unexpected API response format")
            return {'error': 'Unexpected API response format', 'queryURL': result.get('queryURL')}
            
    except Exception as e:
        logger.exception("Unexpected error in set_dataset")
        return {'error': f'Unexpected error: {str(e)}', 'queryURL': None}

def fetch_metric_data(metric_id, district="0", period_type="month", time_periods=2, anomaly_type=None, anomaly_field_name=None, center_lat=None, center_lng=None, radius=None):
    """
    Fetch data for a specific metric from the database and API using map configuration.
    
    Args:
        metric_id (str): The metric ID
        district (str): District filter ("0" for citywide, or specific district number)
        period_type (str): Period type for the data (month, quarter, year)
        time_periods (int): Number of time periods to include (default: 2)
        anomaly_type (str): Anomaly type filter (group_value from anomalies table)
        center_lat (float): Optional latitude for location-based filtering
        center_lng (float): Optional longitude for location-based filtering
        radius (float): Optional radius in meters for location-based filtering
        
    Returns:
        dict: Contains 'data' (DataFrame) or 'error' message
    """
    logger.info(f"Fetching data for metric {metric_id}, district {district}, period {period_type}, time_periods {time_periods}")
    
    try:
        import psycopg2
        import psycopg2.extras
        import os
        from dotenv import load_dotenv
        from datetime import date, timedelta
        
        load_dotenv()
        
        # Connect to database to get metric details
        from db_utils import get_postgres_connection
        conn = get_postgres_connection()
        
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get metric information including map fields
        cursor.execute("SELECT * FROM metrics WHERE id = %s", [metric_id])
        metric = cursor.fetchone()
        
        if not metric:
            cursor.close()
            conn.close()
            return {'error': f'Metric {metric_id} not found'}
        
        # Get map configuration
        map_query = metric.get('map_query', '')
        map_filters = metric.get('map_filters', {})
        map_config = metric.get('map_config', {})
        endpoint = metric.get('endpoint', '')
        
        if not endpoint:
            cursor.close()
            conn.close()
            return {'error': f'Metric {metric_id} missing endpoint'}
        
        # Parse anomaly_type if it's in "field_name:value" format
        if anomaly_type and ':' in anomaly_type and not anomaly_field_name:
            try:
                field_name, field_value = anomaly_type.split(':', 1)
                anomaly_field_name = field_name
                anomaly_type = field_value
                logger.info(f"Parsed anomaly_type: field_name='{anomaly_field_name}', value='{anomaly_type}'")
            except ValueError:
                logger.warning(f"Could not parse anomaly_type '{anomaly_type}' as field_name:value format")
        
        # Use provided anomaly_field_name or look it up if not provided
        if anomaly_type and not anomaly_field_name:
            logger.info(f"Looking up anomaly field for metric_id={metric_id} (type: {type(metric_id)}) and anomaly_type={anomaly_type}")
            
            # Try different approaches to find the anomaly field
            try:
                # First try with metric_id as string
                logger.info(f"Executing anomaly lookup query with metric_id='{str(metric_id)}' and anomaly_type='{anomaly_type}'")
                cursor.execute("""
                    SELECT group_field_name 
                    FROM anomalies 
                    WHERE object_id = %s AND group_value = %s AND is_active = TRUE 
                    ORDER BY 
                        CASE 
                            WHEN group_field_name = 'vacant' THEN 1
                            WHEN group_field_name = 'incident_category' THEN 2
                            WHEN group_field_name = 'service_name' THEN 3
                            ELSE 4
                        END
                    LIMIT 1
                """, [str(metric_id), anomaly_type])
                anomaly_result = cursor.fetchone()
                
                if anomaly_result:
                    anomaly_field_name = anomaly_result['group_field_name']
                    logger.info(f"Found anomaly field name: {anomaly_field_name} for group_value: {anomaly_type}")
                else:
                    logger.warning(f"No anomaly found for metric_id={metric_id}, anomaly_type={anomaly_type}")
                    # Let's check what anomalies exist for this metric
                    cursor.execute("""
                        SELECT group_field_name, group_value 
                        FROM anomalies 
                        WHERE object_id = %s AND is_active = TRUE 
                        LIMIT 5
                    """, [str(metric_id)])
                    existing_anomalies = cursor.fetchall()
                    logger.info(f"Available anomalies for metric {metric_id}: {existing_anomalies}")
                    
            except Exception as e:
                logger.error(f"Error looking up anomaly field: {str(e)}")
                # If lookup fails, we'll use fallback logic in build_map_query
        elif anomaly_type and anomaly_field_name:
            logger.info(f"Using provided anomaly field name: {anomaly_field_name} for group_value: {anomaly_type}")
        
        cursor.close()
        conn.close()
        
        # Build the query using map configuration with location filtering if provided
        query = build_map_query(map_query, map_filters, map_config, district, period_type, time_periods, anomaly_type, anomaly_field_name, center_lat=center_lat, center_lng=center_lng, radius=radius)
        
        if not query:
            return {'error': f'Failed to build query for metric {metric_id}'}
        
        logger.info(f"Built map query: {query}")
        
        # Fetch data using the existing set_dataset function
        context_variables = {}
        result = set_dataset(context_variables, endpoint=endpoint, query=query)
        
        if 'error' in result:
            return result
        
        # Return the dataset and executed query URL
        dataset = context_variables.get('dataset')
        executed_query_url = context_variables.get('executed_query_url')
        
        if dataset is not None and not dataset.empty:
            return {
                'data': dataset,
                'executed_query_url': executed_query_url
            }
        else:
            return {'error': 'No data returned from API'}
            
    except Exception as e:
        logger.exception(f"Error fetching metric data: {str(e)}")
        return {'error': f'Error fetching metric data: {str(e)}'}

def build_map_query(map_query, map_filters, map_config, district, period_type, time_periods, anomaly_type=None, anomaly_field_name=None, center_lat=None, center_lng=None, radius=None):
    """
    Build a query using map configuration and filters.
    
    Args:
        map_query (str): Base map query
        map_filters (dict): Map filters configuration
        map_config (dict): Map configuration
        district (str): District filter
        period_type (str): Period type
        time_periods (int): Number of time periods
        anomaly_type (str): Anomaly type filter (group_value from anomalies table)
        anomaly_field_name (str): The field name to filter on for the anomaly
        center_lat (float): Optional latitude for location-based filtering
        center_lng (float): Optional longitude for location-based filtering
        radius (float): Optional radius in meters for location-based filtering
        
    Returns:
        str: Built query string
    """
    from datetime import date, timedelta
    
    # Start with the base map query
    if not map_query or map_query.strip() == '':
        query = "SELECT *"
    else:
        query = map_query.strip()
    
    # Calculate date ranges based on period_type and time_periods
    target_date = date.today() - timedelta(days=1)
    
    # Debug logging
    logger.debug(f"DEBUG: period_type={period_type}, time_periods={time_periods}, type(time_periods)={type(time_periods)}")
    logger.debug(f"DEBUG: target_date={target_date}")
    
    if period_type == "month":
        # Get the last N months using proper month calculation
        end_date = target_date
        
        # Handle special "since_2024" case
        if time_periods == "since_2024":
            # Set start date to January 1, 2024
            start_date = date(2024, 1, 1)
        else:
            # Calculate start date by subtracting months properly
            try:
                time_periods_int = int(time_periods)
                start_date = end_date - relativedelta(months=time_periods_int)
                logger.debug(f"DEBUG: Calculated start_date={start_date} for time_periods={time_periods_int}")
            except (ValueError, TypeError) as e:
                logger.error(f"ERROR: Invalid time_periods value '{time_periods}': {e}")
                # Fallback to a reasonable default
                start_date = end_date - relativedelta(months=2)
                logger.debug(f"DEBUG: Using fallback start_date={start_date}")
    elif period_type == "week":
        # Get the last N weeks using proper week calculation
        end_date = target_date
        
        # Handle special "since_2024" case
        if time_periods == "since_2024":
            # Set start date to January 1, 2024
            start_date = date(2024, 1, 1)
        else:
            # Calculate start date by subtracting weeks properly
            try:
                time_periods_int = int(time_periods)
                start_date = end_date - timedelta(weeks=time_periods_int)
                logger.debug(f"DEBUG: Calculated start_date={start_date} for time_periods={time_periods_int} weeks")
            except (ValueError, TypeError) as e:
                logger.error(f"ERROR: Invalid time_periods value '{time_periods}' for weeks: {e}")
                start_date = end_date - timedelta(weeks=2)
                logger.debug(f"DEBUG: Using fallback start_date={start_date}")
    elif period_type == "quarter":
        # Get the last N quarters using proper quarter calculation
        end_date = target_date
        try:
            time_periods_int = int(time_periods)
            start_date = end_date - relativedelta(months=3 * time_periods_int)
            logger.debug(f"DEBUG: Calculated start_date={start_date} for time_periods={time_periods_int} quarters")
        except (ValueError, TypeError) as e:
            logger.error(f"ERROR: Invalid time_periods value '{time_periods}' for quarters: {e}")
            start_date = end_date - relativedelta(months=6)
            logger.debug(f"DEBUG: Using fallback start_date={start_date}")
    elif period_type == "year":
        # Get the last N years using proper year calculation
        end_date = target_date
        try:
            time_periods_int = int(time_periods)
            start_date = end_date - relativedelta(years=time_periods_int)
            logger.debug(f"DEBUG: Calculated start_date={start_date} for time_periods={time_periods_int} years")
        except (ValueError, TypeError) as e:
            logger.error(f"ERROR: Invalid time_periods value '{time_periods}' for years: {e}")
            start_date = end_date - relativedelta(years=1)
            logger.debug(f"DEBUG: Using fallback start_date={start_date}")
    else:
        # Default to last 2 months
        end_date = target_date
        start_date = end_date - timedelta(days=60)
    
    # Add WHERE clause if needed
    where_conditions = []
    
    # Add date range filter if specified in map_config
    date_field = map_config.get('date_field')
    if date_field:
        # Debug logging for date range
        logger.debug(f"DEBUG: Using date_field='{date_field}'")
        logger.debug(f"DEBUG: start_date={start_date}, end_date={end_date}")
        
        # Check if the date_field is a complex case statement
        if 'CASE WHEN' in date_field.upper():
            # For complex case statements, we need to handle them differently
            # Extract the base fields from the case statement
            if 'dba_start_date' in date_field and 'location_start_date' in date_field:
                # Use the same logic as the metric query - check both fields for openings
                date_condition = f"((dba_start_date >= '{start_date.strftime('%Y-%m-%d')}' AND dba_start_date <= '{end_date.strftime('%Y-%m-%d')}') OR (location_start_date >= '{start_date.strftime('%Y-%m-%d')}' AND location_start_date <= '{end_date.strftime('%Y-%m-%d')}' AND dba_start_date < '{start_date.strftime('%Y-%m-%d')}'))"
                where_conditions.append(date_condition)
                logger.debug(f"DEBUG: Added complex date condition: {date_condition}")
            elif 'dba_end_date' in date_field and 'location_end_date' in date_field:
                # Use the same logic as the metric query - check both fields for closures
                date_condition = f"((dba_end_date >= '{start_date.strftime('%Y-%m-%d')}' AND dba_end_date <= '{end_date.strftime('%Y-%m-%d')}') OR (location_end_date >= '{start_date.strftime('%Y-%m-%d')}' AND location_end_date <= '{end_date.strftime('%Y-%m-%d')}' AND dba_end_date < '{start_date.strftime('%Y-%m-%d')}'))"
                where_conditions.append(date_condition)
                logger.debug(f"DEBUG: Added complex date condition: {date_condition}")
            else:
                # Fallback to simple field usage
                date_condition = f"{date_field} >= '{start_date.strftime('%Y-%m-%d')}' AND {date_field} <= '{end_date.strftime('%Y-%m-%d')}'"
                where_conditions.append(date_condition)
                logger.debug(f"DEBUG: Added simple date condition: {date_condition}")
        else:
            # Simple field name
            date_condition = f"{date_field} >= '{start_date.strftime('%Y-%m-%d')}' AND {date_field} <= '{end_date.strftime('%Y-%m-%d')}'"
            where_conditions.append(date_condition)
            logger.debug(f"DEBUG: Added simple date condition: {date_condition}")
    
    # Add geometry filter if specified in map_filters
    if map_filters and 'geometry' in map_filters:
        geometry_filter = map_filters['geometry']
        if geometry_filter.get('type') == 'within_polygon':
            field = geometry_filter.get('field', 'location')
            value = geometry_filter.get('value', '')
            if value:
                where_conditions.append(f"within_polygon({field}, '{value}')")
    
    # Add direct filters if specified in map_filters (raw SQL conditions)
    if map_filters and 'direct_filters' in map_filters:
        direct_filters = map_filters['direct_filters']
        if direct_filters and isinstance(direct_filters, str):
            where_conditions.append(f"({direct_filters})")
            logger.debug(f"Added direct filters: {direct_filters}")
    
    # Add static filters if specified in map_filters
    if map_filters and 'static_filters' in map_filters:
        static_filters = map_filters['static_filters']
        logger.debug(f"Processing static filters: {static_filters}")
        
        for filter_item in static_filters:
            field = filter_item.get('field')
            operator = filter_item.get('operator', '=')
            values = filter_item.get('values', [])  # Use 'values' (plural)
            value = filter_item.get('value')  # Fallback to 'value' (singular)
            
            if field and (values or value is not None):
                # Use values if available, otherwise use single value
                if values:
                    if operator.upper() == "IN" and len(values) > 1:
                        values_str = "', '".join(str(v) for v in values)
                        filter_clause = f"{field} IN ('{values_str}')"
                        where_conditions.append(filter_clause)
                        logger.debug(f"Added static IN filter: {filter_clause}")
                    elif len(values) == 1:
                        filter_clause = f"{field} {operator} '{values[0]}'"
                        where_conditions.append(filter_clause)
                        logger.debug(f"Added static filter: {filter_clause}")
                elif value is not None:
                    if isinstance(value, str):
                        filter_clause = f"{field} {operator} '{value}'"
                    else:
                        filter_clause = f"{field} {operator} {value}"
                    where_conditions.append(filter_clause)
                    logger.debug(f"Added static filter: {filter_clause}")
    
    # Add direct filters from map_filters (like incident_category_filter)
    if map_filters:
        for filter_name, filter_config in map_filters.items():
            # Skip special filter types that are handled separately
            if filter_name in ['date_field', 'geometry', 'date_range', 'static_filters']:
                continue
                
            # Handle direct filter structures like incident_category_filter
            if isinstance(filter_config, dict):
                field = filter_config.get("field")
                operator = filter_config.get("operator", "=")
                values = filter_config.get("values", []) if filter_config.get("values") else [filter_config.get("value")]
                
                if field and values:
                    if operator.upper() == "IN" and len(values) > 1:
                        values_str = "', '".join(str(v) for v in values)
                        filter_clause = f"{field} IN ('{values_str}')"
                        where_conditions.append(filter_clause)
                        logger.debug(f"Added direct IN filter: {filter_clause}")
                    elif len(values) == 1:
                        filter_clause = f"{field} {operator} '{values[0]}'"
                        where_conditions.append(filter_clause)
                        logger.debug(f"Added direct filter: {filter_clause}")
    
    # Add district filter if specified
    if district != "0":
        where_conditions.append(f"supervisor_district = '{district}'")
    
    # Add location-based filter if center point and radius are provided
    if center_lat is not None and center_lng is not None and radius is not None:
        # Determine the location field name from map_config or use default
        location_field = map_config.get('location_field', 'location')
        
        # Check if map_config specifies a different location field
        if map_config and 'location_fields' in map_config:
            location_fields = map_config['location_fields']
            if isinstance(location_fields, list) and len(location_fields) > 0:
                # Use the first location field
                if isinstance(location_fields[0], dict):
                    location_field = location_fields[0].get('fieldName', location_field)
                elif isinstance(location_fields[0], str):
                    location_field = location_fields[0]
        
        # Also check the base query to see if it uses 'point' instead of 'location'
        # Some datasets use 'point' field (e.g., camera citations)
        query_lower = query.lower()
        if 'point' in query_lower and 'location' not in query_lower:
            # Check if 'point' is in the SELECT clause
            if 'select' in query_lower:
                select_part = query_lower.split('where')[0] if 'where' in query_lower else query_lower
                if 'point' in select_part:
                    location_field = 'point'
                    logger.info(f"Detected 'point' field in query, using it for location filter")
        
        # Use SoQL's within_circle function for location-based filtering
        # Format: within_circle(location_field, latitude, longitude, radius_in_meters)
        location_condition = f"within_circle({location_field}, {center_lat}, {center_lng}, {radius})"
        where_conditions.append(location_condition)
        logger.info(f"Adding location filter: {location_condition}")
    
    # Add anomaly filter if specified
    if anomaly_type and anomaly_field_name:
        logger.info(f"Adding anomaly filter: {anomaly_field_name} = '{anomaly_type}'")
        where_conditions.append(f"{anomaly_field_name} = '{anomaly_type}'")
    elif anomaly_type:
        # Fallback: try to find the appropriate field if we don't have the field name
        logger.info(f"Adding anomaly filter for group_value: {anomaly_type} (fallback)")
        
        # Try to find the appropriate field from map_config or map_filters
        anomaly_field = None
        
        # Check if we have category_fields in map_config that might contain the anomaly field
        if map_config and 'category_fields' in map_config:
            category_fields = map_config['category_fields']
            if isinstance(category_fields, list) and len(category_fields) > 0:
                # Use the first category field as a potential anomaly field
                anomaly_field = category_fields[0].get('fieldName') if isinstance(category_fields[0], dict) else str(category_fields[0])
        
        # If no category_fields, try to find a suitable field from the query
        if not anomaly_field:
            # Look for common anomaly fields in the query
            query_lower = query.lower()
            if 'incident_category' in query_lower:
                anomaly_field = 'incident_category'
            elif 'service_name' in query_lower:
                anomaly_field = 'service_name'
            elif 'service_subtype' in query_lower:
                anomaly_field = 'service_subtype'
            elif 'call_type_final' in query_lower:
                anomaly_field = 'call_type_final'
            else:
                # Default to a common field that might exist
                anomaly_field = 'incident_category'
        
        if anomaly_field:
            where_conditions.append(f"{anomaly_field} = '{anomaly_type}'")
            logger.debug(f"Added anomaly filter (fallback): {anomaly_field} = '{anomaly_type}'")
    
    # Add date range filter from map_filters if specified (only if not already added from map_config)
    if map_filters and 'date_range' in map_filters and not date_field:
        date_range_filter = map_filters['date_range']
        field = date_range_filter.get('field')
        fallback_field = date_range_filter.get('fallback_field')
        fallback_condition = date_range_filter.get('fallback_condition')
        
        if field:
            # Check if the field is a complex case statement
            if 'CASE WHEN' in field.upper():
                # For complex case statements, we need to handle them differently
                # Extract the base fields from the case statement
                if 'dba_start_date' in field and 'location_start_date' in field:
                    # Use the same logic as the metric query - check both fields for openings
                    where_conditions.append(f"((dba_start_date >= '{start_date.strftime('%Y-%m-%d')}' AND dba_start_date <= '{end_date.strftime('%Y-%m-%d')}') OR (location_start_date >= '{start_date.strftime('%Y-%m-%d')}' AND location_start_date <= '{end_date.strftime('%Y-%m-%d')}' AND dba_start_date < '{start_date.strftime('%Y-%m-%d')}'))")
                elif 'dba_end_date' in field and 'location_end_date' in field:
                    # Use the same logic as the metric query - check both fields for closures
                    where_conditions.append(f"((dba_end_date >= '{start_date.strftime('%Y-%m-%d')}' AND dba_end_date <= '{end_date.strftime('%Y-%m-%d')}') OR (location_end_date >= '{start_date.strftime('%Y-%m-%d')}' AND location_end_date <= '{end_date.strftime('%Y-%m-%d')}' AND dba_end_date < '{start_date.strftime('%Y-%m-%d')}'))")
                else:
                    # Fallback to simple field usage
                    where_conditions.append(f"{field} >= '{start_date.strftime('%Y-%m-%d')}' AND {field} <= '{end_date.strftime('%Y-%m-%d')}'")
            else:
                # Simple field name
                where_conditions.append(f"{field} >= '{start_date.strftime('%Y-%m-%d')}' AND {field} <= '{end_date.strftime('%Y-%m-%d')}'")
        
        if fallback_field and fallback_condition:
            # Add fallback condition
            where_conditions.append(f"({fallback_condition})")
    
    # Combine all conditions
    if where_conditions:
        logger.debug(f"WHERE conditions to apply: {where_conditions}")
        if "WHERE" in query.upper():
            query += f" AND {' AND '.join(where_conditions)}"
        else:
            query += f" WHERE {' AND '.join(where_conditions)}"
    
    # Add LIMIT based on data_point_threshold from map_config
    data_point_threshold = map_config.get('data_point_threshold', 5000)
    if "LIMIT" not in query.upper():
        query += f" LIMIT {data_point_threshold}"
    
    logger.info(f"Final built map query: {query}")
    return query
