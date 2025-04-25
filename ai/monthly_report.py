import os
import json
import logging
import psycopg2
import psycopg2.extras
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
import traceback
import base64
from io import BytesIO
from PIL import Image
import time
import re

# Set up paths to look for .env file
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
possible_env_paths = [
    os.path.join(script_dir, '.env'),  # ai/.env
    os.path.join(project_root, '.env'),  # /.env (project root)
    os.path.join(os.path.expanduser('~'), '.env')  # ~/.env (home directory)
]

# Try loading .env from each location
for env_path in possible_env_paths:
    if os.path.exists(env_path):
        print(f"Loading environment variables from: {env_path}")
        load_dotenv(env_path)
        break

# Import database utilities
from tools.db_utils import get_postgres_connection, execute_with_connection, CustomJSONEncoder

# Import necessary functions from other modules
from webChat import get_dashboard_metric, anomaly_explainer_agent, swarm_client, context_variables, client, AGENT_MODEL, load_and_combine_notes
    
# Configure logging
logger = logging.getLogger(__name__)

# Only configure handlers if they haven't been configured yet
if not logger.handlers:
    # Create logs directory if it doesn't exist
    logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Set the logging level
    logger.setLevel(logging.INFO)
    
    # Create formatters
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create and configure file handler
    file_handler = logging.FileHandler(os.path.join(logs_dir, 'monthly_report.log'))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    
    # Create and configure console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # Store handlers for use with other loggers
    handlers = {
        'file': file_handler,
        'console': console_handler
    }
else:
    # Extract existing handlers if already configured
    handlers = {
        'file': next((h for h in logger.handlers if isinstance(h, logging.FileHandler)), None),
        'console': next((h for h in logger.handlers if isinstance(h, logging.StreamHandler)), None)
    }

# Configure the root logger to capture all logs
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
# Remove existing handlers to avoid duplicates
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
# Add our handlers
for handler_name, handler in handlers.items():
    if handler:
        root_logger.addHandler(handler)

# Also explicitly configure logging for the explainer agent and related modules
log_modules = [
    'explainer_agent',              # Explainer agent logs
    'swarm_client',                 # Swarm client logs
    'swarm',                        # Swarm library logs
    'anomaly_analyzer',             # Anomaly analyzer logs
    'tools.anomaly_detection',      # Tools module logs
    'ai.anomalyAnalyzer',           # AI module logs
    'tools',                        # All tools
    'webChat',                      # Web chat agent logs
    ''                              # Root logger (again to be sure)
]

for log_name in log_modules:
    module_logger = logging.getLogger(log_name)
    module_logger.setLevel(logging.INFO)
    # Remove any existing handlers to avoid duplicates
    for handler in module_logger.handlers[:]:
        module_logger.removeHandler(handler)
    # Add our handlers
    for handler_name, handler in handlers.items():
        if handler:
            module_logger.addHandler(handler)

logger.info("Monthly report logging initialized")
logger.info(f"Log file path: {os.path.join(logs_dir, 'monthly_report.log')}")

# Load environment variables
load_dotenv()

# Only log non-sensitive environment variables
non_sensitive_vars = {k: v for k, v in os.environ.items() 
                     if k.startswith('POSTGRES_') and not k.endswith('PASSWORD')}
logger.info(f"Environment variables (excluding sensitive data): {non_sensitive_vars}")

# Database connection parameters - use POSTGRES_* variables directly
DB_HOST = os.getenv("POSTGRES_HOST", 'localhost')
DB_PORT = os.getenv("POSTGRES_PORT", '5432')
DB_USER = os.getenv("POSTGRES_USER", 'postgres')
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", 'postgres')
DB_NAME = os.getenv("POSTGRES_DB", 'transparentsf')

# Log only non-sensitive connection parameters
logger.info(f"Using database connection parameters: HOST={DB_HOST}, PORT={DB_PORT}, USER={DB_USER}, DB={DB_NAME}")

# Validate and convert DB_PORT to int if it exists
try:
    DB_PORT = int(DB_PORT)
except ValueError:
    logger.error(f"Invalid POSTGRES_PORT value: {DB_PORT}. Must be an integer.")
    DB_PORT = 5432  # Default port

# API Base URL
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
logger.info(f"Using API_BASE_URL: {API_BASE_URL}")

def initialize_monthly_reporting_table():
    """
    Initialize the monthly_reporting table in PostgreSQL if it doesn't exist.
    Also creates the reports table if it doesn't exist.
    """
    logger.info("Initializing tables for monthly reporting")
    
    def init_table_operation(connection):
        """Inner function to initialize the tables within a connection."""
        cursor = connection.cursor()
        
        # First, check and create the reports table if it doesn't exist
        logger.info("Checking if reports table exists...")
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'reports'
            );
        """)
        reports_table_exists = cursor.fetchone()[0]
        
        if not reports_table_exists:
            logger.info("Creating reports table...")
            try:
                cursor.execute("""
                    CREATE TABLE reports (
                        id SERIAL PRIMARY KEY,
                        max_items INTEGER DEFAULT 10,
                        district TEXT DEFAULT '0',
                        period_type TEXT DEFAULT 'month',
                        original_filename TEXT,
                        revised_filename TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                connection.commit()
                logger.info("Reports table created successfully")
            except Exception as e:
                logger.error(f"Error creating reports table: {e}")
                connection.rollback()
                return False
        else:
            logger.info("Reports table already exists")
        
        # Now check if the monthly_reporting table exists and get its columns
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'monthly_reporting'
            ORDER BY ordinal_position
        """)
        
        existing_columns = [col[0] for col in cursor.fetchall()]
        
        # If the table exists but with a different schema, we need to handle it
        if existing_columns:
            logger.info(f"Table monthly_reporting exists with columns: {existing_columns}")
            
            # Check if we have the required columns for our new schema
            required_columns = [
                "id", "report_date", "metric_name", "group_value", 
                "group_field_name", "period_type", "comparison_mean", 
                "recent_mean", "difference", "std_dev", "percent_change", 
                "explanation", "priority", "report_text", "district", 
                "chart_data", "metadata", "created_at", "metric_id", "report_id", "item_title"
            ]
            
            missing_columns = [col for col in required_columns if col not in existing_columns]
            
            if missing_columns:
                logger.info(f"Adding missing columns to monthly_reporting table: {missing_columns}")
                
                # Add missing columns one by one
                for column in missing_columns:
                    try:
                        if column == "id":
                            # Skip id as it's likely already the primary key
                            continue
                        elif column == "report_date":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN report_date DATE DEFAULT CURRENT_DATE")
                        elif column == "metric_name":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN metric_name TEXT")
                        elif column == "group_value":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN group_value TEXT")
                        elif column == "group_field_name":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN group_field_name TEXT")
                        elif column == "period_type":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN period_type TEXT DEFAULT 'month'")
                        elif column == "comparison_mean":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN comparison_mean FLOAT")
                        elif column == "recent_mean":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN recent_mean FLOAT")
                        elif column == "difference":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN difference FLOAT")
                        elif column == "std_dev":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN std_dev FLOAT")
                        elif column == "percent_change":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN percent_change FLOAT")
                        elif column == "explanation":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN explanation TEXT")
                        elif column == "priority":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN priority INTEGER")
                        elif column == "report_text":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN report_text TEXT")
                        elif column == "district":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN district TEXT")
                        elif column == "chart_data":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN chart_data JSONB")
                        elif column == "metadata":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN metadata JSONB")
                        elif column == "created_at":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                        elif column == "metric_id":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN metric_id TEXT")
                        elif column == "report_id":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE")
                        elif column == "item_title":
                            cursor.execute("ALTER TABLE monthly_reporting ADD COLUMN item_title TEXT")

                    except Exception as e:
                        logger.warning(f"Error adding column {column}: {e}")
                        # Continue with other columns even if one fails
                
                connection.commit()
                logger.info("Added missing columns to monthly_reporting table")
            else:
                logger.info("Table monthly_reporting already has all required columns")
        else:
            # Create monthly_reporting table if it doesn't exist
            logger.info("Creating monthly_reporting table...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_reporting (
                    id SERIAL PRIMARY KEY,
                    report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
                    report_date DATE DEFAULT CURRENT_DATE,
                    item_title TEXT,
                    metric_name TEXT,
                    metric_id TEXT,
                    group_value TEXT,
                    group_field_name TEXT,
                    period_type TEXT DEFAULT 'month',
                    comparison_mean FLOAT,
                    recent_mean FLOAT,
                    difference FLOAT,
                    std_dev FLOAT,
                    percent_change FLOAT,
                    explanation TEXT,
                    priority INTEGER,
                    report_text TEXT,
                    district TEXT,
                    chart_data JSONB,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS monthly_reporting_report_date_idx ON monthly_reporting (report_date)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS monthly_reporting_district_idx ON monthly_reporting (district)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS monthly_reporting_priority_idx ON monthly_reporting (priority)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS monthly_reporting_report_id_idx ON monthly_reporting (report_id)
            """)
            
            connection.commit()
            logger.info("Monthly reporting table created successfully")
        
        cursor.close()
        return True
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=init_table_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] == "success":
        return True
    else:
        logger.error(f"Failed to initialize tables: {result['message']}")
        return False

