from fastapi import FastAPI, HTTPException
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

from ai_dataprep import process_single_file  # Ensure these imports are correct
from annual_analysis import export_for_endpoint  # Ensure this import is correct
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

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Define the absolute path to the output directory
script_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(script_dir, 'output')

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)
logger.debug(f"Ensured that output directory exists at '{output_dir}'")

# Mount the output directory to serve static files
app.mount("/output", StaticFiles(directory=output_dir), name="output")
logger.info(f"Mounted '/output' to serve static files from '{output_dir}'")


def find_output_files_for_endpoint(endpoint: str, output_dir: str):
    """
    Search the output directory and its subdirectories for files matching the endpoint.
    Returns a dict with keys being subfolder paths and values being dicts of file types.
    """
    output_files = {}
    logger.debug(f"Searching for output files for endpoint: '{endpoint}' in '{output_dir}'")
    
    for root, dirs, files in os.walk(output_dir):
        matching_files = {}
        for file in files:
            if endpoint in file:
                logger.debug(f"Found file matching endpoint: '{file}' in '{root}'")
                if file.endswith('.html'):
                    matching_files['html'] = os.path.join(root, file)
                elif file.endswith('.md'):
                    matching_files['md'] = os.path.join(root, file)
                elif file.endswith('_assistant_reply.txt'):
                    matching_files['txt'] = os.path.join(root, file)
        
        if matching_files:
            # Use the relative path from output_dir as the key
            rel_path = os.path.relpath(root, output_dir)
            output_files[rel_path] = matching_files

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
    logger.debug(f"Looking for datasets in: {raw_datasets_dir} and {fixed_datasets_dir}")

    if not os.path.exists(raw_datasets_dir):
        logger.error(f"Raw datasets directory '{raw_datasets_dir}' does not exist.")
        return datasets

    # First, get list of all raw dataset files
    raw_files = [f for f in os.listdir(raw_datasets_dir) 
                 if f.endswith('.json') and f != 'analysis_map.json']

    for filename in raw_files:
        try:
            # Check if fixed version exists first
            fixed_file_path = os.path.join(fixed_datasets_dir, filename)
            has_fixed = os.path.exists(fixed_file_path)
            
            # Set the json_path without 'data' prefix
            json_path = os.path.join('datasets', 'fixed' if has_fixed else '', filename)
            
            dataset_info = {
                'filename': filename,
                'category': 'N/A',
                'report_category': None,
                'periodic': 'No',
                'endpoint': 'N/A',
                'item_noun': 'N/A',
                'rows_updated_at': 'N/A',
                'rows_updated_dt': None,
                'has_fixed': has_fixed,
                'json_path': json_path
            }

            # First read metadata from raw file
            raw_file_path = os.path.join(raw_datasets_dir, filename)
            with open(raw_file_path, 'r') as file:
                raw_data = json.load(file)
                if isinstance(raw_data, dict):
                    # Get title and truncate it for item_noun
                    title = raw_data.get('title', 'N/A')
                    dataset_info['item_noun'] = (title[:37] + '...') if len(title) > 60 else title
                    
                    # Extract just the endpoint identifier from the URL, keeping .json
                    endpoint = raw_data.get('endpoint', 'N/A')
                    if endpoint != 'N/A' and '/' in endpoint:
                        # Extract just the endpoint ID from the full URL, keeping .json
                        endpoint = endpoint.split('/')[-1]
                    
                    dataset_info.update({
                        'category': raw_data.get('category', 'N/A'),
                        'endpoint': endpoint,
                    })
                    
                    periodic = raw_data.get('periodic', False)
                    if isinstance(periodic, str):
                        periodic = periodic.lower() == "yes"
                    dataset_info['periodic'] = "Yes" if periodic else "No"

                    rows_updated_at_str = raw_data.get('rows_updated_at', 'N/A')
                    if rows_updated_at_str != 'N/A':
                        try:
                            dt_parsed = datetime.strptime(rows_updated_at_str, '%Y-%m-%dT%H:%M:%SZ')
                            dataset_info['rows_updated_dt'] = dt_parsed
                            dataset_info['rows_updated_at'] = dt_parsed.strftime('%y.%m.%d')
                        except ValueError:
                            pass

            # If fixed version exists, override metadata with fixed version
            if os.path.exists(fixed_file_path):
                dataset_info['has_fixed'] = True
                
                with open(fixed_file_path, 'r') as file:
                    fixed_data = json.load(file)
                    if isinstance(fixed_data, dict):
                        # For fixed files, use report_category instead of category
                        dataset_info['report_category'] = fixed_data.get('report_category', 'N/A')
                        
                        # Update other metadata
                        dataset_info.update({
                            'endpoint': fixed_data.get('endpoint', dataset_info['endpoint']),
                        })
                        
                        # Get title from fixed file and truncate it
                        title = fixed_data.get('title', dataset_info['item_noun'])
                        dataset_info['item_noun'] = (title[:57] + '...') if len(title) > 60 else title
                        
                        periodic = fixed_data.get('periodic', False)
                        if isinstance(periodic, str):
                            periodic = periodic.lower() == "yes"
                        dataset_info['periodic'] = "Yes" if periodic else "No"

                        rows_updated_at_str = fixed_data.get('rows_updated_at', dataset_info['rows_updated_at'])
                        if rows_updated_at_str != 'N/A':
                            try:
                                dt_parsed = datetime.strptime(rows_updated_at_str, '%Y-%m-%dT%H:%M:%SZ')
                                dataset_info['rows_updated_dt'] = dt_parsed
                                dataset_info['rows_updated_at'] = dt_parsed.strftime('%y.%m.%d')
                            except ValueError:
                                pass

                        # Extract just the endpoint identifier from the URL for fixed files too
                        endpoint = fixed_data.get('endpoint', dataset_info['endpoint'])
                        if endpoint != 'N/A' and '/' in endpoint:
                            endpoint = endpoint.split('/')[-1]  # Keep .json extension
                        dataset_info['endpoint'] = endpoint

            datasets.append(dataset_info)

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in file '{filename}': {e}")
        except Exception as e:
            logger.exception(f"Error loading dataset '{filename}': {e}")

    # Sort datasets: items with report_category first, then remaining items by category
    datasets.sort(
        key=lambda x: (
            x['report_category'] is None,  # False (has report_category) comes before True (no report_category)
            x['report_category'] if x['report_category'] is not None else '',  # Sort by report_category if it exists
            x['category'] if x['report_category'] is None else '',  # Sort by category for items without report_category
            x['rows_updated_dt'] if x['rows_updated_dt'] else datetime.min
        )
    )

    logger.info(f"Total datasets loaded: {len(datasets)}")
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


