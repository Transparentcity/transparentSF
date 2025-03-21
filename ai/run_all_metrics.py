#!/usr/bin/env python3
import os
import json
import subprocess
import time
import shutil
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='all_metrics_run.log'
)

# Path to the dashboard queries file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_QUERIES_PATH = os.path.join(SCRIPT_DIR, "data", "dashboard", "dashboard_queries_enhanced.json")
DASHBOARD_QUERIES_FALLBACK = os.path.join(SCRIPT_DIR, "data", "dashboard", "dashboard_queries.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'output')
ANNUAL_DIR = os.path.join(OUTPUT_DIR, 'annual')
MONTHLY_DIR = os.path.join(OUTPUT_DIR, 'monthly')

def clear_output_directories():
    """Clear the annual and monthly output directories before generating new metrics."""
    for dir_path in [ANNUAL_DIR, MONTHLY_DIR]:
        if os.path.exists(dir_path):
            logging.info(f"Clearing directory: {dir_path}")
            print(f"Clearing directory: {dir_path}")
            for item in os.listdir(dir_path):
                item_path = os.path.join(dir_path, item)
                if os.path.isfile(item_path):
                    os.remove(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            logging.info(f"Directory cleared: {dir_path}")
            print(f"Directory cleared: {dir_path}")
        else:
            os.makedirs(dir_path, exist_ok=True)
            logging.info(f"Created directory: {dir_path}")
            print(f"Created directory: {dir_path}")

def load_dashboard_queries():
    """Load the dashboard queries file."""
    try:
        if os.path.exists(DASHBOARD_QUERIES_PATH):
            logging.info(f"Loading enhanced dashboard queries from {DASHBOARD_QUERIES_PATH}")
            with open(DASHBOARD_QUERIES_PATH, 'r') as f:
                return json.load(f)
        else:
            logging.info(f"Loading standard dashboard queries from {DASHBOARD_QUERIES_FALLBACK}")
            with open(DASHBOARD_QUERIES_FALLBACK, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading dashboard queries: {e}")
        return None

def extract_metric_ids(dashboard_queries):
    """Extract all metric IDs from the dashboard queries."""
    metric_ids = []
    
    for top_category, subcategories in dashboard_queries.items():
        for subcategory_name, subcategory_data in subcategories.items():
            if isinstance(subcategory_data, dict) and 'queries' in subcategory_data:
                for query_name, query_data in subcategory_data['queries'].items():
                    # Check if query_data is a dict and has an 'id' field
                    if isinstance(query_data, dict) and 'id' in query_data:
                        metric_ids.append(str(query_data['id']))
    
    return metric_ids

def has_supervisor_district(query_data, dashboard_queries, metric_id):
    """Check if a metric has supervisor_district field in its query."""
    # First try to find the metric by ID
    for top_category, subcategories in dashboard_queries.items():
        for subcategory_name, subcategory_data in subcategories.items():
            if isinstance(subcategory_data, dict) and 'queries' in subcategory_data:
                for query_name, data in subcategory_data['queries'].items():
                    if isinstance(data, dict) and 'id' in data and str(data['id']) == metric_id:
                        # Check if supervisor_district is in category_fields
                        if 'category_fields' in data:
                            for field in data['category_fields']:
                                if isinstance(field, dict) and field.get('fieldName') == 'supervisor_district':
                                    return True
                                elif field == 'supervisor_district':
                                    return True
                        
                        # Also check in the query itself
                        if 'ytd_query' in data and 'supervisor_district' in data['ytd_query']:
                            return True
                        if 'metric_query' in data and 'supervisor_district' in data['metric_query']:
                            return True
    
    return False

def run_analysis(metric_id, period="both", process_districts=False):
    """
    Run the metric analysis for a specific metric ID.
    
    If process_districts is True, this will run a single analysis that includes
    supervisor_district as a category field and will automatically generate 
    district-specific reports during processing.
    """
    try:
        district_str = " with district processing" if process_districts else ""
        logging.info(f"Running analysis for metric ID: {metric_id}, period: {period}{district_str}")
        print(f"Running analysis for metric ID: {metric_id}, period: {period}{district_str}")
        
        # Use the full path to generate_metric_analysis.py
        script_path = os.path.join(SCRIPT_DIR, "generate_metric_analysis.py")
        
        # Base command
        cmd = ["python", script_path, metric_id, "--period", period]
        
        # Add districts flag if needed
        if process_districts:
            cmd.append("--process-districts")
        
        logging.info(f"Running command: {' '.join(cmd)}")
        print(f"Running command: {' '.join(cmd)}")
        
        # Run the command and capture output
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logging.info(f"Successfully completed analysis for metric ID: {metric_id}{district_str}")
            print(f"Successfully completed analysis for metric ID: {metric_id}{district_str}")
        else:
            logging.error(f"Error running analysis for metric ID: {metric_id}{district_str}")
            logging.error(f"Error output: {result.stderr}")
            print(f"Error running analysis for metric ID: {metric_id}{district_str}")
            print(f"Error output: {result.stderr}")
        
        # Add a small delay to avoid overwhelming the system
        time.sleep(1)
        
        return result.returncode == 0
    except Exception as e:
        logging.error(f"Exception running analysis for metric ID {metric_id}{district_str}: {e}")
        print(f"Exception running analysis for metric ID {metric_id}{district_str}: {e}")
        return False

def main():
    """Main function to run all metric analyses."""
    logging.info("Starting analysis for all metrics")
    
    # Clear output directories before running analyses
    clear_output_directories()
    
    # Load dashboard queries
    dashboard_queries = load_dashboard_queries()
    if not dashboard_queries:
        logging.error("Failed to load dashboard queries, exiting")
        return
    
    # Extract metric IDs
    metric_ids = extract_metric_ids(dashboard_queries)
    logging.info(f"Found {len(metric_ids)} metrics to analyze")
    print(f"Found {len(metric_ids)} metrics to analyze")
    
    # Run analysis for each metric ID
    successful = 0
    failed = 0
    
    for i, metric_id in enumerate(metric_ids):
        logging.info(f"Processing metric {i+1}/{len(metric_ids)}: {metric_id}")
        print(f"Processing metric {i+1}/{len(metric_ids)}: {metric_id}")
        
        # Check if this metric has supervisor_district field
        has_district = has_supervisor_district(None, dashboard_queries, metric_id)
        
        if has_district:
            logging.info(f"Metric {metric_id} has supervisor_district field, processing with district filtering")
            print(f"Metric {metric_id} has supervisor_district field, processing with district filtering")
            
            # Run a single analysis that handles district filtering internally
            if run_analysis(metric_id, "both", process_districts=True):
                successful += 1
                print(f"  SUCCESS: Metric {metric_id} (with district processing)")
            else:
                failed += 1
                print(f"  FAILED: Metric {metric_id} (with district processing)")
        else:
            # Run regular analysis for metrics without district
            if run_analysis(metric_id, "both"):
                successful += 1
                print(f"  SUCCESS: Metric {metric_id}")
            else:
                failed += 1
                print(f"  FAILED: Metric {metric_id}")
    
    # Log summary
    logging.info(f"Completed all analyses. Successful: {successful}, Failed: {failed}")
    print(f"Completed all analyses. Successful: {successful}, Failed: {failed}")

if __name__ == "__main__":
    main() 