def select_deltas_to_discuss(period_type='month', top_n=20, bottom_n=20, district=""):
    """
    Step 1: Select the top and bottom N differences to discuss.
    Uses the anomaly_analyzer API endpoint to find the most significant changes.
    
    Args:
        period_type: The time period type (month, quarter, year)
        top_n: Number of top increasing differences to select
        bottom_n: Number of top decreasing differences to select
        district: District number to analyze
        
    Returns:
        Dictionary with top and bottom differences
    """
    logger.info(f"Selecting top {top_n} and bottom {bottom_n} deltas for {period_type}ly report")
    
    try:
        # Use the top-metric-changes API endpoint to get top changes
        top_url = f"{API_BASE_URL}/anomaly-analyzer/api/top-metric-changes?period_type={period_type}&limit={top_n}&district={district}&show_both=false"
        logger.info(f"Requesting top changes from: {top_url}")
        
        top_response = requests.get(top_url)
        logger.info(f"Top changes API response status code: {top_response.status_code}")
        
        if top_response.status_code != 200:
            error_msg = f"Failed to get top changes: Status code {top_response.status_code} - {top_response.text}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        try:
            top_data = top_response.json()
            logger.info(f"Received top changes data: {json.dumps(top_data)[:200]}...")
            
            # Log more details about the returned data structure
            if isinstance(top_data, dict):
                logger.info(f"Top data keys: {list(top_data.keys())}")
                if "top_results" in top_data:
                    logger.info(f"Top results count: {len(top_data['top_results'])}")
                    if top_data['top_results'] and len(top_data['top_results']) > 0:
                        sample_item = top_data['top_results'][0]
                        logger.info(f"Sample top result item keys: {list(sample_item.keys())}")
                        logger.info(f"Sample top result item: {json.dumps(sample_item)}")
        except Exception as e:
            logger.error(f"Error parsing top changes response as JSON: {str(e)}")
            logger.error(f"Raw response content: {top_response.text[:500]}")
            raise
        
        # Use the same endpoint with negative=true for bottom changes
        bottom_url = f"{API_BASE_URL}/anomaly-analyzer/api/top-metric-changes?period_type={period_type}&limit={bottom_n}&district={district}&show_both=false&negative=true"
        logger.info(f"Requesting bottom changes from: {bottom_url}")
        
        bottom_response = requests.get(bottom_url)
        logger.info(f"Bottom changes API response status code: {bottom_response.status_code}")
        
        if bottom_response.status_code != 200:
            error_msg = f"Failed to get bottom changes: Status code {bottom_response.status_code} - {bottom_response.text}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        try:
            bottom_data = bottom_response.json()
            logger.info(f"Received bottom changes data: {json.dumps(bottom_data)[:200]}...")
            
            # Log more details about the returned data structure
            if isinstance(bottom_data, dict):
                logger.info(f"Bottom data keys: {list(bottom_data.keys())}")
                if "bottom_results" in bottom_data:
                    logger.info(f"Bottom results count: {len(bottom_data['bottom_results'])}")
                    if bottom_data['bottom_results'] and len(bottom_data['bottom_results']) > 0:
                        sample_item = bottom_data['bottom_results'][0]
                        logger.info(f"Sample bottom result item keys: {list(sample_item.keys())}")
                        logger.info(f"Sample bottom result item: {json.dumps(sample_item)}")
        except Exception as e:
            logger.error(f"Error parsing bottom changes response as JSON: {str(e)}")
            logger.error(f"Raw response content: {bottom_response.text[:500]}")
            raise
        
        # Format the results from the API to match what we need
        top_changes = []
        for item in top_data.get("top_results", []):
            try:
                # Safely handle None values
                recent_value = float(item.get("recent_value", 0)) if item.get("recent_value") is not None else 0
                previous_value = float(item.get("previous_value", 0)) if item.get("previous_value") is not None else 0
                difference = recent_value - previous_value
                
                top_changes.append({
                    "metric": item.get("object_name", "Unknown"),
                    "metric_id": item.get("object_id", "Unknown"),
                    "group": item.get("group", "All"),
                    "recent_mean": recent_value,
                    "comparison_mean": previous_value,
                    "difference_value": difference,
                    "difference": difference,
                    "district": district
                })
            except (ValueError, TypeError) as e:
                logger.error(f"Error processing top change item: {e}")
                logger.error(f"Problematic item data: {json.dumps(item)}")
                continue
        
        bottom_changes = []
        for item in bottom_data.get("bottom_results", []):
            try:
                # Safely handle None values
                recent_value = float(item.get("recent_value", 0)) if item.get("recent_value") is not None else 0
                previous_value = float(item.get("previous_value", 0)) if item.get("previous_value") is not None else 0
                difference = recent_value - previous_value
                
                bottom_changes.append({
                    "metric": item.get("object_name", "Unknown"),
                    "metric_id": item.get("object_id", "Unknown"),
                    "group": item.get("group", "All"),
                    "recent_mean": recent_value,
                    "comparison_mean": previous_value,
                    "difference_value": difference,
                    "difference": difference,
                    "district": district
                })
            except (ValueError, TypeError) as e:
                logger.error(f"Error processing bottom change item: {e}")
                logger.error(f"Problematic item data: {json.dumps(item)}")
                continue
        
        # Combine results
        all_deltas = {
            "top_changes": top_changes,
            "bottom_changes": bottom_changes,
            "status": "success",
            "period_type": period_type,
            "district": district
        }
        
        logger.info(f"Found {len(all_deltas['top_changes'])} top changes and {len(all_deltas['bottom_changes'])} bottom changes")
        return all_deltas
        
    except Exception as e:
        error_msg = f"Error in select_deltas_to_discuss: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

