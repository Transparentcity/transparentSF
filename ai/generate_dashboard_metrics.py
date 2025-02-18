import os
import json
import logging
import traceback
from datetime import datetime, date, timedelta
from tools.data_fetcher import set_dataset  # Import the set_dataset function
import pandas as pd
import re

# Create logs directory if it doesn't exist
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(logs_dir, 'dashboard_metrics.log')),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def load_json_file(file_path):
    """Load and parse a JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_date_ranges(target_date=None):
    """Calculate the date ranges for YTD comparisons."""
    # If no target_date provided, use yesterday
    if target_date is None:
        target_date = date.today() - timedelta(days=1)
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
    
    # This year's range
    this_year = target_date.year
    this_year_start = f"{this_year}-01-01"
    this_year_end = target_date.strftime('%Y-%m-%d')
    
    # Last year's range - use same day-of-year
    last_year = this_year - 1
    last_year_start = f"{last_year}-01-01"
    last_year_end = target_date.replace(year=last_year).strftime('%Y-%m-%d')
    
    logger.info(f"Date ranges: this_year={this_year_start} to {this_year_end}, last_year={last_year_start} to {last_year_end}")
    
    return {
        'this_year_start': this_year_start,
        'this_year_end': this_year_end,
        'last_year_start': last_year_start,
        'last_year_end': last_year_end
    }

def process_query_for_district(query, endpoint, date_ranges, district_number=None, query_name=None):
    """Process a single query and handle district-level aggregation from the same dataset."""
    try:
        # Get date ranges if not provided
        if not date_ranges:
            date_ranges = get_date_ranges()
            
        modified_query = query
        
        # Replace date placeholders in the query
        modified_query = modified_query.replace('this_year_start', f"'{date_ranges['this_year_start']}'")
        modified_query = modified_query.replace('this_year_end', f"'{date_ranges['this_year_end']}'")
        modified_query = modified_query.replace('last_year_start', f"'{date_ranges['last_year_start']}'")
        modified_query = modified_query.replace('last_year_end', f"'{date_ranges['last_year_end']}'")
        
        # Handle cases where the query uses direct year comparisons
        this_year = datetime.strptime(date_ranges['this_year_end'], '%Y-%m-%d').year
        last_year = this_year - 1
        
        # Define all possible date patterns we need to fix
        date_patterns = [
            # Pattern 1: >= YYYY-01-01 AND < YYYY-01-01
            (f">= '{this_year}-01-01' AND < '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND < '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND <= '{date_ranges['last_year_end']}'"),
            
            # Pattern 2: >= YYYY-01-01 AND <= YYYY-01-01
            (f">= '{this_year}-01-01' AND <= '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND <= '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND <= '{date_ranges['last_year_end']}'"),

            # Pattern 3: >= YYYY-01-01 AND date_issued < YYYY-01-01
            (f">= '{this_year}-01-01' AND date_issued < '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND date_issued <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND date_issued < '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND date_issued <= '{date_ranges['last_year_end']}'")
        ]
        
        # Apply all pattern replacements
        for pattern, replacement in date_patterns:
            modified_query = modified_query.replace(pattern, replacement)
        
        logger.info("Original query:")
        logger.info(query)
        logger.info("Modified query:")
        logger.info(modified_query)
        
        # Create context variables dictionary to store the dataset
        context_variables = {}
        
        # Execute the query once
        result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
        
        if result.get('status') == 'success' and 'dataset' in context_variables:
            # Store the queries for later use in the output
            query_info = {
                'original_query': query,
                'executed_query': modified_query
            }
            
            df = context_variables['dataset']
            logger.debug(f"Dataset shape: {df.shape}")
            logger.debug(f"Dataset columns: {df.columns.tolist()}")
            logger.debug(f"Data types: {df.dtypes}")
            
            results = {}
            
            # Check if this query contains supervisor_district
            has_district = 'supervisor_district' in df.columns
            
            # Get the max date from received_datetime or max_date
            max_date = None
            if 'received_datetime' in df.columns:
                df['received_datetime'] = pd.to_datetime(df['received_datetime'], errors='coerce')
                max_date = df['received_datetime'].max()
            elif 'max_date' in df.columns:
                df['max_date'] = pd.to_datetime(df['max_date'], errors='coerce')
                max_date = df['max_date'].max()
            
            if pd.notnull(max_date):
                max_date = max_date.strftime('%Y-%m-%d')
            
            if has_district:
                # Check if this is a response time metric
                is_response_time = query_name and 'response time' in query_name.lower()
                
                if is_response_time:
                    # For response time metrics, we expect this_year and last_year columns
                    if 'this_year' not in df.columns or 'last_year' not in df.columns:
                        logger.error(f"Required columns not found in dataset. Available columns: {df.columns.tolist()}")
                        return None
                    
                    # Convert columns to numeric
                    df['this_year'] = pd.to_numeric(df['this_year'], errors='coerce')
                    df['last_year'] = pd.to_numeric(df['last_year'], errors='coerce')
                    
                    # Calculate citywide median (district '0')
                    results['0'] = {
                        'lastYear': int(df['last_year'].mean()) if pd.notnull(df['last_year'].mean()) else 0,
                        'thisYear': int(df['this_year'].mean()) if pd.notnull(df['this_year'].mean()) else 0,
                        'lastDataDate': max_date
                    }
                    
                    # Calculate district-level medians
                    for district in range(1, 12):
                        district_df = df[df['supervisor_district'] == str(district)]
                        if not district_df.empty:
                            district_data = {
                                'lastYear': int(district_df['last_year'].mean()) if pd.notnull(district_df['last_year'].mean()) else 0,
                                'thisYear': int(district_df['this_year'].mean()) if pd.notnull(district_df['this_year'].mean()) else 0,
                                'lastDataDate': max_date
                            }
                            results[str(district)] = district_data
                else:
                    # For non-response time metrics, use the existing sum logic
                    if not df.empty:
                        # Convert columns to numeric if they exist
                        if 'last_year' in df.columns and 'this_year' in df.columns:
                            df['last_year'] = pd.to_numeric(df['last_year'], errors='coerce')
                            df['this_year'] = pd.to_numeric(df['this_year'], errors='coerce')
                            
                            results['0'] = {
                                'lastYear': int(df['last_year'].sum()),
                                'thisYear': int(df['this_year'].sum()),
                                'lastDataDate': max_date
                            }
                            
                            # Process each district's data
                            for district in range(1, 12):
                                district_df = df[df['supervisor_district'] == str(district)]
                                if not district_df.empty:
                                    district_data = {
                                        'lastYear': int(district_df['last_year'].sum()),
                                        'thisYear': int(district_df['this_year'].sum()),
                                        'lastDataDate': max_date
                                    }
                                    results[str(district)] = district_data
            else:
                # For non-district queries, just return the total from first row
                if not df.empty:
                    row = df.iloc[0].to_dict()
                    results['0'] = {
                        'lastYear': int(float(row.get('last_year', 0))),
                        'thisYear': int(float(row.get('this_year', 0))),
                        'lastDataDate': max_date
                    }
            
            return {
                'results': results,
                'queries': query_info
            }
            
        else:
            logger.error("Query failed or no data returned")
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
            logger.error(f"Query URL: {result.get('queryURL')}")
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    
    return None

def process_ytd_trend_query(query, endpoint, date_ranges=None, target_date=None, query_name=None):
    """Process a YTD trend query to get historical daily counts."""
    try:
        logger.info(f"Processing YTD trend query for {query_name}")
        
        # Get date ranges if not provided
        if not date_ranges:
            date_ranges = get_date_ranges(target_date)
        
        # Replace date placeholders with actual dates
        modified_query = query.replace("date_trunc_y(date_sub_y(current_date, 1))", f"'{date_ranges['last_year_start']}'")
        modified_query = modified_query.replace("current_date", f"'{date_ranges['this_year_end']}'")
        modified_query = modified_query.replace("last_year_start", f"'{date_ranges['last_year_start']}'")
        
        logger.info(f"Modified query: {modified_query}")
        
        # Create context variables dictionary to store the dataset
        context_variables = {}
        
        # Execute the query
        result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
        logger.info(f"Query result status: {result.get('status')}")
        
        if result.get('status') == 'success' and 'dataset' in context_variables:
            df = context_variables['dataset']
            logger.info(f"Retrieved dataset with shape: {df.shape}")
            
            # Convert date column to datetime
            df['date'] = pd.to_datetime(df['date'])
            
            # Get the last data date from the trend data
            last_data_date = df['date'].max()
            if pd.notnull(last_data_date):
                last_data_date_str = last_data_date.strftime('%Y-%m-%d')
                logger.info(f"Found last data date from trend data: {last_data_date_str}")
                
                # If this date is earlier than this_year_end, update date_ranges
                if last_data_date_str < date_ranges['this_year_end']:
                    logger.info(f"Updating date ranges to use last data date: {last_data_date_str}")
                    date_ranges['this_year_end'] = last_data_date_str
                    date_ranges['last_year_end'] = last_data_date.replace(year=last_data_date.year-1).strftime('%Y-%m-%d')
                    date_ranges['last_data_date'] = last_data_date_str
            
            # Sort by date and convert Timestamp keys to string dates
            trend_data = {
                date.strftime('%Y-%m-%d'): value 
                for date, value in df.sort_values('date').set_index('date')['value'].items()
            }
            
            logger.info(f"Processed {len(trend_data)} trend data points")
            
            return {
                'trend_data': trend_data,
                'last_updated': df['date'].max().strftime('%Y-%m-%d'),
                'original_query': query,
                'executed_query': modified_query
            }
            
        else:
            logger.error("YTD trend query failed or no data returned")
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
            logger.error(f"Query URL: {result.get('queryURL')}")
            logger.error(f"Context variables: {context_variables}")
            
    except Exception as e:
        logger.error(f"Error executing YTD trend query: {str(e)}")
        logger.error(traceback.format_exc())
    
    return None

def generate_ytd_metrics(queries_data, output_dir, target_date=None):
    """Generate a single YTD metrics file for all districts."""
    
    # Initialize the metrics structure
    metrics = {
        "districts": {
            "0": {
                "name": "Citywide",
                "categories": []
            }
        },
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_as_of": None,  # Will be updated with actual data date
            "next_update": None  # Will be updated after processing
        }
    }
    
    # Process each top-level category (safety, economy, etc.)
    for top_category_name, top_category_data in queries_data.items():
        # Initialize category metrics for the top-level category
        top_category_metrics = {
            "category": top_category_name.title(),
            "metrics": []
        }
        
        # Process each subcategory
        for subcategory_name, subcategory_data in top_category_data.items():
            if isinstance(subcategory_data, dict) and 'endpoint' in subcategory_data and 'queries' in subcategory_data:
                endpoint = subcategory_data['endpoint']
                if endpoint and not endpoint.startswith('http'):
                    endpoint = f"https://data.sfgov.org/resource/{endpoint}"
                if endpoint and not endpoint.endswith('.json'):
                    endpoint = f"{endpoint}.json"
                # Get initial date ranges using yesterday as target
                initial_date_ranges = get_date_ranges(target_date)
                
                # Now process each query in the subcategory
                for query_name, query_data in subcategory_data['queries'].items():
                    # Extract queries and metadata
                    if isinstance(query_data, str):
                        metric_query = query_data
                        ytd_query = None
                        metadata = {
                            "summary": "",
                            "definition": "",
                            "data_sf_url": "",
                            "ytd_query": ""
                        }
                    else:
                        metric_query = query_data.get('metric_query', '')
                        ytd_query = query_data.get('ytd_query', '')
                        metadata = {
                            "summary": query_data.get('summary', ''),
                            "definition": query_data.get('definition', ''),
                            "data_sf_url": query_data.get('data_sf_url', ''),
                            "ytd_query": query_data.get('ytd_query', '')
                        }
                    
                    # Create a copy of initial date ranges for this metric
                    date_ranges = initial_date_ranges.copy()
                    trend_data = None
                    
                    # Get the actual last data date first
                    context_variables = {}
                    last_date_query = "SELECT max(date_issued) as last_data_date"
                    last_date_result = set_dataset(context_variables, endpoint=endpoint, query=last_date_query)
                    
                    actual_last_date = None
                    if last_date_result.get('status') == 'success' and 'dataset' in context_variables and not context_variables['dataset'].empty:
                        actual_last_date = pd.to_datetime(context_variables['dataset']['last_data_date'].iloc[0]).date()
                        logger.info(f"Found actual last data date from data: {actual_last_date}")
                    
                    # Process trend data to get truncated date
                    if ytd_query:
                        trend_data = process_ytd_trend_query(ytd_query, endpoint, date_ranges=date_ranges, query_name=query_name)
                        if trend_data and 'last_updated' in trend_data:
                            truncated_date = datetime.strptime(trend_data['last_updated'], '%Y-%m-%d').date()
                            logger.info(f"Found truncated date from trend data: {truncated_date}")
                            
                            # If we have both actual and truncated dates, calculate the offset
                            if actual_last_date:
                                # For monthly truncation (date_trunc_ym)
                                if 'date_trunc_ym' in ytd_query:
                                    # If truncated to beginning of month, use actual last date
                                    if truncated_date.day == 1:
                                        logger.info(f"Monthly truncation detected - using actual last date: {actual_last_date}")
                                        date_ranges['this_year_end'] = actual_last_date.strftime('%Y-%m-%d')
                                        date_ranges['last_year_end'] = actual_last_date.replace(year=actual_last_date.year-1).strftime('%Y-%m-%d')
                                        date_ranges['last_data_date'] = actual_last_date.strftime('%Y-%m-%d')
                                # For daily truncation (date_trunc_y)
                                elif 'date_trunc_y' in ytd_query:
                                    # Calculate days truncated and add them back
                                    days_truncated = (actual_last_date - truncated_date).days
                                    adjusted_date = truncated_date + timedelta(days=days_truncated)
                                    logger.info(f"Daily truncation detected - adding back {days_truncated} days to {truncated_date} = {adjusted_date}")
                                    date_ranges['this_year_end'] = adjusted_date.strftime('%Y-%m-%d')
                                    date_ranges['last_year_end'] = adjusted_date.replace(year=adjusted_date.year-1).strftime('%Y-%m-%d')
                                    date_ranges['last_data_date'] = adjusted_date.strftime('%Y-%m-%d')
                            else:
                                # If we don't have actual_last_date, use the truncated date
                                logger.info(f"No actual last date found, using truncated date: {truncated_date}")
                                date_ranges['this_year_end'] = truncated_date.strftime('%Y-%m-%d')
                                date_ranges['last_year_end'] = truncated_date.replace(year=truncated_date.year-1).strftime('%Y-%m-%d')
                                date_ranges['last_data_date'] = truncated_date.strftime('%Y-%m-%d')
                    
                    # Process metric query with the adjusted date ranges
                    query_results = process_query_for_district(metric_query, endpoint, date_ranges, query_name=query_name)
                    if query_results:
                        results = query_results['results']
                        queries = query_results['queries']
                        
                        # Create metric object with metadata
                        metric_base = {
                            "name": query_name.replace(" YTD", ""),
                            "id": query_name.lower().replace(" ", "_").replace("-", "_").replace("_ytd", "") + "_ytd",
                            "metadata": metadata,
                            "queries": {
                                "metric_query": queries['original_query'],
                                "executed_query": queries['executed_query']
                            }
                        }
                        
                        # Get the last data date from metric results if available
                        metric_last_data_date = None
                        if '0' in results and results['0'].get('lastDataDate'):
                            metric_last_data_date = results['0']['lastDataDate']
                        
                        # Add trend data if it was processed
                        if trend_data:
                            metric_base["trend_data"] = trend_data["trend_data"]
                            # Use metric's last data date if available (for monthly/truncated data),
                            # otherwise use trend's last updated date
                            metric_base["trend_last_updated"] = metric_last_data_date or trend_data["last_updated"]
                            metric_base["queries"]["ytd_query"] = trend_data["original_query"]
                            metric_base["queries"]["executed_ytd_query"] = trend_data["executed_query"]
                        
                        # Update metadata with the most recent data date
                        if metrics['metadata']['data_as_of'] is None or (metric_last_data_date and metric_last_data_date > metrics['metadata']['data_as_of']):
                            metrics['metadata']['data_as_of'] = metric_last_data_date
                        
                        # Add citywide metric
                        if '0' in results:
                            citywide_metric = metric_base.copy()
                            citywide_metric.update({
                                "lastYear": results['0']['lastYear'],
                                "thisYear": results['0']['thisYear'],
                                "lastDataDate": metric_last_data_date or results['0'].get('lastDataDate')
                            })
                            top_category_metrics['metrics'].append(citywide_metric)
                        
                        # Add district metrics
                        for district_num in range(1, 12):
                            district_str = str(district_num)
                            if district_str in results:
                                district_metric = metric_base.copy()
                                district_metric.update({
                                    "lastYear": results[district_str]['lastYear'],
                                    "thisYear": results[district_str]['thisYear'],
                                    "lastDataDate": metric_last_data_date or results[district_str].get('lastDataDate')
                                })
                                
                                # Initialize district if not exists
                                if district_str not in metrics['districts']:
                                    metrics['districts'][district_str] = {
                                        "name": f"District {district_str}",
                                        "categories": []
                                    }
                                
                                # Find or create category for this district
                                district_category = next(
                                    (cat for cat in metrics['districts'][district_str]['categories'] 
                                     if cat['category'] == top_category_name.title()),
                                    None
                                )
                                if district_category is None:
                                    district_category = {
                                        "category": top_category_name.title(),
                                        "metrics": []
                                    }
                                    metrics['districts'][district_str]['categories'].append(district_category)
                                district_category['metrics'].append(district_metric)
        
        if top_category_metrics['metrics']:
            metrics['districts']['0']['categories'].append(top_category_metrics)
    
    # Calculate the next update time (either 5 AM or 11 AM)
    now = datetime.now()
    next_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
    next_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
    
    # If we're past 11 AM, set targets to next day
    if now.hour >= 11:
        next_5am += timedelta(days=1)
        next_11am += timedelta(days=1)
    # If we're past 5 AM but before 11 AM, only adjust the 5 AM target
    elif now.hour >= 5:
        next_5am += timedelta(days=1)
    
    # Find the next closest update time
    next_update = min(next_5am, next_11am)
    metrics['metadata']['next_update'] = next_update.isoformat()
    
    # Save files
    output_file = os.path.join(output_dir, 'ytd_metrics.json')
    dashboard_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dashboard')
    dashboard_file = os.path.join(dashboard_dir, 'ytd_metrics.json')
    history_dir = os.path.join(output_dir, 'history')
    os.makedirs(history_dir, exist_ok=True)
    history_file = os.path.join(history_dir, f'ytd_metrics_{datetime.now().strftime("%Y%m%d")}.json')
    
    # Write to all locations
    for file_path in [output_file, dashboard_file, history_file]:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, indent=2)
        logger.info(f"Metrics file saved to {file_path}")
    
    return metrics

def main():
    """Main function to generate YTD metrics."""
    try:
        # Define paths
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, 'data/dashboard')
        output_dir = os.path.join(script_dir, 'output')  # Changed to use the output directory
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Load queries file
        queries_file = os.path.join(data_dir, 'dashboard_queries.json')
        logger.info(f"Loading queries from {queries_file}")
        queries_data = load_json_file(queries_file)
        
        # Generate single YTD metrics file
        logger.info("Starting YTD metrics generation")
        metrics = generate_ytd_metrics(queries_data, output_dir)
        logger.info("YTD metrics generation completed successfully")
        return metrics
    except Exception as e:
        logger.error(f"Error in main function: {str(e)}")
        logger.error(traceback.format_exc())
        raise

if __name__ == '__main__':
    main() 