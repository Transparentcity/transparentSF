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

# Get script directory and ensure logs directory exists
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Set output directory
OUTPUT_DIR = os.path.join(script_dir, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Create subdirectory for weekly analysis
WEEKLY_DIR = os.path.join(OUTPUT_DIR, 'weekly')
os.makedirs(WEEKLY_DIR, exist_ok=True)

# Define default metrics to use if none are specified
DEFAULT_METRICS = [
    "1",   # Total Police Incidents 
    "2",   # Arrests Presented
    "3",   # Arrests Booked
    "4",   # Police Response Times
    "5",   # 311 Cases
    "6",   # DPW Service Requests
    "8",   # Building Permits
    "14"   # Public Works Projects
]

# Create module logger
logger = logging.getLogger(__name__)

# Log a message to confirm logging is set up
logger.info("==========================================================")
logger.info("Logging configured for generate_weekly_analysis.py")
logger.info(f"Python version: {sys.version}")
logger.info(f"Current working directory: {os.getcwd()}")
logger.info("==========================================================")

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
                                'numeric_id': query_id,
                                'id': metric_id_str,  # Ensure we set both id and metric_id for consistent access
                                'metric_id': metric_id_str
                            }
                    
                    # For string IDs, try to match the query name
                    if isinstance(metric_id, str):
                        # Clean up the query name for comparison
                        clean_query_name = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("_ytd", "")
                        clean_metric_id = metric_id.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("_ytd", "")
                        
                        logger.info(f"Comparing string IDs - Query: {clean_query_name}, Metric: {clean_metric_id}")
                        
                        if clean_query_name == clean_metric_id:
                            logger.info(f"Found match by string ID: {metric_id}")
                            numeric_id = query_data.get('id') if isinstance(query_data, dict) else None
                            return {
                                'top_category': top_category_name,
                                'subcategory': subcategory_name,
                                'query_name': query_name,
                                'query_data': query_data,
                                'endpoint': query_data.get('endpoint', subcategory_data.get('endpoint')),
                                'category_fields': query_data.get('category_fields', []),
                                'location_fields': query_data.get('location_fields', []),
                                'numeric_id': numeric_id,
                                'id': str(numeric_id) if numeric_id is not None else metric_id,
                                'metric_id': str(numeric_id) if numeric_id is not None else metric_id
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
    
    Finds the last complete week and the 6 preceding weeks, treating Jan 1 as the
    first day of the first week of the year.
    
    Returns:
        tuple: (recent_period, comparison_period) each containing start and end dates
    """
    today = date.today()
    
    # Find the first day of the year
    first_day = date(today.year, 1, 1)
    # Calculate day of the year (0-indexed)
    day_of_year = (today - first_day).days
    
    # Calculate the current week number (1-indexed)
    current_week = (day_of_year // 7) + 1
    
    # Find the end of the last complete week
    # Week starts on day 0, 7, 14, etc.
    last_complete_week_end = first_day + timedelta(days=(current_week - 1) * 7 - 1)
    
    # If this date is in the future, use the previous week
    if last_complete_week_end >= today:
        last_complete_week_end = first_day + timedelta(days=(current_week - 2) * 7 - 1)
    
    # Last complete week's start date (7 days)
    last_complete_week_start = last_complete_week_end - timedelta(days=6)
    
    # Comparison period: 6 weeks before the last complete week
    comparison_weeks_start = last_complete_week_start - timedelta(days=42)  # 6 weeks * 7 days
    comparison_weeks_end = last_complete_week_start - timedelta(days=1)
    
    recent_period = {
        'start': last_complete_week_start,
        'end': last_complete_week_end
    }
    
    comparison_period = {
        'start': comparison_weeks_start,
        'end': comparison_weeks_end
    }
    
    logger.info(f"Recent period (last complete week): {last_complete_week_start} to {last_complete_week_end}")
    logger.info(f"Comparison period (previous 6 weeks): {comparison_weeks_start} to {comparison_weeks_end}")
    
    return recent_period, comparison_period

def extract_date_field_from_query(query):
    """Extract the date field from a query."""
    date_fields_to_check = [
        'date', 'incident_date', 'report_date', 'arrest_date', 'received_datetime', 
        'Report_Datetime', 'disposition_date', 'dba_start_date'
    ]
    
    for field in date_fields_to_check:
        if field in query:
            logger.info(f"Found date field in query: {field}")
            return field
    
    # Try to find date_trunc patterns
    date_trunc_match = re.search(r'date_trunc_[ymd]+ *\( *([^\)]+) *\)', query)
    if date_trunc_match:
        field = date_trunc_match.group(1).strip()
        logger.info(f"Found date field from date_trunc: {field}")
        return field
    
    return None

def transform_query_for_weekly(original_query, date_field, category_fields, recent_period, comparison_period, district=None):
    """
    Transform a query for weekly analysis by:
    1. Replacing date placeholders
    2. Using appropriate date ranges for recent and comparison periods
    3. Adding category fields to the SELECT clause
    4. Retrieving daily data for later aggregation by week
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
    # Ensure we have valid date ranges
    if not recent_period or not comparison_period:
        logger.error("Missing date periods in transform_query_for_weekly")
        # Create default periods
        today = date.today()
        if not recent_period:
            recent_period = {
                'start': today - timedelta(days=7),
                'end': today
            }
            logger.warning(f"Using default recent_period: {recent_period}")
        
        if not comparison_period:
            comparison_period = {
                'start': today - timedelta(days=49),  # 7 weeks ago
                'end': today - timedelta(days=8)
            }
            logger.warning(f"Using default comparison_period: {comparison_period}")
    
    # Verify the date fields exist in the period dictionaries
    for period, name in [(recent_period, 'recent_period'), (comparison_period, 'comparison_period')]:
        for field in ['start', 'end']:
            if field not in period or period[field] is None:
                period[field] = date.today() if field == 'end' else date.today() - timedelta(days=7)
                logger.warning(f"Missing {field} in {name}, using default: {period[field]}")
    
    # Format date strings for SQL - with error handling
    try:
        recent_start = recent_period['start'].isoformat()
        recent_end = recent_period['end'].isoformat()
        comparison_start = comparison_period['start'].isoformat()
        comparison_end = comparison_period['end'].isoformat()
    except (AttributeError, TypeError) as e:
        logger.error(f"Error formatting dates: {str(e)}")
        # Use string fallbacks
        recent_start = "2023-01-01" if not isinstance(recent_period.get('start'), date) else recent_period['start'].isoformat()
        recent_end = "2023-01-07" if not isinstance(recent_period.get('end'), date) else recent_period['end'].isoformat()
        comparison_start = "2022-12-01" if not isinstance(comparison_period.get('start'), date) else comparison_period['start'].isoformat()
        comparison_end = "2022-12-31" if not isinstance(comparison_period.get('end'), date) else comparison_period['end'].isoformat()
    
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
        logger.info("Using YTD query format as basis for weekly analysis")
        
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
            
            # Keep the actual date instead of transforming to week
            date_select = f"{date_field_match} as actual_date"
            
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
            group_by_clause = "GROUP BY actual_date, period_type"
            if group_by_fields:
                group_by_clause += ", " + ", ".join(group_by_fields)
                
            transformed_query = f"""
            SELECT 
                {date_select},
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
            ORDER BY actual_date
            """
            
            return transformed_query
        else:
            # If we can't parse the YTD query, fall back to the regular transform
            logger.warning("Could not extract components from YTD query, falling back to standard transformation")

    # Try to extract the FROM clause
    from_match = re.search(r'FROM\s+(.*?)(?:WHERE|GROUP BY|ORDER BY|LIMIT|$)', modified_query, re.IGNORECASE | re.DOTALL)
    
    # If FROM clause not found, try to infer it from the query
    if not from_match:
        # Check if there's a table name after SELECT
        table_match = re.search(r'SELECT.*?FROM\s+([^\s,]+)', modified_query, re.IGNORECASE)
        if table_match:
            from_clause = table_match.group(1).strip()
        else:
            logger.warning("Could not extract FROM clause from query, using modified query with replaced placeholders")
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
        
        # Add district filter if specified and we are not processing multiple districts
        if district is not None and isinstance(district, int) and district > 0 and 'supervisor_district' in modified_query:
            where_clause = where_clause.rstrip() + f" AND supervisor_district = '{district}'\n"
            logger.info(f"Added district filter to WHERE clause: supervisor_district = '{district}'")
    else:
        # Create a new WHERE clause with just date filters for both periods
        where_clause = f"""
        WHERE (
            ({date_field} >= '{comparison_start}' AND {date_field} <= '{comparison_end}')
            OR 
            ({date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}')
        )
        """
        
        # Add district filter if specified and we are not processing multiple districts
        if district is not None and isinstance(district, int) and district > 0 and 'supervisor_district' in modified_query:
            where_clause = where_clause.rstrip() + f" AND supervisor_district = '{district}'\n"
            logger.info(f"Added district filter to new WHERE clause: supervisor_district = '{district}'")
    
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
        
        # Keep the actual date instead of transforming to week
        date_select = f"{date_field} as actual_date"

        # Build the GROUP BY clause with category fields (if any)
        group_by = "GROUP BY actual_date"
        for field_name in category_fields_list:
            group_by += f", {field_name}"
        
        # Add period_type to distinguish recent from comparison
        period_type_select = f", CASE WHEN {date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}' THEN 'recent' ELSE 'comparison' END as period_type"
        
        # Build the complete transformed query with daily data
        transformed_query = f"""
        SELECT 
            {date_select},
            COUNT(*) as value
            {period_type_select}
            {category_select}
        FROM {from_clause}
        {where_clause}
        {group_by}, period_type
        ORDER BY actual_date
        """
        
        return transformed_query
    else:
        # If we couldn't extract or infer the FROM clause, return the modified query
        logger.warning("Could not determine FROM clause, using modified query with replaced placeholders")
        return modified_query

def process_weekly_analysis(metric_info, process_districts=False):
    """
    Process weekly analysis for a given metric
    
    Args:
        metric_info (dict): Metric information including query data
        process_districts (bool): Whether to process district-level data
        
    Returns:
        dict: Analysis results
    """
    # Extract query data
    if not metric_info:
        logger.error("No metric info provided")
        return None
    
    # Get the query name from metric_info, checking both 'name' and 'query_name' fields
    query_name = metric_info.get('name', metric_info.get('query_name', 'Unknown Metric'))
    endpoint = metric_info.get('endpoint', '')
    district_field = metric_info.get('district_field', 'supervisor_district')
    
    # Initialize the context variables for storing data
    context_variables = {}
    
    # Define default metrics if needed
    if 'category_fields' in metric_info:
        category_fields = metric_info['category_fields']
    else:
        # Default category fields for analysis
        category_fields = DEFAULT_METRICS
    
    # Get date ranges for recent and comparison periods
    recent_period, comparison_period = get_weekly_time_ranges()
    
    # Extract query data based on the structure
    if 'query_data' in metric_info and isinstance(metric_info['query_data'], dict):
        # Try to find YTD query first as it's usually better for weekly analysis
        if 'ytd_query' in metric_info['query_data']:
            original_query = metric_info['query_data'].get('ytd_query', '')
            logger.info(f"Using YTD query as the basis for weekly analysis")
        # Also check for queries dictionary that might contain a YTD query
        elif 'queries' in metric_info['query_data'] and isinstance(metric_info['query_data']['queries'], dict):
            if 'ytd_query' in metric_info['query_data']['queries']:
                original_query = metric_info['query_data']['queries'].get('ytd_query', '')
                logger.info(f"Using YTD query from queries dictionary")
            elif 'executed_ytd_query' in metric_info['query_data']['queries']:
                original_query = metric_info['query_data']['queries'].get('executed_ytd_query', '')
                logger.info(f"Using executed YTD query from queries dictionary")
        
        # If no YTD query is found, fall back to the metric query
        if not original_query:
            original_query = metric_info['query_data'].get('metric_query', '')
            logger.info(f"No YTD query found, using regular metric query")
    else:
        original_query = metric_info.get('query_data', '')
        logger.info(f"Using provided query data directly")
    
    if not original_query:
        logger.error(f"No query found for {query_name}")
        return None
    
    logger.info(f"Original query: {original_query}")
    
    # Check if the query uses AVG() aggregation
    uses_avg = detect_avg_aggregation(original_query)
    logger.info(f"Query uses AVG() aggregation: {uses_avg}")
    
    # Define value_field
    value_field = 'value'  # Default
    
    # Handle category fields
    if not category_fields:
        category_fields = []
        logger.info("No category fields defined for this metric. Not using any default fields.")
    
    # Check if supervisor_district exists in category_fields
    has_district = False
    for field in category_fields:
        if isinstance(field, dict) and field.get('fieldName') == district_field:
            has_district = True
            break
        elif field == district_field:
            has_district = True
            break
    
    # If processing districts and supervisor_district not in category_fields, add it
    if process_districts and not has_district:
        # Add supervisor_district as a category field
        category_fields.append('supervisor_district')
        logger.info("Added supervisor_district to category fields for district processing")
        has_district = True
    
    # Determine the date field to use from the query
    date_field = extract_date_field_from_query(original_query)
    if not date_field:
        logger.warning(f"No date field found in query for {query_name}")
        date_field = 'date'  # Default to 'date'
    
    logger.info(f"Using date field: {date_field}")
    
    # Transform the query for weekly analysis
    transformed_query = transform_query_for_weekly(
        original_query=original_query,
        date_field=date_field,
        category_fields=category_fields,
        recent_period=recent_period,
        comparison_period=comparison_period,
        district=None  # We'll handle district filtering later
    )
    
    logger.info(f"Transformed query: {transformed_query}")
    
    # Log the set_dataset call details
    logger.info(f"Calling set_dataset with endpoint: {endpoint}")
    
    # Set the dataset using the endpoint and transformed query
    result = set_dataset(context_variables=context_variables, endpoint=endpoint, query=transformed_query)
    
    if 'error' in result:
        logger.error(f"Error setting dataset for {query_name}: {result['error']}")
        # If error contains "no-such-column: supervisor_district", we can proceed without filtering by district
        if 'supervisor_district' in str(result.get('error', '')).lower() and 'no-such-column' in str(result.get('error', '')).lower():
            logger.warning(f"Supervisor district field not found in dataset. Proceeding without district filtering.")
            # Try again with a modified query without the supervisor_district field
            # Remove supervisor_district from category fields
            cleaned_category_fields = [field for field in category_fields if (isinstance(field, str) and field != 'supervisor_district') or 
                                      (isinstance(field, dict) and field.get('fieldName') != 'supervisor_district')]
            
            # Transform the query again without supervisor_district
            transformed_query = transform_query_for_weekly(
                original_query=original_query,
                date_field=date_field,
                category_fields=cleaned_category_fields,
                recent_period=recent_period,
                comparison_period=comparison_period,
                district=None
            )
            
            logger.info(f"Retrying with modified query without supervisor_district: {transformed_query}")
            result = set_dataset(context_variables=context_variables, endpoint=endpoint, query=transformed_query)
            
            if 'error' in result:
                logger.error(f"Error setting dataset (second attempt) for {query_name}: {result['error']}")
                return None
        else:
            return None
    
    # Get the dataset from context_variables
    if 'dataset' not in context_variables:
        logger.error(f"No dataset found in context for {query_name}")
        return None
    
    dataset = context_variables['dataset']
    
    # Log available columns in dataset
    logger.info(f"Available columns in dataset: {dataset.columns.tolist()}")
    
    # Check if supervisor_district is actually in the dataset columns
    dataset_columns = [col.lower() for col in dataset.columns.tolist()]
    if 'supervisor_district' not in dataset_columns and district_field.lower() not in dataset_columns:
        logger.warning(f"supervisor_district field not found in dataset columns. Removing it from category fields.")
        # Remove supervisor_district from category fields
        category_fields = [field for field in category_fields if (isinstance(field, str) and field.lower() != 'supervisor_district') or 
                          (isinstance(field, dict) and field.get('fieldName', '').lower() != 'supervisor_district')]
        # Set process_districts to False since we don't have district information
        process_districts = False
        has_district = False
    
    # Create or update value field if needed
    if value_field not in dataset.columns:
        if 'this_year' in dataset.columns:
            # Use this_year as the value field
            dataset[value_field] = dataset['this_year']
            logger.info(f"Created {value_field} from this_year column")
        elif dataset.select_dtypes(include=['number']).columns.tolist():
            # Use the first numeric column as the value field
            numeric_cols = [col for col in dataset.columns if 
                            col not in ['actual_date', 'date', 'period_type', 'day', 'week'] and
                            pd.api.types.is_numeric_dtype(dataset[col])]
            if numeric_cols:
                dataset[value_field] = dataset[numeric_cols[0]]
                logger.info(f"Created {value_field} from {numeric_cols[0]} column")
            else:
                # If no suitable numeric column, use 1 as the value
                dataset[value_field] = 1
                logger.info(f"Created {value_field} with default value 1")
        else:
            # If no numeric columns, use 1 as the value
            dataset[value_field] = 1
            logger.info(f"Created {value_field} with default value 1")
    
    # Make sure actual_date field exists (our time series field)
    time_field = 'actual_date'
    if time_field not in dataset.columns:
        if 'day' in dataset.columns:
            # If the query returned 'day' instead of 'actual_date', use that
            time_field = 'day'
            logger.info(f"Using 'day' field instead of 'actual_date'")
        elif 'max_date' in dataset.columns:
            try:
                # Convert max_date to datetime if it's not already
                dataset['max_date'] = pd.to_datetime(dataset['max_date'])
                
                # Calculate start of week (Sunday)
                dataset[time_field] = dataset['max_date']
                logger.info(f"Created '{time_field}' field from max_date column")
            except Exception as e:
                logger.error(f"Error creating '{time_field}' field from max_date: {e}")
                # Use current date's week start
                today = datetime.now()
                dataset[time_field] = today
                logger.info(f"Created '{time_field}' field with current week start")
        else:
            # Try to create actual_date field from any date-like column
            date_columns = [col for col in dataset.columns if 'date' in col.lower() and col != 'period_type']
            if date_columns:
                try:
                    date_col = date_columns[0]
                    dataset[time_field] = pd.to_datetime(dataset[date_col])
                    logger.info(f"Created '{time_field}' field from {date_col} column")
                except Exception as e:
                    logger.error(f"Error creating '{time_field}' field from {date_col}: {e}")
                    # Use current date's week start
                    today = datetime.now()
                    dataset[time_field] = today
                    logger.info(f"Created '{time_field}' field with current week start")
            else:
                # Use current date
                today = datetime.now()
                dataset[time_field] = today
                logger.info(f"No date columns found. Created '{time_field}' field with current date")
    
    # Ensure time field is in datetime format for processing
    try:
        dataset[time_field] = pd.to_datetime(dataset[time_field])
        logger.info(f"Converted {time_field} to datetime format for processing")
    except Exception as e:
        logger.error(f"Error converting {time_field} to datetime: {e}")
    
    # Post-process daily data into weekly data
    logger.info("Processing daily data into weekly aggregations")
    try:
        # Create a first_day_of_year column for reference
        dataset['first_day_of_year'] = dataset[time_field].dt.year.apply(lambda x: pd.Timestamp(year=x, month=1, day=1))
        
        # Create week number relative to start of year
        dataset['week_number'] = ((dataset[time_field] - dataset['first_day_of_year']).dt.days / 7).astype(int) + 1
        
        # Create a week_id column (YYYY-WXX format)
        dataset['week_id'] = dataset[time_field].dt.strftime('%Y-W%W')
        
        # Create a week_starting_date column with the first day of each week (Sunday)
        dataset['week_starting_date'] = dataset[time_field].dt.to_period('W-SUN').dt.start_time
        
        # Group by week and other relevant columns
        group_cols = ['week_id', 'week_starting_date', 'period_type']
        
        # Add category fields to groupby if they exist in the dataset
        for field in category_fields:
            if isinstance(field, dict):
                field_name = field.get('fieldName', '')
            else:
                field_name = field
                
            if field_name and field_name in dataset.columns:
                group_cols.append(field_name)
        
        # Perform the aggregation
        logger.info(f"Aggregating data by week using columns: {group_cols}")
        
        # Define aggregation based on whether we're using AVG or not
        if uses_avg:
            # For AVG, we need to take the mean
            weekly_dataset = dataset.groupby(group_cols).agg({value_field: 'mean'}).reset_index()
        else:
            # For COUNT and SUM, we sum the values
            weekly_dataset = dataset.groupby(group_cols).agg({value_field: 'sum'}).reset_index()
        
        # Replace the daily dataset with the weekly aggregated one
        logger.info(f"Original dataset shape: {dataset.shape}, Weekly aggregated shape: {weekly_dataset.shape}")
        context_variables['dataset'] = weekly_dataset
        dataset = weekly_dataset
        
        # Use week_starting_date as the time field for charting and analysis
        time_field = 'week_starting_date'
        
        context_variables['dataset'] = dataset
        
        logger.info("Successfully aggregated daily data into weekly format")
    except Exception as e:
        logger.error(f"Error during weekly aggregation: {str(e)}")
        logger.error(traceback.format_exc())
        # Continue with the daily data if aggregation fails
        logger.warning("Continuing with daily data due to aggregation failure")
    
    # Define aggregation functions based on whether the query uses AVG()
    agg_functions = {value_field: 'mean'} if uses_avg else {value_field: 'sum'}
    logger.info(f"Using '{list(agg_functions.values())[0]}' aggregation for field {value_field}")
    
    # Validate category fields - ensure they exist in the dataset
    valid_category_fields = []
    for field in category_fields:
        if isinstance(field, dict):
            field_name = field.get('fieldName', '')
        else:
            field_name = field
            
        if field_name and field_name in dataset.columns:
            valid_category_fields.append(field)
            logger.info(f"Validated category field: {field_name}")
        else:
            logger.warning(f"Category field {field_name} not found in dataset. Ignoring.")
    
    # Update category_fields with only valid fields
    category_fields = valid_category_fields
    
    # Initialize lists to store HTML contents for embedding in the final output
    all_html_contents = []
    
    if time_field in dataset.columns:
        try:
            if pd.api.types.is_datetime64_dtype(dataset[time_field]) or isinstance(dataset[time_field].iloc[0], (datetime, pd.Timestamp)):
                # If it's already a datetime, keep it that way
                pass
            else:
                # Otherwise try to convert to datetime
                dataset[time_field] = pd.to_datetime(dataset[time_field])
        except Exception as e:
            logger.error(f"Error standardizing {time_field} field format: {e}")
    
    # Set up filter conditions for date filtering (using the actual time field)
    filter_conditions = [
        {
            'field': time_field,
            'operator': '>=',
            'value': comparison_period['start'].isoformat(),
            'is_date': True
        },
        {
            'field': time_field,
            'operator': '<=', 
            'value': recent_period['end'].isoformat(),
            'is_date': True
        }
    ]
    
    # Generate main time series chart
    logger.info(f"Generating main time series chart for {query_name}")
    main_chart_title = f'{query_name} <br> Weekly Trend'
    
    try:
        # Prepare context for the chart
        context_variables['y_axis_label'] = query_name
        context_variables['chart_title'] = main_chart_title
        context_variables['noun'] = query_name
        
        # Generate the time series chart
        chart_result = generate_time_series_chart(
            context_variables=context_variables,
            time_series_field=time_field,
            numeric_fields=value_field,
            aggregation_period='month',
            filter_conditions=filter_conditions,
            agg_functions=agg_functions
        )
        
        logger.info(f"Successfully generated main time series chart for {query_name}")
        
        # Append chart HTML to content lists if available
        if chart_result:
            if isinstance(chart_result, dict) and 'chart_html' in chart_result:
                all_html_contents.append(str(chart_result['chart_html']))
            else:
                all_html_contents.append(str(chart_result))
        else:
            logger.warning(f"No main chart result returned for {query_name}")
    except Exception as e:
        logger.error(f"Error generating main time series chart for {query_name}: {str(e)}")
        logger.error(traceback.format_exc())
    
    # Process each category field for charts
    charts = {}
    for field in category_fields:
        # Skip supervisor_district field if process_districts is already handling it
        if (isinstance(field, str) and field.lower() == 'supervisor_district') or \
           (isinstance(field, dict) and field.get('fieldName', '').lower() == 'supervisor_district'):
            if process_districts:
                logger.info(f"Skipping individual processing for supervisor_district field as it will be handled in district processing")
                continue
        
        field_name = field if isinstance(field, str) else field.get('fieldName', '')
        if not field_name or field_name.lower() not in [col.lower() for col in dataset.columns]:
            logger.warning(f"Field {field_name} not found in dataset columns. Skipping.")
            continue
        
        logger.info(f"Processing category field: {field_name} for {query_name}")
        
        try:
            # Generate chart for this category field
            context_variables['chart_title'] = f"{query_name} <br> {value_field} by Week by {field_name}"
            
            cat_chart_result = generate_time_series_chart(
                context_variables=context_variables,
                time_series_field=time_field,
                numeric_fields=value_field,
                aggregation_period='month',
                filter_conditions=filter_conditions,
                agg_functions=agg_functions,
                group_field=field_name
            )
            
            logger.info(f"Successfully generated chart for {field_name} for {query_name}")
            
            # Append chart result to content lists
            if cat_chart_result:
                if isinstance(cat_chart_result, dict) and 'chart_html' in cat_chart_result:
                    all_html_contents.append(str(cat_chart_result['chart_html']))
                else:
                    all_html_contents.append(str(cat_chart_result))
            else:
                logger.warning(f"No chart result returned for {field_name} for {query_name}")
        except Exception as e:
            logger.error(f"Error generating chart for {field_name} for {query_name}: {str(e)}")
            logger.error(traceback.format_exc())
        
        try:
            # Run anomaly detection
            anomaly_results = anomaly_detection(
                df=dataset,
                value_field=value_field,
                category_field=field_name,
                date_field=time_field,
                recent_period=recent_period,
                comparison_period=comparison_period,
                agg_function=list(agg_functions.values())[0]
            )
            
            # Add anomaly text to the content
            if anomaly_results and 'html_content' in anomaly_results:
                # Convert HTML content to Markdown instead of using raw HTML
                html_content = anomaly_results['html_content']
                
                # Create a markdown version of the anomaly content
                markdown_content = f"### Anomalies by {field_name}\n\n"
                markdown_content += f"Recent Period: {recent_period['start']} to {recent_period['end']}\n\n"
                markdown_content += f"Comparison Period: {comparison_period['start']} to {comparison_period['end']}\n\n"
                
                # If we have formatted anomalies, create a markdown table
                if 'anomalies' in anomaly_results and anomaly_results['anomalies']:
                    # Add markdown table header
                    markdown_content += f"| {field_name} | Recent Period | Comparison Period | Change | % Change |\n"
                    markdown_content += "|" + "---|" * 5 + "\n"
                    
                    # Add rows for each anomaly
                    for anomaly in anomaly_results['anomalies']:
                        markdown_content += f"| {anomaly['category']} | {anomaly['recent']:.1f} | {anomaly['comparison']:.1f} | {anomaly['abs_change']:.1f} | {anomaly['pct_change']:.1f}% |\n"
                else:
                    markdown_content += "No significant anomalies detected.\n"
                
                # Add the markdown content instead of HTML
                all_html_contents.append(markdown_content)
        except Exception as e:
            logger.error(f"Error detecting anomalies for {field_name} for {query_name}: {str(e)}")
            logger.error(traceback.format_exc())
    
    # Process district-level data if requested and if supervisor_district field exists
    if process_districts and has_district:
        # Check that supervisor_district column actually exists before proceeding
        district_col = None
        for col in dataset.columns:
            if col.lower() == 'supervisor_district' or col.lower() == district_field.lower():
                district_col = col
                break
                
        if not district_col:
            logger.warning(f"Cannot process districts: supervisor_district or {district_field} column not found in dataset")
        else:
            logger.info(f"Processing district-level data using column: {district_col}")
            
            # Get all unique district values
            districts = dataset[district_col].unique()
            logger.info(f"Found {len(districts)} unique districts to process: {districts}")
            
            # Process each district separately
            for district in districts:
                if district is None or (isinstance(district, str) and district.strip() == ''):
                    logger.info(f"Skipping empty/null district value")
                    continue
                    
                logger.info(f"Processing data for district: {district}")
                
                try:
                    # Filter data for this district
                    district_dataset = dataset[dataset[district_col] == district].copy()
                    
                    if district_dataset.empty:
                        logger.warning(f"No data for district {district} - skipping")
                        continue
                        
                    # Create a separate context with just this district's data
                    district_context = context_variables.copy()
                    district_context['dataset'] = district_dataset
                    district_context['chart_title'] = f"{query_name} - District {district}"
                    
                    # Create a district-specific HTML content list
                    district_html_contents = []
                    
                    # Generate chart for this district
                    district_chart_result = generate_time_series_chart(
                        context_variables=district_context,
                        time_series_field=time_field,
                        numeric_fields=value_field,
                        aggregation_period='month',
                        filter_conditions=filter_conditions,
                        agg_functions=agg_functions
                    )
                    
                    logger.info(f"Successfully generated chart for district {district}")
                    
                    # Save district-specific analysis
                    if district_chart_result:
                        # Convert district to integer if possible for file naming
                        try:
                            district_num = int(district)
                        except (ValueError, TypeError):
                            district_num = str(district).replace(' ', '_')
                            
                        # Add district chart to district content list
                        district_html_contents.append(f"## District {district}\n\n")
                        if isinstance(district_chart_result, dict) and 'chart_html' in district_chart_result:
                            district_html_contents.append(str(district_chart_result['chart_html']))
                        else:
                            district_html_contents.append(str(district_chart_result))
                            
                        # Try to detect anomalies for this district
                        try:
                            district_anomalies = anomaly_detection(
                                df=district_dataset,
                                value_field=value_field,
                                category_field=None,  # No need for category field since we're already filtering by district
                                date_field=time_field,
                                recent_period=recent_period,
                                comparison_period=comparison_period
                            )
                            
                            if district_anomalies:
                                # Convert HTML to markdown for district anomalies too
                                district_html_contents.append(f"### Anomalies for District {district}\n\n")
                                
                                # If we have HTML content, convert it to markdown
                                if 'html_content' in district_anomalies:
                                    # Create markdown version
                                    markdown_content = f"Recent Period: {recent_period['start']} to {recent_period['end']}\n\n"
                                    markdown_content += f"Comparison Period: {comparison_period['start']} to {comparison_period['end']}\n\n"
                                    
                                    # If we have formatted anomalies, create a markdown table
                                    if 'anomalies' in district_anomalies and district_anomalies['anomalies']:
                                        # Add markdown table header
                                        if district_anomalies['anomalies'][0].get('category'):
                                            # With category field
                                            category_field = next(key for key in district_anomalies['anomalies'][0].keys() if key == 'category')
                                            markdown_content += f"| {category_field} | Recent Period | Comparison Period | Change | % Change |\n"
                                            markdown_content += "|" + "---|" * 5 + "\n"
                                            
                                            # Add rows for each anomaly
                                            for anomaly in district_anomalies['anomalies']:
                                                markdown_content += f"| {anomaly['category']} | {anomaly['recent']:.1f} | {anomaly['comparison']:.1f} | {anomaly['abs_change']:.1f} | {anomaly['pct_change']:.1f}% |\n"
                                        else:
                                            # Without category field
                                            markdown_content += "| Metric | Recent Period | Comparison Period | Change | % Change |\n"
                                            markdown_content += "|" + "---|" * 5 + "\n"
                                            
                                            # Add one row for the district level
                                            anomaly = district_anomalies['anomalies'][0]
                                            markdown_content += f"| District {district} | {anomaly['recent']:.1f} | {anomaly['comparison']:.1f} | {anomaly['abs_change']:.1f} | {anomaly['pct_change']:.1f}% |\n"
                                    else:
                                        markdown_content += "No significant anomalies detected for this district.\n"
                                    
                                    district_html_contents.append(markdown_content)
                                else:
                                    district_html_contents.append("No significant anomalies detected for this district.\n")
                        except Exception as e:
                            logger.error(f"Error detecting anomalies for district {district}: {str(e)}")
                            
                        # Create district-specific result and save it
                        district_content = "\n\n".join(district_html_contents)
                        district_result = {
                            'metric_id': metric_info.get('id', ''),
                            'name': f"{query_name} - District {district}",
                            'content': district_content,
                            'html_contents': district_html_contents,
                            'date_range': f"{recent_period['start']} to {recent_period['end']}"
                        }
                        
                        # Get numeric metric ID
                        numeric_id = metric_info.get('numeric_id') or metric_info.get('id')
                        if not numeric_id and 'metric_id' in metric_info:
                            numeric_id = metric_info['metric_id']
                        
                        # Save the district-specific result to files
                        try:
                            save_weekly_analysis(district_result, numeric_id, district=district_num)
                            logger.info(f"Saved district-specific analysis for district {district}")
                        except Exception as e:
                            logger.error(f"Error saving district-specific analysis for district {district}: {str(e)}")
                            logger.error(traceback.format_exc())
                            
                        # Add a reference to this district's analysis in the main content
                        numeric_id = metric_info.get('numeric_id') or metric_info.get('id') 
                        if not numeric_id and 'metric_id' in metric_info:
                            numeric_id = metric_info['metric_id']
                            
                        if not numeric_id or (isinstance(numeric_id, str) and numeric_id.strip() == ''):
                            # Use sanitized query name if no ID available
                            metric_file_id = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
                        else:
                            metric_file_id = numeric_id
                            
                        all_html_contents.append(f"## [District {district} Analysis](/{district_num}/{metric_file_id}.md)\n\n")
                    else:
                        logger.warning(f"No chart result returned for district {district}")
                        
                except Exception as e:
                    logger.error(f"Error processing district {district}: {str(e)}")
                    logger.error(traceback.format_exc())
    
    # Combine all markdown content
    all_content = "\n\n".join(all_html_contents)
    
    # Return the result object
    return {
        'metric_id': metric_info.get('id', ''),
        'name': query_name,
        'content': all_content,
        'html_contents': all_html_contents,
        'date_range': f"{recent_period['start']} to {recent_period['end']}"
    }

def save_weekly_analysis(result, metric_id, district=None):
    """Save weekly analysis results to markdown files following the directory structure used
    in generate_metric_analysis.py."""
    # Create output directory if it doesn't exist
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    
    # Use the numeric metric_id if available
    # First try from the input parameter
    file_metric_id = metric_id
    
    # Ensure we have a valid metric_id
    if not file_metric_id or (isinstance(file_metric_id, str) and file_metric_id.strip() == ''):
        # Try to get it from the result object
        file_metric_id = result.get('metric_id', '')
        
        if not file_metric_id or (isinstance(file_metric_id, str) and file_metric_id.strip() == ''):
            # As a last resort, generate a sanitized metric ID from the query name
            query_name = result.get('name', '')
            if query_name:
                file_metric_id = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
            else:
                file_metric_id = "unknown_metric"
            logger.warning(f"Missing metric_id for {query_name}, using sanitized query name: {file_metric_id}")
    
    # Create district subfolder using the district number - 0 for citywide
    if district is not None:
        district_dir = os.path.join(WEEKLY_DIR, f"{district}")
        os.makedirs(district_dir, exist_ok=True)
        output_path = district_dir
    else:
        # Default to folder 0 if no district specified (treating as citywide)
        district_dir = os.path.join(WEEKLY_DIR, "0")
        os.makedirs(district_dir, exist_ok=True)
        output_path = district_dir
    
    # Generate filename - just metric_id.md (no date needed as it will be overwritten each week)
    md_filename = f"{file_metric_id}.md"
    md_path = os.path.join(output_path, md_filename)
    
    # Also save as JSON for programmatic access
    json_filename = f"{file_metric_id}.json"
    json_path = os.path.join(output_path, json_filename)
    
    # Check for other files with similar metric names and remove them
    # This helps avoid duplicate files when names change
    query_name = result.get('name', '')
    if query_name:
        # Create a cleaned version of the name for comparison
        cleaned_name = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
        
        # Look for files matching this pattern, except our target files
        for filename in os.listdir(output_path):
            file_path = os.path.join(output_path, filename)
            # Check if it might be a previously saved version with different naming
            if (cleaned_name in filename and 
                filename != md_filename and 
                filename != json_filename and
                not filename.startswith(".")):  # Skip hidden files
                try:
                    os.remove(file_path)
                    logger.info(f"Removed old file with different naming: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not remove old file {file_path}: {str(e)}")
    
    # Log the file path being used
    logger.info(f"Saving weekly analysis to: {md_path} and {json_path}")
    
    # Get the markdown and HTML content
    markdown_content = result.get('content', '')
    html_content = result.get('html_contents', [])
    
    try:
        # Write markdown file
        with open(md_path, 'w') as f:
            f.write(markdown_content)
        logger.info(f"Successfully wrote markdown file ({len(markdown_content)} chars) to {md_path}")
    except Exception as e:
        logger.error(f"Error writing markdown file to {md_path}: {str(e)}")
    
    try:
        # Write JSON file with both markdown and HTML content
        json_data = {
            'metric_id': file_metric_id,
            'name': result.get('name', ''),
            'content': markdown_content,
            'html_contents': html_content,
            'date_range': result.get('date_range', ''),
            'generated_at': datetime.now().isoformat()
        }
        
        with open(json_path, 'w') as f:
            json.dump(json_data, f, indent=2)
        logger.info(f"Successfully wrote JSON file to {json_path}")
    except Exception as e:
        logger.error(f"Error writing JSON file to {json_path}: {str(e)}")
    
    # Get district description based on district value
    if district == 0 or district is None:
        district_info = " for Citywide"
    else:
        district_info = f" for District {district}"
        
    logger.info(f"Saved weekly analysis for {file_metric_id}{district_info} to {md_path} and {json_path}")
    
    return {
        'md_path': md_path,
        'json_path': json_path
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
        query_name = metric_info.get('name', metric_info.get('query_name', 'Unknown Metric'))
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
                # Make sure numeric metric_id is set correctly in the result
                numeric_metric_id = metric_id  # This is the ID passed to find_metric_in_queries
                if not result.get('metric_id') or result.get('metric_id') == '':
                    result['metric_id'] = numeric_metric_id
                    logger.info(f"Set numeric metric_id in result: {numeric_metric_id}")
                
                # Save the analysis results to files
                saved_paths = save_weekly_analysis(result, numeric_metric_id, district=0)
                # Add file paths to the result object
                if saved_paths:
                    result['md_path'] = saved_paths.get('md_path')
                    result['json_path'] = saved_paths.get('json_path')
                
                all_results.append(result)
                successful_metrics.append(metric_id)
                metric_end_time = datetime.now()
                duration = (metric_end_time - metric_start_time).total_seconds()
                logger.info(f"Completed weekly analysis for {metric_id} - Duration: {duration:.2f} seconds")
                logger.info(f"Analysis saved to: {result.get('md_path', 'Unknown path')}")
                
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
        query_name = result.get('name', '')
        file_path = result.get('md_path', '')
        json_path = result.get('json_path', '')
        
        logger.info(f"Adding result {i+1}/{len(results)} to newsletter: {metric_id} - {query_name}")
        
        # Add a section for this metric
        newsletter_content += f"### {query_name}\n\n"
        
        # Include links to the full analysis
        if file_path:
            md_relative_path = os.path.relpath(file_path, OUTPUT_DIR)
            newsletter_content += f"[View full analysis (Markdown)]({md_relative_path})\n\n"
            logger.info(f"Added link to markdown analysis file: {md_relative_path}")
        else:
            logger.warning(f"No markdown file path for {metric_id} - {query_name}")
            
        if json_path:
            json_relative_path = os.path.relpath(json_path, OUTPUT_DIR)
            newsletter_content += f"[View JSON data]({json_relative_path})\n\n"
            logger.info(f"Added link to JSON data file: {json_relative_path}")
        else:
            logger.warning(f"No JSON file path for {metric_id} - {query_name}")
        
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