def prioritize_deltas(deltas, max_items=10):
    """
    Step 2: Prioritize the deltas for discussion based on their importance.
    This uses the explainer agent to determine which changes are most significant.
    
    Args:
        deltas: Dictionary with top and bottom changes from select_deltas_to_discuss
        max_items: Maximum number of items to prioritize
        
    Returns:
        List of prioritized items with explanations
    """
    from webChat import client, AGENT_MODEL, load_and_combine_notes
    
    logger.info(f"Prioritizing deltas for discussion (max {max_items} items)")
    
    try:
        if deltas.get("status") != "success":
            return {"status": "error", "message": "Invalid deltas input"}
        
        # Combine top and bottom changes
        combined_changes = deltas.get("top_changes", []) + deltas.get("bottom_changes", [])
        
        # Get combined notes with YTD metrics for reference
        notes_text = load_and_combine_notes()
        logger.info(f"Loaded {len(notes_text)} characters of combined notes for context")
        
        # Format changes for the agent to analyze
        changes_text = "Here are the metrics with significant changes:\n\n"
        for idx, change in enumerate(combined_changes, 1):
            metric = change.get("metric", "Unknown")
            metric_id = change.get("metric_id", "Unknown")
            group = change.get("group", "Unknown")
            # Safely handle None values for group
            if group is None:
                group = "All"
            diff = change.get("difference_value", 0)
            recent = change.get("recent_mean", 0)
            comparison = change.get("comparison_mean", 0)
            district = change.get("district", "0")
            
            # Add an index field to each change for reference
            change["index"] = idx
            
            # Calculate percent change
            if comparison != 0:
                pct_change = (diff / comparison) * 100
                pct_text = f"{pct_change:+.1f}%"
            else:
                pct_text = "N/A"
            
            changes_text += f"{idx}. {metric} for {group}: {recent:.1f} vs {comparison:.1f} ({pct_text}), District: {district}\n"
        
        # Create a prompt for the agent, asking for a structured JSON response
        prompt = f"""You are a data journalist helping create a monthly newsletter.
Given the following list of metrics with significant changes, identify EXACTLY {max_items} most important items to highlight in a monthly newsletter.

Consider these factors when prioritizing:
1. Magnitude of the change (both absolute and percentage)  higher is better
2. Public importance of the metric (e.g., crime most important, then public safety, then housing, then the rest) higher is better
3. Compare the direction of the change with teh Year to Date info about the metrtic.  Same direction is better. 
4.- Represents a new development that requires special attention


RECENT CHANGES:
{changes_text}

YEAR-TO-DATE METRICS AND NOTES:
{notes_text[:5000]}

Return your response as a JSON object with a property named "items" that contains an array of EXACTLY {max_items} objects with the following structure:
```json
{{
  "items": [
    {{
      "index": 1,
      "metric": "Metric Name",
      "metric_id": "Metric ID",
      "group": "Group Value",
      "priority": 1,
      "explanation": "Detailed explanation of why this change is significant",
      "trend_analysis": "Whether this is a counter-trend or continuation of existing patterns",
      "follow_up": "Suggested follow-up questions or additional context"
    }},
    {{
      "index": 2,
      ...
    }},
    ...more items to make exactly {max_items} total
  ]
}}
```

VERY IMPORTANT REQUIREMENTS:
1. The "items" array MUST contain EXACTLY {max_items} objects, no more and no less.
2. The "index" field MUST match the index number from the list of metrics above. This is essential for accurate processing.
3. The array must be sorted by priority (1 being highest).
4. Each object must include the exact metric name from the list above.
5. The response must be valid JSON that can be parsed directly.
6. Do not include any commentary or explanation outside the JSON structure.

You MUST select {max_items} different metrics. This is a hard requirement.
"""

        # Make API call to get prioritized list
        response = client.chat.completions.create(
            model=AGENT_MODEL,
            messages=[{"role": "system", "content": "You are a data analyst specializing in municipal data analysis. Provide responses in structured JSON format when requested."},
                     {"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        response_content = response.choices[0].message.content
        logger.info(f"Received JSON response of length: {len(response_content)}")
        
        try:
            # Parse the JSON response
            prioritized_json = json.loads(response_content)
            logger.info(f"Parsed JSON: {json.dumps(prioritized_json)[:200]}...")
            
            # Extract the array if the response is wrapped in an object
            prioritized_items_raw = []
            if isinstance(prioritized_json, dict):
                # Try different possible keys that might contain the items array
                for key in ["items", "prioritized_items", "metrics", "results", "data"]:
                    if key in prioritized_json:
                        prioritized_items_raw = prioritized_json.get(key, [])
                        logger.info(f"Found items under key: {key}")
                        break
                
                # If no known key was found but there's a single array in the dict, use it
                if not prioritized_items_raw:
                    for value in prioritized_json.values():
                        if isinstance(value, list):
                            prioritized_items_raw = value
                            logger.info(f"Found items array as a value")
                            break
                            
                # If still no array, treat the entire object as a single-item array if it has 'metric'
                if not prioritized_items_raw and "metric" in prioritized_json:
                    prioritized_items_raw = [prioritized_json]
                    logger.info("Treating entire object as a single item")
            elif isinstance(prioritized_json, list):
                prioritized_items_raw = prioritized_json
                logger.info("Found direct array of items")
            else:
                logger.warning(f"Unexpected JSON structure: {type(prioritized_json)}")
                
            # Print what we found for debugging
            logger.info(f"Extracted {len(prioritized_items_raw)} items from JSON")
            for i, item in enumerate(prioritized_items_raw[:3]):  # Print first 3 for debugging
                logger.info(f"  Item {i+1}: {json.dumps(item)[:100]}...")
            
            # Process each item to match with original data
            prioritized_items = []
            for item in prioritized_items_raw:
                # Get the item index to match with original data
                item_index = item.get("index")
                if item_index is not None:
                    # Find the original change data by index
                    original_change = None
                    for change in combined_changes:
                        if change.get("index") == item_index:
                            original_change = change
                            break
                    
                    if original_change:
                        logger.info(f"Found original data for item index {item_index}: {original_change.get('metric')}")
                        # Create prioritized item with data from both the AI response and original change
                        prioritized_items.append({
                            "metric": original_change.get("metric"),
                            "metric_id": original_change.get("metric_id", "Unknown"),
                            "group": original_change.get("group", "All"),
                            "priority": item.get("priority", 999),
                            "recent_mean": original_change.get("recent_mean", 0),
                            "comparison_mean": original_change.get("comparison_mean", 0),
                            "difference": original_change.get("difference_value", 0),
                            "district": original_change.get("district", "0"),
                            "explanation": item.get("explanation", ""),
                            "trend_analysis": item.get("trend_analysis", ""),
                            "follow_up": item.get("follow_up", "")
                        })
                    else:
                        logger.warning(f"Could not find original data for item index {item_index}")
                else:
                    # Fallback to matching by name if no index
                    metric_name = item.get("metric", "").strip()
                    group_value = item.get("group", "All").strip()
                    
                    # Find the original change data
                    original_change = None
                    for change in combined_changes:
                        if change.get("metric") == metric_name and (change.get("group") == group_value or (change.get("group") is None and group_value == "All")):
                            original_change = change
                            break
                    
                    if original_change:
                        logger.info(f"Found original data for metric: {metric_name} by name matching")
                        # Create prioritized item with data from both the AI response and original change
                        prioritized_items.append({
                            "metric": metric_name,
                            "metric_id": original_change.get("metric_id", "Unknown"),
                            "group": group_value,
                            "priority": item.get("priority", 999),
                            "recent_mean": original_change.get("recent_mean", 0),
                            "comparison_mean": original_change.get("comparison_mean", 0),
                            "difference": original_change.get("difference_value", 0),
                            "district": original_change.get("district", "0"),
                            "explanation": item.get("explanation", ""),
                            "trend_analysis": item.get("trend_analysis", ""),
                            "follow_up": item.get("follow_up", "")
                        })
                    else:
                        logger.warning(f"Could not find original data for metric: {metric_name}, group: {group_value}")
            
            # Sort by priority
            prioritized_items.sort(key=lambda x: x.get("priority", 999))
            
            # Log prioritized items
            for item in prioritized_items:
                logger.info(f"Prioritized item: {item.get('metric')} - {item.get('group')} (Priority: {item.get('priority')})")
                logger.info(f"  Explanation length: {len(item.get('explanation', ''))}")
                logger.info(f"  Trend analysis: {item.get('trend_analysis', '')[:50]}...")
            
            logger.info(f"Prioritized {len(prioritized_items)} items for the report")
            return {"status": "success", "prioritized_items": prioritized_items}
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Raw response: {response_content[:200]}...")
            return {"status": "error", "message": f"Failed to parse JSON response: {e}"}
        
    except Exception as e:
        error_msg = f"Error in prioritize_deltas: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

def store_prioritized_items(prioritized_items, period_type='month', district="0"):
    """
    Store the prioritized items in the monthly_reporting table
    
    Args:
        prioritized_items: List of prioritized items with explanations
        period_type: The time period type (month, quarter, year)
        district: District number
        
    Returns:
        Status dictionary
    """
    logger.info(f"Storing {len(prioritized_items)} prioritized items in the database")
    
    if not prioritized_items or not isinstance(prioritized_items, list):
        return {"status": "error", "message": "Invalid prioritized items"}
    
    def store_items_operation(connection):
        cursor = connection.cursor()
        
        # Get the current date for the report
        report_date = datetime.now().date()
        
        # Generate filenames for the report
        original_filename = f"monthly_report_{district}_{report_date.strftime('%Y_%m')}.html"
        revised_filename = f"monthly_report_{district}_{report_date.strftime('%Y_%m')}_revised.html"
        
        # Create a record in the reports table first
        cursor.execute("""
            INSERT INTO reports (
                max_items, district, period_type, original_filename, revised_filename,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            ) RETURNING id
        """, (
            len(prioritized_items),
            district,
            period_type,
            original_filename,
            revised_filename
        ))
        
        report_id = cursor.fetchone()[0]
        logger.info(f"Created report record with ID: {report_id}")
        
        # Insert each prioritized item
        inserted_ids = []
        for item in prioritized_items:
            # Calculate percent change
            comparison_mean = item.get("comparison_mean", 0)
            if comparison_mean != 0:
                percent_change = (item.get("difference", 0) / comparison_mean) * 100
            else:
                percent_change = 0
                
            # Log the data before insertion
            logger.info(f"Storing item: {item.get('metric')} - Priority: {item.get('priority')}")
            logger.info(f"  Explanation: '{item.get('explanation', '')[:50]}...' (length: {len(item.get('explanation', ''))})")
            logger.info(f"  Trend analysis: '{item.get('trend_analysis', '')[:50]}...'")
            
            # Create metadata JSON to store additional fields
            metadata = {
                "trend_analysis": item.get("trend_analysis", ""),
                "follow_up": item.get("follow_up", "")
            }
            
            # Create item title from metric name and group
            metric_name = item.get("metric", "")
            metric_id = item.get("metric_id", "Unknown")
            group_value = item.get("group", "All")
            item_title = f"{metric_name} - {group_value}" if group_value != "All" else metric_name
                
            # Prepare the data
            cursor.execute("""
                INSERT INTO monthly_reporting (
                    report_id, report_date, item_title, metric_name, metric_id, group_value, group_field_name, 
                    period_type, comparison_mean, recent_mean, difference, 
                    percent_change, explanation, priority, district, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING id
            """, (
                report_id,
                report_date,
                item_title,
                metric_name,
                metric_id,
                group_value,
                "group", # Default group field name
                period_type,
                item.get("comparison_mean", 0),
                item.get("recent_mean", 0),
                item.get("difference", 0),
                percent_change,
                item.get("explanation", ""),
                item.get("priority", 999),
                item.get("district", district),
                json.dumps(metadata)
            ))
            
            inserted_id = cursor.fetchone()[0]
            inserted_ids.append(inserted_id)
            
            # Check if the insertion worked
            cursor.execute("SELECT LENGTH(explanation) FROM monthly_reporting WHERE id = %s", (inserted_id,))
            expl_length = cursor.fetchone()[0]
            logger.info(f"Stored item with ID {inserted_id}, explanation length in DB: {expl_length}")
        
        connection.commit()
        cursor.close()
        
        logger.info(f"Successfully stored {len(inserted_ids)} items in the database")
        return inserted_ids
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=store_items_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] == "success":
        return {"status": "success", "inserted_ids": result["result"]}
    else:
        return {"status": "error", "message": result["message"]}

def generate_explanations(report_ids):
    """
    Step 3: Generate detailed explanations for each prioritized item using the explainer agent.
    Updates the monthly_reporting table with the explanations.
    
    Args:
        report_ids: List of report IDs to explain
        
    Returns:
        Status dictionary
    """
    logger.info(f"Generating explanations for {len(report_ids)} items")
    
    def generate_explanations_operation(connection):
        cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        successful_explanations = 0
        
        for report_id in report_ids:
            # Get the report item
            cursor.execute("""
                SELECT * FROM monthly_reporting WHERE id = %s
            """, (report_id,))
            
            item = cursor.fetchone()
            if not item:
                logger.warning(f"Report item with ID {report_id} not found")
                continue
            
            # Format the data for the explainer agent
            metric_name = item["metric_name"]
            metric_id = item["metric_id"]  # Get the metric_id from the item
            group_value = item["group_value"]
            recent_mean = item["recent_mean"]
            comparison_mean = item["comparison_mean"]
            difference = item["difference"]
            percent_change = item["percent_change"]
            district = item["district"]
            period_type = item["period_type"]
            
            # Calculate time periods for context - check the report date
            report_date = item.get("report_date") or datetime.now().date()
            if isinstance(report_date, str):
                report_date = datetime.strptime(report_date, "%Y-%m-%d").date()
            
            # Get the previous month's date (the more recent month in the comparison)
            if report_date.month == 1:  # January case
                previous_month_date = report_date.replace(month=12, year=report_date.year-1)
            else:
                previous_month_date = report_date.replace(month=report_date.month-1)
            
            # Get the month before the previous month (the earlier month in the comparison)
            if previous_month_date.month == 1:  # January case
                comparison_month_date = previous_month_date.replace(month=12, year=previous_month_date.year-1)
            else:
                comparison_month_date = previous_month_date.replace(month=previous_month_date.month-1)
            
            # Format the month names
            recent_month = previous_month_date.strftime("%B %Y")
            previous_month = comparison_month_date.strftime("%B %Y")
            
            logger.info(f"Comparing data between {previous_month} and {recent_month}")
            
            # Calculate delta if not already in the item
            delta = item.get("difference", recent_mean - comparison_mean)
            
            # Add detailed context about the item for logging
            logger.info(f"Generating explanation for report_id={report_id}, metric={metric_name} (ID: {metric_id}), group={group_value}")
            logger.info(f"  Values: recent={recent_mean}, comparison={comparison_mean}, diff={difference}, percent_change={percent_change}")
            logger.info(f"  Period: {period_type}, District: {district}")
            
            # Create a simple session for the anomaly explainer agent
            session_id = f"report_{report_id}"
            session_context = context_variables.copy()
            
            # Create a session for the agent
            sessions = {}
            sessions[session_id] = {
                "messages": [],
                "agent": anomaly_explainer_agent,
                "context_variables": session_context
            }
            
            # Create a simple, direct prompt for the agent
            direction = "increased" if delta > 0 else "decreased"
            percent_change_str = f"{abs(percent_change):.2f}%" if percent_change is not None else "unknown percentage"
            
            prompt = f"""Please explain why the metric '{metric_name}' (ID: {metric_id})  {direction} from {comparison_mean} to {recent_mean} ({percent_change_str}) between {previous_month} and {recent_month} for district {district}.

Use the available tools to research this change and provide a comprehensive explanation that can be included in a monthly newsletter for city residents.
You should first look to supervisor district and see if there are localized trends in a particular neighborhood.  If so, you should include that in your explanation. 
Other than thay, prefer to share anomalies or datapoints that explain a large portion of the difference in the metric.  
If the anomaly relates to business location openings or closing, user set_dataset to get the dba_name of the businesses and share some of those.

Your response MUST follow this structured format:

EXPLANATION: [Your clear explanation of what happened]

DO NOT include any additional content, headers, or formatting outside of this structure. The explanation will be extracted directly for use in a report."""
            
            logger.info(f"Prompt for explainer agent: {prompt[:200]}...")
            
            # Add prompt to the session
            session_data = sessions[session_id]
            session_data["messages"].append({"role": "user", "content": prompt})
            
            # Process with the agent
            try:
                logger.info(f"Running explainer agent for metric: {metric_id}")
                # Run the agent in a non-streaming mode to get the complete response
                response = swarm_client.run(
                    agent=anomaly_explainer_agent,
                    messages=session_data["messages"],
                    context_variables=session_data["context_variables"],
                    stream=False
                )
                
                # Log information about the response type and attributes
                logger.info(f"Explainer agent response type: {type(response)}")
                if hasattr(response, '__dict__'):
                    logger.info(f"Response attributes: {list(response.__dict__.keys())}")
                elif isinstance(response, dict):
                    logger.info(f"Response keys: {list(response.keys())}")
                
                # Log the raw response for debugging
                try:
                    if hasattr(response, 'model_dump'):
                        logger.info(f"Response model_dump: {json.dumps(response.model_dump())[:500]}...")
                    elif isinstance(response, dict):
                        logger.info(f"Response dict: {json.dumps(response)[:500]}...")
                    elif hasattr(response, '__dict__'):
                        logger.info(f"Response __dict__: {json.dumps(response.__dict__)[:500]}...")
                    else:
                        logger.info(f"Response string: {str(response)[:500]}...")
                except Exception as dump_error:
                    logger.error(f"Error dumping response for logging: {str(dump_error)}")
                
                explanation = ""
                chart_data = None
                
                # Check if response contains content
                if response:
                    # Try to extract explanation from the response messages
                    try:
                        # Check if response has a 'messages' attribute
                        if hasattr(response, 'messages') and response.messages:
                            logger.info(f"Response has messages attribute with {len(response.messages)} messages")
                            # Get the last message
                            last_message = response.messages[-1]
                            logger.info(f"Last message type: {type(last_message)}")
                            
                            # Check if the message has content
                            if hasattr(last_message, 'content') and last_message.content:
                                explanation = str(last_message.content)
                                logger.info(f"Found explanation in last message content: {explanation[:100]}...")
                            # Check if the message has text
                            elif hasattr(last_message, 'text') and last_message.text:
                                explanation = str(last_message.text)
                                logger.info(f"Found explanation in last message text: {explanation[:100]}...")
                            # Check if the message is a dict with content
                            elif isinstance(last_message, dict) and 'content' in last_message:
                                explanation = str(last_message['content'])
                                logger.info(f"Found explanation in last message dict content: {explanation[:100]}...")
                            # Check if the message is a dict with text
                            elif isinstance(last_message, dict) and 'text' in last_message:
                                explanation = str(last_message['text'])
                                logger.info(f"Found explanation in last message dict text: {explanation[:100]}...")
                        
                        # If no explanation found in messages, try to extract from the response itself
                        if not explanation:
                            # If response is a string, try to parse it as JSON
                            if isinstance(response, str):
                                logger.info("Response is a string, attempting to parse as JSON")
                                try:
                                    response_json = json.loads(response)
                                    logger.info(f"Successfully parsed response as JSON: {json.dumps(response_json)[:200]}...")
                                    
                                    # Check if the JSON has a 'messages' field
                                    if 'messages' in response_json and response_json['messages']:
                                        messages = response_json['messages']
                                        if isinstance(messages, list) and len(messages) > 0:
                                            # Look at the last message
                                            last_message = messages[-1]
                                            if isinstance(last_message, dict) and 'content' in last_message:
                                                explanation = str(last_message['content'])
                                                logger.info(f"Found explanation in JSON messages[].content: {explanation[:100]}...")
                                            elif isinstance(last_message, dict) and 'text' in last_message:
                                                explanation = str(last_message['text'])
                                                logger.info(f"Found explanation in JSON messages[].text: {explanation[:100]}...")
                                except json.JSONDecodeError:
                                    logger.warning("Failed to parse response string as JSON")
                            
                            # If response has a model_dump method (Pydantic model)
                            elif hasattr(response, 'model_dump'):
                                logger.info("Response has model_dump method, using it")
                                response_json = response.model_dump()
                                logger.info(f"Response model_dump: {json.dumps(response_json)[:200]}...")
                                
                                # Check if the model_dump has a 'messages' field
                                if 'messages' in response_json and response_json['messages']:
                                    messages = response_json['messages']
                                    if isinstance(messages, list) and len(messages) > 0:
                                        # Look at the last message
                                        last_message = messages[-1]
                                        if isinstance(last_message, dict) and 'content' in last_message:
                                            explanation = str(last_message['content'])
                                            logger.info(f"Found explanation in model_dump messages[].content: {explanation[:100]}...")
                                        elif isinstance(last_message, dict) and 'text' in last_message:
                                            explanation = str(last_message['text'])
                                            logger.info(f"Found explanation in model_dump messages[].text: {explanation[:100]}...")
                            
                            # If response has a __dict__ attribute
                            elif hasattr(response, '__dict__'):
                                logger.info("Response has __dict__ attribute, using it")
                                response_dict = response.__dict__
                                logger.info(f"Response __dict__ keys: {list(response_dict.keys())}")
                                
                                # Check if the __dict__ has a 'messages' field
                                if 'messages' in response_dict and response_dict['messages']:
                                    messages = response_dict['messages']
                                    if isinstance(messages, list) and len(messages) > 0:
                                        # Look at the last message
                                        last_message = messages[-1]
                                        if hasattr(last_message, 'content') and last_message.content:
                                            explanation = str(last_message.content)
                                            logger.info(f"Found explanation in __dict__ messages[].content: {explanation[:100]}...")
                                        elif hasattr(last_message, 'text') and last_message.text:
                                            explanation = str(last_message.text)
                                            logger.info(f"Found explanation in __dict__ messages[].text: {explanation[:100]}...")
                                        elif isinstance(last_message, dict) and 'content' in last_message:
                                            explanation = str(last_message['content'])
                                            logger.info(f"Found explanation in __dict__ messages[] dict content: {explanation[:100]}...")
                                        elif isinstance(last_message, dict) and 'text' in last_message:
                                            explanation = str(last_message['text'])
                                            logger.info(f"Found explanation in __dict__ messages[] dict text: {explanation[:100]}...")
                            
                            # If response is already a dict
                            elif isinstance(response, dict):
                                logger.info("Response is already a dict")
                                
                                # Check if the dict has a 'messages' field
                                if 'messages' in response and response['messages']:
                                    messages = response['messages']
                                    if isinstance(messages, list) and len(messages) > 0:
                                        # Look at the last message
                                        last_message = messages[-1]
                                        if isinstance(last_message, dict) and 'content' in last_message:
                                            explanation = str(last_message['content'])
                                            logger.info(f"Found explanation in dict messages[].content: {explanation[:100]}...")
                                        elif isinstance(last_message, dict) and 'text' in last_message:
                                            explanation = str(last_message['text'])
                                            logger.info(f"Found explanation in dict messages[].text: {explanation[:100]}...")
                    except Exception as e:
                        logger.error(f"Error extracting explanation from response messages: {e}")
                        logger.error(f"Problematic response: {str(response)[:200]}...")
                        logger.error(traceback.format_exc())
                    
                    # If we still don't have an explanation, try to find chart data
                    if not explanation:
                        # Look for chart data in the response
                        try:
                            # Check if response has a 'chart_data' attribute
                            if hasattr(response, 'chart_data') and response.chart_data:
                                chart_data = response.chart_data
                                logger.info(f"Found chart_data in response attribute: {json.dumps(chart_data)[:100]}...")
                            
                            # Check if response has a 'chart_html' attribute
                            elif hasattr(response, 'chart_html') and response.chart_html:
                                chart_html = response.chart_html
                                logger.info(f"Found chart_html in response attribute: {str(chart_html)[:100]}...")
                                chart_data = {"html": str(chart_html)}
                            
                            # Check if response has tool_calls with chart data
                            elif hasattr(response, 'tool_calls') and response.tool_calls:
                                for tool_call in response.tool_calls:
                                    if isinstance(tool_call, dict):
                                        if 'chart' in str(tool_call).lower():
                                            logger.info(f"Found chart data in tool_calls dict")
                                            chart_data = tool_call
                                            break
                                    else:
                                        if hasattr(tool_call, 'function') and 'chart' in str(tool_call.function).lower():
                                            logger.info(f"Found chart data in tool_calls object")
                                            chart_data = tool_call
                                            break
                        except Exception as e:
                            logger.error(f"Error extracting chart data: {e}")
                            logger.error(f"Chart data extraction error traceback: {traceback.format_exc()}")
                
                # Validate the explanation
                if explanation:
                    if len(explanation) < 10:  # If explanation is too short
                        logger.warning(f"Extracted explanation is too short: {explanation}")
                        explanation = ""
                    elif len(explanation) > 5000:  # If explanation is too long
                        logger.warning(f"Extracted explanation is too long ({len(explanation)} chars), truncating")
                        explanation = explanation[:5000] + "..."
                
                # If explanation is still empty after all attempts, use a fallback
                if not explanation:
                    logger.warning(f"No explanation returned from agent for {metric_name}")
                    
                    # First try to use the explanation already in the database
                    if item.get("explanation"):
                        explanation = item.get("explanation")
                        logger.info(f"Using existing explanation from prioritization step: {explanation[:100]}...")
                    else:
                        # If there's no explanation at all, create a generic one
                        explanation = f"Unable to generate an automated explanation for the change in {metric_name} ({percent_change_str}) between {previous_month} and {recent_month}."
                
            except Exception as e:
                logger.error(f"Error running explainer agent: {str(e)}", exc_info=True)
                logger.error(f"Traceback: {traceback.format_exc()}")
                explanation = f"Error generating explanation for {metric_name}: {str(e)}"
                
            # Log the final explanation that will be stored
            logger.info(f"Final explanation to be stored for {metric_name} (length: {len(explanation)}): {explanation[:200]}...")
            
            # Prepare chart_html for storage in the database
            chart_html = None
            if chart_data:
                try:
                    # Extract chart HTML if it exists
                    if hasattr(chart_data, 'content'):
                        chart_html = chart_data.content
                    elif isinstance(chart_data, dict) and 'content' in chart_data:
                        chart_html = chart_data['content']
                    elif isinstance(chart_data, str) and ('<div' in chart_data or '<svg' in chart_data):
                        chart_html = chart_data
                    
                    if chart_html:
                        logger.info(f"Extracted chart HTML (first 100 chars): {str(chart_html)[:100]}...")
                except Exception as e:
                    logger.error(f"Error extracting chart HTML: {e}")
            
            # Update the database with the detailed explanation and chart data
            if explanation:  # Only update if we have a valid explanation
                try:
                    if chart_html:
                        cursor.execute("""
                            UPDATE monthly_reporting
                            SET report_text = %s, chart_data = %s
                            WHERE id = %s
                        """, (explanation, json.dumps({"html": str(chart_html)}), report_id))
                        logger.info(f"Updated explanation and chart data for report ID {report_id}")
                    else:
                        cursor.execute("""
                            UPDATE monthly_reporting
                            SET report_text = %s
                            WHERE id = %s
                        """, (explanation, report_id))
                        logger.info(f"Updated explanation for report ID {report_id}")
                    
                    successful_explanations += 1
                except Exception as db_error:
                    logger.error(f"Database error while updating explanation: {str(db_error)}")
                    logger.error(traceback.format_exc())
            else:
                logger.warning(f"No valid explanation to store for report ID {report_id}")
        
        connection.commit()
        cursor.close()
        
        return successful_explanations
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=generate_explanations_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] == "success":
        logger.info(f"Successfully generated explanations for {result['result']} items")
        return {"status": "success", "message": f"Explanations generated for {result['result']} items"}
    else:
        return {"status": "error", "message": result["message"]}

def generate_monthly_report(report_date=None, district="0"):
    """
    Step 4: Generate the final monthly report with charts and annotations
    
    Args:
        report_date: The date for the report (defaults to current month)
        district: District number
        
    Returns:
        Path to the generated report file
    """
    from webChat import client, AGENT_MODEL
    
    logger.info(f"Generating monthly report for district {district}")
    
    # Set report date to current date if not provided
    if not report_date:
        report_date = datetime.now().date()
    
    def generate_report_operation(connection):
        cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get all report items for this date and district, ordered by priority
        cursor.execute("""
            SELECT *, 
                   metadata->>'anomaly_id' as anomaly_id, -- Extract potential anomaly_id from metadata
                   metric_id -- Placeholder for metric_id, needs proper retrieval logic
            FROM monthly_reporting 
            WHERE report_date = %s AND district = %s
            ORDER BY priority
        """, (report_date, district))
        
        items = cursor.fetchall()
        logger.info(f"Found {len(items)} report items for date {report_date} and district {district}")
        if not items:
            logger.warning(f"No report items found for date {report_date} and district {district}")
            cursor.close()
            return None
        
        # Format the report data
        report_data = []
        for item in items:
            # Check if there's chart data
            chart_html = None
            if item["chart_data"]:
                try:
                    chart_data = item["chart_data"]
                    if isinstance(chart_data, dict) and "html" in chart_data:
                        chart_html = chart_data["html"]
                    elif isinstance(chart_data, str):
                        chart_data_dict = json.loads(chart_data)
                        if "html" in chart_data_dict:
                            chart_html = chart_data_dict["html"]
                except Exception as e:
                    logger.error(f"Error parsing chart data: {e}")
            
            # Attempt to get IDs for chart placeholders
            metric_id = item.get("metric_id") # Placeholder value for now
            anomaly_id = item.get("anomaly_id") # Might be None if not an anomaly item
            
            # Decide which chart placeholder format to use
            chart_placeholder = ""
            if anomaly_id:
                try:
                    # Validate anomaly_id is an integer
                    int(anomaly_id) 
                    chart_placeholder = f"[CHART:anomaly:{anomaly_id}]"
                except (ValueError, TypeError):
                    anomaly_id = None # Invalid anomaly_id
            
            # Fallback to time series if no valid anomaly_id or if metric_id is preferred
            if not anomaly_id and metric_id is not None:
                try:
                    # Validate metric_id, district, period_type
                    int(metric_id)
                    str(item["district"])
                    str(item["period_type"])
                    chart_placeholder = f"[CHART:time_series:{metric_id}:{item['district']}:{item['period_type']}]"
                except (ValueError, TypeError, KeyError):
                    metric_id = None # Invalid data

            report_data.append({
                "metric": item["metric_name"],
                "metric_id": item["metric_id"],
                "group": item["group_value"],
                "recent_mean": item["recent_mean"],
                "comparison_mean": item["comparison_mean"],
                "difference": item["difference"],
                "percent_change": item["percent_change"],
                "explanation": item["explanation"],
                "report_text": item["report_text"],
                "priority": item["priority"],
                "chart_html": chart_html, # Keep original chart_html if already generated
                "chart_placeholder": chart_placeholder, # Add the placeholder string
                "trend_analysis": item["metadata"].get("trend_analysis", "") if item["metadata"] else "",
                "follow_up": item["metadata"].get("follow_up", "") if item["metadata"] else ""
            })
        
        cursor.close()
        
        # Get district name for the report title
        district_name = f"District {district}"
        if district == "0":
            district_name = "Citywide"
            
        # Load officials data
        officials_data = {}
        try:
            officials_path = Path(__file__).parent / 'data' / 'dashboard' / 'officials.json'
            with open(officials_path, 'r') as f:
                officials_json = json.load(f)
                for official in officials_json.get("officials", []):
                    officials_data[official.get("district")] = official
        except Exception as e:
            logger.error(f"Error loading officials data: {e}")
            
        # Get the district official
        district_official = officials_data.get(district, {})
        official_name = district_official.get("name", "")
        official_role = district_official.get("role", "")
        
        # Create a prompt for generating the report
        report_items_text = ""
        for i, item in enumerate(report_data, 1):
            report_items_text += f"""
ITEM {i}: {item['metric']} - {item['group']}
Change: {item['recent_mean']:.2f} vs {item['comparison_mean']:.2f} ({item['percent_change']:+.2f}%)
Summary: {item['explanation']}
Trend Analysis: {item['trend_analysis']}
Follow-up Questions: {item['follow_up']}
Potential Chart: {item['chart_placeholder'] if item['chart_placeholder'] else 'No'}

Detailed Analysis:
{item['report_text']}

"""
            if item['chart_html']:
                report_items_text += f"Chart Available: Yes\n"
        
        # Calculate the month for the report title (the more recent month in the comparison)
        if report_date.month == 1:  # January case
            report_month_date = report_date.replace(month=12, year=report_date.year-1)
        else:
            report_month_date = report_date.replace(month=report_date.month-1)
            
        # Format the month name for the report title
        current_month = report_month_date.strftime("%B %Y")
        
        # Load prompt from JSON file
        try:
            prompts_path = Path(__file__).parent / 'data' / 'prompts.json'
            with open(prompts_path, 'r') as f:
                prompts = json.load(f)
                prompt_template = prompts['monthly_report']['generate_report']['prompt']
                system_message = prompts['monthly_report']['generate_report']['system']
                
                # Format the prompt with the required variables
                prompt = prompt_template.format(
                    district_name=district_name,
                    current_month=current_month,
                    official_name=official_name,
                    official_role=official_role,
                    report_items_text=report_items_text
                )
        except Exception as e:
            logger.error(f"Error loading prompts from JSON: {e}")
            raise

        # Make API call to generate the report
        logger.info("Making API call to generate report content")
        response = client.chat.completions.create(
            model=AGENT_MODEL,
            messages=[{"role": "system", "content": system_message},
                     {"role": "user", "content": prompt}],
            temperature=0.2
        )

        report_text = response.choices[0].message.content
        logger.info(f"Received report content (length: {len(report_text)})")
        
        # Once we have the report text, insert any charts if they exist
        for i, item in enumerate(report_data, 1):
            if item.get('chart_html'):
                # Create the chart HTML content we want to insert
                chart_content_to_insert = item['chart_html']
                
                # Look for a section that mentions this metric to insert the chart nearby
                metric_mentions = [
                    item['metric'],
                    # Add variations if needed (e.g., removing emojis)
                    item['metric'].replace("🏠", "Housing").replace("💊", "Drug").replace("🚫", "Business") 
                ]
                
                insertion_point = -1
                for mention in metric_mentions:
                    mention_pos = report_text.find(mention)
                    if mention_pos != -1:
                        # Find the end of the paragraph containing the mention
                        paragraph_end = report_text.find("\\n\\n", mention_pos)
                        if paragraph_end != -1:
                            insertion_point = paragraph_end + 2 # Insert after the double newline
                            break
                        else:
                            # If no double newline found, try finding the end of the line
                            line_end = report_text.find("\\n", mention_pos)
                            if line_end != -1:
                                insertion_point = line_end + 1 # Insert after the newline
                                break
                            else:
                                # If no newline found, just insert after the mention (less ideal)
                                insertion_point = mention_pos + len(mention)
                                break

                if insertion_point != -1:
                    # Insert the chart HTML at the determined point
                    report_text = report_text[:insertion_point] + f"\\n{chart_content_to_insert}\\n\\n" + report_text[insertion_point:]
                    logger.info(f"Inserted chart for '{item['metric']}' after its mention.")
                else:
                    # If we couldn't find a suitable mention, append the chart at the end
                    report_text += f"\\n\\n{chart_content_to_insert}\\n"
                    logger.info(f"Appended chart for '{item['metric']}' at the end of the report.")
        
        # Create directory for reports if it doesn't exist
        reports_dir = Path(__file__).parent / 'output' / 'reports'
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Save the report to a file as HTML
        report_filename = f"monthly_report_{district}_{report_date.strftime('%Y_%m')}.html"
        report_path = reports_dir / report_filename
        
        # Add proper HTML structure with modern styling
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{district_name} Monthly Newsletter - {current_month}</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500&display=swap">
    <style>
        :root {{
            --ink-black: #000000;
            --dark-gray: #333333;
            --medium-gray: #666666;
            --light-gray: #E8E9EB;
            --white: #ffffff;
            --text-color: #222222;
            --border-color: #CCCCCC;
            --accent-color: #444444;
        }}
        
        body {{
            font-family: 'IBM Plex Sans', Arial, Helvetica Neue, sans-serif;
            line-height: 1.6;
            color: var(--text-color);
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: var(--white);
        }}
        
        /* Logo styles */
        .logo-container {{
            display: flex;
            align-items: center;
            margin: 20px 0 40px 0;
            padding-left: 0;
            justify-content: flex-start;
        }}
        
        .logo-text {{
            font-weight: 400;
            font-size: 28px;
            color: var(--ink-black);
            margin-right: 8px;
        }}
        
        .logo-box {{
            position: relative;
            border: 0;
            border-radius: 8px;
            padding: 0px 3px;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 48px;
            width: 50px;
            margin-top: 1px;
            background-color: transparent;
            overflow: hidden;
        }}
        
        .logo-box-top-right {{
            position: absolute;
            top: 0;
            right: 0;
            width: 61.8%;
            height: 61.8%;
            border-top: 3px solid var(--ink-black);
            border-right: 3px solid var(--ink-black);
            border-top-right-radius: 8px;
        }}
        
        .logo-box-bottom-left {{
            position: absolute;
            bottom: 0;
            left: 0;
            width: 61.8%;
            height: 61.8%;
            border-bottom: 3px solid var(--ink-black);
            border-left: 3px solid var(--ink-black);
            border-bottom-left-radius: 8px;
        }}
        
        .logo-box-text {{
            font-weight: 600;
            font-size: 24px;
            color: var(--ink-black);
            line-height: 28px;
        }}
        
        /* Typography */
        h1 {{
            font-family: 'Inter', Arial, Helvetica Neue, sans-serif;
            font-weight: 700;
            font-size: 36px;
            line-height: 42px;
            color: var(--ink-black);
            margin-top: 0;
            margin-bottom: 20px;
        }}
        
        h2 {{
            font-family: 'Inter', Arial, Helvetica Neue, sans-serif;
            font-weight: 500;
            font-size: 24px;
            line-height: 32px;
            color: var(--ink-black);
            margin-top: 30px;
            margin-bottom: 15px;
        }}
        
        h3 {{
            font-family: 'Inter', Arial, Helvetica Neue, sans-serif;
            font-weight: 400;
            font-size: 18px;
            line-height: 28px;
            color: var(--ink-black);
            margin-top: 25px;
            margin-bottom: 10px;
        }}
        
        p {{
            font-family: 'IBM Plex Sans', Arial, Helvetica Neue, sans-serif;
            font-size: 14px;
            line-height: 22px;
            margin-bottom: 15px;
        }}
        
        .caption {{
            font-family: 'IBM Plex Sans', Arial, Helvetica Neue, sans-serif;
            font-size: 12px;
            line-height: 18px;
            color: var(--medium-gray);
            margin-top: 5px;
            margin-bottom: 20px;
        }}
        
        /* Content sections */
        .section {{
            margin-bottom: 30px;
            padding: 20px;
            background-color: var(--light-gray);
            border-radius: 8px;
        }}
        
        .highlight {{
            background-color: var(--light-gray);
            padding: 15px;
            border-radius: 6px;
            margin: 20px 0;
            border-left: 4px solid var(--ink-black);
        }}
        
        .key-takeaways {{
            background-color: var(--white);
            padding: 15px;
            border-radius: 6px;
            margin: 20px 0;
            border-left: 4px solid #007bff;
        }}
        
        .data-point {{
            color: var(--ink-black);
            font-weight: 500;
        }}
        
        .call-to-action {{
            background-color: var(--ink-black);
            color: var(--white);
            padding: 10px 15px;
            border-radius: 4px;
            display: inline-block;
            margin: 10px 0;
            text-decoration: none;
        }}
        
        .chart-container {{
            margin: 20px 0;
            padding: 15px;
            background-color: var(--light-gray);
            border-radius: 6px;
        }}
        
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
            font-size: 12px;
            color: var(--medium-gray);
        }}
    </style>
