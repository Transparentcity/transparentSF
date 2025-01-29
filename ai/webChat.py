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
from fastapi.responses import StreamingResponse, HTMLResponse
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
    total_files = 0
    
    logger.info("Starting to load and combine notes")
    
    # First find the most recent aggregated summary
    aggregated_dir = data_folder / 'aggregated_summaries'
    if aggregated_dir.exists():
        aggregated_files = list(aggregated_dir.glob('aggregated_summaries_*.txt'))
        if aggregated_files:
            latest_aggregated = max(aggregated_files, key=lambda x: x.stat().st_mtime)
            logger.info(f"Loading most recent aggregated summary: {latest_aggregated}")
            combined_text = latest_aggregated.read_text(encoding='utf-8')
            total_files += 1
            logger.info(f"Aggregated summary size: {len(combined_text)} characters")
    
    # Then load any individual summaries that are newer than the aggregated summary
    latest_mtime = latest_aggregated.stat().st_mtime if 'latest_aggregated' in locals() else 0
    
    for file_path in data_folder.glob('*.json_summary.txt'):
        if file_path.stat().st_mtime > latest_mtime:
            logger.info(f"Loading newer summary: {file_path}")
            file_content = file_path.read_text(encoding='utf-8')
            logger.info(f"File {file_path.name} size: {len(file_content)} characters")
            combined_text += '\n' + file_content
            total_files += 1
    
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

