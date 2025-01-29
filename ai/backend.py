from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote

import os
import json
from datetime import datetime
import pytz
import subprocess  # ADDED
import glob

from ai_dataprep import process_single_file  # Ensure these imports are correct
from periodic_analysis import export_for_endpoint  # Ensure this import is correct
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Capture all levels
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log"),  # Log to file
        logging.StreamHandler()                # Log to console
    ]
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = None  # Will be set by main.py

# Define the absolute path to the output directory
script_dir = os.path.dirname(os.path.abspath(__file__))  # Gets the /ai directory
output_dir = os.path.join(script_dir, 'output')  # /ai/output

logger.debug(f"Script directory: {script_dir}")
logger.debug(f"Output directory: {output_dir}")


def set_templates(t):
    """Set the templates instance for this router"""
    global templates
    templates = t
    logger.debug("Templates set in backend router")


def find_output_files_for_endpoint(endpoint: str, output_dir: str):
    """Search the output directory for files matching the endpoint."""
    logger.debug(f"Searching for files matching endpoint '{endpoint}' in directory: {output_dir}")
    output_files = {}
    
    # Remove .json extension if present
    endpoint = endpoint.replace('.json', '')
    logger.debug(f"Cleaned endpoint: {endpoint}")
    
    if not os.path.exists(output_dir):
        logger.error(f"Output directory does not exist: {output_dir}")
        return output_files
        
    # Log all files in output directory
    logger.debug("Full output directory contents:")
    for root, dirs, files in os.walk(output_dir):
        #logger.debug(f"Directory: {root}")
        #logger.debug(f"Files: {files}")
        
        matching_files = {}
        latest_timestamp = 0
        
        # Find the most recent file of each type
        for file in files:
            if endpoint in file:
                file_path = os.path.join(root, file)
                file_timestamp = os.path.getmtime(file_path)
                
                if file.endswith('.html'):
                    if 'html' not in matching_files or file_timestamp > latest_timestamp:
                        matching_files['html'] = file_path
                        latest_timestamp = file_timestamp
                elif file.endswith('.md'):
                    if 'md' not in matching_files or file_timestamp > latest_timestamp:
                        matching_files['md'] = file_path
                        latest_timestamp = file_timestamp
                elif file.endswith('_summary.txt'):
                    if 'txt' not in matching_files or file_timestamp > latest_timestamp:
                        matching_files['txt'] = file_path
                        latest_timestamp = file_timestamp
        
        if matching_files:
            rel_path = os.path.relpath(root, output_dir)
            output_files[rel_path] = matching_files
            logger.debug(f"Added matching files for path {rel_path}: {matching_files}")
     #   else:
            # logger.debug(f"No matching files found in directory {root}")

    # logger.debug(f"Final output files found: {json.dumps(output_files, indent=2)}")
    return output_files


def get_output_file_url(file_path: str, output_dir: str):
    """
    Convert a file path to a URL accessible via the FastAPI app.
    """
    if not isinstance(file_path, (str, bytes, os.PathLike)):
        logger.warning(f"Invalid file_path type: {type(file_path)}. Expected string path.")
        return None
    try:
        relative_path = os.path.relpath(file_path, output_dir)
        url_path = "/output/" + "/".join(quote(part) for part in relative_path.split(os.sep))
        logger.debug(f"Converted file path '{file_path}' to URL '{url_path}'")
        return url_path
    except Exception as e:
        logger.error(f"Error converting file path to URL: {str(e)}")
        return None


def check_fixed_file_exists(filename: str) -> bool:
    """Check if a file exists in the fixed datasets folder."""
    current_dir = os.path.dirname(__file__)
    fixed_path = os.path.join(current_dir, 'data', 'datasets', 'fixed', filename)
    return os.path.exists(fixed_path)