</head>
<body>
    <div class="logo-container">
        <div class="logo-text">Transparent</div>
        <div class="logo-box">
            <div class="logo-box-top-right"></div>
            <div class="logo-box-bottom-left"></div>
            <div class="logo-box-text">SF</div>
        </div>
    </div>
    <div class="key-takaways">
    </div>
    {report_text}
    <div class="footer">
        <p>Generated on {datetime.now().strftime('%B %d, %Y')} by TransparentSF</p>
    </div>
</body>
</html>
"""
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"Monthly newsletter saved to {report_path}")
        return str(report_path)
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=generate_report_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] == "success" and result["result"]:
        return {"status": "success", "report_path": result["result"]}
    elif result["status"] == "success" and not result["result"]:
        return {"status": "error", "message": "No report items found"}
    else:
        return {"status": "error", "message": result["message"]}

def proofread_and_revise_report(report_path):
    """
    Step 5: Proofread and revise the monthly newsletter
    
    Args:
        report_path: Path to the newsletter file to proofread
        
    Returns:
        Path to the revised newsletter file
    """
    from webChat import client, AGENT_MODEL
    
    logger.info(f"Proofreading and revising newsletter at {report_path}")
    
    try:
        # Read the newsletter
        with open(report_path, 'r', encoding='utf-8') as f:
            newsletter_text = f.read()
        
        # Create a prompt for proofreading
        prompt = f"""You are a professional editor tasked with proofreading and improving a citizen-focused newsletter for San Francisco.

