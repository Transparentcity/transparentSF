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
from fastapi import FastAPI, Request, Cookie
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uuid

# ------------------------------
# Configuration and Setup
# ------------------------------

load_dotenv()
# Confirm that there is an openai_api_key set
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

# Initialize FastAPI app
app = FastAPI()

# Serve static files and templates
# Create static directory if it doesn't exist
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")
# Mount the static directory
templates = Jinja2Templates(directory="templates")

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to capture all levels of log messages
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

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
AGENT_MODEL = "gpt-4o"

# Set Qdrant collection
collection_name = "SFPublicData"

# Session management (simple in-memory store)
sessions = {}
# Load and combine the data
data_folder = './data'  # Replace with the actual path

# Initialize context_variables with an empty DataFrame
combined_df = {"dataset": pd.DataFrame()}

def load_and_combine_notes():
    logger = logging.getLogger(__name__)
    data_folder = Path('output/')
    combined_text = ''
    
    logger.info(f"Starting to load and combine notes from {data_folder} and its subfolders")
    
    # Use rglob to recursively find all .txt files
    for file_path in data_folder.rglob('*.txt'):
        logger.debug(f"Reading file: {file_path}")
        try:
            combined_text += file_path.read_text(encoding='utf-8') + '\n'
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
    
    logger.info("Finished loading and combining notes")
    print(f"First 100 characters:\n{combined_text[:100]}")
    print(f"Total length: {len(combined_text)} characters ({len(combined_text.split())} tokens)")
    
    return combined_text

combined_notes=load_and_combine_notes() 

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
    notes = context_variables.get("notes", "").strip()
    logger = logging.getLogger(__name__)
    logger.info("Context variables contents:")
    for key, value in context_variables.items():
        if isinstance(value, pd.DataFrame):
            logger.info(f"{key}: DataFrame with shape {value.shape}")
        else:
            logger.info(f"{key}: {type(value)} - {value[:200]}...")  # Show first 200 chars for strings
    if notes is not None and len(notes.strip()) > 0:
        logger.info("Notes found in the dataset.")
        return {"notes": notes}
    else:
        logger.error("No notes found or notes are empty.")
        return {"error": "No notes found or notes are empty."}


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

def transfer_to_journalist_agent(context_variables, *args, **kwargs):
    """
    Transfers the conversation to the Data Agent.
    """
    return journalist_agent

# Define the anomaly finder agent
analyst_agent = Agent(
    model=AGENT_MODEL,
    name="Analyst",
     instructions="""
    **Function Usage:**

    - Use `query_docs(context_variables, "SFPublicData", query)` to search for datasets. The `query` parameter is a string describing the data the user is interested in. always pass the context_variables and the collection name is allways "SFPublicData"
    - Use the `transfer_to_journalist_agent` function (without any parameters) to transfer to the journalist agent. 
    - Use `set_dataset(endpoint, query)` Query and endpoint are required parameters . to set the dataset after the user selects one. The `endpoint` is the dataset identifier (e.g., `'abcd-1234.json'`), and `query` is the SoQL query string.  There are often valid soql querties in your docs for each endpoint to show you how to format your queries.  
        Here's an example call: 
        Here are some examples of valid soql queries:

    """,
    # functions=[get_notes, query_docs, transfer_to_journalist_agent],
    functions=[query_docs, set_dataset, get_dataset, set_columns, get_data_summary, anomaly_detection, generate_time_series_chart, transfer_to_journalist_agent],
    context_variables=context_variables,
    debug=False,
)