@app.get("/backend")
async def backend(request: Request):
    logger.info("Received request for /backend endpoint")
    datasets_info = load_and_sort_json()
    logger.debug(f"Loaded {len(datasets_info)} datasets")

    # Build output links for each dataset and check for MD/HTML files
    for dataset in datasets_info:
        endpoint = dataset['endpoint']
        output_files = find_output_files_for_endpoint(endpoint, output_dir)
        
        # Build URLs - handle the nested structure
        links = {}
        has_analysis = False
        for folder, files in output_files.items():
            folder_links = {}
            for file_type, file_path in files.items():
                if isinstance(file_path, str):
                    url = get_output_file_url(file_path, output_dir)
                    if url:
                        folder_links[file_type] = url
                        if file_type in ['md', 'html']:
                            has_analysis = True
            if folder_links:
                links[folder] = folder_links
        
        dataset['output_links'] = links
        dataset['has_analysis'] = has_analysis
        
        # Get last run date from MD/HTML files if they exist
        if has_analysis:
            last_run_date = get_md_file_date(output_files)
            if last_run_date:
                dataset['last_run_date'] = last_run_date
                dataset['last_run_str'] = last_run_date.strftime('%y.%m.%d')
            else:
                dataset['last_run_date'] = None
                dataset['last_run_str'] = 'N/A'
        else:
            dataset['last_run_date'] = None
            dataset['last_run_str'] = 'N/A'

    # Sort datasets with the new priority
    datasets_info.sort(
        key=lambda d: (
            not d['has_analysis'],  # True (has analysis) comes first
            not d['has_fixed'],     # Then by fixed status
            d['report_category'] is None,  # Then by report category presence
            d['report_category'] if d['report_category'] is not None else '',
            d['category'] if d['report_category'] is None else '',
            d['last_run_date'] if d['last_run_date'] else datetime.min
        )
    )

    # Add sidebar_buttons to the template context
    sidebar_buttons = [
        {"id": "analyze-checked-btn", "text": "Analyze Checked"},
        {"id": "reload-vector-db-btn", "text": "Reload Vector DB"}
    ]

    logger.info("Rendering 'backend.html' template with dataset information")
    return templates.TemplateResponse('backend.html', {
        "request": request, 
        "datasets": datasets_info,
        "sidebar_buttons": sidebar_buttons
    })