# Define the anomaly finder agent
analyst_agent = Agent(
    model=AGENT_MODEL,
    name="Analyst",
     instructions="""
    **Function Usage:**

    - Use `query_docs(context_variables, "SFPublicData", query)` to search for datasets. The `query` parameter is a string describing the data the user is interested in. always pass the context_variables and the collection name is allways "SFPublicData"
    - Use the `transfer_to_researcher_agent` function (without any parameters) to transfer to the researcher agent. 
    - Use `set_dataset(endpoint, query)` Query and endpoint are required parameters . to set the dataset after the user selects one. The `endpoint` is the dataset identifier (e.g., `'abcd-1234.json'`), and `query` is the SoQL query string.  There are often valid soql querties in your docs for each endpoint to show you how to format your queries.  
        Here's an example call: 
        Here are some examples of valid soql queries:
    - Use `generate_time_series_chart(context_variables, column_name, start_date, end_date, aggregation_period, return_html=False)` to generate a time series chart. 

    """,
    # functions=[get_notes, query_docs, transfer_to_researcher_agent],
    functions=[query_docs, set_dataset, get_dataset, set_columns, get_data_summary, anomaly_detection, generate_time_series_chart, transfer_to_researcher_agent],
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
    # gsf_file = os.path.join(data_folder, 'GSF.csv')
    vera_file = os.path.join(data_folder, 'cleaned_vcusNov19.csv')
    
    # Load the CSV files with detected encoding
    # gsf_df = read_csv_with_encoding(gsf_file)
    vera_df = read_csv_with_encoding(vera_file)
    
    # Combine the DataFrames
    set_dataset_in_context(context_variables, vera_df)

    return vera_df



Researcher_agent = Agent(
    model=AGENT_MODEL,
    name="Researcher",
     instructions="""
        Role: You are a researcher for Transparent SF, focusing on trends in city data.
        Purpose: help the user find objective data and specific details on their question. 
        Avoid speculating on causes or using value terms (like "good" or "bad"). Report the "what," not the "why."

        Examples: Instead of just saying property crime is down, highlight specifics (e.g., auto theft down 40% from it's trailing 2-year average).
        Data Categories: Public Safety, City Management and Ethics, Health, Housing, Drugs, Homelessness, etc.
        
        Deliverables:
        List of notable trends.
        Query URLs generating raw data.
        URLs of supporting charts.
        
        Tools:
        get_notes() always start here this is a sumamry of everyhting in your docs. Use it to determine what data is available, and what to search for in your query_docs() calls.  It contains no links or charts, so don't share any links or charts with the user without checking your docs first. 
        query_docs(context_variables, "<Collection Name>", query) to gather details from:
        
        There are many collections you can search.  Sometimes you might want to look at multiple collections to get the data you need. 
        
        Each collection is named as follows:
        
        timeframe_location
        timeframes are one of the following:
        annual
        monthly
        daily

        location is one of the following:
        citywide
        or
        district_<number>
        
        
        Only make one query_docs() call per category due to response length. 
        Use generate_ghost_post(context_variables, content, title) to produce a simple HTML post once content is finalized.
        Ensure chart/image src links are correct and accessible.
        Analyst Handoff: Use Transfer_to_analyst_agent() only if specifically requested to do so by the user.

        """,
    functions=[get_notes, query_docs, transfer_to_analyst_agent, generate_ghost_post],  
    # functions=[query_docs, generate_ghost_post, transfer_to_analyst_agent],  
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
    combined_data = load_and_combine_climate_data()
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
    logger.info(f"""
=== Message Truncation Started ===
Initial message count: {len(messages)}
""")
    
    # First pass: Truncate any individual messages that are too long
    for msg in messages:
        content = msg.get("content", "")
        if len(content) > MAX_SINGLE_MESSAGE:
            msg["content"] = f"[TRUNCATED]...{content[-MAX_SINGLE_MESSAGE//2:]}"
            logger.info(f"""
Truncated large message:
Original length: {len(content)}
New length: {len(msg['content'])}
""")
    
    # Calculate total length
    total_length = sum(len(str(msg.get("content", ""))) for msg in messages)
    
    if total_length <= MAX_MESSAGE_LENGTH * 0.9:  # Leave 10% buffer
        return messages
    
    # Keep the last message (user input) and most recent context
    truncated_messages = []
    running_length = 0
    
    # Always keep the last message
    last_message = messages[-1]
    running_length += len(str(last_message.get("content", "")))
    truncated_messages.append(last_message)
    
    # Add recent messages until we approach the limit
    for msg in reversed(messages[:-1]):
        msg_length = len(str(msg.get("content", "")))
        if running_length + msg_length > MAX_MESSAGE_LENGTH * 0.8:  # More conservative buffer
            break
        running_length += msg_length
        truncated_messages.insert(0, msg)
    
    # If we have space, add a summary message at the start
    if len(truncated_messages) < len(messages):
        summary = {
            "role": "system",
            "content": f"[HISTORY SUMMARY] Previous conversation had {len(messages) - len(truncated_messages)} older messages that were truncated to save space."
        }
        truncated_messages.insert(0, summary)
    
    logger.info(f"""
=== Message Truncation Complete ===
Original messages: {len(messages)}
Truncated messages: {len(truncated_messages)}
Original length: {total_length}
New length: {sum(len(str(msg.get('content', ''))) for msg in truncated_messages)}
""")
    
    return truncated_messages

async def generate_response(user_input, session_data):
    logger.info(f"""
=== New Chat Interaction ===
Timestamp: {datetime.datetime.now().isoformat()}
Session ID: {id(session_data)}
Current Agent: {session_data['agent'].name}
User Input: {user_input[:200]}{'...' if len(user_input) > 200 else ''}
""")
    
    messages = session_data["messages"]
    agent = session_data["agent"]
    context_variables = session_data.get("context_variables") or {}

    # Log initial state
    logger.info(f"""
Context State:
Message History: {len(messages)} messages
Active Context Variables: {', '.join(context_variables.keys())}
Last Message Preview: {messages[-1]['content'][:100] + '...' if messages else 'No previous messages'}
""")

    # Append user message
    messages.append({"role": "user", "content": user_input})
    
    # Truncate messages if needed before sending to agent
    original_message_count = len(messages)
    truncated_messages = truncate_messages(messages)
    if len(truncated_messages) < original_message_count:
        logger.warning(f"""
Messages were truncated:
Original count: {original_message_count}
Truncated count: {len(truncated_messages)}
Removed: {original_message_count - len(truncated_messages)} messages
""")
        messages = truncated_messages

    try:
        # Run the agent
        logger.info("Starting agent interaction")
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
        current_function_name = None

        for chunk in response_generator:
            # Determine chunk type
            chunk_type = (
                "content" if "content" in chunk and chunk["content"] is not None
                else "tool_call" if "tool_calls" in chunk and chunk["tool_calls"] is not None
                else "delimiter" if "delim" in chunk
                else "unknown"
            )
            
            logger.debug(f"""
Processing {chunk_type} chunk:
Content preview: {str(chunk)[:150]}...
""")

            # Handle content
            if chunk_type == "content":
                content_piece = chunk["content"]
                assistant_message["content"] += content_piece
                message = {
                    "type": "content",
                    "sender": assistant_message["sender"],
                    "content": content_piece
                }
                logger.debug(f"""
Added content to response:
Content piece: {content_piece[:100]}...
""")
                yield json.dumps(message) + "\n"

            # Handle tool calls
            elif chunk_type == "tool_call":
                logger.info("Processing tool calls")
                for tool_call in chunk["tool_calls"]:
                    function_info = tool_call.get("function")
                    if not function_info:
                        continue

                    if function_info.get("name"):
                        current_function_name = function_info["name"]
                        logger.info(f"""
Tool call started:
Function: {current_function_name}
""")

                    if not current_function_name:
                        continue

                    arguments_fragment = function_info.get("arguments", "")
                    logger.debug(f"""
Received function arguments:
Arguments fragment: {arguments_fragment}
""")

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
Complete tool call received:
Function: {current_function_name}
Arguments: {json.dumps(arguments_json, indent=2)}
""")

                        incomplete_tool_call["arguments"] = arguments_json
                        message = json.dumps(incomplete_tool_call) + "\n"
                        yield message

                        # Process the function call
                        function_to_call = function_mapping.get(current_function_name)
                        if function_to_call:
                            logger.info(f"Executing function: {current_function_name}")
                            try:
                                if current_function_name == "generate_time_series_chart":
                                    logger.info(f"""
Generating time series chart:
Arguments: {json.dumps(arguments_json, indent=2)}
""")
                                    # Create a clean copy of context without the dataset
                                    chart_context = {
                                        key: value 
                                        for key, value in context_variables.items() 
                                        if key == 'dataset'  # Only keep the dataset
                                    }
                                    result = function_to_call(chart_context, **arguments_json)
                                    
                                    if isinstance(result, tuple):
                                        markdown_content, _ = result  # Ignore the HTML content
                                        logger.info(f"""
Chart generated successfully:
Markdown length: {len(markdown_content)}
""")
                                        
                                        # Only store and send the markdown content
                                        assistant_message["content"] += f"\n{markdown_content}\n"
                                        yield json.dumps({
                                            "type": "content",
                                            "sender": assistant_message["sender"],
                                            "content": markdown_content
                                        }) + "\n"
                                    else:
                                        logger.warning(f"""
Unexpected chart result format:
Result type: {type(result)}
""")
                                        yield json.dumps({
                                            "type": "content",
                                            "sender": assistant_message["sender"],
                                            "content": str(result)
                                        }) + "\n"
                                else:
                                    result = function_to_call(context_variables, **arguments_json)
                                    logger.info(f"""
Function executed successfully:
Function: {current_function_name}
Result type: {type(result)}
Result preview: {str(result)[:200]}...
""")
                                    
                                    # Handle agent transfer
                                    if isinstance(result, Agent):
                                        session_data["agent"] = result
                                        assistant_message["sender"] = session_data["agent"].name
                                        logger.info(f"""
Agent transferred:
New agent: {session_data['agent'].name}
""")

                                session_data["context_variables"] = context_variables
                                
                            except Exception as e:
                                logger.error(f"""
Error executing function:
Function: {current_function_name}
Error: {str(e)}
Arguments: {json.dumps(arguments_json, indent=2)}
""")
                                raise

                        incomplete_tool_call = None
                        current_function_name = None
                    except json.JSONDecodeError:
                        # Still accumulating arguments
                        pass

            # Handle end of message
            if "delim" in chunk and chunk["delim"] == "end":
                logger.info(f"""
Message complete:
Final message length: {len(assistant_message['content'])}
""")
                messages.append(assistant_message)
                assistant_message = {"role": "assistant", "content": "", "sender": agent.name}

    except Exception as e:
        logger.error(f"""
Error in generate_response:
Error type: {type(e).__name__}
Error message: {str(e)}
Current function: {current_function_name}
Last message length: {len(assistant_message.get('content', ''))}
""")
        raise

    logger.info(f"""
Chat interaction completed:
Total messages: {len(messages)}
Final message preview: {messages[-1].get('content', '')[:200]}...
""")

@router.post("/api/chat")
async def chat(request: Request, session_id: str = Cookie(None)):
    logger.debug("Chat endpoint called")
    try:
        data = await request.json()
        user_input = data.get("query")
        logger.debug(f"Received query: {user_input}")

        # Get or create session data
        if session_id is None or session_id not in sessions:
            session_id = str(uuid.uuid4())
            sessions[session_id] = {
                "messages": [],
                "agent": Researcher_agent,
                "context_variables": {"dataset": combined_df["dataset"], "notes": combined_notes}
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
        return {"status": "success"}
    return {"status": "error", "message": "No active session found"}
