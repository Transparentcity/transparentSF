import os
import json
import qdrant_client
from openai import OpenAI
import openai
from swarm import Swarm, Agent
from tools.anomaly_detection import anomaly_detection
import pandas as pd
from dotenv import load_dotenv
import logging
from tools.data_fetcher import set_dataset
from tools.vector_query import query_docs  #
from tools.genChart import generate_time_series_chart
from tools.retirementdata import read_csv_with_encoding
from tools.genGhostPost import generate_ghost_post
from pathlib import Path
# Import FastAPI and related modules
from fastapi import APIRouter, Request, Cookie
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uuid
import datetime
import sys

# ------------------------------
# Configuration and Setup
# ------------------------------

load_dotenv()
# Confirm that there is an openai_api_key set
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

# Initialize APIRouter
router = APIRouter()

# Serve static files and templates
# Create static directory if it doesn't exist
if not os.path.exists("static"):
    os.makedirs("static")
router.mount("/static", StaticFiles(directory="static"), name="static")
# Mount the static directory
templates = Jinja2Templates(directory="templates")

# Configure logging with more detailed format
# Get absolute path for logs directory
current_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(current_dir, 'logs')
log_file = os.path.join(logs_dir, 'webchat.log')

# Create logs directory if it doesn't exist
os.makedirs(logs_dir, exist_ok=True)

# Test write access and log startup
try:
    with open(log_file, 'a', encoding='utf-8') as f:
        startup_message = f"\n{'='*50}\nLog started at {datetime.datetime.now()}\nLog file: {log_file}\nPython path: {sys.executable}\n{'='*50}\n"
        f.write(startup_message)
except Exception as e:
    print(f"Error writing to log file: {e}")
    raise

# Configure root logger first
logging.basicConfig(
    level=logging.INFO,  # Set to INFO level
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Log to stdout for debugging
        logging.FileHandler(log_file, mode='a', encoding='utf-8')
    ]
)

# Configure our module's logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Remove any existing handlers to avoid duplication
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Add handlers specifically for our logger
file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
stream_handler = logging.StreamHandler(sys.stdout)

# Create a custom formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# Test the logger
logger.info(f"WebChat logging initialized at {datetime.datetime.now()}")
logger.info(f"Log file location: {log_file}")

# Initialize connections
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

# Initialize OpenAI client for direct API calls
openai.api_key = openai_api_key

# Initialize the Swarm client
swarm_client = Swarm()
client = OpenAI()

qdrant = qdrant_client.QdrantClient(host="localhost", port=6333)

# Set embedding model
EMBEDDING_MODEL = "text-embedding-3-large"
# AGENT_MODEL = "gpt-3.5-turbo-16k"
# AGENT_MODEL = "gpt-3.5-turbo-16k"
AGENT_MODEL = "gpt-4o"

# Set Qdrant collection
collection_name = "SFPublicData"

# Session management (simple in-memory store)
sessions = {}
# Load and combine the data
data_folder = './data'  # Replace with the actual path

# Initialize context_variables with an empty DataFrame
combined_df = {"dataset": pd.DataFrame()}

# Add environment check at the top with other configurations
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

def load_and_combine_notes():
    logger = logging.getLogger(__name__)
    
    script_dir = Path(__file__).parent
    output_dir = script_dir / 'output'
    combined_text = ""
    total_files = 0
    
    # Find and load all summary text files
    for summary_file in output_dir.rglob('*_summary.txt'):
        try:
            with open(summary_file, 'r', encoding='utf-8') as f:
                content = f.read()
                combined_text += f"\n{'='*80}\nSummary: {summary_file.name}\n{'='*80}\n\n{content}\n\n"
                total_files += 1
        except Exception as e:
            logger.error(f"Error reading summary file {summary_file}: {e}")
    
    # Load YTD metrics
    ytd_file = script_dir / 'output' / 'dashboard' / 'district_0.json'  # Use citywide data
    if ytd_file.exists():
        logger.info("Loading current YTD metrics")
        try:
            with open(ytd_file, 'r', encoding='utf-8') as f:
                ytd_data = json.load(f)
            
            # Format YTD metrics as text
            ytd_text = "\nYear-to-Date (YTD) Metrics Summary:\n\n"
            
            # Add citywide metrics
            ytd_text += f"Citywide Statistics:\n"
            for category in ytd_data.get("categories", []):
                ytd_text += f"\n{category['category']}:\n"
                for metric in category.get("metrics", []):
                    name = metric.get("name", "")
                    this_year = metric.get("thisYear", 0)
                    last_year = metric.get("lastYear", 0)
                    last_date = metric.get("lastDataDate", "")
                    
                    # Calculate percent change
                    if last_year != 0:
                        pct_change = ((this_year - last_year) / last_year) * 100
                        change_text = f"({pct_change:+.1f}% vs last year)"
                    else:
                        change_text = "(no prior year data)"
                    
                    ytd_text += f"- {name}: {this_year:,} {change_text} as of {last_date}\n"
            
            combined_text += "\n" + ytd_text
            logger.info("Successfully added YTD metrics to notes")
            
        except Exception as e:
            logger.error(f"Error processing YTD metrics: {e}")
    
    logger.info(f"""
Notes loading complete:
Total files processed: {total_files}
Total combined length: {len(combined_text)} characters
First 100 characters: {combined_text[:100]}
""")
    
    return combined_text