BRAND GUIDELINES:
- Voice: Intelligent but accessible, civic-minded (not partisan)
- Tone: Factual, clear, non-righteous, with occasional dry wit
- Focus: Impact over ideology, precision over polish
- Approach: Assume no ill intention from public officials, drive accountability while being fair and data-driven
- Style: Clean and clear in design and tone, calm and credible

Please review the following newsletter and make these improvements:


1. Fix any grammatical or spelling errors
3. Ensure consistent formatting and style
6. Ensure the tone aligns with our brand guidelines - intelligent but accessible, civic-minded, and focused on impact
7. Make sure the HTML formatting is clean and consistent
8. Ensure the newsletter feels like a communication to citizens, not a formal report
9. Remove any refereces to markdown calls for embeddd charts that remain in the file and haven't been swapped for HTML. 

Here's the newsletter:

{newsletter_text}

Please provide the revised version of the newsletter, maintaining the same overall structure but with your improvements.
Keep all HTML tags intact and ensure the formatting remains clean and consistent.
"""

        # Make API call for proofreading
        response = client.chat.completions.create(
            model=AGENT_MODEL,
            messages=[{"role": "system", "content": "You are a professional editor and proofreader for a civic transparency organization."},
                     {"role": "user", "content": prompt}],
            temperature=0.1
        )

        revised_text = response.choices[0].message.content
        
        # Save the revised newsletter
        report_path_obj = Path(report_path)
        revised_path = report_path_obj.parent / f"{report_path_obj.stem}_revised{report_path_obj.suffix}"
        
        with open(revised_path, 'w', encoding='utf-8') as f:
            f.write(revised_text)
        
        # Expand chart references in the revised report
        logger.info(f"Expanding chart references in revised report: {revised_path}")
        expand_result = expand_chart_references(revised_path)
        if not expand_result:
            logger.warning("Failed to expand chart references in the revised report")
        
        logger.info(f"Revised newsletter saved to {revised_path}")
        return {"status": "success", "revised_report_path": str(revised_path)}
        
    except Exception as e:
        error_msg = f"Error in proofread_and_revise_report: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

def run_monthly_report_process(district="0", period_type="month", max_report_items=10):
    """
    Run the complete monthly newsletter generation process
    
    Args:
        district: District number (0 for citywide)
        period_type: Time period type (month, quarter, year)
        max_report_items: Maximum number of items to include in the newsletter
        
    Returns:
        Status dictionary with newsletter path
    """
    logger.info(f"Starting monthly newsletter process for district {district}")
    
    try:
        # Step 0: Initialize the monthly_reporting table
        logger.info("Step 0: Initializing monthly_reporting table")
        init_result = initialize_monthly_reporting_table()
        if not init_result:
            return {"status": "error", "message": "Failed to initialize monthly_reporting table"}
        
        # Step 1: Select deltas to discuss
        logger.info("Step 1: Selecting deltas to discuss")
        deltas = select_deltas_to_discuss(period_type=period_type, district=district)
        if deltas.get("status") != "success":
            return deltas
            
        # Step 2: Prioritize deltas and get explanations
        logger.info("Step 2: Prioritizing deltas")
        prioritized = prioritize_deltas(deltas, max_items=max_report_items)
        if prioritized.get("status") != "success":
            return prioritized
        
        # Store prioritized items in the database
        logger.info("Step 2.5: Storing prioritized items")
        store_result = store_prioritized_items(
            prioritized.get("prioritized_items", []),
            period_type=period_type,
            district=district
        )
        if store_result.get("status") != "success":
            return store_result
            
        # Step 3: Generate detailed explanations
        logger.info("Step 3: Generating explanations")
        explanation_result = generate_explanations(store_result.get("inserted_ids", []))
        if explanation_result.get("status") != "success":
            return explanation_result
            
        # Step 4: Generate the monthly newsletter
        logger.info("Step 4: Generating monthly newsletter")
        newsletter_result = generate_monthly_report(district=district)
        if newsletter_result.get("status") != "success":
            return newsletter_result
            
        # Step 4.5: Expand chart references in the newsletter
        logger.info("Step 4.5: Expanding chart references in the newsletter")
        expand_result = expand_chart_references(newsletter_result.get("report_path"))
        if not expand_result:
            logger.warning("Failed to expand chart references in the newsletter")
            
        # Step 5: Proofread and revise the newsletter
        logger.info("Step 5: Proofreading and revising newsletter")
        revised_result = proofread_and_revise_report(newsletter_result.get("report_path"))
        if revised_result.get("status") != "success":
            return revised_result
            
        # Update the district's top_level.json with the revised report filename
        try:
            # Get the district directory path
            script_dir = Path(__file__).parent
            dashboard_dir = script_dir / 'output' / 'dashboard'
            district_dir = dashboard_dir / district
            
            # Read the current top_level.json
            top_level_file = district_dir / 'top_level.json'
            if top_level_file.exists():
                with open(top_level_file, 'r', encoding='utf-8') as f:
                    top_level_data = json.load(f)
                
                # Add the revised report filename
                top_level_data['monthly_report'] = Path(revised_result['revised_report_path']).name
                
                # Save the updated top_level.json
                with open(top_level_file, 'w', encoding='utf-8') as f:
                    json.dump(top_level_data, f, indent=2)
                logger.info(f"Updated top_level.json for district {district} with revised report filename")
            else:
                logger.warning(f"top_level.json not found for district {district}")
        except Exception as e:
            logger.error(f"Error updating top_level.json: {str(e)}")
            # Don't fail the process if this update fails
            
        # Use the original newsletter path since proofreading is skipped
        revised_newsletter_path = newsletter_result.get("report_path")
        
        # # Step 6: Generate an email-compatible version of the report
        # logger.info("Step 6: Generating email-compatible version")
        # email_result = generate_email_compatible_report(revised_newsletter_path) # Use original path
        # if not email_result:
        #     logger.warning("Failed to generate email-compatible report")
            
        # # Step 7: Generate an inline version of the report with data URLs
        # logger.info("Step 7: Generating inline version")
        # inline_result = generate_inline_report(revised_newsletter_path) # Use original path
        # if not inline_result:
        #     logger.warning("Failed to generate inline report")
            
        logger.info("Monthly newsletter process completed successfully")
        return {
            "status": "success",
            "newsletter_path": newsletter_result.get("report_path"),
            "revised_newsletter_path": revised_newsletter_path # Return original path as revised path
        #     "email_newsletter_path": email_result,
        #     "inline_newsletter_path": inline_result
         }
        
    except Exception as e:
        error_msg = f"Error in run_monthly_report_process: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

def get_monthly_reports_list():
    """
    Get a list of all monthly newsletters from the database.
    
    Returns:
        List of newsletter objects with id, report_date, district, filename, etc.
    """
    logger.info("Getting list of monthly newsletters from database")
    
    def get_reports_operation(connection):
        cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Query the reports table
        cursor.execute("""
            SELECT id, district, period_type, max_items, original_filename, revised_filename, 
                   created_at, updated_at
            FROM reports
            ORDER BY created_at DESC
        """)
        
        reports = cursor.fetchall()
        
        # Format the reports
        formatted_reports = []
        for report in reports:
            # Format district name
            district = report['district']
            district_name = f"District {district}"
            if district == "0":
                district_name = "Citywide"
            
            # Extract date from filename (format: monthly_report_0_2025_04.html)
            filename = report['original_filename']
            parts = filename.split('_')
            
            if len(parts) >= 4:
                year = parts[3]
                month = parts[4].split('.')[0]  # Remove .html extension
                report_date = f"{year}-{month}-01T00:00:00"  # ISO format date
            else:
                # Fallback to created_at if filename doesn't match expected format
                report_date = report['created_at'].isoformat()
            
            # Count the number of items for this report
            cursor.execute("""
                SELECT COUNT(*) FROM monthly_reporting WHERE report_id = %s
            """, (report['id'],))
            
            item_count = cursor.fetchone()[0]
            
            # Create a report object
            formatted_reports.append({
                "id": report['id'],
                "report_date": report_date,
                "district": district,
                "district_name": district_name,
                "period_type": report['period_type'],
                "max_items": report['max_items'],
                "item_count": item_count,
                "original_filename": report['original_filename'],
                "revised_filename": report['revised_filename'],
                "created_at": report['created_at'].isoformat(),
                "updated_at": report['updated_at'].isoformat()
            })
        
        cursor.close()
        return formatted_reports
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=get_reports_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] == "success":
        return result["result"]
    else:
        logger.error(f"Error getting monthly reports: {result['message']}")
        return []

def delete_monthly_report(report_id):
    """
    Delete a specific monthly newsletter file and its database records.
    
    Args:
        report_id: The ID of the newsletter to delete (index in the list)
        
    Returns:
        Status dictionary
    """
    logger.info(f"Deleting monthly newsletter with ID {report_id}")
    
    def delete_report_operation(connection):
        cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get the report details
        cursor.execute("""
            SELECT id, original_filename, revised_filename
            FROM reports
            WHERE id = %s
        """, (report_id,))
        
        report = cursor.fetchone()
        if not report:
            cursor.close()
            return None
        
        # Delete records from monthly_reporting table
        cursor.execute("""
            DELETE FROM monthly_reporting
            WHERE report_id = %s
        """, (report_id,))
        
        # Delete record from reports table
        cursor.execute("""
            DELETE FROM reports
            WHERE id = %s
        """, (report_id,))
        
        connection.commit()
        cursor.close()
        
        return dict(report)
    
    # Execute the operation with proper connection handling
    result = execute_with_connection(
        operation=delete_report_operation,
        db_host=DB_HOST,
        db_port=DB_PORT,
        db_name=DB_NAME,
        db_user=DB_USER,
        db_password=DB_PASSWORD
    )
    
    if result["status"] != "success":
        return {"status": "error", "message": result["message"]}
    
    if not result["result"]:
        return {"status": "error", "message": f"Report with ID {report_id} not found in database"}
    
    # Now delete the files
    try:
        report = result["result"]
        reports_dir = Path(__file__).parent / 'output' / 'reports'
        
        # Check if the directory exists
        if not reports_dir.exists():
            logger.warning(f"Reports directory does not exist: {reports_dir}")
            return {"status": "error", "message": "Reports directory not found"}
        
        # Delete original file
        original_path = reports_dir / report['original_filename']
        if original_path.exists():
            original_path.unlink()
            logger.info(f"Deleted original newsletter file: {original_path}")
        
        # Delete revised file if it exists
        if report['revised_filename']:
            revised_path = reports_dir / report['revised_filename']
            if revised_path.exists():
                revised_path.unlink()
                logger.info(f"Deleted revised newsletter file: {revised_path}")
        
        return {"status": "success", "message": f"Newsletter {report['original_filename']} and its database records deleted successfully"}
    except Exception as e:
        error_msg = f"Error deleting newsletter files: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

def generate_chart_image(chart_type, params, output_dir=None):
    """
    Generate a static image of a chart for email compatibility.
    
    Args:
        chart_type: Type of chart ('time_series' or 'anomaly')
        params: Parameters for the chart (metric_id, district, etc.)
        output_dir: Directory to save the image (defaults to a temporary directory)
        
    Returns:
        Path to the generated image file
    """
    logger.info(f"Generating static image for {chart_type} chart with params: {params}")
    
    try:
        # Create output directory if not provided
        if not output_dir:
            output_dir = Path(__file__).parent / 'output' / 'charts'
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate a unique filename
        timestamp = int(time.time())
        filename = f"{chart_type}_{timestamp}.png"
        output_path = output_dir / filename
        
        # Construct the URL for the chart
        if chart_type == 'time_series':
            url = f"/backend/time-series-chart?metric_id={params.get('metric_id', 1)}&district={params.get('district', 0)}&period_type={params.get('period_type', 'year')}#chart-section"
        elif chart_type == 'anomaly':
            url = f"/anomaly-analyzer/anomaly-chart?id={params.get('id', 27338)}#chart-section"
        else:
            raise ValueError(f"Unsupported chart type: {chart_type}")
        
        # Use a headless browser service to capture the chart as an image
        # This could be implemented using Selenium, Playwright, or a service like Browserless
        # For this example, we'll use a simple approach with requests and PIL
        
        # Make a request to the chart URL
        full_url = f"{API_BASE_URL}{url}"  # Only use API_BASE_URL for server-side requests
        response = requests.get(full_url)
        if response.status_code != 200:
            logger.error(f"Failed to fetch chart from {full_url}: {response.status_code}")
            return None
        
        # For a real implementation, you would use a headless browser to render the page
        # and capture the chart element as an image
        # For this example, we'll create a placeholder image
        
        # Create a placeholder image (in a real implementation, this would be the actual chart)
        img = Image.new('RGB', (800, 450), color=(255, 255, 255))
        
        # Save the image
        img.save(output_path)
        
        logger.info(f"Generated chart image at {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Error generating chart image: {str(e)}", exc_info=True)
        return None

def generate_email_compatible_report(report_path, output_path=None):
    """
    Generate an email-compatible version of the report by replacing iframes with static images.
    
    Args:
        report_path: Path to the original report HTML file
        output_path: Path to save the email-compatible report (defaults to report_path with _email suffix)
        
    Returns:
        Path to the email-compatible report
    """
    logger.info(f"Generating email-compatible version of report: {report_path}")
    
    try:
        # Set default output path if not provided
        if not output_path:
            report_path_obj = Path(report_path)
            output_path = report_path_obj.parent / f"{report_path_obj.stem}_email{report_path_obj.suffix}"
        
        # Read the original report
        with open(report_path, 'r', encoding='utf-8') as f:
            report_html = f.read()
        
        # Expand chart references in the report
        logger.info(f"Expanding chart references in report for email compatibility: {report_path}")
        expand_result = expand_chart_references(report_path)
        if not expand_result:
            logger.warning("Failed to expand chart references in the report for email compatibility")
            
        # Read the report again after expanding chart references
        with open(report_path, 'r', encoding='utf-8') as f:
            report_html = f.read()
        
        # Find all iframe elements
        import re
        iframe_pattern = r'<iframe[^>]*src="([^"]*)"[^>]*></iframe>'
        iframes = re.findall(iframe_pattern, report_html)
        
        # Replace each iframe with a static image
        for iframe_url in iframes:
            # Extract chart type and parameters from the URL
            if 'time-series-chart' in iframe_url:
                # Extract parameters from URL
                params = {}
                for param in iframe_url.split('?')[1].split('&'):
                    key, value = param.split('=')
                    params[key] = value
                
                # Generate a static image for the time series chart
                image_path = generate_time_series_chart_image(
                    metric_id=params.get('metric_id', 1),
                    district=params.get('district', 0),
                    period_type=params.get('period_type', 'year')
                )
                if not image_path:
                    logger.warning(f"Failed to generate image for {iframe_url}")
                    continue
                
                # Create an img tag to replace the iframe
                img_tag = f'<img src="cid:{image_path.name}" alt="Time Series Chart" style="max-width: 100%; height: auto;" />'
                
                # Replace the iframe with the img tag
                iframe_tag = f'<iframe[^>]*src="{iframe_url}"[^>]*></iframe>'
                report_html = re.sub(iframe_tag, img_tag, report_html)
                
            elif 'anomaly-chart' in iframe_url:
                chart_type = 'anomaly'
                # Extract parameters from URL
                params = {}
                for param in iframe_url.split('?')[1].split('&'):
                    key, value = param.split('=')
                    params[key] = value
                
                # Generate a static image for the chart
                image_path = generate_chart_image(chart_type, params)
                if not image_path:
                    logger.warning(f"Failed to generate image for {iframe_url}")
                    continue
                
                # Create an img tag to replace the iframe
                img_tag = f'<img src="cid:{image_path.name}" alt="Chart" style="max-width: 100%; height: auto;" />'
                
                # Replace the iframe with the img tag
                iframe_tag = f'<iframe[^>]*src="{iframe_url}"[^>]*></iframe>'
                report_html = re.sub(iframe_tag, img_tag, report_html)
            else:
                logger.warning(f"Unsupported iframe URL: {iframe_url}")
                continue
        
        # Write the email-compatible report
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_html)
        
        logger.info(f"Generated email-compatible report at {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Error generating email-compatible report: {str(e)}", exc_info=True)
        return None

def generate_chart_data_url(chart_type, params):
    """
    Generate a chart as a data URL for direct embedding in HTML.
    
    Args:
        chart_type: Type of chart ('time_series' or 'anomaly')
        params: Parameters for the chart (metric_id, district, etc.)
        
    Returns:
        Data URL of the chart image
    """
    logger.info(f"Generating data URL for {chart_type} chart with params: {params}")
    
    try:
        # Construct the URL for the chart
        if chart_type == 'time_series':
            url = f"/backend/time-series-chart?metric_id={params.get('metric_id', 1)}&district={params.get('district', 0)}&period_type={params.get('period_type', 'year')}#chart-section"
        elif chart_type == 'anomaly':
            url = f"/anomaly-analyzer/anomaly-chart?id={params.get('id', 27338)}#chart-section"
        else:
            raise ValueError(f"Unsupported chart type: {chart_type}")
        
        # Use a headless browser service to capture the chart as an image
        # This could be implemented using Selenium, Playwright, or a service like Browserless
        # For this example, we'll use a simple approach with requests and PIL
        
        # Make a request to the chart URL
        full_url = f"{API_BASE_URL}{url}"  # Only use API_BASE_URL for server-side requests
        response = requests.get(full_url)
        if response.status_code != 200:
            logger.error(f"Failed to fetch chart from {full_url}: {response.status_code}")
            return None
        
        # For a real implementation, you would use a headless browser to render the page
        # and capture the chart element as an image
        # For this example, we'll create a placeholder image
        
        # Create a placeholder image (in a real implementation, this would be the actual chart)
        img = Image.new('RGB', (800, 450), color=(255, 255, 255))
        
        # Convert the image to a data URL
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        data_url = f"data:image/png;base64,{img_str}"
        
        logger.info(f"Generated data URL for {chart_type} chart")
        return data_url
        
    except Exception as e:
        logger.error(f"Error generating chart data URL: {str(e)}", exc_info=True)
        return None

def generate_inline_report(report_path, output_path=None):
    """
    Generate a report with inline data URLs for charts, making it suitable for email.
    
    Args:
        report_path: Path to the original report HTML file
        output_path: Path to save the inline report (defaults to report_path with _inline suffix)
        
    Returns:
        Path to the inline report
    """
    logger.info(f"Generating inline version of report: {report_path}")
    
    try:
        # Set default output path if not provided
        if not output_path:
            report_path_obj = Path(report_path)
            output_path = report_path_obj.parent / f"{report_path_obj.stem}_inline{report_path_obj.suffix}"
        
        # Read the original report
        with open(report_path, 'r', encoding='utf-8') as f:
            report_html = f.read()
            
        # Expand chart references in the report
        logger.info(f"Expanding chart references in report for inline version: {report_path}")
        expand_result = expand_chart_references(report_path)
        if not expand_result:
            logger.warning("Failed to expand chart references in the report for inline version")
            
        # Read the report again after expanding chart references
        with open(report_path, 'r', encoding='utf-8') as f:
            report_html = f.read()
        
        # Find all iframe elements
        import re
        iframe_pattern = r'<iframe[^>]*src="([^"]*)"[^>]*></iframe>'
        iframes = re.findall(iframe_pattern, report_html)
        
        # Replace each iframe with an inline image
        for iframe_url in iframes:
            # Extract chart type and parameters from the URL
            if 'time-series-chart' in iframe_url:
                # Extract parameters from URL
                params = {}
                for param in iframe_url.split('?')[1].split('&'):
                    key, value = param.split('=')
                    params[key] = value
                
                # Generate a data URL for the time series chart
                data_url = generate_time_series_chart_data_url(
                    metric_id=params.get('metric_id', 1),
                    district=params.get('district', 0),
                    period_type=params.get('period_type', 'year')
                )
                if not data_url:
                    logger.warning(f"Failed to generate data URL for {iframe_url}")
                    continue
                
                # Create an img tag with the data URL
                img_tag = f'<img src="{data_url}" alt="Time Series Chart" style="max-width: 100%; height: auto;" />'
                
                # Replace the iframe with the img tag
                iframe_tag = f'<iframe[^>]*src="{iframe_url}"[^>]*></iframe>'
                report_html = re.sub(iframe_tag, img_tag, report_html)
                
            elif 'anomaly-chart' in iframe_url:
                chart_type = 'anomaly'
                # Extract parameters from URL
                params = {}
                for param in iframe_url.split('?')[1].split('&'):
                    key, value = param.split('=')
                    params[key] = value
                
                # Generate a data URL for the chart
                data_url = generate_chart_data_url(chart_type, params)
                if not data_url:
                    logger.warning(f"Failed to generate data URL for {iframe_url}")
                    continue
                
                # Create an img tag with the data URL
                img_tag = f'<img src="{data_url}" alt="Chart" style="max-width: 100%; height: auto;" />'
                
                # Replace the iframe with the img tag
                iframe_tag = f'<iframe[^>]*src="{iframe_url}"[^>]*></iframe>'
                report_html = re.sub(iframe_tag, img_tag, report_html)
            else:
                logger.warning(f"Unsupported iframe URL: {iframe_url}")
                continue
        
        # Write the inline report
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_html)
        
        logger.info(f"Generated inline report at {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Error generating inline report: {str(e)}", exc_info=True)
        return None

def generate_time_series_chart_image(metric_id, district, period_type, output_dir=None):
    """
    Generate a static image of a time series chart for email compatibility.
    
    Args:
        metric_id: ID of the metric to chart
        district: District number
        period_type: Period type (year, month, etc.)
        output_dir: Directory to save the image (defaults to a temporary directory)
        
    Returns:
        Path to the generated image file
    """
    logger.info(f"Generating static image for time series chart: metric_id={metric_id}, district={district}, period_type={period_type}")
    
    try:
        # Create output directory if not provided
        if not output_dir:
            output_dir = Path(__file__).parent / 'output' / 'charts'
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate a unique filename
        timestamp = int(time.time())
        filename = f"time_series_{metric_id}_{district}_{period_type}_{timestamp}.png"
        output_path = output_dir / filename
        
        # Construct the URL for the chart
        url = f"/backend/time-series-chart?metric_id={metric_id}&district={district}&period_type={period_type}#chart-section"
        
        # Use a headless browser service to capture the chart as an image
        # This could be implemented using Selenium, Playwright, or a service like Browserless
        # For this example, we'll use a simple approach with requests and PIL
        
        # Make a request to the chart URL
        full_url = f"{API_BASE_URL}{url}"  # Only use API_BASE_URL for server-side requests
        response = requests.get(full_url)
        if response.status_code != 200:
            logger.error(f"Failed to fetch chart from {full_url}: {response.status_code}")
            return None
        
        # For a real implementation, you would use a headless browser to render the page
        # and capture the chart element as an image
        # For this example, we'll create a placeholder image
        
        # Create a placeholder image (in a real implementation, this would be the actual chart)
        img = Image.new('RGB', (800, 450), color=(255, 255, 255))
        
        # Save the image
        img.save(output_path)
        
        logger.info(f"Generated time series chart image at {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Error generating time series chart image: {str(e)}", exc_info=True)
        return None

def generate_time_series_chart_data_url(metric_id, district, period_type):
    """
    Generate a time series chart as a data URL for direct embedding in HTML.
    
    Args:
        metric_id: ID of the metric to chart
        district: District number
        period_type: Period type (year, month, etc.)
        
    Returns:
        Data URL of the chart image
    """
    logger.info(f"Generating data URL for time series chart: metric_id={metric_id}, district={district}, period_type={period_type}")
    
    try:
        # Construct the URL for the chart
        url = f"/backend/time-series-chart?metric_id={metric_id}&district={district}&period_type={period_type}#chart-section"
        
        # Use a headless browser service to capture the chart as an image
        # This could be implemented using Selenium, Playwright, or a service like Browserless
        # For this example, we'll use a simple approach with requests and PIL
        
        # Make a request to the chart URL
        full_url = f"{API_BASE_URL}{url}"  # Only use API_BASE_URL for server-side requests
        response = requests.get(full_url)
        if response.status_code != 200:
            logger.error(f"Failed to fetch chart from {full_url}: {response.status_code}")
            return None
        
        # For a real implementation, you would use a headless browser to render the page
        # and capture the chart element as an image
        # For this example, we'll create a placeholder image
        
        # Create a placeholder image (in a real implementation, this would be the actual chart)
        img = Image.new('RGB', (800, 450), color=(255, 255, 255))
        
        # Convert the image to a data URL
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        data_url = f"data:image/png;base64,{img_str}"
        
        logger.info(f"Generated data URL for time series chart")
        return data_url
        
    except Exception as e:
        logger.error(f"Error generating time series chart data URL: {str(e)}", exc_info=True)
        return None

class PathEncoder(json.JSONEncoder):
    """Custom JSON encoder that can handle Path objects"""
    def default(self, obj):
        if isinstance(obj, Path):
            return str(obj)
        return super().default(obj)

def expand_chart_references(report_path):
    """
    Process a report file and replace simplified chart references with full HTML.
    
    Args:
        report_path: Path to the report file to process
        
    Returns:
        Path to the processed report file
    """
    logger.info(f"Expanding chart references in report: {report_path}")
    
    try:
        # Read the report file
        with open(report_path, 'r', encoding='utf-8') as f:
            report_html = f.read()
        
        # Define patterns for simplified chart references
        time_series_pattern = r'\[CHART:time_series:(\d+):(\d+):(\w+)\]'
        anomaly_pattern = r'\[CHART:anomaly:(\d+)\]'
        
        # Define pattern for direct image references
        image_pattern_with_alt = r'<img[^>]*src="([^"]+)"[^>]*alt="([^"]+)"[^>]*>'
        image_pattern_without_alt = r'<img[^>]*src="([^"]+)"[^>]*>'
        
        # Replace time series chart references
        def replace_time_series(match):
            metric_id = match.group(1)
            district = match.group(2)
            period_type = match.group(3)
            
            return f"""