@app.get("/prep-data/{filename}")
async def prep_data(filename: str):
    logger.info(f"Received request to prep data for file: '{filename}'")
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


@app.get("/run_analysis/{endpoint}")
async def run_analysis(endpoint: str):
    """Run analysis for a given endpoint."""
    logger.info(f"Received request to run analysis for endpoint: '{endpoint}'")
    
    # Remove .json extension if present
    endpoint = endpoint.replace('.json', '')
    
    error_log = []
    try:
        logger.debug(f"Attempting to run export_for_endpoint with endpoint: {endpoint}")
        export_for_endpoint(endpoint, output_folder=output_dir,
                          log_file_path=os.path.join(output_dir, 'processing_log.txt'))
        
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


@app.get("/get-updated-links/{endpoint}")
async def get_updated_links(endpoint: str):
    """
    Returns the updated output links for a single endpoint. Helps the front-end
    refresh the row's output links after an operation.
    """
    # Remove .json extension if present to match how files are stored
    endpoint = endpoint.replace('.json', '')
    
    output_files = find_output_files_for_endpoint(endpoint, output_dir)
    links = {}
    for folder, files in output_files.items():
        folder_links = {}
        for file_type, file_path in files.items():
            if isinstance(file_path, str):
                url = get_output_file_url(file_path, output_dir)
                if url:
                    folder_links[file_type] = url
        if folder_links:
            links[folder] = folder_links
    return JSONResponse({'links': links})


# --- ADDED: Route to reload vector DB
@app.get("/reload_vector_db")
async def reload_vector_db():
    """
    Reload the vector DB by running the script load_analysis2vec.py with no params.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "vector_loader.py")
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Vector DB reloaded successfully.")
            return JSONResponse({
                "status": "success",
                "message": "Vector DB reloaded successfully.",
                "output": result.stdout
            })
        else:
            logger.error(f"Failed to reload Vector DB: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": "Failed to reload Vector DB.",
                "output": result.stderr
            })
    except Exception as e:
        logger.exception(f"Error reloading Vector DB: {str(e)}")
        return JSONResponse({"status": "error", "message": str(e)})


@app.get("/dataset-json/{filename:path}")
async def get_dataset_json(filename: str):
    """Serve the JSON file for a dataset."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Remove any leading '../' from the filename for security
    clean_filename = filename.replace('../', '')
    
    # Add 'data' to the path here instead of expecting it in the URL
    file_path = os.path.join(current_dir, 'data', clean_filename)
    
    if not os.path.exists(file_path):
        logger.error(f"JSON file not found: {file_path}")
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        file_path,
        media_type="application/json",
        filename=os.path.basename(clean_filename)
    )


@app.get("/endpoint-json/{endpoint}")
async def get_endpoint_json(endpoint: str):
    """Serve the JSON file for an endpoint's analysis results."""
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


if __name__ == '__main__':
    import uvicorn
    logger.info("Starting FastAPI application")
    uvicorn.run(app, host="0.0.0.0", port=8000)