def load_and_sort_json():
    """
    Loads all .json files from both data/datasets and data/datasets/fixed directories,
    extracts required information, and sorts them with fixed files at the top.
    """
    datasets = []
    current_dir = os.path.dirname(os.path.abspath(__file__))
    raw_datasets_dir = os.path.join(current_dir, 'data', 'datasets')
    fixed_datasets_dir = os.path.join(current_dir, 'data', 'datasets', 'fixed')
    output_dir = os.path.join(current_dir, 'output')
    
    logger.debug(f"Loading datasets from: {raw_datasets_dir} and {fixed_datasets_dir}")

    if not os.path.exists(raw_datasets_dir):
        logger.error(f"Raw datasets directory not found: {raw_datasets_dir}")
        return datasets

    # First, get list of all raw dataset files
    try:
        raw_files = [f for f in os.listdir(raw_datasets_dir) 
                    if f.endswith('.json') and f != 'analysis_map.json']
        logger.debug(f"Found raw files: {raw_files}")
    except Exception as e:
        logger.error(f"Error listing raw datasets directory: {e}")
        return datasets

    # Process each file
    for filename in raw_files:
        try:
            # Check if there's a fixed version
            has_fixed = os.path.exists(os.path.join(fixed_datasets_dir, filename))
            file_path = os.path.join(fixed_datasets_dir if has_fixed else raw_datasets_dir, filename)
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Get the last run time by checking output files
            last_run = None
            endpoint = filename.replace('.json', '')
            for root, _, files in os.walk(output_dir):
                for file in files:
                    if endpoint in file:
                        file_path = os.path.join(root, file)
                        file_time = os.path.getmtime(file_path)
                        if last_run is None or file_time > last_run:
                            last_run = file_time

            # Check if any output files exist for this dataset
            has_output = last_run is not None
            
            dataset_info = {
                'filename': filename,
                'json_path': os.path.join('data', 'datasets', 'fixed' if has_fixed else '', filename),
                'category': data.get('category', 'Uncategorized'),
                'report_category': data.get('report_category', data.get('category', 'Uncategorized')),
                'item_noun': data.get('item_noun', 'items'),
                'title': data.get('title', 'Untitled'),
                'has_fixed': has_fixed,
                'endpoint': filename,
                'last_run': last_run,
                'last_run_str': datetime.fromtimestamp(last_run).strftime('%Y-%m-%d %H:%M:%S') if last_run else 'Never',
                'has_output': has_output
            }
            datasets.append(dataset_info)
            
        except Exception as e:
            logger.error(f"Error processing file {filename}: {e}")
            continue

    # Sort datasets:
    # 1. Has output files (True before False)
    # 2. Category
    # 3. Last run time (most recent first)
    datasets.sort(key=lambda x: (
        not x['has_output'],  # False sorts after True
        x['report_category'] if x['has_fixed'] else x['category'],
        -(x['last_run'] or 0)  # Use 0 for None values, negative for descending order
    ))

    return datasets


def get_md_file_date(output_files):
    """Get the most recent date from MD or HTML files in output_files."""
    latest_date = None
    for folder, files in output_files.items():
        for file_type, file_path in files.items():
            if file_type in ['md', 'html']:
                try:
                    file_date = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if latest_date is None or file_date > latest_date:
                        latest_date = file_date
                except Exception:
                    continue
    return latest_date


@router.get("/")
async def backend_root(request: Request):
    """Serve the backend interface."""
    logger.debug("Backend root route called")
    if templates is None:
        logger.error("Templates not initialized in backend router")
        raise RuntimeError("Templates not initialized")
    
    # Load datasets here
    try:
        datasets = load_and_sort_json()
        logger.debug(f"Loaded {len(datasets)} datasets")
    except Exception as e:
        logger.error(f"Error loading datasets: {e}")
        datasets = []

    logger.debug("Serving backend.html template")
    return templates.TemplateResponse("backend.html", {
        "request": request,
        "datasets": datasets
    })


@router.get("/prep-data/{filename}")
async def prep_data(filename: str):
    logger.debug(f"Prep data called for filename: {filename}")
    current_dir = os.path.dirname(__file__)
    datasets_folder = os.path.join(current_dir, 'data', 'datasets')
    output_folder = os.path.join(current_dir, 'data', 'datasets/fixed')
    error_log = []

    # Check if file exists
    file_path = os.path.join(datasets_folder, filename)
    if not os.path.exists(file_path):
        logger.error(f"File '{filename}' not found in datasets directory.")
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in datasets directory.")

    try:
        # Process data
        process_single_file(filename, datasets_folder, output_folder, datetime.now(pytz.UTC), error_log)

        if error_log:
            logger.warning(f"Data prepared with warnings for file '{filename}': {error_log}")
            return JSONResponse({
                'status': 'warning',
                'message': 'Data prepared with warnings.',
                'errors': error_log
            })

        logger.info(f"File '{filename}' prepared successfully.")
        return JSONResponse({
            'status': 'success',
            'message': f'File {filename} prepared successfully.'
        })
    except Exception as e:
        logger.exception(f"Error preparing data for file '{filename}': {str(e)}")
        return JSONResponse({'status': 'error', 'message': str(e)})