<div style="position: relative; width: 100%; height: 0; padding-bottom: 80%; margin-bottom: 30px;">
    <iframe src="/backend/time-series-chart?metric_id={metric_id}&district={district}&period_type={period_type}#chart-section" style="width: 100%; height: 100%; border: none; position: absolute; top: 0; left: 0;" frameborder="0" scrolling="no"></iframe>  
</div>
"""
        
        # Replace anomaly chart references
        def replace_anomaly(match):
            anomaly_id = match.group(1)
            
            return f"""
<div style="position: relative; width: 100%; height: 0; padding-bottom: 100%; margin-bottom: 30px;">
    <iframe src="/anomaly-analyzer/anomaly-chart?id={anomaly_id}#chart-section" style="width: 100%; height: 100%; border: none; position: absolute; top: 0; left: 0;" frameborder="0" scrolling="no"></iframe>
</div>
"""
        
        # Replace direct image references with iframes
        def replace_image_with_alt(match):
            img_tag = match.group(0)
            img_src = match.group(1)
            img_alt = match.group(2)
            
            # Check if it's an anomaly chart image
            if img_src.startswith("anomaly_"):
                # Extract anomaly ID from the filename
                parts = img_src.split("_")
                if len(parts) >= 2:
                    anomaly_id = parts[1].split(".")[0]  # Remove file extension
                    
                    return f"""