combined_notes=load_and_combine_notes() 

def load_and_combine_climate_data():
    data_folder = 'data/climate'
    vera_file = os.path.join(data_folder, 'vcus-2025.csv')
    
    # Load the CSV file with detected encoding
    try:
        vera_df = pd.read_csv(vera_file)
        logger = logging.getLogger(__name__)
        logger.info(f"Successfully loaded climate data from {vera_file}")
        logger.info(f"DataFrame shape: {vera_df.shape}")
        return vera_df
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error loading climate data: {e}")
        return pd.DataFrame()  # Return empty DataFrame on error

# Load the climate data
combined_df = {"dataset": pd.DataFrame()}

context_variables = {
    "dataset": combined_df,  # Store the actual DataFrame here
    "notes": combined_notes  # Store the notes string here
}

# Define the maximum number of messages to keep in context
MAX_HISTORY = 10
SUMMARY_INTERVAL = 10

def get_dataset(context_variables, *args, **kwargs):
    """
    Returns the dataset for analysis.
    """
    dataset = context_variables.get("dataset")
    if dataset is not None and not dataset.empty:
        return dataset
    else:
        return {'error': 'Dataset is not available or is empty.'}

def get_notes(context_variables, *args, **kwargs):
    """
    Returns the notes from context variables, with length checking and logging.
    """
    logger = logging.getLogger(__name__)
    
    try:
        notes = context_variables.get("notes", "").strip()
        total_length = len(notes)
        
        logger.info(f"""
=== get_notes called ===
Total length: {total_length} characters
Approximate tokens: {len(notes.split())}
First 100 chars: {notes[:100]}
Number of lines: {len(notes.splitlines())}
""")
        
        # If notes exceed OpenAI's limit, truncate them
        if total_length > MAX_MESSAGE_LENGTH:
            logger.warning(f"""
Notes exceed maximum length:
Current length: {total_length}
Maximum allowed: {MAX_MESSAGE_LENGTH}
Difference: {total_length - MAX_MESSAGE_LENGTH}
""")
            # Keep the first part and last part with a message in between
            keep_length = (MAX_MESSAGE_LENGTH // 2) - 100  # Leave room for the truncation message
            truncation_message = "\n\n[CONTENT TRUNCATED DUE TO LENGTH]\n\n"
            notes = notes[:keep_length] + truncation_message + notes[-keep_length:]
            logger.info(f"""
Notes truncated:
New length: {len(notes)}
Truncation point: {keep_length}
""")
        
        if notes:
            return {"notes": notes}
        else:
            logger.error("No notes found or notes are empty")
            return {"error": "No notes found or notes are empty"}
            
    except Exception as e:
        logger.error(f"Error in get_notes: {str(e)}")
        return {"error": f"Error processing notes: {str(e)}"}

def get_columns(context_variables, *args, **kwargs):

    """
    Returns the list of columns in the dataset.
    """
    dataset = context_variables.get("dataset")
    if dataset is not None and not dataset.empty:
        return {"columns": dataset.columns.tolist()}
    else:
        return {"error": "Dataset is not available or is empty."}

def set_columns(context_variabls, *args, **kwargs):
    """
    Sets the columns for the query.
    """
    columns = kwargs.get("columns")
    if columns:
        return {"columns": columns}
    else:
        return {"error": "No columns provided."}

def get_data_summary(context_variables,*args, **kwargs):
    """
    Returns a statistical summary of the dataset.
    """
    dataset = context_variables.get("dataset")
    if dataset is not None:
        summary = dataset.describe(include='all').to_dict()
        return {"summary": summary}
    else:
        return {"error": "Dataset is not available."}     

def transfer_to_analyst_agent(context_variables, *args, **kwargs):
    """
    Transfers the conversation to the Anomaly Finder Agent.
    """
    return analyst_agent

def transfer_to_researcher_agent(context_variables, *args, **kwargs):
    """
    Transfers the conversation to the Data Agent.
    """
    return Researcher_agent

def get_dashboard_metric(context_variables, district_number=0, metric_id=None):
    """
    Retrieves dashboard metric data for a specific district and metric.
    
    Args:
        context_variables: The context variables dictionary
        district_number: The district number (0 for citywide, 1-11 for specific districts). Can be int or string.
        metric_id: The specific metric ID to retrieve. If None, returns the top_level.json file.
    
    Returns:
        A dictionary containing the metric data or error message
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Convert district_number to int if it's a string
        try:
            district_number = int(district_number)
        except (TypeError, ValueError):
            return {"error": f"Invalid district number format: {district_number}. Must be a number between 0 and 11."}
        
        # Validate district number range
        if district_number < 0 or district_number > 11:
            return {"error": f"Invalid district number: {district_number}. Must be between 0 (citywide) and 11."}
        
        # Construct the base path - looking one level up from the script
        script_dir = Path(__file__).parent
        dashboard_dir = script_dir / 'output' / 'dashboard'
        
        logger.info(f"Looking for dashboard data in: {dashboard_dir}")
        
        # If metric_id is None, return the top-level district summary
        if metric_id is None:
            file_path = dashboard_dir / f"district_{district_number}.json"
            logger.info(f"Fetching top-level dashboard data for district {district_number} from {file_path}")
        else:
            # Metric ID is provided, look in the district-specific folder
            district_folder = dashboard_dir / str(district_number)
            
            # If metric_id doesn't end with .json, add it
            if not metric_id.endswith('.json'):
                metric_id = f"{metric_id}.json"
                
            file_path = district_folder / metric_id
            logger.info(f"Fetching specific metric '{metric_id}' for district {district_number} from {file_path}")
        
        # Check if the file exists
        if not file_path.exists():
            # If specific metric file doesn't exist, list available metrics
            if metric_id is not None:
                available_metrics = []
                district_folder = dashboard_dir / str(district_number)
                if district_folder.exists():
                    available_metrics = [f.name for f in district_folder.glob('*.json')]
                
                return {
                    "error": f"Metric '{metric_id}' not found for district {district_number}",
                    "available_metrics": available_metrics
                }
            else:
                return {"error": f"Dashboard data not found for district {district_number}"}
        
        # Read and parse the JSON file
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Add metadata about the source
        result = {
            "source": str(file_path),
            "district": district_number,
            "metric_id": metric_id,
            "data": data
        }
        
        logger.info(f"Successfully retrieved dashboard metric data from {file_path}")
        return result
        
    except Exception as e:
        logger.error(f"Error retrieving dashboard metric: {str(e)}", exc_info=True)
        return {"error": f"Error retrieving dashboard metric: {str(e)}"}

# Define the anomaly finder agent
analyst_agent = Agent(
    model=AGENT_MODEL,
    name="Analyst",
     instructions="""
    **Function Usage:**

    - Use `query_docs(context_variables, "SFPublicData", query)` to search for datasets. The `query` parameter is a string describing the data the user is interested in. always pass the context_variables and the collection name is allways "SFPublicData"
    - Use the `transfer_to_researcher_agent` function (without any parameters) to transfer to the researcher agent. 
    - Use `set_dataset(context_variables, endpoint="dataset-id.json", query="your-soql-query")` to set the dataset. Both parameters are required:
        - endpoint: The dataset identifier (e.g., 'ubvf-ztfx.json')  It should come from a query_docs() call.
        - query: The complete SoQL query string using standard SQL syntax (e.g., 'select field1, field2 where condition')
        Example usage:
        set_dataset(context_variables, endpoint="ubvf-ztfx.json", query="select unique_id, collision_date, number_injured where accident_year = 2025")
    - Use `generate_time_series_chart(context_variables, column_name, start_date, end_date, aggregation_period, return_html=False)` to generate a time series chart. 
    - Use `get_dashboard_metric(context_variables, district_number, metric_id)` to retrieve dashboard metric data:
        - district_number: Integer from 0 (citywide) to 11 (specific district)
        - metric_id: Optional. The specific metric ID to retrieve (e.g., '🚨_violent_crime_incidents_ytd'). If not provided, returns the top-level district summary.

    """,
    # functions=[get_notes, query_docs, transfer_to_researcher_agent],
    functions=[query_docs, set_dataset, get_dataset, set_columns, get_data_summary, anomaly_detection, generate_time_series_chart, get_dashboard_metric, transfer_to_researcher_agent],
    context_variables=context_variables,
    debug=True,
)

def update_agent_instructions_with_columns(columns):
    """
    Updates the analyst agent's instructions to include the available columns.
    """
    column_list_str = ', '.join(columns)
    logger.info(f"Updating agent instructions with columns: {column_list_str}")  # Log before updating

    # Split the base instructions at the Dataset Awareness section if it exists
    base_instructions = analyst_agent.instructions.split("Dataset Awareness:", 1)[0]

    # Create the new instructions by combining base instructions with column information
    analyst_agent.instructions = base_instructions + f"""Dataset Awareness:
Available Columns in the Dataset:
You have access to the following columns: {column_list_str}. ANNOUNCE THESE TO THE USER.
"""

def load_and_combine_climate_data():
    data_folder = 'data/climate'
    vera_file = os.path.join(data_folder, 'cleaned_vcusNov19.csv')
    
    # Load the CSV files with detected encoding
    vera_df = read_csv_with_encoding(vera_file)
    
    # Combine the DataFrames
    set_dataset_in_context(context_variables, vera_df)

    return vera_df

def format_table(context_variables, title=None):
    """
    Formats data from context_variables into a markdown table with an optional title.
    Args:
        context_variables: The context variables containing the dataset
        title: Optional title for the table
    Returns:
        The formatted table as markdown
    """
    logger.info(f"""
=== format_table called ===
Title: {title}
Context variables keys: {list(context_variables.keys())}
""")
    
    try:
        data = context_variables.get("dataset")
        logger.info(f"""
Data retrieved from context:
Type: {type(data)}
Is None: {data is None}
""")
            
        if data is None:
            logger.error("No dataset found in context")
            return {"error": "No dataset found in context"}
            
        # If it's a dict with 'status' or 'error', return that directly
        if isinstance(data, dict):
            logger.info(f"Data is dict with keys: {list(data.keys())}")
            if 'error' in data:
                logger.error(f"Error in data: {data['error']}")
                return {"error": data['error']}
            if 'status' in data:
                logger.info(f"Status in data: {data['status']}")
                return {"status": data['status']}
        
        # Format as DataFrame
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            df = data
            logger.info(f"""
DataFrame details:
Shape: {df.shape}
Columns: {df.columns.tolist()}
""")
        else:
            logger.info(f"Converting data to DataFrame from type: {type(data)}")
            df = pd.DataFrame(data)

        # Get total row count
        total_rows = len(df)
        rows_per_page = 50
        
        logger.info(f"""
Pagination details:
Total rows: {total_rows}
Rows per page: {rows_per_page}
""")
        
        # Start with padding and separator
        markdown = "\n---\n"
        
        if title:
            markdown += f"### {title}\n\n"
        
        # Add dataset size info
        if total_rows > rows_per_page:
            markdown += f"*Showing first {rows_per_page} of {total_rows} rows*\n\n"
            # Take only first 50 rows for display
            display_df = df.head(rows_per_page)
        else:
            markdown += f"*Total rows: {total_rows}*\n\n"
            display_df = df
            
        # Convert DataFrame to markdown and add padding
        table_md = display_df.to_markdown(index=False)
        markdown += table_md + "\n"
        
        # Add pagination info if needed
        if total_rows > rows_per_page:
            remaining = total_rows - rows_per_page
            markdown += f"\n*{remaining:,} more rows not shown*\n"
        
        # Add bottom padding and separator
        markdown += "---\n"
        
        logger.info(f"""
Generated markdown:
Length: {len(markdown)}
Preview: {markdown[:500]}...
""")
        
        # Store pagination info in context for potential follow-up
        context_variables["table_pagination"] = {
            "total_rows": total_rows,
            "current_page": 1,
            "rows_per_page": rows_per_page,
            "total_pages": (total_rows + rows_per_page - 1) // rows_per_page
        }
        
        # Return the markdown directly - no more direct response bypass
        return {"status": "Table formatted successfully", "content": markdown}
        
    except Exception as e:
        logger.error(f"Error formatting table: {str(e)}", exc_info=True)
        return {"error": f"Error formatting table: {str(e)}"}

# New function to get dashboard metric data

# Add a new function to handle pagination
def format_table_page(context_variables, page_number, title=None):
    """
    Formats a specific page of the dataset.
    Args:
        context_variables: The context variables containing the dataset
        page_number: The page number to display (1-based)
        title: Optional title for the table
    Returns:
        Status message for the LLM
    """
    try:
        data = context_variables.get("dataset")
        if data is None:
            return {"error": "No dataset found in context"}
            
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            df = data
        else:
            df = pd.DataFrame(data)

        pagination = context_variables.get("table_pagination", {})
        if not pagination:
            return {"error": "No pagination info found. Please display the table first."}

        rows_per_page = pagination["rows_per_page"]
        total_rows = len(df)
        total_pages = (total_rows + rows_per_page - 1) // rows_per_page

        if not (1 <= page_number <= total_pages):
            return {"error": f"Invalid page number. Please specify a page between 1 and {total_pages}"}

        start_idx = (page_number - 1) * rows_per_page
        end_idx = min(start_idx + rows_per_page, total_rows)
        
        markdown = "\n---\n"
        
        if title:
            markdown += f"### {title}\n\n"
        
        markdown += f"*Page {page_number} of {total_pages} (rows {start_idx + 1}-{end_idx} of {total_rows})*\n\n"
            
        # Get the rows for this page
        display_df = df.iloc[start_idx:end_idx]
        table_md = display_df.to_markdown(index=False)
        markdown += table_md + "\n"
        
        # Add navigation info
        nav_info = []
        if page_number > 1:
            nav_info.append(f"Previous: Page {page_number - 1}")
        if page_number < total_pages:
            nav_info.append(f"Next: Page {page_number + 1}")
        
        if nav_info:
            markdown += f"\n*{' | '.join(nav_info)}*\n"
        
        markdown += "---\n"
        
        # Store the formatted table in context for direct streaming
        context_variables["last_formatted_table"] = markdown
        
        # Update pagination info
        context_variables["table_pagination"]["current_page"] = page_number
        
        return {"status": "Table page formatted successfully", "pagination": context_variables["table_pagination"]}
        
    except Exception as e:
        logger.error(f"Error formatting table page: {str(e)}")
        return {"error": f"Error formatting table page: {str(e)}"}

# Function mapping
function_mapping = {
    'transfer_to_analyst_agent': transfer_to_analyst_agent,
    'transfer_to_researcher_agent': transfer_to_researcher_agent,
    'get_dataset': get_dataset,
    'get_notes': get_notes,
    'get_columns': get_columns,
    'get_data_summary': get_data_summary,
    'anomaly_detection': anomaly_detection,
    'query_docs': query_docs,
    'set_dataset': set_dataset,
    'generate_time_series_chart': generate_time_series_chart,
    'get_dashboard_metric': get_dashboard_metric,
    'format_table': format_table,
    'format_table_page': format_table_page,
}


Researcher_agent = Agent(
    model=AGENT_MODEL,
    name="Researcher",
    instructions="""
    Role: You are a researcher for Transparent SF, focusing on trends in city data.
    Purpose: help the user find objective data and specific details on their question. 
    
    - get_notes() ALWAYS Start here. This is a summary of all the analysis you have available to you in your docs. Use it to determine what data is available, and what to search for in your query_docs() calls.  It contains no links or charts, so don't share any links or charts with the user without checking your docs first. 
    - Use query_docs("collection_name=<Collection_Name>", query=<words or phrases>) to review analysis of city data.  THis is a semantic search of Qdrant, not a SQL query
        There are many collections you can search. Sometimes you might want to look at multiple collections to get the data you need. 
        
        Each collection is named as follows:
        
        timeframe_location
    
        timeframes are one of the following:
        annual
        monthly
    
        location is one of the following:
        citywide
        or
        district_<number>
        
        So one example collection is "annual_citywide"
        another example is "monthly_district_1"

        There is a collection called "YTD" that contains the year to date metrics for the city of San Francisco.  Use this to get the latest metrics for the city.  It also includes details by date and has queries you can modift to get more details or data you need.

        There is also a special collection called "SFPublicData" that contains all the data from the city of San Francisco including all the table names and column names you would need to set_dataset because the answer to the user's question isn't in the other collections.  Call this before you use set_dataset().

        If, after searching through your docs, you can't find the data you need, you can check to see if there is a table available in the "SFPublicData" collection that you might want to query. 
        Be specific in your queries to answer the exact question the user is asking.  Select the minimum amount of data necessary to answer the question and how the query do as much of the processing as possible. 
        If you can't be precise in your query, don't set_dataset, just answer the user with the info you have. 

        - Use `set_dataset(endpoint="dataset-id.json", query="your-soql-query")` to set the dataset. Both parameters are required:
        - endpoint: The dataset identifier (e.g., 'ubvf-ztfx.json')  It should come from a query_docs() call.
        - query: The complete SoQL query string using standard SQL syntax (e.g., 'select field1, field2 where condition')
        Example usage:
        set_dataset(endpoint="ubvf-ztfx.json", query="select unique_id, collision_date, number_injured where accident_year = 2025")

        - Use `get_dashboard_metric(district_number, metric_id)` to retrieve dashboard metric data:
          - district_number: Integer from 0 (citywide) to 11 (specific district)
          - metric_id: Optional. The specific metric ID to retrieve (e.g., '🚨_violent_crime_incidents_ytd'). If not provided, returns the top-level district summary.
          Example usage:
          get_dashboard_metric(0, "🚨_violent_crime_incidents_ytd") # Get citywide violent crime data
          get_dashboard_metric(5) # Get all metrics for District 5

        Query Format:
        - Use standard SQL syntax: "select field1, field2 where condition"
        - Do not use $ prefixes in your queries
        - No FROM clause is needed, just make sure to pass in the endpoint.
        - Example: "select dba_name, location_start_date where supervisor_district = '6' and location_start_date >= '2025-01-01'"
        
    When displaying data:
    1. Whenever possible, use charts and graphs from your docs to illustrate your findings.  To better find charts add the term charts to your query.
    2. Return them in the same markdown format you find in the docs, with no changes. DO NOT ADD or CHANGE THE URLS
    3. Include relevant titles and context with your tables and charts. 
    4. Follow up tables with explanations of key insights or trends. 
    5. If you can't find the data you need, just say so.  Don't make up data or information. 
    6. Don't speculate as to causes or "WHY" something is happening.  Just report on WHAT is happening. 
    
    """,
    functions=[get_notes, query_docs, set_dataset, get_dashboard_metric, transfer_to_analyst_agent, generate_ghost_post],
    context_variables=context_variables,
    debug=True
)

def set_dataset_in_context(context_variables, dataset):
    """
    Sets the dataset in the context variables and updates the agent's instructions with the column names.
    """
    context_variables["dataset"] = dataset
    
    # Retrieve and update column names in the agent's instructions
    columns = get_columns(context_variables).get("columns", [])
    if columns:
        update_agent_instructions_with_columns(columns)

def load_data(context_variables):
    """
    Loads and combines data, setting the dataset and updating the agent instructions with column names.
    """
    combined_data=combined_df["dataset"]
    # combined_data = load_and_combine_climate_data()
    set_dataset_in_context(context_variables, combined_data)
    return combined_data

# Ensure the dataset is set in the context variables when initializing
combined_df = {"dataset": load_data(context_variables)}

def process_and_print_streaming_response(response):
    content = ""
    last_sender = ""
    MAX_BUFFER_SIZE = 1024 * 1024  # 1MB limit

    for chunk in response:
        # Reset buffer if it gets too large
        if len(content) > MAX_BUFFER_SIZE:
            content = ""
            logger.warning("Content buffer exceeded maximum size, clearing buffer")

        if "sender" in chunk:
            last_sender = chunk["sender"]

        if "content" in chunk and chunk["content"] is not None:
            if not content and last_sender:
                print(f"\033[94m{last_sender}:\033[0m", end=" ", flush=True)
                last_sender = ""
            print(chunk["content"], end="", flush=True)
            content += chunk["content"]

        if "tool_calls" in chunk and chunk["tool_calls"] is not None:
            for tool_call in chunk["tool_calls"]:
                f = tool_call["function"]
                name = f["name"]
                if not name:
                    continue
                print(f"\033[94m{last_sender}: \033[95m{name}\033[0m()")

                function_to_call = function_mapping.get(name)
                if function_to_call:
                    try:
                        result = function_to_call(**json.loads(f["arguments"]))
                        
                        # Add logging for chart generation results
                        if name == "generate_time_series_chart":
                            logger.debug(f"Chart generation result type: {type(result)}")
                            if isinstance(result, tuple):
                                markdown_content, _ = result  # Ignore the HTML content
                                logger.info(f"Chart generated successfully. Markdown length: {len(markdown_content)}")
                                
                                # Only store and send the markdown content
                                content = ""  # Clear the buffer after processing
                                return markdown_content  # Return just the markdown portion
                            else:
                                logger.debug(f"Unexpected result format: {result}")
                        
                        content = ""  # Clear the buffer after processing
                        return result
                    except Exception as e:
                        logger.error(f"Error processing tool call {name}: {e}")
                        content = ""  # Clear the buffer on error
                        return None

        if "delim" in chunk and chunk["delim"] == "end" and content:
            print()  # End of response message
            content = ""  # Clear the buffer

        if "response" in chunk:
            content = ""  # Clear the buffer
            return chunk["response"]

    # Clear any remaining content at the end
    content = ""
    return None

def pretty_print_messages(messages) -> None:
    for message in messages:
        if message["role"] != "assistant":
            continue

        # print agent name in blue
        print(f"\033[94m{message['sender']}\033[0m:", end=" ")

        # print response, if any
        if message["content"]:
            print(message["content"])

        # print tool calls in purple, if any
        tool_calls = message.get("tool_calls") or []
        if len(tool_calls) > 1:
            print()
        for tool_call in tool_calls:
            f = tool_call["function"]
            name, args = f["name"], f["arguments"]
            arg_str = json.dumps(json.loads(args)).replace(":", "=")
            print(f"\033[95m{name}\033[0m({arg_str[1:-1]})")


function_mapping = {
    'transfer_to_analyst_agent': transfer_to_analyst_agent,
    'transfer_to_researcher_agent': transfer_to_researcher_agent,
    'get_dataset': get_dataset,
    'get_notes': get_notes,
    'get_columns': get_columns,
    'get_data_summary': get_data_summary,
    'anomaly_detection': anomaly_detection,
    'query_docs': query_docs,
    'set_dataset': set_dataset,
    'generate_time_series_chart': generate_time_series_chart,
    'get_dashboard_metric': get_dashboard_metric,
    'format_table': format_table,
    'format_table_page': format_table_page,
}


# ------------------------------------
# Web Routes and Session Management
# ------------------------------------

def get_session(session_id: str = Cookie(None)):
    if session_id and session_id in sessions:
        return sessions[session_id]
    else:
        # Initialize new session
        new_session_id = str(uuid.uuid4())
        sessions[new_session_id] = {
            "messages": [],
            "agent": Researcher_agent,  # Start with researcher
            "context_variables": {"dataset": combined_df["dataset"],"notes": combined_notes}  # Initialize context_variables
        }
        return sessions[new_session_id]

def summarize_conversation(messages):
    """
    Generates a summary of the conversation.
    You can implement this using a separate OpenAI call or a simple heuristic.
    """
    # Example: Simple concatenation (for demonstration; consider using a model for better summaries)
    summary = "Conversation Summary:\n"
    for msg in messages:
        summary += f"{msg['role']}: {msg['content']}\n"
    return summary

# Define maximum message length (OpenAI's limit)
MAX_MESSAGE_LENGTH = 1048576  # 1MB in characters
MAX_SINGLE_MESSAGE = 100000   # Maximum length for any single message

def truncate_messages(messages):
    """
    Truncate messages to stay within OpenAI's context limits.
    More aggressive truncation that preserves the most recent context.
    """
    # Set stricter limits to leave room for functions and responses
    MAX_SINGLE_MESSAGE = 24000  # ~6k tokens
    MAX_TOTAL_LENGTH = 100000   # ~25k tokens, leaving plenty of room for functions and responses
    
    logger.info(f"""
=== Message Truncation Started ===
Initial message count: {len(messages)}
""")
    
    # Always keep the last message (user input)
    if not messages:
        return messages
        
    last_message = messages[-1]
    truncated_messages = [last_message]
    
    # First pass: Truncate any individual messages that are too long
    for msg in messages[:-1]:  # Skip the last message as we're keeping it complete
        content = msg.get("content", "")
        if len(content) > MAX_SINGLE_MESSAGE:
            msg["content"] = f"[TRUNCATED]...{content[-MAX_SINGLE_MESSAGE//2:]}"
            logger.info(f"""
Truncated large message:
Original length: {len(content)}
New length: {len(msg['content'])}
""")
    
    # Calculate how much space we have left
    current_length = len(str(last_message.get("content", "")))
    
    # Add messages from most recent to oldest until we approach the limit
    for msg in reversed(messages[:-1]):
        msg_length = len(str(msg.get("content", "")))
        if current_length + msg_length > MAX_TOTAL_LENGTH:
            break
        current_length += msg_length
        truncated_messages.insert(0, msg)
    
    # If we truncated any messages, add a summary message
    if len(truncated_messages) < len(messages):
        removed_count = len(messages) - len(truncated_messages)
        summary = {
            "role": "system",
            "content": f"[Note: {removed_count} earlier messages were removed to stay within context limits]"
        }
        truncated_messages.insert(0, summary)
    
    logger.info(f"""
=== Message Truncation Complete ===
Original messages: {len(messages)}
Truncated messages: {len(truncated_messages)}
Estimated total length: {current_length:,} characters
""")
    
    return truncated_messages

@router.post("/api/chat")
async def chat(request: Request, session_id: str = Cookie(None)):
    logger.info("Chat endpoint called")
    try:
        data = await request.json()
        user_input = data.get("query")
        logger.info(f"""
=== New User Message ===
Session ID: {session_id if session_id else 'New Session'}
Timestamp: {datetime.datetime.now().isoformat()}
Message:
{user_input}
===============================
""")

        # Get or create session data
        if session_id is None or session_id not in sessions:
            session_id = str(uuid.uuid4())
            sessions[session_id] = {
                "messages": [],
                "agent": Researcher_agent,
                "context_variables": {"dataset": combined_df["dataset"], "notes": combined_notes}
            }
            logger.info(f"Created new session: {session_id}")

        session_data = sessions[session_id]

        # Create StreamingResponse
        response = StreamingResponse(
            generate_response(user_input, session_data),
            media_type="text/plain"
        )

        # Set cookie attributes based on environment
        cookie_settings = {
            "key": "session_id",
            "value": session_id,
            "httponly": True,
            "max_age": 3600,
            "path": "/"
        }

        # Add secure and samesite settings only in production
        if IS_PRODUCTION:
            cookie_settings.update({
                "secure": True,
                "samesite": "None"
            })
        else:
            cookie_settings.update({
                "secure": False,
                "samesite": "Lax"
            })

        response.set_cookie(**cookie_settings)
        return response
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}", exc_info=True)
        raise

@router.post("/api/reset")
async def reset_conversation(session_id: str = Cookie(None)):
    """Reset the conversation state for the current session."""
    if session_id and session_id in sessions:
        # Reinitialize the session with default values
        sessions[session_id] = {
            "messages": [],
            "agent": Researcher_agent,  # Reset to default agent
            "context_variables": {"dataset": combined_df["dataset"], "notes": combined_notes}
        }
        response = JSONResponse({"status": "success"})
        
        # Set cookie attributes based on environment
        cookie_settings = {
            "key": "session_id",
            "value": session_id,
            "httponly": True,
            "max_age": 3600,
            "path": "/"
        }

        # Add secure and samesite settings only in production
        if IS_PRODUCTION:
            cookie_settings.update({
                "secure": True,
                "samesite": "None"
            })
        else:
            cookie_settings.update({
                "secure": False,
                "samesite": "Lax"
            })

        response.set_cookie(**cookie_settings)
        return response
    return JSONResponse({"status": "error", "message": "No active session found"})

async def generate_response(user_input, session_data):
    logger.info(f"""
=== Starting Agent Response ===
Session ID: {id(session_data)}
Current Agent: {session_data['agent'].name}
Timestamp: {datetime.datetime.now().isoformat()}
""")
    
    messages = session_data["messages"]
    agent = session_data["agent"]
    context_variables = session_data.get("context_variables") or {}
    current_function_name = None  # Initialize at the top level

    # Append user message
    messages.append({"role": "user", "content": user_input})
    
    # Truncate messages before sending to agent
    truncated_messages = truncate_messages(messages)

    try:
        # Run the agent
        response_generator = swarm_client.run(
            agent=agent,
            messages=truncated_messages,
            context_variables=context_variables,
            stream=True,
            debug=False,
        )
        
        # Initialize assistant message
        assistant_message = {"role": "assistant", "content": "", "sender": agent.name}
        incomplete_tool_call = None

        for chunk in response_generator:
            # Handle tool calls first
            if "tool_calls" in chunk and chunk["tool_calls"] is not None:
                for tool_call in chunk["tool_calls"]:
                    function_info = tool_call.get("function")
                    if not function_info:
                        continue

                    if function_info.get("name"):
                        current_function_name = function_info["name"]
                        logger.debug(f"Receiving tool call: {current_function_name}")

                    if not current_function_name:
                        continue

                    arguments_fragment = function_info.get("arguments", "")

                    if incomplete_tool_call is None or incomplete_tool_call["function_name"] != current_function_name:
                        incomplete_tool_call = {
                            "type": "tool_call",
                            "sender": assistant_message["sender"],
                            "function_name": current_function_name,
                            "arguments": ""
                        }

                    incomplete_tool_call["arguments"] += arguments_fragment

                    try:
                        arguments_json = json.loads(incomplete_tool_call["arguments"])
                        logger.info(f"""
=== Tool Call ===
Function: {current_function_name}
Arguments: {json.dumps(arguments_json, indent=2)}
""")

                        incomplete_tool_call["arguments"] = arguments_json
                        message = json.dumps(incomplete_tool_call) + "\n"
                        yield message

                        # Process the function call
                        function_to_call = function_mapping.get(current_function_name)
                        if function_to_call:
                            try:
                                result = function_to_call(context_variables, **arguments_json)
                                logger.info(f"""
=== Tool Result ===
Function: {current_function_name}
Result: {str(result)[:500]}{'...' if len(str(result)) > 500 else ''}
""")
                                # If the result has content (like from format_table), send it as a message
                                if isinstance(result, dict) and "content" in result:
                                    message = {
                                        "type": "content",
                                        "sender": assistant_message["sender"],
                                        "content": result["content"]
                                    }
                                    yield json.dumps(message) + "\n"
                            except Exception as e:
                                logger.error(f"""
=== Tool Error ===
Function: {current_function_name}
Error: {str(e)}
""")
                                raise

                        incomplete_tool_call = None
                        current_function_name = None
                    except json.JSONDecodeError:
                        # Still accumulating arguments
                        pass

            # Handle content
            elif "content" in chunk and chunk["content"] is not None:
                content_piece = chunk["content"]
                assistant_message["content"] += content_piece
                message = {
                    "type": "content",
                    "sender": assistant_message["sender"],
                    "content": content_piece
                }
                yield json.dumps(message) + "\n"

            # Handle end of message
            if "delim" in chunk and chunk["delim"] == "end":
                # Always append assistant message if it has content
                if assistant_message["content"]:
                    messages.append(assistant_message)
                    logger.info(f"""
=== Agent Response Complete ===
Timestamp: {datetime.datetime.now().isoformat()}
Agent: {assistant_message['sender']}
Response:
{assistant_message['content']}
===============================
""")
                # Reset for next message
                assistant_message = {"role": "assistant", "content": "", "sender": agent.name}

    except Exception as e:
        logger.error(f"""
=== Error in Response Generation ===
Error type: {type(e).__name__}
Error message: {str(e)}
Current function: {current_function_name}
Timestamp: {datetime.datetime.now().isoformat()}
===============================
""")
        raise

    logger.info(f"""
=== Chat Interaction Summary ===
Total messages: {len(messages)}
Timestamp: {datetime.datetime.now().isoformat()}
===============================
""")