@router.get("/run_analysis/{endpoint}")
async def run_analysis(endpoint: str, period_type: str = 'year'):
    """Run analysis for a given endpoint."""
    logger.debug(f"Run analysis called for endpoint: {endpoint} with period_type: {period_type}")
    
    # Remove .json extension if present
    endpoint = endpoint.replace('.json', '')
    
    error_log = []
    try:
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create period-specific output folder
        period_folder = {'year': 'annual', 'month': 'monthly', 'day': 'daily', 'ytd': 'ytd'}[period_type]
        period_output_dir = os.path.join(output_dir, period_folder)
        os.makedirs(period_output_dir, exist_ok=True)
        
        logger.debug(f"Attempting to run export_for_endpoint with endpoint: {endpoint} and period_type: {period_type}")
        export_for_endpoint(endpoint, 
                          period_type=period_type,
                          output_folder=period_output_dir,  # Use period-specific output folder
                          log_file_path=os.path.join(logs_dir, 'processing_log.txt'))
        
        if error_log:
            logger.warning(f"Analysis completed with warnings for endpoint '{endpoint}': {error_log}")
            return JSONResponse({
                'status': 'warning', 
                'message': 'Analysis completed with warnings.', 
                'errors': error_log
            })

        logger.info(f"Analysis for endpoint '{endpoint}' completed successfully.")
        return JSONResponse({
            'status': 'success', 
            'message': f'Analysis for endpoint {endpoint} completed successfully.'
        })
    except Exception as e:
        logger.exception(f"Error running analysis for endpoint '{endpoint}': {str(e)}")
        return JSONResponse({'status': 'error', 'message': str(e)})


@router.get("/get-updated-links/{endpoint}")
async def get_updated_links(endpoint: str):
    """Returns the updated output links for a single endpoint."""
    logger.debug(f"Get updated links called for endpoint: {endpoint}")
    
    # Get the absolute path to the output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'output')
    logger.debug(f"Looking for files in output directory: {output_dir}")
    
    # Verify directory exists and is accessible
    if not os.path.exists(output_dir):
        logger.error(f"Output directory does not exist: {output_dir}")
        return JSONResponse({'links': {}, 'message': 'Output directory not found'})
    
    if not os.path.isdir(output_dir):
        logger.error(f"Output path exists but is not a directory: {output_dir}")
        return JSONResponse({'links': {}, 'message': 'Invalid output directory'})
    
    try:
        # List directory contents
        dir_contents = os.listdir(output_dir)
        logger.debug(f"Output directory contents: {dir_contents}")
    except Exception as e:
        logger.error(f"Error reading output directory: {str(e)}")
        return JSONResponse({'links': {}, 'message': f'Error reading output directory: {str(e)}'})
    
    output_files = find_output_files_for_endpoint(endpoint, output_dir)
    logger.debug(f"Found output files: {json.dumps(output_files, indent=2)}")
    
    links = {}
    for folder, files in output_files.items():
        folder_links = {}
        for file_type, file_path in files.items():
            url = get_output_file_url(file_path, output_dir)
            if url:
                folder_links[file_type] = url
                logger.debug(f"Added URL for {file_type}: {url}")
        if folder_links:
            links[folder] = folder_links
    
    logger.debug(f"Returning links: {json.dumps(links, indent=2)}")
    return JSONResponse({'links': links})


# --- ADDED: Route to reload vector DB
@router.get("/reload_vector_db")
async def reload_vector_db():
    """
    Reload the vector DB by running the vector_loader_periodic.py script.
    """
    logger.debug("Reload vector DB called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "vector_loader_periodic.py")
        log_file = os.path.join(script_dir, "logs", "vector_loader.log")
        
        # Clear the log file before running
        with open(log_file, 'w') as f:
            f.write("")  # Clear the file
            
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        
        # Read the log file content
        try:
            with open(log_file, 'r') as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"Error reading log file: {str(e)}"
            
        if result.returncode == 0:
            logger.info("Vector DB reloaded successfully.")
            return JSONResponse({
                "status": "success",
                "message": "Vector DB reloaded successfully.",
                "output": result.stdout,
                "log_content": log_content
            })
        else:
            logger.error(f"Failed to reload Vector DB: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": "Failed to reload Vector DB.",
                "output": result.stderr,
                "log_content": log_content
            })
    except Exception as e:
        logger.exception(f"Error reloading Vector DB: {str(e)}")
        return JSONResponse({
            "status": "error", 
            "message": str(e),
            "log_content": "Error occurred before log file could be read"
        })


@router.get("/dataset-json/{filename:path}")
async def get_dataset_json(filename: str):
    """Serve the JSON file for a dataset."""
    logger.debug(f"Get dataset JSON called for filename: {filename}")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Remove any leading '../' from the filename for security
    clean_filename = filename.lstrip('.').lstrip('/')
    
    # Try the exact path first
    file_path = os.path.join(current_dir, clean_filename)
    
    if not os.path.exists(file_path):
        logger.error(f"JSON file not found: {file_path}")
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        file_path,
        media_type="application/json",
        filename=os.path.basename(clean_filename)
    )


