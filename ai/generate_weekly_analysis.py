import os
import json
import logging
import traceback
import argparse
import sys
from datetime import datetime, date, timedelta
import pandas as pd
import re
from pathlib import Path
import schedule
import time

from tools.data_fetcher import set_dataset
from tools.genChart import generate_time_series_chart
from tools.anomaly_detection import anomaly_detection

# Configure logging
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Set output directory
OUTPUT_DIR = os.path.join(script_dir, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Create subdirectory for weekly analysis
WEEKLY_DIR = os.path.join(OUTPUT_DIR, 'weekly')
os.makedirs(WEEKLY_DIR, exist_ok=True)

# Configure root logger first
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Remove any existing handlers from root logger
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add file handler to root logger
root_file_handler = logging.FileHandler(os.path.join(logs_dir, 'weekly_metric_analysis.log'))
root_file_handler.setFormatter(formatter)
root_logger.addHandler(root_file_handler)

# Add console handler to root logger
root_console_handler = logging.StreamHandler()
root_console_handler.setFormatter(formatter)
root_logger.addHandler(root_console_handler)

# Now configure the module logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Log a message to confirm logging is set up
logger.info("Logging configured for generate_weekly_analysis.py")

def load_json_file(file_path):
    """Load a JSON file and return its contents."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading JSON file {file_path}: {e}")
        return None

def find_metric_in_queries(queries_data, metric_id):
    """Find a specific metric in the dashboard queries data structure."""
    # Convert metric_id to string for consistent comparison
    metric_id_str = str(metric_id)
    logger.info(f"Searching for metric ID: {metric_id_str}")
    
    # Log the structure we're searching through
    logger.info(f"Searching through queries data structure: {list(queries_data.keys())}")
        
    for top_category_name, top_category_data in queries_data.items():
        logger.info(f"Checking top category: {top_category_name}")
        for subcategory_name, subcategory_data in top_category_data.items():
            logger.info(f"Checking subcategory: {subcategory_name}")
            if isinstance(subcategory_data, dict) and 'queries' in subcategory_data:
                for query_name, query_data in subcategory_data['queries'].items():
                    # Log the query we're checking
                    logger.info(f"Checking query: {query_name}")
                    
                    # Check for numeric ID match
                    if isinstance(query_data, dict):
                        query_id = query_data.get('id')
                        # Convert query_id to string for comparison
                        query_id_str = str(query_id) if query_id is not None else None
                        logger.info(f"Comparing metric ID {metric_id_str} with query ID {query_id_str}")
                        
                        if query_id_str == metric_id_str:
                            # Found a match by numeric ID
                            logger.info(f"Found match by numeric ID: {metric_id_str}")
                            # Check for endpoint at query level first, then fallback to subcategory level
                            endpoint = None
                            if 'endpoint' in query_data:
                                endpoint = query_data.get('endpoint')
                                logger.info(f"Using query-level endpoint: {endpoint}")
                            else:
                                endpoint = subcategory_data.get('endpoint', None)
                                logger.info(f"Using subcategory-level endpoint: {endpoint}")
                                
                            return {
                                'top_category': top_category_name,
                                'subcategory': subcategory_name,
                                'query_name': query_name,
                                'query_data': query_data,
                                'endpoint': endpoint,
                                'category_fields': query_data.get('category_fields', []) if isinstance(query_data, dict) else [],
                                'location_fields': query_data.get('location_fields', []) if isinstance(query_data, dict) else [],
                                'numeric_id': query_data.get('id', None) if isinstance(query_data, dict) else None,
                                'metric_id': metric_id_str  # Ensure we always have the metric_id from the search
                            }
                    
                    # For string IDs, try to match the query name
                    if isinstance(metric_id, str):
                        # Clean up the query name for comparison
                        clean_query_name = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("_ytd", "")
                        clean_metric_id = metric_id.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("_ytd", "")
                        
                        logger.info(f"Comparing string IDs - Query: {clean_query_name}, Metric: {clean_metric_id}")
                        
                        if clean_query_name == clean_metric_id:
                            logger.info(f"Found match by string ID: {metric_id}")
                            return {
                                'top_category': top_category_name,
                                'subcategory': subcategory_name,
                                'query_name': query_name,
                                'query_data': query_data,
                                'endpoint': query_data.get('endpoint', subcategory_data.get('endpoint')),
                                'category_fields': query_data.get('category_fields', []),
                                'location_fields': query_data.get('location_fields', []),
                                'numeric_id': query_data.get('id'),
                                'metric_id': metric_id
                            }
    
    logger.error(f"Metric with ID '{metric_id_str}' not found in dashboard queries")
    return None

def detect_avg_aggregation(query):
    """
    Detect if a query uses an AVG() aggregation function for the main value field.
    
    Args:
        query (str): The SQL query to analyze
        
    Returns:
        bool: True if the query uses AVG() aggregation, False otherwise
    """
    if not query:
        return False
        
    # Look for AVG() function in the query
    avg_pattern = r'AVG\s*\(([^)]+)\)'
    avg_matches = re.findall(avg_pattern, query, re.IGNORECASE)
    
    # If we found any AVG() function, return True
    return len(avg_matches) > 0

def get_weekly_time_ranges():
    """
    Calculate recent and comparison periods for weekly analysis.
    
    The recent period is the last complete 7 days (excluding today).
    The comparison period is the previous 4 weeks (28 days) before the recent period.
    
    Returns:
        tuple: (recent_period, comparison_period) each containing start and end dates
    """
    today = date.today()
    
    # Recent period: last 7 complete days (excluding today)
    recent_end = today - timedelta(days=1)  # Yesterday
    recent_start = recent_end - timedelta(days=6)  # 7 days including yesterday
    
    # Comparison period: previous 4 weeks (28 days) before recent period
    comparison_end = recent_start - timedelta(days=1)  # Day before recent period
    comparison_start = comparison_end - timedelta(days=27)  # 28 days before
    
    recent_period = {
        'start': recent_start,
        'end': recent_end
    }
    
    comparison_period = {
        'start': comparison_start,
        'end': comparison_end
    }
    
    logger.info(f"Recent period: {recent_start} to {recent_end}")
    logger.info(f"Comparison period: {comparison_start} to {comparison_end}")
    
    return recent_period, comparison_period

def extract_date_field_from_query(query):
    """Extract the date field from a query."""
    date_fields_to_check = [
        'date', 'incident_date', 'report_date', 'arrest_date', 'received_datetime', 
        'Report_Datetime', 'disposition_date', 'dba_start_date'
    ]
    
    for field in date_fields_to_check:
        if field in query:
            logging.info(f"Found date field in query: {field}")
            return field
    
    # Try to find date_trunc patterns
    date_trunc_match = re.search(r'date_trunc_[ymd]+ *\( *([^\)]+) *\)', query)
    if date_trunc_match:
        field = date_trunc_match.group(1).strip()
        logging.info(f"Found date field from date_trunc: {field}")
        return field
    
    return None

def transform_query_for_weekly(original_query, date_field, category_fields, recent_period, comparison_period, district=None):
    """
    Transform a query for weekly analysis by:
    1. Replacing date placeholders
    2. Using appropriate date ranges for recent and comparison periods
    3. Adding category fields to GROUP BY
    4. Creating a week-level granularity for weekly analysis
    5. Adding district filter if specified
    
    Args:
        original_query (str): The original SQL query
        date_field (str): The name of the date field
        category_fields (list): List of category fields
        recent_period (dict): Recent period date range
        comparison_period (dict): Comparison period date range
        district (int, optional): District number to filter by
        
    Returns:
        str: Transformed SQL query
    """
    # Format date strings for SQL
    recent_start = recent_period['start'].isoformat()
    recent_end = recent_period['end'].isoformat()
    comparison_start = comparison_period['start'].isoformat()
    comparison_end = comparison_period['end'].isoformat()
    
    # Replace any date placeholders in the original query
    modified_query = original_query
    replacements = {
        'this_year_start': f"'{recent_start}'",
        'this_year_end': f"'{recent_end}'",
        'last_year_start': f"'{comparison_start}'",
        'last_year_end': f"'{comparison_end}'",
        'start_date': f"'{comparison_start}'",
        'current_date': f"'{recent_end}'"
    }
    
    # Apply replacements - ensure we're not creating malformed field names
    for placeholder, value in replacements.items():
        # Make sure we're only replacing standalone instances of the placeholder
        modified_query = re.sub(r'([=<>:\s]|^)' + re.escape(placeholder) + r'([=<>:\s]|$)', 
                                r'\1' + value + r'\2', 
                                modified_query)
    
    # Determine if it's a YTD query by checking format
    is_ytd_query = ('as date, COUNT(*)' in modified_query or 
                   'as date,' in modified_query or 
                   'date_trunc_ymd' in modified_query)
    
    # If it's a YTD query, we'll modify it to work with our weekly analysis
    if is_ytd_query:
        logging.info("Using YTD query format as basis for weekly analysis")
        
        # Extract the core table and WHERE conditions from the original query
        # This pattern looks for date_trunc, field selection, conditions
        ytd_pattern = r'SELECT\s+date_trunc_[ymd]+\((.*?)\)\s+as\s+date,\s+([^W]+)WHERE\s+(.*?)(?:GROUP BY|ORDER BY|LIMIT|$)'
        ytd_match = re.search(ytd_pattern, modified_query, re.IGNORECASE | re.DOTALL)
        
        if ytd_match:
            date_field_match = ytd_match.group(1).strip()
            value_part = ytd_match.group(2).strip()
            where_part = ytd_match.group(3).strip()
            
            # Remove current_date references and replace with our recent_end
            where_part = re.sub(r'<=\s*current_date', f"<= '{recent_end}'", where_part)
            
            # For weekly analysis, get the start of the week
            # Using date_trunc_ymd alone since we can't use date_add_d in SoQL
            date_trunc = f"date_trunc_ymd({date_field_match})"
            
            # Build the category fields part of the SELECT and GROUP BY
            category_select = ""
            group_by_fields = []
            
            for field in category_fields:
                if isinstance(field, dict):
                    field_name = field.get('fieldName', '')
                else:
                    field_name = field
                
                if field_name:
                    category_select += f", {field_name}"
                    group_by_fields.append(field_name)
            
            # Add period_type to distinguish recent from comparison - FIXED POSITION
            period_type_select = f", CASE WHEN {date_field_match} >= '{recent_start}' AND {date_field_match} <= '{recent_end}' THEN 'recent' ELSE 'comparison' END as period_type"
            
            # Build the complete transformed query
            group_by_clause = "GROUP BY week_start, period_type"
            if group_by_fields:
                group_by_clause += ", " + ", ".join(group_by_fields)
                
            transformed_query = f"""
            SELECT 
                {date_trunc} as week_start,
                {value_part}
                {period_type_select}
                {category_select}
            WHERE 
                {where_part} AND
                (
                    ({date_field_match} >= '{comparison_start}' AND {date_field_match} <= '{comparison_end}')
                    OR 
                    ({date_field_match} >= '{recent_start}' AND {date_field_match} <= '{recent_end}')
                )
            {group_by_clause}
            ORDER BY week_start
            """
            
            return transformed_query
        else:
            # If we can't parse the YTD query, fall back to the regular transform
            logging.warning("Could not extract components from YTD query, falling back to standard transformation")

    # Try to extract the FROM clause
    from_match = re.search(r'FROM\s+(.*?)(?:WHERE|GROUP BY|ORDER BY|LIMIT|$)', modified_query, re.IGNORECASE | re.DOTALL)
    
    # If FROM clause not found, try to infer it from the query
    if not from_match:
        # Check if there's a table name after SELECT
        table_match = re.search(r'SELECT.*?FROM\s+([^\s,]+)', modified_query, re.IGNORECASE)
        if table_match:
            from_clause = table_match.group(1).strip()
        else:
            logging.warning("Could not extract FROM clause from query, using modified query with replaced placeholders")
            return modified_query
    else:
        from_clause = from_match.group(1).strip()
    
    # Try to extract the WHERE clause from the modified query
    where_match = re.search(r'WHERE\s+(.*?)(?:GROUP BY|ORDER BY|LIMIT|$)', modified_query, re.IGNORECASE | re.DOTALL)
    where_clause = ""
    if where_match:
        # Keep the original WHERE clause but add date filters for both periods
        original_where = where_match.group(1).strip()
        where_clause = f"""
        WHERE ({original_where}) AND (
            ({date_field} >= '{comparison_start}' AND {date_field} <= '{comparison_end}')
            OR 
            ({date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}')
        )
        """
        
        # Add district filter if specified
        if district is not None and 'supervisor_district' in modified_query:
            where_clause = where_clause.rstrip() + f" AND supervisor_district = '{district}'\n"
            logging.info(f"Added district filter to WHERE clause: supervisor_district = '{district}'")
    else:
        # Create a new WHERE clause with just date filters for both periods
        where_clause = f"""
        WHERE (
            ({date_field} >= '{comparison_start}' AND {date_field} <= '{comparison_end}')
            OR 
            ({date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}')
        )
        """
        
        # Add district filter if specified
        if district is not None and 'supervisor_district' in modified_query:
            where_clause = where_clause.rstrip() + f" AND supervisor_district = '{district}'\n"
            logging.info(f"Added district filter to new WHERE clause: supervisor_district = '{district}'")
    
    # If we have a valid FROM clause, proceed with transformation
    if from_clause:
        # Build the category fields part of the SELECT clause
        category_select = ""
        category_fields_list = []  # To track field names for GROUP BY
        
        for field in category_fields:
            if isinstance(field, dict):
                field_name = field.get('fieldName', '')
            else:
                field_name = field
            
            if field_name:
                category_select += f", {field_name}"
                category_fields_list.append(field_name)
        
        # For weekly analysis, use date_trunc_ymd to get the start of the week
        # SoQL doesn't support date_add_d, so we'll use a simpler approach
        date_transform = f"""
        date_trunc_ymd({date_field}) as week_start
        """

        # Build the GROUP BY clause with category fields
        group_by = "GROUP BY week_start"
        for field_name in category_fields_list:
            group_by += f", {field_name}"
        
        # Add period_type to distinguish recent from comparison
        period_type_select = f", CASE WHEN {date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}' THEN 'recent' ELSE 'comparison' END as period_type"
        
        # Build the complete transformed query with weekly aggregation
        transformed_query = f"""
        SELECT 
            {date_transform},
            COUNT(*) as value
            {period_type_select}
            {category_select}
        FROM {from_clause}
        {where_clause}
        {group_by}, period_type
        ORDER BY week_start
        """
        
        return transformed_query
    else:
        # If we couldn't extract or infer the FROM clause, return the modified query
        logging.warning("Could not determine FROM clause, using modified query with replaced placeholders")
        return modified_query

def process_weekly_analysis(metric_info, process_districts=False):
    """Process weekly metric analysis with optional district processing."""
    # Extract metric information
    metric_id = metric_info.get('metric_id', '')
    query_name = metric_info.get('query_name', metric_id)
    definition = metric_info.get('definition', '')
    summary = metric_info.get('summary', '')
    endpoint = metric_info.get('endpoint', '')
    data_sf_url = metric_info.get('data_sf_url', '')
    
    # Get time ranges for weekly analysis
    recent_period, comparison_period = get_weekly_time_ranges()
    
    # Create context variables and set the dataset
    context_variables = {}
    
    # Get the query from metric_info
    original_query = None
    if isinstance(metric_info.get('query_data'), dict):
        # First check if there's a YTD query available to use as the basis
        if 'ytd_query' in metric_info['query_data']:
            original_query = metric_info['query_data'].get('ytd_query', '')
            logging.info(f"Using YTD query as the basis for weekly analysis")
        # Also check for queries dictionary that might contain a YTD query
        elif 'queries' in metric_info['query_data'] and isinstance(metric_info['query_data']['queries'], dict):
            if 'ytd_query' in metric_info['query_data']['queries']:
                original_query = metric_info['query_data']['queries'].get('ytd_query', '')
                logging.info(f"Using YTD query from queries dictionary")
            elif 'executed_ytd_query' in metric_info['query_data']['queries']:
                original_query = metric_info['query_data']['queries'].get('executed_ytd_query', '')
                logging.info(f"Using executed YTD query from queries dictionary")
        
        # If no YTD query is found, fall back to the metric query
        if not original_query:
            original_query = metric_info['query_data'].get('metric_query', '')
            logging.info(f"No YTD query found, using regular metric query")
    else:
        original_query = metric_info.get('query_data', '')
        logging.info(f"Using provided query data directly")
    
    if not original_query:
        logging.error(f"No query found for {query_name}")
        return None
    
    logging.info(f"Original query: {original_query}")
    
    # Check if the query uses AVG() aggregation
    uses_avg = detect_avg_aggregation(original_query)
    logging.info(f"Query uses AVG() aggregation: {uses_avg}")
    
    # Define value_field
    value_field = 'value'
    
    # Extract category fields from metric_info
    category_fields = metric_info.get('category_fields', [])
    # Only use category fields that are explicitly defined - no default
    if not category_fields:
        category_fields = []
        logging.info("No category fields defined for this metric. Not using any default fields.")
    
    # Check if supervisor_district exists in category_fields
    has_district = False
    for field in category_fields:
        if (isinstance(field, dict) and field.get('fieldName') == 'supervisor_district') or field == 'supervisor_district':
            has_district = True
            break
    
    # If process_districts is True, make sure supervisor_district is used as a category field
    if process_districts and not has_district and 'supervisor_district' in original_query:
        # Add supervisor_district as a category field
        category_fields.append('supervisor_district')
        logging.info("Added supervisor_district to category fields for district processing")
        has_district = True
    
    # Determine the date field to use from the query
    date_field = extract_date_field_from_query(original_query)
    if not date_field:
        logging.warning(f"No date field found in query for {query_name}")
        date_field = 'date'  # Default to 'date'
    
    logging.info(f"Using date field: {date_field}")
    
    # Transform the query for weekly analysis
    transformed_query = transform_query_for_weekly(
        original_query, 
        date_field, 
        category_fields, 
        recent_period, 
        comparison_period
    )
    
    logging.info(f"Transformed query: {transformed_query}")
    
    # Log the set_dataset call details
    logging.info(f"Calling set_dataset with endpoint: {endpoint}")
    
    # Set the dataset using the endpoint and transformed query
    result = set_dataset(context_variables=context_variables, endpoint=endpoint, query=transformed_query)
    
    if 'error' in result:
        logging.error(f"Error setting dataset for {query_name}: {result['error']}")
        return None
    
    # Get the dataset from context_variables
    if 'dataset' not in context_variables:
        logging.error(f"No dataset found in context for {query_name}")
        return None
    
    dataset = context_variables['dataset']
    
    # Log available columns in dataset
    logging.info(f"Available columns in dataset: {dataset.columns.tolist()}")
    
    # Create or update value field if needed
    if value_field not in dataset.columns:
        if 'this_year' in dataset.columns:
            # Use this_year as the value field
            dataset[value_field] = dataset['this_year']
            logging.info(f"Created {value_field} from this_year column")
        elif dataset.select_dtypes(include=['number']).columns.tolist():
            # Use the first numeric column as the value field
            numeric_cols = dataset.select_dtypes(include=['number']).columns.tolist()
            # Filter out date-related columns
            numeric_cols = [col for col in numeric_cols if not any(date_term in col.lower() for date_term in ['year', 'month', 'day', 'date', 'week'])]
            
            if numeric_cols:
                dataset[value_field] = dataset[numeric_cols[0]]
                logging.info(f"Created {value_field} from {numeric_cols[0]} column")
            else:
                # If no suitable numeric column, use 1 as the value
                dataset[value_field] = 1
                logging.info(f"Created {value_field} with default value 1")
        else:
            # If no numeric columns, use 1 as the value
            dataset[value_field] = 1
            logging.info(f"Created {value_field} with default value 1")
    
    # Define aggregation functions based on whether the query uses AVG()
    agg_functions = {value_field: 'mean'} if uses_avg else {value_field: 'sum'}
    logging.info(f"Using '{list(agg_functions.values())[0]}' aggregation for field {value_field}")
    
    # Validate category fields - ensure they exist in the dataset
    valid_category_fields = []
    for field in category_fields:
        if isinstance(field, dict):
            field_name = field.get('fieldName', '')
        else:
            field_name = field
        
        if field_name and field_name in dataset.columns:
            valid_category_fields.append(field)
            logging.info(f"Validated category field: {field_name}")
        else:
            logging.warning(f"Category field {field_name} not found in dataset. Ignoring.")
    
    # Update category_fields with only valid fields
    category_fields = valid_category_fields
    
    # Make sure week_start field exists (our time series field)
    time_field = 'week_start'
    if time_field not in dataset.columns:
        if 'day' in dataset.columns:
            # If the query returned 'day' instead of 'week_start', use that
            time_field = 'day'
            logging.info(f"Using 'day' field instead of 'week_start'")
        elif 'max_date' in dataset.columns:
            try:
                # Convert max_date to datetime and create week_start field
                dataset['max_date'] = pd.to_datetime(dataset['max_date'])
                # Calculate start of week (Sunday)
                dataset[time_field] = dataset['max_date'] - pd.to_timedelta(dataset['max_date'].dt.dayofweek, unit='d')
                dataset[time_field] = dataset[time_field].dt.strftime('%Y-%m-%d')
                logging.info(f"Created '{time_field}' field from max_date column")
            except Exception as e:
                logging.error(f"Error creating '{time_field}' field from max_date: {e}")
                # Use current date's week start
                today = datetime.now()
                week_start = today - timedelta(days=today.weekday())
                dataset[time_field] = week_start.strftime('%Y-%m-%d')
                logging.info(f"Created '{time_field}' field with current week start")
        else:
            # Try to create week_start field from any date-like column
            date_columns = [col for col in dataset.columns if any(date_term in col.lower() for date_term in ['date', 'time', 'day'])]
            if date_columns:
                try:
                    date_col = date_columns[0]
                    dataset[date_col] = pd.to_datetime(dataset[date_col])
                    # Calculate start of week (Sunday)
                    dataset[time_field] = dataset[date_col] - pd.to_timedelta(dataset[date_col].dt.dayofweek, unit='d')
                    dataset[time_field] = dataset[time_field].dt.strftime('%Y-%m-%d')
                    logging.info(f"Created '{time_field}' field from {date_col} column")
                except Exception as e:
                    logging.error(f"Error creating '{time_field}' field from {date_col}: {e}")
                    # Use current date's week start
                    today = datetime.now()
                    week_start = today - timedelta(days=today.weekday())
                    dataset[time_field] = week_start.strftime('%Y-%m-%d')
                    logging.info(f"Created '{time_field}' field with current week start")
            else:
                # Use current date's week start
                today = datetime.now()
                week_start = today - timedelta(days=today.weekday())
                dataset[time_field] = week_start.strftime('%Y-%m-%d')
                logging.info(f"No date columns found. Created '{time_field}' field with current week start")
                
    # Ensure time field is in the correct format for filtering
    try:
        # Convert to datetime then back to string format to standardize
        dataset[time_field] = pd.to_datetime(dataset[time_field]).dt.strftime('%Y-%m-%d')
    except Exception as e:
        logging.error(f"Error standardizing {time_field} field format: {e}")
    
    # Set up filter conditions for date filtering (using the time field)
    filter_conditions = [
        {'field': time_field, 'operator': '<=', 'value': recent_period['end'].isoformat()},
        {'field': time_field, 'operator': '>=', 'value': comparison_period['start'].isoformat()},
    ]
    
    # Update the dataset in context_variables
    context_variables['dataset'] = dataset
    
    # Initialize lists to store markdown and HTML content
    all_markdown_contents = []
    all_html_contents = []
    
    # Process the overall (citywide) analysis
    # Clean the metric name for the y-axis label
    y_axis_label = query_name.replace("📊", "").strip()
    context_variables['y_axis_label'] = y_axis_label
    
    # Convert recent and comparison periods to string format for anomaly detection
    string_recent_period = {
        'start': recent_period['start'].strftime('%Y-%m-%d'),
        'end': recent_period['end'].strftime('%Y-%m-%d')
    }
    
    string_comparison_period = {
        'start': comparison_period['start'].strftime('%Y-%m-%d'),
        'end': comparison_period['end'].strftime('%Y-%m-%d')
    }
    
    # Generate main time series chart
    logging.info(f"Generating main time series chart for {query_name}")
    main_chart_title = f'{query_name} <br> Weekly Trend'
    
    # Create a separate context for the main chart
    main_context = context_variables.copy()
    main_context['chart_title'] = main_chart_title
    main_context['noun'] = query_name
    
    try:
        # Generate time series chart - now using week_start directly without extra aggregation
        chart_result = generate_time_series_chart(
            context_variables=main_context,
            time_series_field=time_field,
            numeric_fields=value_field,
            aggregation_period=None,  # No need for further aggregation since data is already weekly
            max_legend_items=10,
            filter_conditions=filter_conditions,
            show_average_line=True,
            agg_functions=agg_functions,
            return_html=True,
            output_dir=WEEKLY_DIR
        )
        
        logging.info(f"Successfully generated main time series chart for {query_name}")
        
        # Append chart HTML to content lists if available
        if chart_result:
            if isinstance(chart_result, tuple):
                markdown_content, html_content = chart_result
                all_markdown_contents.append(markdown_content)
                all_html_contents.append(html_content)
            elif isinstance(chart_result, dict) and 'html' in chart_result:
                all_html_contents.append(chart_result['html'])
            else:
                all_html_contents.append(str(chart_result))
        else:
            logging.warning(f"No main chart result returned for {query_name}")
    except Exception as e:
        logging.error(f"Error generating main time series chart for {query_name}: {str(e)}")
        logging.error(traceback.format_exc())
    
    # Process by category fields
    for category_field in category_fields:
        # Get the actual field name
        if isinstance(category_field, dict):
            category_field_name = category_field.get('fieldName', '')
            category_field_display = category_field.get('name', category_field_name)
        else:
            category_field_name = category_field
            category_field_display = category_field
        
        # Skip if category field is not in dataset
        if category_field_name not in dataset.columns:
            logging.warning(f"Category field '{category_field_name}' not found in dataset for {query_name}")
            continue
        
        logging.info(f"Processing category field: {category_field_name} for {query_name}")
        
        try:
            # Generate time series chart for each category field
            chart_title = f"{query_name} <br> {value_field} by Week by {category_field_display}"
            category_context = context_variables.copy()
            category_context['chart_title'] = chart_title
            
            # Generate the chart with grouping by category field
            cat_chart_result = generate_time_series_chart(
                context_variables=category_context,
                time_series_field=time_field,
                numeric_fields=value_field,
                aggregation_period=None,  # Data is already weekly
                max_legend_items=10,
                group_field=category_field_name,
                filter_conditions=filter_conditions,
                show_average_line=False,
                agg_functions=agg_functions,
                return_html=True,
                output_dir=WEEKLY_DIR
            )
            
            logging.info(f"Successfully generated chart for {category_field_name} for {query_name}")
            
            # Append chart result to content lists
            if cat_chart_result:
                if isinstance(cat_chart_result, tuple):
                    markdown_content, html_content = cat_chart_result
                    all_markdown_contents.append(markdown_content)
                    all_html_contents.append(html_content)
                elif isinstance(cat_chart_result, dict) and 'html' in cat_chart_result:
                    all_html_contents.append(cat_chart_result['html'])
                else:
                    all_html_contents.append(str(cat_chart_result))
            else:
                logging.warning(f"No chart result returned for {category_field_name} for {query_name}")
        except Exception as e:
            logging.error(f"Error generating chart for {category_field_name} for {query_name}: {str(e)}")
            logging.error(traceback.format_exc())
        
        try:
            # Detect anomalies
            anomaly_results = anomaly_detection(
                context_variables=context_variables,
                group_field=category_field_name,
                filter_conditions=filter_conditions,
                min_diff=2,
                recent_period=string_recent_period,
                comparison_period=string_comparison_period,
                date_field=time_field,
                numeric_field=value_field,
                y_axis_label=y_axis_label,
                title=f"{query_name} - {value_field} by {category_field_display}",
                period_type='week',
                agg_function='mean' if uses_avg else 'sum',
                output_dir='weekly'
            )
            
            # Get markdown and HTML content from anomaly results
            if anomaly_results:
                markdown_content = anomaly_results.get('markdown', anomaly_results.get('anomalies_markdown', 'No anomalies detected.'))
                html_content = anomaly_results.get('html', anomaly_results.get('anomalies', 'No anomalies detected.'))
                
                # Append content to lists
                all_markdown_contents.append(markdown_content)
                all_html_contents.append(html_content)
        except Exception as e:
            logging.error(f"Error detecting anomalies for {category_field_name} for {query_name}: {str(e)}")
            logging.error(traceback.format_exc())
    
    # Combine all markdown content
    query_string = ", ".join([f"{cond['field']} {cond['operator']} {cond['value']}" for cond in filter_conditions])
    combined_markdown = f"# {metric_id} - {query_name}\n\n**Analysis Type:** Weekly\n\n**Period:** {recent_period['start']} to {recent_period['end']}\n\n**Filters:** {query_string}\n\n{''.join(all_markdown_contents)}"
    
    # Combine all HTML content - ensure all items are strings
    html_content_strings = []
    for content in all_html_contents:
        if isinstance(content, str):
            html_content_strings.append(content)
        elif isinstance(content, tuple):
            html_content_strings.append(content[0] if content else "")
        else:
            html_content_strings.append(str(content) if content else "")
    
    combined_html = f"<h1>{metric_id} - {query_name}</h1>\n<p><strong>Analysis Type:</strong> Weekly</p>\n<p><strong>Period:</strong> {recent_period['start']} to {recent_period['end']}</p>\n<p><strong>Filters:</strong> {query_string}</p>\n{''.join(html_content_strings)}"
    
    # Save the analysis files
    save_result = save_weekly_analysis(
        {
            'query_name': query_name,
            'markdown': combined_markdown,
            'html': combined_html,
            'metric_id': metric_id
        },
        metric_id
    )
    
    return {
        'query_name': query_name,
        'metric_id': metric_id,
        'markdown': combined_markdown,
        'html': combined_html,
        'file_path': save_result.get('md_path', '')
    }

def save_weekly_analysis(result, metric_id, output_dir=None):
    """Save weekly analysis results to markdown files."""
    # Create output directory if it doesn't exist
    if output_dir is None:
        output_dir = WEEKLY_DIR
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Use result's metric_id if available, otherwise fallback to the provided metric_id
    file_metric_id = result.get('metric_id', metric_id)
    
    # Ensure we have a valid metric_id
    if not file_metric_id or file_metric_id.strip() == '':
        # Generate a sanitized metric ID from the query name if no metric_id is available
        query_name = result.get('query_name', '')
        file_metric_id = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
        logging.warning(f"Missing metric_id for {query_name}, using sanitized query name: {file_metric_id}")
    
    # Generate unique filename with date for weekly analysis
    today = datetime.now().strftime('%Y-%m-%d')
    md_filename = f"{file_metric_id}_{today}.md"
    md_path = os.path.join(output_dir, md_filename)
    
    # Log the file path being used
    logging.info(f"Saving weekly analysis to: {md_path}")
    
    # Get the markdown content
    markdown_content = result.get('markdown', '')
    
    # Write markdown file
    with open(md_path, 'w') as f:
        f.write(markdown_content)
        
    logging.info(f"Saved weekly analysis for {file_metric_id} to {md_path}")
    
    return {
        'md_path': md_path
    }

def run_weekly_analysis(metrics_list=None, process_districts=False):
    """Run weekly analysis for specified metrics or all metrics if none specified."""
    start_time = datetime.now()
    logger.info(f"========== STARTING WEEKLY ANALYSIS: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Process districts: {process_districts}")
    
    if metrics_list:
        logger.info(f"Analyzing {len(metrics_list)} specified metrics: {', '.join(str(m) for m in metrics_list)}")
    else:
        logger.info("No specific metrics provided, using default set")
    
    # Load dashboard queries
    dashboard_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries.json")
    enhanced_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries_enhanced.json")
    
    # Log the paths we're trying to use
    logger.info(f"Looking for enhanced queries at: {enhanced_queries_path}")
    logger.info(f"Looking for standard queries at: {dashboard_queries_path}")
    
    # Try to load enhanced queries first, fall back to regular queries
    if os.path.exists(enhanced_queries_path):
        logger.info("Found enhanced dashboard queries file")
        dashboard_queries = load_json_file(enhanced_queries_path)
        if dashboard_queries:
            logger.info(f"Successfully loaded enhanced dashboard queries with {len(dashboard_queries)} top categories")
        else:
            logger.error("Failed to load enhanced dashboard queries")
    else:
        logger.warning(f"Enhanced queries file not found at {enhanced_queries_path}")
        logger.info("Using standard dashboard queries")
        dashboard_queries = load_json_file(dashboard_queries_path)
        if dashboard_queries:
            logger.info(f"Successfully loaded standard dashboard queries with {len(dashboard_queries)} top categories")
        else:
            logger.error("Failed to load standard dashboard queries")
    
    if not dashboard_queries:
        logger.error("Failed to load dashboard queries")
        return
    
    # Process each metric
    all_results = []
    successful_metrics = []
    failed_metrics = []
    metrics_using_ytd = []  # Track metrics that used YTD queries
    
    # Log metric processing start
    logger.info(f"Beginning to process {len(metrics_list) if metrics_list else 'default set of'} metrics")
    
    for i, metric_id in enumerate(metrics_list):
        metric_start_time = datetime.now()
        logger.info(f"[{i+1}/{len(metrics_list)}] Processing weekly analysis for metric: {metric_id} - Started at {metric_start_time.strftime('%H:%M:%S')}")
        
        # Find the metric in the queries
        logger.info(f"Searching for metric '{metric_id}' in dashboard queries...")
        metric_info = find_metric_in_queries(dashboard_queries, metric_id)
        if not metric_info:
            logger.error(f"Metric with ID '{metric_id}' not found in dashboard queries")
            failed_metrics.append(metric_id)
            continue
        
        # Log metric details found
        query_name = metric_info.get('query_name', metric_id)
        logger.info(f"Found metric '{metric_id}' - Query Name: '{query_name}'")
        logger.info(f"Category: {metric_info.get('top_category', 'Unknown')}/{metric_info.get('subcategory', 'Unknown')}")
        
        # Make sure metric_id is in the metric_info
        if 'metric_id' not in metric_info:
            metric_info['metric_id'] = metric_id
            logger.info(f"Added missing metric_id '{metric_id}' to metric_info")
        
        # Check if the metric has YTD queries
        has_ytd_query = False
        if isinstance(metric_info.get('query_data'), dict):
            if 'ytd_query' in metric_info['query_data']:
                has_ytd_query = True
                logger.info(f"Metric '{metric_id}' has a YTD query available")
            elif 'queries' in metric_info['query_data'] and isinstance(metric_info['query_data']['queries'], dict):
                if 'ytd_query' in metric_info['query_data']['queries'] or 'executed_ytd_query' in metric_info['query_data']['queries']:
                    has_ytd_query = True
                    logger.info(f"Metric '{metric_id}' has a YTD query in the queries dictionary")
        
        # Process the weekly analysis
        logger.info(f"Starting weekly analysis processing for '{query_name}'...")
        try:
            result = process_weekly_analysis(metric_info, process_districts=process_districts)
            if result:
                all_results.append(result)
                successful_metrics.append(metric_id)
                metric_end_time = datetime.now()
                duration = (metric_end_time - metric_start_time).total_seconds()
                logger.info(f"Completed weekly analysis for {metric_id} - Duration: {duration:.2f} seconds")
                logger.info(f"Analysis saved to: {result.get('file_path', 'Unknown path')}")
                
                # Check if YTD query was used based on log messages
                if has_ytd_query:
                    metrics_using_ytd.append(metric_id)
                    logger.info(f"Successfully used YTD query for {metric_id}")
            else:
                logger.error(f"Failed to complete weekly analysis for {metric_id} - result was None")
                failed_metrics.append(metric_id)
        except Exception as e:
            logger.error(f"Exception while processing metric {metric_id}: {str(e)}")
            logger.error(traceback.format_exc())
            failed_metrics.append(metric_id)
    
    # Log summary statistics
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    logger.info(f"========== WEEKLY ANALYSIS COMPLETE: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Total duration: {duration:.2f} seconds")
    logger.info(f"Metrics processed: {len(metrics_list) if metrics_list else 0}")
    logger.info(f"Successful: {len(successful_metrics)} - {', '.join(str(m) for m in successful_metrics) if successful_metrics else 'None'}")
    logger.info(f"Failed: {len(failed_metrics)} - {', '.join(str(m) for m in failed_metrics) if failed_metrics else 'None'}")
    logger.info(f"Using YTD queries: {len(metrics_using_ytd)} - {', '.join(str(m) for m in metrics_using_ytd) if metrics_using_ytd else 'None'}")
    
    # Return results for potential newsletter generation
    return all_results

def generate_weekly_newsletter(results):
    """Generate a weekly newsletter based on the analysis results."""
    start_time = datetime.now()
    logger.info(f"========== STARTING NEWSLETTER GENERATION: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    
    if not results:
        logger.warning("No results to include in newsletter - skipping generation")
        return None
    
    logger.info(f"Generating newsletter with {len(results)} metric results")
    
    today = datetime.now().strftime('%Y-%m-%d')
    newsletter_title = f"SF Data Weekly Trends - {today}"
    logger.info(f"Newsletter title: {newsletter_title}")
    
    # Start building the newsletter content
    newsletter_content = f"# {newsletter_title}\n\n"
    newsletter_content += "## This Week's Data Trends\n\n"
    
    # Add each analysis result to the newsletter
    for i, result in enumerate(results):
        metric_id = result.get('metric_id', '')
        query_name = result.get('query_name', '')
        file_path = result.get('file_path', '')
        
        logger.info(f"Adding result {i+1}/{len(results)} to newsletter: {metric_id} - {query_name}")
        
        # Add a section for this metric
        newsletter_content += f"### {query_name}\n\n"
        
        # Include a link to the full analysis
        if file_path:
            relative_path = os.path.relpath(file_path, OUTPUT_DIR)
            newsletter_content += f"[View full analysis]({relative_path})\n\n"
            logger.info(f"Added link to analysis file: {relative_path}")
        else:
            logger.warning(f"No file path for {metric_id} - {query_name}, skipping link")
        
        # Include a summary of key findings (this would need to be extracted from the analysis)
        newsletter_content += "Key findings:\n"
        # This is a placeholder - in a real implementation, you'd parse the analysis
        # to extract important trends, anomalies, etc.
        newsletter_content += "- Weekly data analysis completed\n"
        newsletter_content += "- Check the full report for detailed trends\n\n"
    
    # Save the newsletter
    newsletter_path = os.path.join(WEEKLY_DIR, f"weekly_newsletter_{today}.md")
    logger.info(f"Saving newsletter to: {newsletter_path}")
    
    try:
        with open(newsletter_path, 'w') as f:
            f.write(newsletter_content)
        logger.info(f"Successfully saved newsletter ({len(newsletter_content)} characters)")
    except Exception as e:
        logger.error(f"Error saving newsletter to {newsletter_path}: {str(e)}")
        logger.error(traceback.format_exc())
        return None
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"========== NEWSLETTER GENERATION COMPLETE: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Total duration: {duration:.2f} seconds")
    logger.info(f"Newsletter saved to: {newsletter_path}")
    
    return newsletter_path

def scheduled_weekly_task():
    """Task to run weekly analysis and generate newsletter."""
    start_time = datetime.now()
    logger.info(f"========== STARTING SCHEDULED WEEKLY TASK: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    
    # Run analysis for all metrics
    logger.info("Running weekly analysis with district processing enabled")
    try:
        results = run_weekly_analysis(process_districts=True)
        
        if results:
            logger.info(f"Weekly analysis completed successfully with {len(results)} metrics")
        else:
            logger.warning("Weekly analysis completed but returned no results")
            
        # Generate the newsletter
        logger.info("Proceeding to newsletter generation")
        newsletter_path = generate_weekly_newsletter(results)
        
        if newsletter_path:
            logger.info(f"Weekly task completed successfully, newsletter saved to {newsletter_path}")
        else:
            logger.error("Weekly task did not generate a newsletter")
    except Exception as e:
        logger.error(f"Exception in scheduled weekly task: {str(e)}")
        logger.error(traceback.format_exc())
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"========== SCHEDULED WEEKLY TASK COMPLETE: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Total task duration: {duration:.2f} seconds ({duration/60:.2f} minutes)")

def main():
    """Main function to generate weekly metric analysis."""
    start_time = datetime.now()
    logger.info(f"========== WEEKLY ANALYSIS APP STARTING: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    
    # Parse command line arguments
    logger.info("Parsing command line arguments")
    parser = argparse.ArgumentParser(description='Generate weekly analysis for SF data metrics')
    parser.add_argument('--metric_id', help='ID of a specific metric to analyze', default=None)
    parser.add_argument('--metrics', help='Comma-separated list of metric IDs to analyze', default=None)
    parser.add_argument('--schedule', action='store_true', help='Run as a scheduled task every Thursday at 11am')
    parser.add_argument('--process-districts', action='store_true', 
                        help='Process and generate separate reports for each supervisor district if available')
    args = parser.parse_args()
    
    # Log parsed arguments
    logger.info(f"Arguments: metric_id={args.metric_id}, metrics={args.metrics}, schedule={args.schedule}, process_districts={args.process_districts}")
    
    if args.schedule:
        logger.info("Setting up scheduled task for weekly analysis (Thursdays at 11am)")
        
        # Schedule the job to run every Thursday at 11am
        schedule.every().thursday.at("11:00").do(scheduled_weekly_task)
        
        # Keep the script running
        logger.info("Scheduler is running, press Ctrl+C to exit")
        
        try:
            while True:
                pending_jobs = len(schedule.get_jobs())
                logger.debug(f"Checking pending jobs: {pending_jobs}")
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user (Ctrl+C)")
        except Exception as e:
            logger.error(f"Scheduler error: {str(e)}")
            logger.error(traceback.format_exc())
    else:
        # Run immediately for specified metrics
        logger.info("Running in immediate execution mode")
        metrics_list = []
        
        if args.metric_id:
            metrics_list.append(args.metric_id)
            logger.info(f"Added single metric from --metric_id: {args.metric_id}")
        
        if args.metrics:
            added_metrics = args.metrics.split(',')
            metrics_list.extend(added_metrics)
            logger.info(f"Added {len(added_metrics)} metrics from --metrics parameter")
        
        # If no metrics specified, use default set in run_weekly_analysis
        if not metrics_list:
            logger.info("No specific metrics requested, using default set")
        else:
            logger.info(f"Prepared to analyze {len(metrics_list)} metrics: {', '.join(metrics_list)}")
        
        try:
            # Run the analysis
            process_start = datetime.now()
            logger.info(f"Starting analysis process at {process_start.strftime('%H:%M:%S')}")
            
            results = run_weekly_analysis(
                metrics_list=metrics_list if metrics_list else None,
                process_districts=args.process_districts
            )
            
            # Generate a newsletter
            if results:
                logger.info(f"Analysis completed with {len(results)} results, generating newsletter")
                newsletter_path = generate_weekly_newsletter(results)
                
                if newsletter_path:
                    logger.info(f"Analysis completed, newsletter saved to {newsletter_path}")
                else:
                    logger.warning("Analysis completed but no newsletter was generated")
            else:
                logger.warning("Analysis completed but returned no results, skipping newsletter generation")
                
        except Exception as e:
            logger.error(f"Error during analysis execution: {str(e)}")
            logger.error(traceback.format_exc())
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"========== WEEKLY ANALYSIS APP COMPLETE: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ==========")
    logger.info(f"Total app execution time: {duration:.2f} seconds ({duration/60:.2f} minutes)")

if __name__ == "__main__":
    main() 