<div style="position: relative; width: 100%; height: 0; padding-bottom: 100%; margin-bottom: 30px;">
  <div style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
    <iframe src="/anomaly-analyzer/anomaly-chart?id={anomaly_id}#chart-section" style="width: 100%; height: 100%; border: none; position: absolute; top: 0; left: 0;" frameborder="0" scrolling="no"></iframe>
  </div>
</div>
"""
            
            # If we can't determine the chart type, keep the original image
            return img_tag
        
        def replace_image_without_alt(match):
            img_tag = match.group(0)
            img_src = match.group(1)
            
            # Check if it's an anomaly chart image
            if img_src.startswith("anomaly_"):
                # Extract anomaly ID from the filename
                parts = img_src.split("_")
                if len(parts) >= 2:
                    anomaly_id = parts[1].split(".")[0]  # Remove file extension
                    
                    return f"""
<div style="position: relative; width: 100%; height: 0; padding-bottom: 100%; margin-bottom: 30px;">
  <div style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">
    <iframe src="/anomaly-analyzer/anomaly-chart?id={anomaly_id}#chart-section" style="width: 100%; height: 100%; border: none; position: absolute; top: 0; left: 0;" frameborder="0" scrolling="no"></iframe>
  </div>
</div>
"""
            
            # If we can't determine the chart type, keep the original image
            return img_tag
        
        # Apply replacements
        report_html = re.sub(time_series_pattern, replace_time_series, report_html)
        report_html = re.sub(anomaly_pattern, replace_anomaly, report_html)
        report_html = re.sub(image_pattern_with_alt, replace_image_with_alt, report_html)
        report_html = re.sub(image_pattern_without_alt, replace_image_without_alt, report_html)
        
        # Write the processed report
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_html)
        
        logger.info(f"Expanded chart references in report: {report_path}")
        return report_path
        
    except Exception as e:
        error_msg = f"Error in expand_chart_references: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return report_path

if __name__ == "__main__":
    # Run the monthly report process
    # You can adjust the max_report_items to control how many items appear in the report
    result = run_monthly_report_process(max_report_items=3, district="0")
    print(json.dumps(result, indent=2, cls=PathEncoder)) 