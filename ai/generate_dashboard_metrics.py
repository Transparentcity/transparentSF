import os
import json
from datetime import datetime, date, timedelta
from tools.data_fetcher import set_dataset  # Import the set_dataset function

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

def process_ytd_metrics(queries_data, district_number=None, target_date=None):
    """Process YTD metrics for a specific district or citywide if district is None."""
    processed_metrics = {}
    
    # Get date ranges for the queries
    date_ranges = get_date_ranges(target_date)
    
    for category, data in queries_data.items():
        if isinstance(data, dict) and 'queries' in data:
            endpoint = data.get('endpoint')
            if not endpoint:
                print(f"No endpoint found for category {category}")
                continue
                
            # Ensure endpoint has .json extension
            if not endpoint.endswith('.json'):
                endpoint = f"{endpoint}.json"
            
            processed_metrics[category] = {}
            
            for metric_name, query in data['queries'].items():
                if not query:
                    print(f"No query found for metric {metric_name}")
                    continue
                    
                # Add district filter if district is specified
                if district_number:
                    district_filter = f" AND supervisor_district = '{district_number}'"
                    query = query.replace("GROUP BY", f"{district_filter} GROUP BY")
                
                try:
                    # Replace date placeholders in the query
                    query = query.replace('this_year_start', f"'{date_ranges['this_year_start']}'")
                    query = query.replace('this_year_end', f"'{date_ranges['this_year_end']}'")
                    query = query.replace('last_year_start', f"'{date_ranges['last_year_start']}'")
                    query = query.replace('last_year_end', f"'{date_ranges['last_year_end']}'")
                    
                    print(f"Executing query for {metric_name} with endpoint {endpoint}")
                    print(f"Query: {query}")
                    # Execute the query using set_dataset with empty context_variables since we've done the substitution
                    result = set_dataset(context_variables={}, endpoint=endpoint, query=query)
                    if result is not None and isinstance(result, dict) and 'data' in result:
                        # Get the first row from the data array
                        rows = result['data']
                        if rows and len(rows) > 0:
                            row = rows[0]  # Get first row
                            processed_metrics[category][metric_name] = {
                                'last_year': float(row.get('last_year', 0)),
                                'this_year': float(row.get('this_year', 0)),
                                'change': float(row.get('delta', 0)),
                                'percent_change': float(row.get('perc_diff', 0))
                            }
                except Exception as e:
                    print(f"Error executing query for {metric_name}: {str(e)}")
                    continue
    
    return processed_metrics

def get_district_metrics(metrics_data, district_number, queries_data, target_date=None):
    """Extract metrics relevant to a specific district using dashboard queries."""
    # Process YTD metrics
    return process_ytd_metrics(queries_data, district_number, target_date)

def generate_official_dashboard(officials_data, metrics_data, queries_data, output_dir, target_date=None):
    """Generate dashboard data for each official and citywide."""
    
    # Create output directory if it doesn't exist
    dashboard_dir = os.path.join(output_dir, 'dashboards')
    os.makedirs(dashboard_dir, exist_ok=True)
    
    # Get date ranges
    date_ranges = get_date_ranges(target_date)
    
    # Generate citywide dashboard first
    citywide_metrics = get_district_metrics(metrics_data, None, queries_data, target_date)
    citywide_data = {
        'scope': 'citywide',
        'date_ranges': {
            'last_year': {
                'start': date_ranges['last_year_start'],
                'end': date_ranges['last_year_end']
            },
            'this_year': {
                'start': date_ranges['this_year_start'],
                'end': date_ranges['this_year_end']
            }
        },
        'metrics': citywide_metrics,
        'generated_at': datetime.now().isoformat()
    }
    
    citywide_file = os.path.join(dashboard_dir, 'citywide_dashboard.json')
    with open(citywide_file, 'w', encoding='utf-8') as f:
        json.dump(citywide_data, f, indent=2)
    
    # Process each official's district
    for official in officials_data['officials']:
        official_id = official['id']
        district_num = official['district']
        
        # Get metrics for this district using queries
        district_metrics = get_district_metrics(metrics_data, district_num, queries_data, target_date)
        
        # Create dashboard data structure
        dashboard_data = {
            'scope': f'district-{district_num}',
            'date_ranges': {
                'last_year': {
                    'start': date_ranges['last_year_start'],
                    'end': date_ranges['last_year_end']
                },
                'this_year': {
                    'start': date_ranges['this_year_start'],
                    'end': date_ranges['this_year_end']
                }
            },
            'official': {
                'name': official['name'],
                'role': official['role'],
                'district': district_num,
                'id': official_id
            },
            'metrics': district_metrics,
            'generated_at': datetime.now().isoformat()
        }
        
        # Save to file
        output_file = os.path.join(dashboard_dir, f'{official_id}_dashboard.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(dashboard_data, f, indent=2)

def main():
    # Define paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, 'data')
    output_dir = os.path.join(script_dir, 'output')
    
    # Load input files
    officials_file = os.path.join(data_dir, 'officials.json')
    metrics_file = os.path.join(data_dir, 'metrics.json')
    queries_file = os.path.join(data_dir, 'dashboard_queries.json')
    
    officials_data = load_json_file(officials_file)
    metrics_data = load_json_file(metrics_file)
    queries_data = load_json_file(queries_file)
    
    # Generate dashboard files
    generate_official_dashboard(officials_data, metrics_data, queries_data, output_dir)
    
    print(f"Dashboard files generated in {os.path.join(output_dir, 'dashboards')}")

if __name__ == '__main__':
    main() 