def update_agent_instructions_with_columns(columns):
    """
    Updates the analyst agent's instructions to include the available columns.
    """
    column_list_str = ', '.join(columns)
    logger.info(f"Updating agent instructions with columns: {column_list_str}")  # Log before updating

    # Use a regular string and .format() method
    analyst_agent.instructions = """ 
    Your Objectives: You are an expert in anomaly detection. Your primary job is to identify anomalies in the data provided. When the conversation is transferred to you, get the get_data_summary and share it with the user. This will help them understand the data better.
    Dataset Awareness:
    Available Columns in the Dataset:
    You have access to the following columns: [{column_list_str}]. ANNOUNCE THESE TO THE USER.

    Upon starting the conversation, immediately retrieve the dataset using the get_dataset() function.
    Announce to the user that you have access to the dataset and provide the list of available columns.
    Anomaly Detection Expertise:

    You are an expert in anomaly detection. Your primary job is to identify anomalies in the provided data.
    Engage with the User:

    Clarify Requirements:
    Ask specific questions to gather necessary parameters such as date ranges, fields of interest, filters, and grouping preferences.
    When the user mentions column names, verify them against the available columns. If discrepancies are found, politely correct the user and provide the correct column names.

    Engage with the user to clarify their requirements.
    Ask specific questions to gather necessary parameters such as date ranges, fields of interest, filters, and grouping preferences.
    Find the Appropriate Dataset:

    Use the query_docs function to search for relevant datasets in the "SFPublicData" knowledge base based on the user's needs.
    Present the user with a shortlist of the most relevant datasets, including titles, descriptions, endpoints, and available columns.
    Assist the user in selecting the most suitable dataset.
    Gather Necessary Query Parameters:
    
**Function Usage:**

    - Use `query_docs(context_variables, "SFPublicData", query)` to search for datasets. The `query` parameter is a string describing the data the user is interested in.
    - Use `set_dataset(endpoint, query)` to set the dataset after the user selects one. The `endpoint` is the dataset identifier (e.g., `'abcd-1234.json'`), and `query` is the SoQL query string.
    - Use `generate_time_series_chart` to create visualizations of the aggregated data.

    **User Interaction:**

    - Always communicate clearly and professionally.
    - If you need additional information, ask the user specific questions.
    - Do not overwhelm the user with too many questions at once; prioritize based on what is essential to proceed.

    **SoQL Query Construction:**

    - Be aware that SoQL differs from SQL in several key ways:
      - SoQL does not support the `EXTRACT()` function; use functions like `date_trunc_y()` or `date_trunc_ym()` for year and month.
      - There is no `FROM` statement; the dataset is specified via the endpoint.
      - Use parentheses in `WHERE` clauses to ensure correct logical grouping when combining `AND` and `OR`.
    - **Never leave placeholders or blanks in the query; ensure all parameters are filled with exact values.**
    - Double-check that all fields and functions used are supported by SoQL and exist in the dataset.

    **Data Freshness and Relevance:**

    - Prefer datasets that are up-to-date and most relevant to the user's query.
    - Inform the user if the data they are requesting is outdated or has limitations.

    **Example Workflow:**

    **Understanding User Needs:**

    **Finding the Appropriate Dataset:**

    **Gathering Query Parameters:**

    **Generating the SoQL Query:**

    Agent constructs the query:

    ```soql
    SELECT incident_date, incident_type, severity
    WHERE incident_date >= '2023-08-01' AND incident_date <= '2023-09-30'
    AND (severity = 'High' OR severity = 'Critical')
    ORDER BY incident_date DESC
    ```
    Agent ensures the query is URL-encoded if necessary.

    **Setting the Dataset:**

    Agent uses:

    ```python
    set_dataset(
        endpoint='abcd-1234.json',
        query="SELECT incident_date, incident_type, severity WHERE incident_date >= '2023-08-01' AND incident_date <= '2023-09-30' AND (severity = 'High' OR severity = 'Critical') ORDER BY incident_date DESC"
    )
    ```

    Once a dataset is selected, discuss with the user to determine, user set_columns to lock in the column names for this query session. 
    Specific columns they are interested in. You can show column names or fieldNames to the user, but only use fieldNames when making the query.
    Exact date ranges (start and end dates in 'YYYY-MM-DD' format).
    Any filters or conditions (e.g., categories, regions, statuses).
    Grouping and aggregation requirements.
    Generate a Complete SoQL Query:

    Construct a SoQL query that incorporates all the parameters provided by the user. Remember to use column functions like date_trunc_y() or date_trunc_ym() for date grouping.
    Ensure the query includes:
    A SELECT clause with the desired columns.
    A WHERE clause with exact dates and specified conditions.
    GROUP BY, ORDER BY, and LIMIT clauses as needed.
    Validate that all columns used in the query exist in the dataset's schema.
    Make sure the query is properly URL-encoded when needed.
    Set the Dataset:

    Use the set_dataset function to retrieve the data and store it in the context variables.
    The set_dataset function requires two parameters:
    endpoint: The 9-character dataset identifier plus the .json extension (e.g., 'wg3w-h783.json'). NOT THE ENTIRE URL.
    query: The complete SoQL query string.
    Confirm that the data has been successfully retrieved.
    Transfer to the Anomaly Finder Agent:

    Generate and Display Data Visualizations:

    The generate_time_series_chart function creates a time series chart by aggregating numeric fields over specified time intervals, applying optional grouping and filter conditions. This function is suitable for visualizing trends, comparing groups, and filtering data dynamically based on specific requirements.

    Function Call Structure
    ```python
    markdown_chart = generate_time_series_chart(
        context_variables={{'dataset': df}},             # Dictionary containing the dataset under 'dataset'
        time_series_field='date',              # Column name representing time
        numeric_fields=['sales', 'expenses'],  # List of numeric fields to visualize
        aggregation_period='month',            # Aggregation period ('day', 'week', 'month', etc.)
        group_field='agent',                   # Optional field for grouping (e.g., 'agent')
        agg_functions={{{{'sales': 'sum'}}}},      # Optional aggregation functions for numeric fields
        filter_conditions=[                    # Optional filter conditions for specific records
            {{{{"field": "status", "operator": "==", "value": "Completed"}}}},
            {{{{"field": "sales", "operator": ">", "value": 500}}}}
        ]
    )
    Function Call Structure
    ```python
    markdown_chart = generate_time_series_chart(
        context_variables=context,             # Dictionary containing the dataset under 'dataset'
        time_series_field='date',              # Column name representing time
        numeric_fields=['sales', 'expenses'],  # List of numeric fields to visualize
        aggregation_period='month',            # Aggregation period ('day', 'week', 'month', etc.)
        group_field='agent',                   # Optional field for grouping (e.g., 'agent')
        agg_functions={{'sales': 'sum'}},      # Optional aggregation functions for numeric fields
        filter_conditions=[                    # Optional filter conditions for specific records
            {{"field": "status", "operator": "==", "value": "Completed"}},
            {{"field": "sales", "operator": ">", "value": 500}}
        ]
    )
    ```
    Key Parameters
    **context_variables:**

    Contains the dataset in the format `{{'dataset': <your_dataframe>}}`.
    Ensure the dataset is properly loaded into this dictionary under the key `'dataset'`.

    **time_series_field:**

    Specifies the column representing time (e.g., `'date'`).
    This field will be used to aggregate data over the period specified in `aggregation_period`.

    **numeric_fields:**

    A list of numeric columns to visualize (e.g., `['sales', 'expenses']`).
    Ensure these fields are numerical, as the function will aggregate them according to the specified aggregation functions.

    **aggregation_period** (optional, defaults to `'day'`):

    Specifies the time interval for data aggregation, such as `'day'`, `'week'`, `'month'`, `'quarter'`, or `'year'`.

    **group_field** (optional):

    The field by which to group data (e.g., `'agent'` or `'category'`).
    If provided, the chart will display a breakdown by this field; otherwise, it will generate an aggregated time series without grouping.

    **agg_functions** (optional):

    A dictionary defining aggregation functions for each numeric field.
    Example: `{{'sales': 'sum', 'expenses': 'mean'}}`.
    If not specified, default aggregation (`'sum'`) will be applied to all numeric fields.

    **filter_conditions** (optional):

    A list of dictionaries, each specifying a condition for filtering records based on specific fields.

    **Format:**
    ```python
    filter_conditions = [
        {{"field": "<field_name>", "operator": "<operator>", "value": <value>}}
    ]
    ```

    **Operators:**

    Supported operators include `==` (equals), `!=` (not equals), `>`, `<`, `>=`, and `<=`.

    **Example:**
    ```python
    filter_conditions = [
        {{"field": "status", "operator": "==", "value": "Completed"}},
        {{"field": "sales", "operator": ">", "value": 500}}
    ]
    ```
    This example keeps only records where the status is `"Completed"` and sales are greater than `500`.

    **Filtering Data with `filter_conditions`**

    When `filter_conditions` is provided, the function uses `filter_data_by_date_and_conditions` to apply filters. Here's how this works:

    **Date Filtering:**

    If `filter_conditions` include date-based criteria (e.g., `{{"field": "transaction_date", "operator": ">", "value": "2023-01-01"}}`), the function will:

    - Parse `value` in the filter condition as a date.
    - Filter records based on whether the `transaction_date` meets the specified condition (`>` in this example).

    **Range Filtering Using `start_date` and `end_date` (optional):**

    If you wish to filter records within a date range:

    - Set `start_date` and `end_date` in the `filter_data_by_date_and_conditions` function call.
    - This will exclude records outside the specified range, adding an additional layer to the filtering process.

    **Non-Date Filters:**

    - Conditions not related to dates are applied directly.
    - The function supports filtering based on numeric or string matches, using the specified operator.
    - For example, `{{"field": "sales", "operator": ">", "value": 500}}` filters for records with sales greater than `500`.

    **Displaying the Chart**

    The `generate_time_series_chart` function outputs a Markdown string pointing to the chart image. Here's how to display it:

    ```markdown
    ![Chart](<relative_path_to_chart>)
    ```
    The chart will be saved in the `static` directory with a unique filename, and the relative path to this file is returned as Markdown content to be displayed in the interface.

    **Example Workflow**

    To create a monthly time series chart of sales and expenses grouped by agent, showing only records where status is `"Completed"` and sales are above `500`, you would call:

    ```python
    markdown_chart = generate_time_series_chart(
        context_variables={'dataset': df}, 
        time_series_field='date',
        numeric_fields=['sales', 'expenses'],
        aggregation_period='month',
        group_field='agent',
        agg_functions={'sales': 'sum', 'expenses': 'mean'},
        filter_conditions=[
            {"field": "status", "operator": "==", "value": "Completed"},
            {"field": "sales", "operator": ">", "value": 500}
        ]
    )
    ```
    The `markdown_chart` output will contain the Markdown string for displaying the generated chart.

    **Troubleshooting Tips**

    - Ensure `context_variables` has the dataset under the key `'dataset'`.
    - Make sure all fields in `numeric_fields`, `time_series_field`, and `group_field` exist in the dataset.
    - When using `filter_conditions`, check that values match the types in your dataset (e.g., convert dates to strings if needed).
    - If an error occurs in parsing or filtering dates, check the format and validity of all date fields and conditions.

    These instructions should guide you in using `generate_time_series_chart` with full control over data visualization, aggregation, grouping, and filtering.

    **Important Guidelines:**

    **Function Usage:**

    - Use `query_docs(context_variables, "SFPublicData", query)` to search for datasets. The `query` parameter is a string describing the data the user is interested in. always pass the context_variables and the collection name is allways "SFPublicData"
    - Use `set_dataset(endpoint, query)` to set the dataset after the user selects one. The `endpoint` is the dataset identifier (e.g., `'abcd-1234.json'`), and `query` is the SoQL query string.
    - Use `generate_time_series_chart` to create visualizations of the aggregated data.

    **User Interaction:**

    - Always communicate clearly and professionally.
    - If you need additional information, ask the user specific questions.
    - Do not overwhelm the user with too many questions at once; prioritize based on what is essential to proceed.

    **SoQL Query Construction:**

    - Be aware that SoQL differs from SQL in several key ways:
      - SoQL does not support the `EXTRACT()` function; use functions like `date_trunc_y()` or `date_trunc_ym()` for year and month.
      - There is no `FROM` statement; the dataset is specified via the endpoint.
      - Use parentheses in `WHERE` clauses to ensure correct logical grouping when combining `AND` and `OR`.
    - **Never leave placeholders or blanks in the query; ensure all parameters are filled with exact values.**
    - Double-check that all fields and functions used are supported by SoQL and exist in the dataset.

    **Data Freshness and Relevance:**

    - Prefer datasets that are up-to-date and most relevant to the user's query.
    - Inform the user if the data they are requesting is outdated or has limitations.

    **Example Workflow:**

    **Understanding User Needs:**

    **Finding the Appropriate Dataset:**

    **Gathering Query Parameters:**

    **Generating the SoQL Query:**

    Agent constructs the query:

    ```soql
    SELECT incident_date, incident_type, severity
    WHERE incident_date >= '2023-08-01' AND incident_date <= '2023-09-30'
    AND (severity = 'High' OR severity = 'Critical')
    ORDER BY incident_date DESC
    ```
    Agent ensures the query is URL-encoded if necessary.

    **Setting the Dataset:**

    Agent uses:

    ```python
    set_dataset(
        endpoint='abcd-1234.json',
        query="SELECT incident_date, incident_type, severity WHERE incident_date >= '2023-08-01' AND incident_date <= '2023-09-30' AND (severity = 'High' OR severity = 'Critical') ORDER BY incident_date DESC"
    )
    ```
    Agent confirms the data has been retrieved.

    - Use the `get_dataset` function (without any parameters) to access the dataset.
    - Use the `set_columns` function (without any parameters) to set columns from a dataset the user wants to query
    - Use the `get_data_summary` function (without any parameters) to get a statistical summary of the data.
    - Use the `transfer_to_journalist_agent` function (without any parameters) to transfer to the journalist agent. 
    - Use the `anomaly_detection` function to perform anomaly detection on the dataset. When calling this function, ensure you correctly pass values for the following parameters:
    - `group_field`: Specify the column name by which you want to group the data. Use the result of `get_columns` to decide which column is suitable for grouping (e.g., `'Category'`).
    - `filter_conditions`: Pass in a list of conditions to filter the data. Use this to narrow down the dataset for specific analysis. The format is a list of dictionaries with `'field'`, `'operator'`, and `'value'` keys.

    **Example:**

    ```python
    filter_conditions = [
        {'field': 'Date', 'operator': '>', 'value': '2022-01-01'},
        {'field': 'Region', 'operator': '==', 'value': 'San Francisco'}
    ]
    min_diff = 2  # Numeric values only

    recent_period = {'start': '2024-09-01', 'end': '2024-09-30'}
    comparison_period = {'start': '2023-08-01', 'end': '2023-08-31'}

    anomaly_detection(
        group_field='Category',
        filter_conditions=filter_conditions,
        min_diff=min_diff,
        recent_period=recent_period,
        comparison_period=comparison_period,
        date_field='DATE',
        numeric_field='COUNT'
    )
    ```
    **Note:** You must provide a `recent_period` and `comparison_period`. If the user doesn't provide them, then use the values in the example above.

    **Generating and Displaying Charts:**

    - Use the `generate_time_series_chart` function to create visualizations of the aggregated data.
    - The results from `generate_time_series_chart` are returned as Markdown text and should be displayed to the user within the conversation interface.

    **Example:**

    ```python
    markdown_chart = generate_time_series_chart(
        context_variables=context,
        time_series_field='date',
        numeric_fields=['sales', 'expenses'],
        aggregation_period='month',
        group_field='agent'
    )
    ```
    Agent then displays the chart:

    ```markdown
    ![Chart](../static/chart_20240427_123456_abcdef123456.png)
    ```
    **Remember:**

    - Always validate user inputs for correctness and completeness before constructing the query.
    - Keep the conversation user-focused, aiming to make the process smooth and efficient.
    - Endpoints are 9 characters long and are not the full URL. They are unique identifiers for datasets.
    - Your text should be in Markdown for best formatting.
    """


