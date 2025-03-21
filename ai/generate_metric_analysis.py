import os
import json
import logging
import traceback
import argparse
from datetime import datetime, date, timedelta
import pandas as pd
import re
from pathlib import Path

from tools.data_fetcher import set_dataset
from tools.genChart import generate_time_series_chart
from tools.anomaly_detection import anomaly_detection
from summarize_posts import create_document_summary

# Configure logging
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Set output directory
OUTPUT_DIR = os.path.join(script_dir, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Create subdirectories for different period types
ANNUAL_DIR = os.path.join(OUTPUT_DIR, 'annual')
MONTHLY_DIR = os.path.join(OUTPUT_DIR, 'monthly')
os.makedirs(ANNUAL_DIR, exist_ok=True)
os.makedirs(MONTHLY_DIR, exist_ok=True)

# Configure root logger first
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Remove any existing handlers from root logger
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add file handler to root logger
root_file_handler = logging.FileHandler(os.path.join(logs_dir, 'metric_analysis.log'))
root_file_handler.setFormatter(formatter)
root_logger.addHandler(root_file_handler)

# Add console handler to root logger
root_console_handler = logging.StreamHandler()
root_console_handler.setFormatter(formatter)
root_logger.addHandler(root_console_handler)

# Now configure the module logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# The module logger will inherit handlers from the root logger
# so we don't need to add handlers to it

# Log a message to confirm logging is set up
logger.info("Logging configured for generate_metric_analysis.py")
root_logger.info("Root logger configured for generate_metric_analysis.py")

def load_json_file(file_path):
    """Load a JSON file and return its contents."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading JSON file {file_path}: {e}")
        return None

def get_time_ranges(period_type):
    """
    Calculate recent and comparison periods based on period type.
    
    Args:
        period_type (str): One of 'year', 'month', 'day', or 'ytd'
    
    Returns:
        tuple: (recent_period, comparison_period) each containing start and end dates
    """
    today = date.today()
    
    if period_type == 'ytd':
        # Current year from Jan 1 to yesterday
        recent_period = {
            'start': date(today.year, 1, 1),
            'end': today - timedelta(days=1)
        }
        # Same days last year
        comparison_period = {
            'start': date(today.year - 1, 1, 1),
            'end': date(today.year - 1, today.month, today.day) - timedelta(days=1)
        }
    elif period_type == 'year':
        # Use previous year as the recent period
        previous_year = today.year - 1
        recent_period = {
            'start': date(previous_year, 1, 1),
            'end': date(previous_year, 12, 31)
        }
        # Compare to 6 years before that
        earliest_comparison_year = previous_year - 6
        comparison_period = {
            'start': date(earliest_comparison_year, 1, 1),
            'end': date(previous_year - 1, 12, 31)
        }
    elif period_type == 'month':
        # Use the previous complete month
        if today.month == 1:
            recent_month = 12
            recent_year = today.year - 1
        else:
            recent_month = today.month - 1
            recent_year = today.year
            
        # Calculate last day of the month
        if recent_month == 12:
            last_day = 31
        elif recent_month in [4, 6, 9, 11]:
            last_day = 30
        elif recent_month == 2:
            # Handle leap years
            if recent_year % 4 == 0 and (recent_year % 100 != 0 or recent_year % 400 == 0):
                last_day = 29
            else:
                last_day = 28
        else:
            last_day = 31
            
        recent_period = {
            'start': date(recent_year, recent_month, 1),
            'end': date(recent_year, recent_month, last_day)
        }
        
        # Compare to previous 24 months
        comparison_start_month = recent_month
        comparison_start_year = recent_year - 2
        
        comparison_period = {
            'start': date(comparison_start_year, comparison_start_month, 1),
            'end': date(recent_year, recent_month, 1) - timedelta(days=1)
        }
    else:  # day
        # Last complete day
        yesterday = today - timedelta(days=1)
        
        recent_period = {
            'start': yesterday,
            'end': yesterday
        }
        
        # Compare to same day in previous weeks
        comparison_start = yesterday - timedelta(days=28)  # 4 weeks ago
        comparison_end = yesterday - timedelta(days=1)  # yesterday
        
        comparison_period = {
            'start': comparison_start,
            'end': comparison_end
        }
    
    return recent_period, comparison_period

def find_metric_in_queries(queries_data, metric_id):
    """Find a specific metric in the dashboard queries data structure."""
    # Try to convert metric_id to int for numeric ID matching
    numeric_id = None
    try:
        numeric_id = int(metric_id)
    except (ValueError, TypeError):
        pass
        
    for top_category_name, top_category_data in queries_data.items():
        for subcategory_name, subcategory_data in top_category_data.items():
            if isinstance(subcategory_data, dict) and 'queries' in subcategory_data:
                for query_name, query_data in subcategory_data['queries'].items():
                    # If numeric_id is available, check for numeric match first
                    if numeric_id is not None and isinstance(query_data, dict) and query_data.get('id') == numeric_id:
                        # Found a match by numeric ID
                        # Check for endpoint at query level first, then fallback to subcategory level
                        endpoint = None
                        if isinstance(query_data, dict) and 'endpoint' in query_data:
                            endpoint = query_data.get('endpoint')
                            logging.info(f"Using query-level endpoint: {endpoint}")
                        else:
                            endpoint = subcategory_data.get('endpoint', None)
                            logging.info(f"Using subcategory-level endpoint: {endpoint}")
                            
                        return {
                            'top_category': top_category_name,
                            'subcategory': subcategory_name,
                            'query_name': query_name,
                            'query_data': query_data,
                            'endpoint': endpoint,
                            'category_fields': query_data.get('category_fields', []) if isinstance(query_data, dict) else [],
                            'location_fields': query_data.get('location_fields', []) if isinstance(query_data, dict) else [],
                            'numeric_id': query_data.get('id', None) if isinstance(query_data, dict) else None,
                            'metric_id': str(metric_id)  # Ensure we always have the metric_id from the search
                        }
                        
                    # Check if this is the metric we're looking for by string ID
                    current_id = query_name.lower().replace(" ", "_").replace("-", "_").replace("_ytd", "") + "_ytd"
                    if current_id == metric_id:
                        # Check for endpoint at query level first, then fallback to subcategory level
                        endpoint = None
                        if isinstance(query_data, dict) and 'endpoint' in query_data:
                            endpoint = query_data.get('endpoint')
                            logging.info(f"Using query-level endpoint: {endpoint}")
                        else:
                            endpoint = subcategory_data.get('endpoint', None)
                            logging.info(f"Using subcategory-level endpoint: {endpoint}")
                            
                        # Return all the relevant information
                        return {
                            'top_category': top_category_name,
                            'subcategory': subcategory_name,
                            'query_name': query_name,
                            'query_data': query_data,
                            'endpoint': endpoint,
                            'category_fields': query_data.get('category_fields', []) if isinstance(query_data, dict) else [],
                            'location_fields': query_data.get('location_fields', []) if isinstance(query_data, dict) else [],
                            'numeric_id': query_data.get('id', None) if isinstance(query_data, dict) else None,
                            'metric_id': str(metric_id)  # Ensure we always have the metric_id from the search
                        }
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

def process_metric_analysis(metric_info, period_type='month', process_districts=False):
    """Process metric analysis for a given period type with optional district processing."""
    # Extract metric information
    metric_id = metric_info.get('metric_id', '')
    query_name = metric_info.get('query_name', metric_id)
    definition = metric_info.get('definition', '')
    summary = metric_info.get('summary', '')
    endpoint = metric_info.get('endpoint', '')
    data_sf_url = metric_info.get('data_sf_url', '')
    
    # Determine period description based on period_type
    period_desc = 'Monthly' if period_type == 'month' else 'Annual'
    
    # Get time ranges based on period type
    recent_period, comparison_period = get_time_ranges(period_type)
    
    # Create context variables and set the dataset
    context_variables = {}
    
    # Get the query from metric_info - USE YTD QUERY INSTEAD OF METRIC QUERY
    original_query = None
    if isinstance(metric_info.get('query_data'), dict):
        original_query = metric_info['query_data'].get('ytd_query', '')
        if not original_query:
            # Fall back to metric_query if ytd_query is not available
            original_query = metric_info['query_data'].get('metric_query', '')
    else:
        original_query = metric_info.get('query_data', '')
    
    if not original_query:
        logging.error(f"No query found for {query_name}")
        return None
    
    logging.info(f"Original query: {original_query}")
    
    # Check if the query uses AVG() aggregation
    uses_avg = detect_avg_aggregation(original_query)
    logging.info(f"Query uses AVG() aggregation: {uses_avg}")
    
    # Define value_field here, before using it in agg_functions
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
    
    # Determine the appropriate date field name based on period type and query
    date_field_name = determine_date_field_name(original_query, date_field, period_type)
    
    # Set up filter conditions for date filtering
    filter_conditions = []
    if period_type == 'year':
        # For year periods, make sure we're comparing strings with strings
        year_end = str(recent_period['end'].year)
        year_start = str(comparison_period['start'].year)
        filter_conditions = [
            {'field': date_field_name, 'operator': '<=', 'value': year_end},
            {'field': date_field_name, 'operator': '>=', 'value': year_start},
        ]
        logging.info(f"Year filter conditions: {filter_conditions}")
    else:
        # For other period types, use the date objects
        filter_conditions = [
            {'field': date_field_name, 'operator': '<=', 'value': recent_period['end']},
            {'field': date_field_name, 'operator': '>=', 'value': comparison_period['start']},
        ]
        logging.info(f"Standard filter conditions: {filter_conditions}")
    
    # Transform the query for the specified period type
    transformed_query = transform_query_for_period(
        original_query, 
        date_field, 
        category_fields, 
        period_type, 
        recent_period, 
        comparison_period
    )
    
    logging.info(f"Transformed query: {transformed_query}")
    
    # Log the set_dataset call details
    logging.info(f"Calling set_dataset with endpoint: {endpoint}")
    logging.info(f"Query being used: {transformed_query}")
    
    # Create output file base name
    output_file_base = f"{metric_id}_{period_type}_analysis"
    logging.info(f"Base output file name: {output_file_base}")
    
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
            numeric_cols = [col for col in numeric_cols if not any(date_term in col.lower() for date_term in ['year', 'month', 'day', 'date'])]
            
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
    
    # Now define agg_functions using the value_field
    if uses_avg:
        agg_functions = {value_field: 'mean'}
        logging.info(f"Using 'mean' aggregation for field {value_field} based on AVG() detection in query")
    else:
        agg_functions = {value_field: 'sum'}
        logging.info(f"Using default 'sum' aggregation for field {value_field}")
    
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
    
    # If no valid category fields, proceed without category-based analysis
    if not category_fields:
        logging.info("No valid category fields for this dataset. Proceeding with time-series analysis only.")
    
    # Create period field if it doesn't exist
    period_field = f"{period_type}_period"
    if period_field not in dataset.columns:
        if 'max_date' in dataset.columns:
            try:
                # Convert max_date to datetime
                dataset['max_date'] = pd.to_datetime(dataset['max_date'])
                
                # Create period field based on period_type
                if period_type == 'month':
                    # Format as YYYY-MM
                    dataset[period_field] = dataset['max_date'].dt.strftime('%Y-%m')
                else:  # year
                    # Format as YYYY
                    dataset[period_field] = dataset['max_date'].dt.strftime('%Y')
                
                logging.info(f"Created {period_field} from max_date column")
            except Exception as e:
                logging.error(f"Error creating {period_field} from max_date: {e}")
        else:
            # If no max_date column, use current date
            current_date = datetime.now()
            if period_type == 'month':
                period_value = current_date.strftime('%Y-%m')
            else:  # year
                period_value = current_date.strftime('%Y')
            
            dataset[period_field] = period_value
            logging.info(f"Created {period_field} with current date: {period_value}")
    
    # Update filter conditions to use period_field instead of date_field_name
    filter_conditions = [
        {'field': period_field, 'operator': '<=', 'value': recent_period['end'].isoformat(), 'is_date': False},
        {'field': period_field, 'operator': '>=', 'value': comparison_period['start'].isoformat(), 'is_date': False},
    ]
    
    # Log the updated filter conditions
    logging.info(f"Updated filter conditions: {filter_conditions}")
    
    # Create year field if it doesn't exist (needed for anomaly detection)
    if 'year' not in dataset.columns:
        if period_field in dataset.columns:
            try:
                if period_type == 'month':
                    # Extract year from month_period (format: YYYY-MM)
                    dataset['year'] = dataset[period_field].str[:4]
                else:  # year
                    # Year period is already in YYYY format
                    dataset['year'] = dataset[period_field]
                
                logging.info(f"Created 'year' field from {period_field}")
            except Exception as e:
                logging.error(f"Error creating 'year' field from {period_field}: {e}")
                # Default to current year
                dataset['year'] = datetime.now().year
                logging.info(f"Created 'year' field with current year: {datetime.now().year}")
        elif 'max_date' in dataset.columns:
            try:
                # Extract year from max_date
                dataset['year'] = pd.to_datetime(dataset['max_date']).dt.year
                logging.info("Created 'year' field from max_date column")
            except Exception as e:
                logging.error(f"Error creating 'year' field from max_date: {e}")
                # Default to current year
                dataset['year'] = datetime.now().year
                logging.info(f"Created 'year' field with current year: {datetime.now().year}")
        else:
            # Default to current year
            dataset['year'] = datetime.now().year
            logging.info(f"Created 'year' field with current year: {datetime.now().year}")
    
    # Update the dataset in context_variables
    context_variables['dataset'] = dataset
    
    # Initialize lists to store markdown and HTML content
    all_markdown_contents = []
    all_html_contents = []
    
    # First, process the overall (citywide) analysis as district 0
    process_analysis_result = process_single_analysis(
        context_variables=context_variables.copy(),
        category_fields=category_fields,
        period_type=period_type,
        period_field=period_field,
        filter_conditions=filter_conditions,
        query_name=f"{query_name} - Citywide",
        period_desc=f"{period_desc} - Citywide",
        value_field=value_field,
        recent_period=recent_period,
        comparison_period=comparison_period,
        uses_avg=uses_avg,
        agg_functions=agg_functions,
        district=0,  # Use 0 for citywide analysis
        metric_id=metric_id
    )
    
    if process_analysis_result:
        result = {
            'query_name': f"{query_name} - Citywide",
            'period_type': period_type,
            'markdown': process_analysis_result.get('markdown', ''),
            'html': process_analysis_result.get('html', ''),
            'metric_id': metric_id
        }
        
        # Save the analysis files for the citywide result (district 0)
        save_analysis_files(result, metric_id, period_type, district=0)
    
    # Process district-specific analysis if needed
    if process_districts and has_district and 'supervisor_district' in dataset.columns:
        # Get list of districts in the dataset
        districts = dataset['supervisor_district'].dropna().unique()
        logging.info(f"Found {len(districts)} districts in dataset: {districts}")
        
        # Process each district
        for district in districts:
            try:
                # Skip non-numeric or invalid districts
                try:
                    district_num = int(district)
                    if district_num < 1 or district_num > 11:
                        logging.warning(f"Skipping invalid district number: {district}")
                        continue
                except (ValueError, TypeError):
                    logging.warning(f"Skipping non-numeric district: {district}")
                    continue
                
                logging.info(f"Processing district {district}")
                
                # Create district-specific filter conditions by copying the original ones
                district_filter_conditions = filter_conditions.copy()
                district_filter_conditions.append({
                    'field': 'supervisor_district',
                    'operator': '=',
                    'value': str(district)
                })
                
                # Process analysis for this district
                district_result = process_single_analysis(
                    context_variables=context_variables.copy(),
                    category_fields=[f for f in category_fields if not (isinstance(f, dict) and f.get('fieldName') == 'supervisor_district') 
                                    and f != 'supervisor_district'],  # Remove supervisor_district from category fields
                    period_type=period_type,
                    period_field=period_field,
                    filter_conditions=district_filter_conditions,
                    query_name=f"{query_name} - District {district}",
                    period_desc=f"{period_desc} - District {district}",
                    value_field=value_field,
                    recent_period=recent_period,
                    comparison_period=comparison_period,
                    uses_avg=uses_avg,
                    agg_functions=agg_functions,
                    district=district,
                    metric_id=metric_id
                )
                
                if district_result:
                    result = {
                        'query_name': f"{query_name} - District {district}",
                        'period_type': period_type,
                        'markdown': district_result.get('markdown', ''),
                        'html': district_result.get('html', ''),
                        'metric_id': metric_id
                    }
                    
                    # Save the analysis files for this district
                    save_analysis_files(result, metric_id, period_type, district=district)
            except Exception as e:
                logging.error(f"Error processing district {district}: {e}")
                logging.error(traceback.format_exc())
    
    return process_analysis_result

def process_single_analysis(context_variables, category_fields, period_type, period_field, 
                           filter_conditions, query_name, period_desc, value_field,
                           recent_period, comparison_period, uses_avg, agg_functions, district=None, metric_id=None):
    """Process a single analysis with the given parameters."""
    # Get a copy of the dataset to avoid modifying the original
    dataset = context_variables['dataset'].copy()
    
    # Make a new context variables dictionary
    context = context_variables.copy()
    context['dataset'] = dataset
    
    # Convert recent and comparison periods to string format for anomaly detection
    string_recent_period = {
        'start': recent_period['start'].strftime('%Y-%m-%d'),
        'end': recent_period['end'].strftime('%Y-%m-%d')
    }
    
    string_comparison_period = {
        'start': comparison_period['start'].strftime('%Y-%m-%d'),
        'end': comparison_period['end'].strftime('%Y-%m-%d')
    }
    
    # Initialize lists to store markdown and HTML content
    all_markdown_contents = []
    all_html_contents = []
    
    # Generate time series chart first
    logging.info(f"Generating main time series chart for {query_name}")
    main_chart_title = f'{query_name} <br> {period_desc} Trend'
    
    # Create a separate context for the main chart
    main_context = context.copy()
    main_context['chart_title'] = main_chart_title
    main_context['noun'] = query_name
    
    try:
        # Use period_field for time series chart
        chart_result = generate_time_series_chart(
            context_variables=main_context,
            time_series_field=period_field,
            numeric_fields=value_field,
            aggregation_period=period_type,
            max_legend_items=10,
            filter_conditions=filter_conditions,
            show_average_line=True,
            agg_functions=agg_functions,
            return_html=True,
            output_dir=ANNUAL_DIR if period_type == 'year' else MONTHLY_DIR
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
        
        # Skip supervisor_district if we're doing district-specific analysis
        if district is not None and category_field_name == 'supervisor_district':
            logging.info(f"Skipping supervisor_district category for district-specific analysis")
            continue
        
        logging.info(f"Processing category field: {category_field_name} for {query_name}")
        
        try:
            # Generate time series chart for each category field
            chart_title = f"{query_name} <br> {value_field} by {period_desc} by {category_field_display}"
            category_context = context.copy()
            category_context['chart_title'] = chart_title
            
            # Generate the chart with grouping by category field
            cat_chart_result = generate_time_series_chart(
                context_variables=category_context,
                time_series_field=period_field,
                numeric_fields=value_field,
                aggregation_period=period_type,
                max_legend_items=10,
                group_field=category_field_name,
                filter_conditions=filter_conditions,
                show_average_line=False,
                agg_functions=agg_functions,
                return_html=True,
                output_dir=ANNUAL_DIR if period_type == 'year' else MONTHLY_DIR
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
                context_variables=context,
                group_field=category_field_name,
                filter_conditions=filter_conditions,
                min_diff=2,
                recent_period=string_recent_period,
                comparison_period=string_comparison_period,
                date_field=period_field,
                numeric_field=value_field,
                y_axis_label=value_field,
                title=f"{query_name} - {value_field} by {category_field_display}",
                period_type=period_type,
                agg_function='mean' if uses_avg else 'sum',
                output_dir='monthly' if period_type == 'month' else 'annual'
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
    
    # Get endpoint and data_sf_url from context if available
    endpoint = context_variables.get('endpoint', '')
    data_sf_url = context_variables.get('data_sf_url', '')
    
    # Include metric_id in the title
    metric_title = metric_id if metric_id else query_name
    
    # Combine all markdown content
    query_string = ", ".join([f"{cond['field']} {cond['operator']} {cond['value']}" for cond in filter_conditions])
    combined_markdown = f"# {metric_title} - {query_name}\n\n**Analysis Type:** {period_desc}\n\n**Filters:** {query_string}\n\n{''.join(all_markdown_contents)}"
    
    # Combine all HTML content - ensure all items are strings
    html_content_strings = []
    for content in all_html_contents:
        if isinstance(content, str):
            html_content_strings.append(content)
        elif isinstance(content, tuple):
            html_content_strings.append(content[0] if content else "")
        else:
            html_content_strings.append(str(content) if content else "")
    
    combined_html = f"<h1>{metric_title} - {query_name}</h1>\n<p><strong>Analysis Type:</strong> {period_desc}</p>\n<p><strong>Filters:</strong> {query_string}</p>\n{''.join(html_content_strings)}"
    
    # Create result dictionary
    result = {
        'query_name': query_name,
        'period_type': period_type,
        'markdown': combined_markdown,
        'html': combined_html,
        'metric_id': metric_id
    }
    
    return result

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

def determine_date_field_name(query, date_field, period_type):
    """Determine the appropriate date field name based on query and period type."""
    if 'date_trunc_ymd' in query:
        return 'day'
    elif 'date_trunc_ym' in query:
        return 'month'
    elif 'date_trunc_y' in query:
        return 'year'
    elif 'as date' in query.lower():
        return 'date'
    elif date_field in ['year', 'month', 'day'] and date_field != period_type:
        return period_type
    else:
        return date_field

def transform_query_for_period(original_query, date_field, category_fields, period_type, recent_period, comparison_period, district=None):
    """
    Transform a query for monthly or annual analysis by:
    1. Replacing date placeholders
    2. Using appropriate date ranges for recent and comparison periods
    3. Adding category fields to GROUP BY
    4. Creating appropriate period fields
    5. Adding district filter if specified
    
    Args:
        original_query (str): The original SQL query
        date_field (str): The name of the date field
        category_fields (list): List of category fields
        period_type (str): 'month' or 'year'
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
    # We make a copy to avoid modifying the original query while iterating
    modified_query = original_query
    replacements = {
        'this_year_start': f"'{recent_start}'",
        'this_year_end': f"'{recent_end}'",
        'last_year_start': f"'{comparison_start}'",
        'last_year_end': f"'{comparison_end}'",
        'start_date': f"'{comparison_start}'",
        'current_date': f"'{recent_end}'"
    }
    
    # Apply replacements correctly - ensure we're not creating malformed field names
    for placeholder, value in replacements.items():
        # Make sure we're only replacing standalone instances of the placeholder
        # by checking for word boundaries or operators before/after
        modified_query = re.sub(r'([=<>:\s]|^)' + re.escape(placeholder) + r'([=<>:\s]|$)', 
                                r'\1' + value + r'\2', 
                                modified_query)
    
    # Determine if it's a YTD query by checking format
    is_ytd_query = ('as date, COUNT(*)' in modified_query or 
                   'as date,' in modified_query or 
                   'date_trunc_ymd' in modified_query)
    
    # If it's a YTD query, we'll modify it to work with our period types
    if is_ytd_query:
        logging.info("Using YTD query format as basis")
        
        # Extract the core table and WHERE conditions from the original query
        # This pattern looks for date_trunc, field selection, conditions
        ytd_pattern = r'SELECT\s+date_trunc_[ymd]+\((.*?)\)\s+as\s+date,\s+([^W]+)WHERE\s+(.*?)(?:GROUP BY|ORDER BY|$)'
        ytd_match = re.search(ytd_pattern, modified_query, re.IGNORECASE | re.DOTALL)
        
        if ytd_match:
            date_field_match = ytd_match.group(1).strip()
            value_part = ytd_match.group(2).strip()
            where_part = ytd_match.group(3).strip()
            
            # Remove current_date references and replace with our recent_end
            where_part = re.sub(r'<=\s*current_date', f"<= '{recent_end}'", where_part)
            
            # Generate appropriate date_trunc based on period_type
            if period_type == 'year':
                date_trunc = f"date_trunc_y({date_field_match})"
                period_field = "year_period"
            else:  # month
                date_trunc = f"date_trunc_ym({date_field_match})"
                period_field = "month_period"
            
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
            group_by_clause = f"GROUP BY {period_field}, period_type"
            if group_by_fields:
                group_by_clause += ", " + ", ".join(group_by_fields)
                
            transformed_query = f"""
            SELECT 
                {date_trunc} as {period_field},
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
            ORDER BY {period_field}
            """
            
            return transformed_query
        else:
            # If we can't parse the YTD query, fall back to the modified query
            return modified_query
    
    # For non-YTD queries, check if the query already has a date_trunc function for the period
    if period_type == 'month' and 'date_trunc_ym' in modified_query:
        # Just use the modified query with replaced placeholders
        logging.info("Query already has date_trunc_ym, using original query with replaced placeholders")
        return modified_query
    elif period_type == 'year' and 'date_trunc_y' in modified_query:
        # Just use the modified query with replaced placeholders
        logging.info("Query already has date_trunc_y, using original query with replaced placeholders")
        return modified_query
    
    # Special case for the police incidents query
    if 'Report_Datetime' in modified_query and 'supervisor_district' in modified_query:
        # We know this is the police incidents query
        logging.info("Detected police incidents query format")
        
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
        
        # Build the date transformation part based on period_type
        if period_type == 'month':
            date_transform = f"date_trunc_ym(Report_Datetime) as month_period"
        else:  # year
            date_transform = f"date_trunc_y(Report_Datetime) as year_period"
        
        # Build the GROUP BY clause with category fields
        group_by = f"GROUP BY {period_type}_period"
        for field_name in category_fields_list:
            group_by += f", {field_name}"
        
        # Log the GROUP BY clause
        logging.info(f"GROUP BY clause: {group_by}")
        
        # Build the date range filter - include both recent and comparison periods
        # We need to include both periods but also add a flag to distinguish them
        date_range = f"""
        WHERE (
            (Report_Datetime >= '{comparison_start}' AND Report_Datetime <= '{comparison_end}')
            OR 
            (Report_Datetime >= '{recent_start}' AND Report_Datetime <= '{recent_end}')
        )
        """
        
        # Add district filter if specified
        if district is not None:
            date_range += f" AND supervisor_district = '{district}'"
            logging.info(f"Added district filter to query: supervisor_district = '{district}'")
        
        # Build the complete transformed query - simplified to just count records
        # Note: In Socrata API, we don't need a FROM clause
        transformed_query = f"""
        SELECT 
            {date_transform},
            COUNT(*) as value,
            CASE 
                WHEN Report_Datetime >= '{recent_start}' AND Report_Datetime <= '{recent_end}' THEN 'recent'
                ELSE 'comparison'
            END as period_type
            {category_select}
        {date_range}
        {group_by}, period_type
        ORDER BY {period_type}_period
        """
        
        return transformed_query
    
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
        
        # Build the date transformation part based on period_type
        if period_type == 'month':
            date_transform = f"date_trunc_ym({date_field}) as month_period"
        else:  # year
            date_transform = f"date_trunc_y({date_field}) as year_period"
        
        # Build the GROUP BY clause with category fields
        group_by = f"GROUP BY {period_type}_period"
        for field_name in category_fields_list:
            group_by += f", {field_name}"
        
        # Add period_type to distinguish recent from comparison
        period_type_select = f", CASE WHEN {date_field} >= '{recent_start}' AND {date_field} <= '{recent_end}' THEN 'recent' ELSE 'comparison' END as period_type"
        
        # Log the GROUP BY clause
        logging.info(f"GROUP BY clause: {group_by}, period_type")
        
        # Build the complete transformed query - simplified to just count records
        transformed_query = f"""
        SELECT 
            {date_transform},
            COUNT(*) as value
            {period_type_select}
            {category_select}
        FROM {from_clause}
        {where_clause}
        {group_by}, period_type
        ORDER BY {period_type}_period
        """
        
        return transformed_query
    else:
        # If we couldn't extract or infer the FROM clause, return the modified query
        logging.warning("Could not determine FROM clause, using modified query with replaced placeholders")
        return modified_query

def save_analysis_files(result, metric_id, period_type, output_dir=None, district=None):
    """Save analysis results to markdown and HTML files."""
    # Create output directory if it doesn't exist
    if output_dir is None:
        output_dir = OUTPUT_DIR
    
    # Create subdirectories for different period types - only use monthly and annual
    period_dirs = {
        'month': 'monthly',
        'year': 'annual'
    }
    
    for dir_name in period_dirs.values():
        os.makedirs(os.path.join(output_dir, dir_name), exist_ok=True)
    
    # Map period_type to directory name
    dir_name = period_dirs.get(period_type, 'other')
    
    # Use result's metric_id if available, otherwise fallback to the provided metric_id
    file_metric_id = result.get('metric_id', metric_id)
    
    # Ensure we have a valid metric_id
    if not file_metric_id or file_metric_id.strip() == '':
        # Generate a sanitized metric ID from the query name if no metric_id is available
        query_name = result.get('query_name', '')
        file_metric_id = query_name.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
        logging.warning(f"Missing metric_id for {query_name}, using sanitized query name: {file_metric_id}")
    
    # Handle missing query_name
    query_name = result.get('query_name', file_metric_id)
    if not query_name:
        query_name = file_metric_id
    
    # Generate filenames - district is now handled as a number (0 for citywide)
    md_filename = f"{file_metric_id}_{period_type}_analysis.md"
    html_filename = f"{file_metric_id}_{period_type}_analysis.html"
    
    # Log the filename being used
    logging.info(f"Using filename base: {file_metric_id}_{period_type}_analysis")
    
    # Create district subfolder using the district number - 0 for citywide
    if district is not None:
        district_dir = os.path.join(output_dir, dir_name, f"{district}")
        os.makedirs(district_dir, exist_ok=True)
        output_path = district_dir
    else:
        # Default to folder 0 if no district specified (treating as citywide)
        district_dir = os.path.join(output_dir, dir_name, "0")
        os.makedirs(district_dir, exist_ok=True)
        output_path = district_dir
    
    # Create full paths
    md_path = os.path.join(output_path, md_filename)
    html_path = os.path.join(output_path, html_filename)
    
    # Log the file paths being used
    logging.info(f"Saving analysis to: {md_path} and {html_path}")
    
    # Get markdown content
    md_content = result.get('markdown_content', '')
    if not md_content:
        md_content = result.get('markdown', '')
    
    # Fix chart image paths - ensure correct format for image links
    md_content = md_content.replace('![Chart]/', '![Chart](/')
    
    # Write markdown file
    with open(md_path, 'w') as f:
        f.write(md_content)
    
    # Get HTML content
    html_content = result.get('html_content', '')
    if not html_content:
        html_content = result.get('html', '')
    
    # Write HTML file
    with open(html_path, 'w') as f:
        f.write(html_content)
    
    # Get district description based on district value
    if district == 0 or district is None:
        district_info = " for Citywide"
    else:
        district_info = f" for District {district}"
        
    logging.info(f"Saved {period_type} analysis for {query_name}{district_info} to {md_path} and {html_path}")
    
    return {
        'md_path': md_path,
        'html_path': html_path
    }

def main():
    """Main function to generate metric analysis."""
    parser = argparse.ArgumentParser(description='Generate analysis for a specific metric')
    parser.add_argument('metric_id', help='ID of the metric to analyze (e.g., "arrests_presented_to_da_ytd")')
    parser.add_argument('--period', '-p', choices=['monthly', 'annual', 'both'], default='both',
                        help='Period type for analysis: monthly (24 months lookback), annual (10 years lookback), or both (default)')
    parser.add_argument('--process-districts', action='store_true', 
                        help='Process and generate separate reports for each supervisor district if available')
    args = parser.parse_args()
    
    metric_id = args.metric_id
    period_choice = args.period
    process_districts = args.process_districts
    
    # Verify metric_id is not empty
    if not metric_id or metric_id.strip() == '':
        logger.error("metric_id cannot be empty")
        return
    
    # Log the metric ID we're processing
    logger.info(f"Processing metric ID: '{metric_id}'")
    
    # Create output directories - only create monthly and annual
    for dir_name in ['monthly', 'annual']:
        os.makedirs(os.path.join(OUTPUT_DIR, dir_name), exist_ok=True)
    
    # Load dashboard queries
    dashboard_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries.json")
    enhanced_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries_enhanced.json")
    
    # Try to load enhanced queries first, fall back to regular queries
    if os.path.exists(enhanced_queries_path):
        logger.info("Using enhanced dashboard queries")
        dashboard_queries = load_json_file(enhanced_queries_path)
    else:
        logger.info("Using standard dashboard queries")
        dashboard_queries = load_json_file(dashboard_queries_path)
    
    if not dashboard_queries:
        logger.error("Failed to load dashboard queries")
        return
    
    # Find the metric in the queries
    metric_info = find_metric_in_queries(dashboard_queries, metric_id)
    if not metric_info:
        logger.error(f"Metric with ID '{metric_id}' not found in dashboard queries")
        return
    
    # Make sure metric_id is in the metric_info
    if 'metric_id' not in metric_info:
        metric_info['metric_id'] = metric_id
    
    # Log whether we're processing districts
    if process_districts:
        logger.info(f"Found metric: {metric_info['query_name']} with ID {metric_info['metric_id']} in category {metric_info['top_category']} - will process districts if available")
    else:
        logger.info(f"Found metric: {metric_info['query_name']} with ID {metric_info['metric_id']} in category {metric_info['top_category']}")
    
    # Process the metric based on the selected period type
    analysis_results = []
    
    # Monthly analysis (24 months lookback)
    if period_choice in ['monthly', 'both']:
        logger.info(f"Processing monthly analysis for metric ID: {metric_info['metric_id']}")
        monthly_analysis = process_metric_analysis(metric_info, period_type='month', process_districts=process_districts)
        if monthly_analysis:
            analysis_results.append(monthly_analysis)
    
    # Annual analysis (10 years lookback)
    if period_choice in ['annual', 'both']:
        logger.info(f"Processing annual analysis for metric ID: {metric_info['metric_id']}")
        annual_analysis = process_metric_analysis(metric_info, period_type='year', process_districts=process_districts)
        if annual_analysis:
            analysis_results.append(annual_analysis)
    
    # The save_analysis_files is now called within process_metric_analysis
    logger.info(f"Analysis complete for metric: {metric_info['metric_id']}")

if __name__ == "__main__":
    main() 