@router.get("/endpoint-json/{endpoint}")
async def get_endpoint_json(endpoint: str):
    """Serve the JSON file for an endpoint's analysis results."""
    logger.debug(f"Get endpoint JSON called for endpoint: {endpoint}")
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        results_dir = os.path.join(current_dir, 'output')  # Changed from analysis_map/individual_results
        
        # Look for files matching the endpoint
        matching_files = []
        for root, _, files in os.walk(results_dir):
            for file in files:
                if file.endswith('.json') and endpoint in file:
                    matching_files.append(os.path.join(root, file))
        
        if not matching_files:
            raise HTTPException(status_code=404, detail="No results found for this endpoint")
            
        # Use the most recent file if multiple exist
        latest_file = max(matching_files, key=os.path.getmtime)
        
        # Read the file content instead of serving the file directly
        try:
            with open(latest_file, 'r') as f:
                content = json.load(f)
            return JSONResponse(content=content)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid JSON file")
        except IOError:
            raise HTTPException(status_code=500, detail="Error reading file")
            
    except Exception as e:
        logger.exception(f"Error serving endpoint JSON for '{endpoint}': {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# Add new endpoint for summarizing posts
@router.get("/summarize_posts")
async def summarize_posts():
    """
    Run the summarize posts script.
    """
    logger.debug("Summarize posts called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "summarize_posts.py")
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Posts summarized successfully.")
            return JSONResponse({
                "status": "success",
                "message": "Posts summarized successfully.",
                "output": result.stdout
            })
        else:
            logger.error(f"Failed to summarize posts: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": "Failed to summarize posts.",
                "output": result.stderr
            })
    except Exception as e:
        logger.exception(f"Error summarizing posts: {str(e)}")
        return JSONResponse({"status": "error", "message": str(e)})


def count_tokens(text: str) -> int:
    """Rough estimate of token count based on words and punctuation."""
    # Split on whitespace and count punctuation as separate tokens
    words = text.split()
    # Add count of common punctuation marks that might be separate tokens
    punctuation_count = text.count('.') + text.count(',') + text.count('!') + \
                       text.count('?') + text.count(';') + text.count(':')
    return len(words) + punctuation_count


@router.get("/get-aggregated-summary")
async def get_aggregated_summary():
    """
    Aggregate all summary .txt files into a single file and return its link.
    """
    logger.debug("Get aggregated summary called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'output')
        
        # Create aggregated summaries directory if it doesn't exist
        agg_dir = os.path.join(output_dir, 'aggregated_summaries')
        os.makedirs(agg_dir, exist_ok=True)
        
        # Find all summary .txt files
        summary_files = []
        total_content = ""
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith('_summary.txt'):
                    summary_files.append(os.path.join(root, file))
        
        if not summary_files:
            logger.debug("No summary files found")
            return JSONResponse({"link": None})
        
        # Create aggregated file with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        agg_filename = f'aggregated_summaries_{timestamp}.txt'
        agg_path = os.path.join(agg_dir, agg_filename)
        
        # Aggregate content from all summary files
        with open(agg_path, 'w', encoding='utf-8') as agg_file:
            for i, summary_file in enumerate(summary_files, 1):
                try:
                    with open(summary_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        total_content += content
                        
                    # Add file header
                    filename = os.path.basename(summary_file)
                    agg_file.write(f"\n{'='*80}\n")
                    agg_file.write(f"Summary {i}: {filename}\n")
                    agg_file.write(f"{'='*80}\n\n")
                    agg_file.write(content)
                    agg_file.write("\n\n")
                except Exception as e:
                    logger.error(f"Error reading summary file {summary_file}: {str(e)}")
                    continue
        
        # Calculate total token count
        token_count = count_tokens(total_content)
        
        # Generate URL for the aggregated file
        url = f"/output/aggregated_summaries/{agg_filename}"
        logger.debug(f"Created aggregated summary at: {url}")
        
        return JSONResponse({
            "link": url,
            "token_count": token_count
        })
        
    except Exception as e:
        logger.exception(f"Error creating aggregated summary: {str(e)}")
        return JSONResponse({"status": "error", "message": str(e)})


@router.get("/get-log-files")
async def get_log_files():
    """Get a list of all log files in the logs directory."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(script_dir, 'logs')
        
        if not os.path.exists(logs_dir):
            return JSONResponse({"files": []})
            
        log_files = []
        for file in os.listdir(logs_dir):
            if file.endswith('.log') or file.endswith('.txt'):
                file_path = os.path.join(logs_dir, file)
                # Get file stats
                stats = os.stat(file_path)
                log_files.append({
                    "name": file,
                    "size": stats.st_size,
                    "modified": datetime.fromtimestamp(stats.st_mtime).isoformat()
                })
                
        # Sort by modification time, most recent first
        log_files.sort(key=lambda x: x["modified"], reverse=True)
        
        return JSONResponse({"files": log_files})
    except Exception as e:
        logger.exception(f"Error getting log files: {str(e)}")
        return JSONResponse({"error": str(e)})