def load_and_combine_climate_data():
    data_folder = 'data/climate'
    # gsf_file = os.path.join(data_folder, 'GSF.csv')
    vera_file = os.path.join(data_folder, 'cleaned_vcusNov19.csv')
    
    # Load the CSV files with detected encoding
    # gsf_df = read_csv_with_encoding(gsf_file)
    vera_df = read_csv_with_encoding(vera_file)
    
    # Combine the DataFrames
    set_dataset_in_context(context_variables, vera_df)

    return vera_df



journalist_agent = Agent(
    model=AGENT_MODEL,
    name="Journalist",
     instructions="""
       You are a reporter for anomalous SF. You investigate, discover, research and report on notable trends in city data that others might be overlooking. You job is to use your Query_docs function to find some interesting trends to research more carefully. You want to bring attention to details in the City data that illustrate broader trends that other might be covering in the medita. Your mission is to bring objective data into the conversation. So for example instead of reporting on an overall decline in property crime in SF, which is by itself quite notable, you might instead illustrate it through the more relatable detail that auto theft which had been quite high is down by more than 40% from its 2 year average. Then you might choose that anomaly for some further research to determine where its down, or which types are down, etc. Your style is crystal clear, sometimes pithy but always factual and to-the-point. You always share the "ehat" and never speculate on the "why". No value language that might equate a drop in crime for example as being a "good" thing when it might be a data error. Your team has assembled analysis of dozens of key city databases in Public Safety, City Management and Ethics, Health, Housing, Drugs, Homelessness, etc. 
       
       Your output here is a list of trends, the query URL that generated the raw data, which is available in the document, the urls of the charts supporting the data, and questions you would ask an analyst to answer for you.

       Note especially if there are any YTD trends that are significantly above the prior year, those might be part of a longer term trend that the analyst should investigate.
       
       Use get_notes() to get the notes for the data you are researching.  Once you have  asense for the kinds of metrics available, you can get the details you need like chart links and queries for raw data from your docs, which you can search. 

        Use query_docs(context_variables, "<Collection Name>", query) for data not found in your notes. Pass only the query string describing the required data.

        Collection names are:
        - district_<number>
        - citywide

        If you are looking into a district, use the district_<number> collection.
        If you are looking into a citywide trend, use the citywide collection.
        
        Only make one document_query per call back to your API, as the response can sometime be long.  You will want to process it one category at a time. 
        use generate_ghost_post(context_variables, content, title) to generate a ghost post.  It accepts a title and simple html content.

        Call generate_ghost_post(context_variables, content,title) only after ensuring the content is complete, concise, and properly formatted.  MAke it a very simple html.

        For every image or chart, ensure the src is correct and accessible.
        Use Transfer_to_analyst_agent() ONLY if asked to by the user.

        """,
    # functions=[get_notes, query_docs, transfer_to_analyst_agent, generate_ghost_post],  
    functions=[query_docs,  get_notes, generate_ghost_post, transfer_to_analyst_agent],  
    context_variables=context_variables,
    debug=True,
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

    for chunk in response:
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

        if "delim" in chunk and chunk["delim"] == "end" and content:
            print()  # End of response message
            content = ""

        if "response" in chunk:
            return chunk["response"]
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
    'transfer_to_journalist_agent': transfer_to_journalist_agent,
    'get_dataset': get_dataset,
    'get_notes': get_notes,
    'get_columns': get_columns,
    'get_data_summary': get_data_summary,
    'anomaly_detection': anomaly_detection,
    'query_docs': query_docs,  # Use the handler instead of direct query_docs
    'set_dataset': set_dataset,
    'generate_category_chart': generate_time_series_chart,
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
            "agent": journalist_agent,  # Start with journalist
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

async def generate_response(user_input, session_data):
    messages = session_data["messages"]
    agent = session_data["agent"]
    context_variables = session_data.get("context_variables") or {}

    # Append user message
    messages.append({"role": "user", "content": user_input})

    # Truncate messages to the last MAX_HISTORY
    if len(messages) > MAX_HISTORY:
        messages = messages[-MAX_HISTORY:]
        session_data["messages"] = messages  # Update the session with truncated messages

    # Optionally, summarize if exceeding a certain threshold
    if len(messages) > SUMMARY_INTERVAL:
        summary = summarize_conversation(messages[:-5])  # Summarize all but the last 5 messages
        messages = messages[-5:]  # Keep the last 5 messages
        messages.insert(0, {"role": "system", "content": summary})  # Add summary at the beginning
        session_data["messages"] = messages  # Update the session with summarized messages

    # Run the agent
    response_generator = swarm_client.run(
        agent=agent,
        messages=messages,
        context_variables=context_variables,
        stream=True,
        debug=False,
    )

    # Initialize assistant message
    assistant_message = {"role": "assistant", "content": "", "sender": agent.name}

    # Buffer for incomplete tool call arguments
    incomplete_tool_call = None
    current_function_name = None

    for chunk in response_generator:
        # Handle content
        if "content" in chunk and chunk["content"] is not None:
            content_piece = chunk["content"]
            assistant_message["content"] += content_piece

            # Send content as a JSON message to the client
            message = {
                "type": "content",
                "sender": assistant_message["sender"],
                "content": content_piece
            }
            yield json.dumps(message) + "\n"

        # Handle tool calls
        if "tool_calls" in chunk and chunk["tool_calls"] is not None:
            for tool_call in chunk["tool_calls"]:
                function_info = tool_call.get("function")
                if not function_info:
                    continue  # Skip if function info is missing

                # Update current_function_name if 'name' is provided
                if function_info.get("name"):
                    current_function_name = function_info["name"]

                # If we don't have a function name yet, skip processing
                if not current_function_name:
                    continue  # Cannot process without a function name

                arguments_fragment = function_info.get("arguments", "")

                # Initialize or append to incomplete tool call
                if incomplete_tool_call is None or incomplete_tool_call["function_name"] != current_function_name:
                    incomplete_tool_call = {
                        "type": "tool_call",
                        "sender": assistant_message["sender"],
                        "function_name": current_function_name,
                        "arguments": ""
                    }

                incomplete_tool_call["arguments"] += arguments_fragment

                # Try parsing the arguments
                try:
                    arguments_json = json.loads(incomplete_tool_call["arguments"])
                    incomplete_tool_call["arguments"] = arguments_json

                    # Send the complete tool call to the client
                    message = json.dumps(incomplete_tool_call) + "\n"
                    yield message

                    # Log the tool call
                    print(f"Sent tool_call message: {incomplete_tool_call}")
                    logger.debug(f"Processing tool call: {current_function_name}, Arguments fragment: {arguments_fragment}")

                    # Process the function call
                    function_to_call = function_mapping.get(current_function_name)
                    if function_to_call:
                        # Call the function with the arguments
                        function_args = incomplete_tool_call["arguments"]
                        if not isinstance(function_args, dict):
                            function_args = {}
                        result = function_to_call(context_variables, **function_args)

                        # If the function returns an Agent, update session_data["agent"]
                        if isinstance(result, Agent):
                            session_data["agent"] = result
                            assistant_message["sender"] = session_data["agent"].name
                            print(f"Agent transferred to {session_data['agent'].name}")

                        # Ensure the context variables are updated in the session data
                        session_data["context_variables"] = context_variables

                    # Reset the incomplete tool call and current function name
                    incomplete_tool_call = None
                    current_function_name = None
                except json.JSONDecodeError:
                    # Incomplete JSON, wait for more data
                    pass

        # Handle end of message
        if "delim" in chunk and chunk["delim"] == "end":
            # Assistant message is complete
            messages.append(assistant_message)
            # Reset assistant message
            assistant_message = {"role": "assistant", "content": "", "sender": session_data["agent"].name}

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/chat")
async def chat(request: Request, session_id: str = Cookie(None)):
    data = await request.json()
    user_input = data.get("query")

    # Retrieve or create session data
    if session_id is None or session_id not in sessions:
        # Create new session
        session_id = str(uuid.uuid4())
        sessions[session_id] = {
            "messages": [],
            "agent": journalist_agent,  # Start with analyst
            "context_variables": {"dataset": combined_df["dataset"],"notes": combined_notes}
        }

    session_data = sessions[session_id]

    # Create StreamingResponse
    response = StreamingResponse(
        generate_response(user_input, session_data),
        media_type="text/plain"
    )

    # Set the session_id as a cookie
    response.set_cookie(key="session_id", value=session_id)

    return response

# ------------------------------------
# Run the App with Uvicorn
# ------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webChat:app", host="0.0.0.0", port=8001, reload=True)
