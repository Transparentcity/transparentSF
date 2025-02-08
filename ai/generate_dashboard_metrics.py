import os
import json
import logging
import traceback
from datetime import datetime, date, timedelta
from tools.data_fetcher import set_dataset  # Import the set_dataset function
import pandas as pd

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
    if target_date is None:
        # Use yesterday as the default target date
        target_date = date.today() - timedelta(days=1)
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
    
    # This year's range
    this_year = target_date.year
    this_year_start = f"{this_year}-01-01"
    this_year_end = target_date.strftime('%Y-%m-%d')
    
    # Last year's range
    last_year = this_year - 1
    last_year_start = f"{last_year}-01-01"
    last_year_end = (target_date.replace(year=last_year)).strftime('%Y-%m-%d')
    
    return {
        'this_year_start': this_year_start,
        'this_year_end': this_year_end,
        'last_year_start': last_year_start,
        'last_year_end': last_year_end
    }

def process_query_for_district(query, endpoint, date_ranges, district_number=None, query_name=None):
    """Process a single query and handle district-level aggregation from the same dataset."""
    try:
        modified_query = query
        
        # Replace date placeholders in the query
        modified_query = modified_query.replace('this_year_start', f"'{date_ranges['this_year_start']}'")
        modified_query = modified_query.replace('this_year_end', f"'{date_ranges['this_year_end']}'")
        modified_query = modified_query.replace('last_year_start', f"'{date_ranges['last_year_start']}'")
        modified_query = modified_query.replace('last_year_end', f"'{date_ranges['last_year_end']}'")
        
        logger.info("Executing query")
        logger.debug(f"Query: {modified_query}")
        
        # Create context variables dictionary to store the dataset
        context_variables = {}
        
        # Execute the query once
        result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
        
        if result.get('status') == 'success' and 'dataset' in context_variables:
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
            
            return results
            
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

def generate_ytd_metrics(queries_data, output_dir, target_date=None):
    """Generate a single YTD metrics file for all districts."""
    
    # Get date ranges
    date_ranges = get_date_ranges(target_date)
    
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
            "data_as_of": date_ranges['this_year_end'],
            "next_update": (datetime.now() + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0).isoformat()
        }
    }
    
    # Process each top-level category (safety, economy, etc.)
    for top_category_name, top_category_data in queries_data.items():
        # Initialize category metrics for the top-level category
        top_category_metrics = {
            "category": top_category_name.title(),
            "metrics": []  # Changed from subcategories to metrics
        }
        
        # Process each subcategory (fire, traffic, business, etc.)
        for subcategory_name, subcategory_data in top_category_data.items():
            if isinstance(subcategory_data, dict) and 'endpoint' in subcategory_data and 'queries' in subcategory_data:
                endpoint = subcategory_data['endpoint']
                if endpoint and not endpoint.endswith('.json'):
                    endpoint = f"{endpoint}.json"
                
                # Process each query in the subcategory
                for query_name, query in subcategory_data['queries'].items():
                    # Process query once and get results for all districts if applicable
                    results = process_query_for_district(query, endpoint, date_ranges, query_name=query_name)
                    if results:
                        # Add citywide metric
                        if '0' in results:
                            metric = {
                                "name": query_name.replace(" YTD", ""),
                                "id": query_name.lower().replace(" ", "_").replace("-", "_").replace("_ytd", "") + "_ytd",
                                "lastYear": results['0']['lastYear'],
                                "thisYear": results['0']['thisYear'],
                                "lastDataDate": results['0'].get('lastDataDate')
                            }
                            top_category_metrics['metrics'].append(metric)
                        
                        # Add district metrics if they exist
                        for district_num in range(1, 12):
                            district_str = str(district_num)
                            if district_str in results:
                                metric = {
                                    "name": query_name.replace(" YTD", ""),
                                    "id": query_name.lower().replace(" ", "_").replace("-", "_").replace("_ytd", "") + "_ytd",
                                    "lastYear": results[district_str]['lastYear'],
                                    "thisYear": results[district_str]['thisYear'],
                                    "lastDataDate": results[district_str].get('lastDataDate')
                                }
                                
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
                                district_category['metrics'].append(metric)
        
        if top_category_metrics['metrics']:  # Changed from subcategories to metrics
            metrics['districts']['0']['categories'].append(top_category_metrics)
    
    # Save to output directory
    output_file = os.path.join(output_dir, 'ytd_metrics.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2)
    logger.info(f"YTD metrics file generated at {output_file}")
    
    # Save to dashboard directory
    dashboard_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dashboard')
    dashboard_file = os.path.join(dashboard_dir, 'ytd_metrics.json')
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2)
    logger.info(f"YTD metrics file copied to {dashboard_file}")
    
    # Also save a timestamped copy for historical tracking
    timestamp = datetime.now().strftime('%Y%m%d')
    history_dir = os.path.join(output_dir, 'history')
    os.makedirs(history_dir, exist_ok=True)
    history_file = os.path.join(history_dir, f'ytd_metrics_{timestamp}.json')
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2)
    logger.info(f"Historical copy saved at {history_file}")
    
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