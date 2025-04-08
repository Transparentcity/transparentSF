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
import shutil  # Add this import at the top with other imports

from ai_dataprep import process_single_file  # Ensure these imports are correct
from periodic_analysis import export_for_endpoint  # Ensure this import is correct
import logging
from generate_dashboard_metrics import main as generate_metrics
from tools.data_fetcher import fetch_data_from_api
import pandas as pd
from pathlib import Path
from openai import OpenAI
from qdrant_client import QdrantClient
import time
from tools.enhance_dashboard_queries import enhance_dashboard_queries  # Add this import
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Changed from DEBUG to INFO
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

logger.info(f"Script directory: {script_dir}")
logger.info(f"Output directory: {output_dir}")


def set_templates(t):
    """Set the templates instance for this router"""
    global templates
    templates = t
    logger.info("Templates set in backend router")


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
    logger.info(f"Run analysis called for endpoint: {endpoint} with period_type: {period_type}")
    
    # Remove .json extension if present
    endpoint = endpoint.replace('.json', '')
    
    error_log = []
    try:
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create period-specific output folder
        period_folder_map = {
            'year': 'annual',
            'month': 'monthly',
            'day': 'daily',
            'ytd': 'ytd'
        }
        
        if period_type not in period_folder_map:
            raise ValueError(f"Invalid period_type: {period_type}. Must be one of: {', '.join(period_folder_map.keys())}")
            
        period_folder = period_folder_map[period_type]
        period_output_dir = os.path.join(output_dir, period_folder)
        os.makedirs(period_output_dir, exist_ok=True)
        
        logger.info(f"Attempting to run export_for_endpoint with endpoint: {endpoint} and period_type: {period_type}")
        export_for_endpoint(endpoint, 
                          period_type=period_type,
                          output_folder=period_output_dir,
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
        return JSONResponse({
            'status': 'error', 
            'message': str(e)
        }, status_code=500)


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


@router.get("/reload_sfpublic")
async def reload_sfpublic():
    """Reload the SF Public Data collection."""
    logger.debug("Reload SF Public Data collection called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "vector_loader_sfpublic.py")
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
            logger.info("SF Public Data collection reloaded successfully.")
            return JSONResponse({
                "status": "success",
                "message": "SF Public Data collection reloaded successfully.",
                "output": result.stdout,
                "log_content": log_content
            })
        else:
            logger.error(f"Failed to reload SF Public Data collection: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": "Failed to reload SF Public Data collection.",
                "output": result.stderr,
                "log_content": log_content
            })
    except Exception as e:
        logger.exception(f"Error reloading SF Public Data collection: {str(e)}")
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
    Aggregate all summary .txt files from the output directory and return their combined content.
    Also includes current YTD metrics data.
    """
    logger.debug("Get aggregated summary called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'output')
        
        # Find all summary .txt files
        summary_files = []
        total_content = ""
        
        # Walk through output directory and find all summary text files
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith('_summary.txt'):
                    summary_files.append(os.path.join(root, file))
        
        if not summary_files:
            logger.debug("No summary files found")
            return JSONResponse({"content": "", "token_count": 0})
        
        # Aggregate content from all summary files
        aggregated_content = []
        for i, summary_file in enumerate(summary_files, 1):
            try:
                with open(summary_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    total_content += content
                
                # Add file header and content to aggregated content
                filename = os.path.basename(summary_file)
                section = f"\n{'='*80}\nSummary {i}: {filename}\n{'='*80}\n\n{content}\n\n"
                aggregated_content.append(section)
                    
            except Exception as e:
                logger.error(f"Error reading summary file {summary_file}: {str(e)}")
                continue
        
        # Add YTD metrics data
        ytd_file = os.path.join(script_dir, 'data', 'dashboard', 'ytd_metrics.json')
        if os.path.exists(ytd_file):
            try:
                with open(ytd_file, 'r', encoding='utf-8') as f:
                    ytd_data = json.load(f)
                
                # Format YTD metrics as text
                ytd_text = "\n" + "="*80 + "\nYear-to-Date (YTD) Metrics Summary:\n" + "="*80 + "\n\n"
                
                # Add citywide metrics
                if "districts" in ytd_data and "0" in ytd_data["districts"]:
                    citywide = ytd_data["districts"]["0"]
                    ytd_text += f"Citywide Statistics:\n"
                    
                    for category in citywide.get("categories", []):
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
                
                aggregated_content.append(ytd_text)
                logger.info("Successfully added YTD metrics to aggregated summary")
                
            except Exception as e:
                logger.error(f"Error processing YTD metrics: {str(e)}")
        
        # Join all content
        final_content = "".join(aggregated_content)
        
        # Calculate total token count
        token_count = count_tokens(total_content)
        
        return JSONResponse({
            "content": final_content,
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


@router.get("/disk-space")
async def get_disk_space():
    """Get disk space information for the current drive."""
    try:
        # Get disk usage for the current directory's drive
        total, used, free = shutil.disk_usage(os.path.abspath(os.sep))
        
        return JSONResponse({
            "total": total,
            "available": free,
            "used": used
        })
    except Exception as e:
        logger.exception(f"Error getting disk space: {str(e)}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )


@router.post("/clear-html-files")
async def clear_html_files():
    """Delete all HTML files from the output directory and its subdirectories."""
    logger.debug("Clear HTML files called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'output')
        
        # Count of deleted files
        deleted_count = 0
        
        # Walk through all subdirectories
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.endswith('.html'):
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.debug(f"Deleted file: {file_path}")
                    except Exception as e:
                        logger.error(f"Error deleting file {file_path}: {str(e)}")
        
        logger.info(f"Successfully deleted {deleted_count} HTML files")
        return JSONResponse({
            "status": "success",
            "message": f"Successfully deleted {deleted_count} HTML files"
        })
        
    except Exception as e:
        logger.exception(f"Error clearing HTML files: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })


@router.post("/clear-period-files/{period_type}")
async def clear_period_files(period_type: str):
    """Delete all files from a specific period folder (monthly or annual)."""
    logger.debug(f"Clear {period_type} files called")
    
    # Validate period_type
    period_folder_map = {
        'year': 'annual',
        'month': 'monthly',
        'day': 'daily',
        'ytd': 'ytd'
    }
    
    if period_type not in period_folder_map:
        logger.error(f"Invalid period_type: {period_type}")
        return JSONResponse({
            "status": "error",
            "message": f"Invalid period_type: {period_type}. Must be one of: {', '.join(period_folder_map.keys())}"
        }, status_code=400)
        
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        period_folder = period_folder_map[period_type]
        output_dir = os.path.join(script_dir, 'output', period_folder)
        
        # Check if directory exists
        if not os.path.exists(output_dir):
            logger.debug(f"Directory does not exist: {output_dir}")
            return JSONResponse({
                "status": "success",
                "message": f"No {period_folder} files to delete"
            })
        
        # Count of deleted files
        deleted_count = 0
        
        # Delete all files and subdirectories
        for root, dirs, files in os.walk(output_dir, topdown=False):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.debug(f"Deleted file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {str(e)}")
            
            # Delete empty directories
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    # Only remove if empty
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        logger.debug(f"Deleted directory: {dir_path}")
                except Exception as e:
                    logger.error(f"Error deleting directory {dir_path}: {str(e)}")
        
        logger.info(f"Successfully deleted {deleted_count} files from {period_folder} folder")
        return JSONResponse({
            "status": "success", 
            "message": f"Successfully deleted {deleted_count} files from {period_folder} folder"
        })
        
    except Exception as e:
        logger.exception(f"Error clearing {period_type} files: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })


@router.get("/generate_ytd_metrics")
async def generate_ytd_metrics():
    """Generate YTD metrics on demand."""
    logger.debug("Generate YTD metrics called")
    try:
        generate_metrics()
        logger.info("YTD metrics generated successfully")
        return JSONResponse({
            "status": "success",
            "message": "YTD metrics generated successfully"
        })
    except Exception as e:
        logger.exception(f"Error generating YTD metrics: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/run_all_metrics")
async def run_all_metrics(period_type: str = 'year'):
    """Run analysis for all available endpoints."""
    logger.info(f"Run all metrics called with period_type: {period_type}")
    
    # Handle weekly analysis separately
    if period_type == 'week':
        try:
            logger.info("Running weekly analysis for all default metrics")
            
            # Import necessary functions
            from generate_weekly_analysis import run_weekly_analysis, generate_weekly_newsletter
            
            # Run the weekly analysis
            results = run_weekly_analysis(process_districts=True)
            
            # Generate a newsletter
            newsletter_path = generate_weekly_newsletter(results)
            
            if results:
                successful = len(results)
                failed = 0
                
                # Get the metric IDs for the successful analyses
                metric_ids = [result.get('metric_id', 'unknown') for result in results]
                
                return JSONResponse({
                    "status": "success",
                    "message": f"Weekly analysis completed successfully for {successful} metrics.",
                    "results": {
                        "total": successful,
                        "successful": successful,
                        "failed": 0,
                        "metrics": metric_ids,
                        "newsletter_path": newsletter_path
                    }
                })
            else:
                return JSONResponse({
                    "status": "error",
                    "message": "Weekly analysis returned no results.",
                    "results": {
                        "total": 0,
                        "successful": 0,
                        "failed": 0
                    }
                })
        except ImportError as e:
            logger.error(f"Could not import weekly analysis functions: {str(e)}")
            return JSONResponse({
                "status": "error",
                "message": f"Missing required module for weekly analysis: {str(e)}"
            }, status_code=500)
        except Exception as e:
            logger.exception(f"Error running weekly analysis: {str(e)}")
            return JSONResponse({
                "status": "error",
                "message": f"Error running weekly analysis: {str(e)}"
            }, status_code=500)
    
    # For other period types, use the run_all_metrics.py script
    try:
        # Run the run_all_metrics.py script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "run_all_metrics.py")
        
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(script_dir, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Run the script
        logger.info(f"Running script: {script_path}")
        
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("All metrics processed successfully")
            
            # Parse the output to find success/failure counts
            output = result.stdout
            
            # Try to extract success and failure counts from the last line
            lines = output.strip().split('\n')
            last_line = lines[-1] if lines else ""
            
            # Default values if parsing fails
            successful = 0
            failed = 0
            total = 0
            
            # Try to parse the completion message: "Completed all analyses. Successful: X, Failed: Y"
            if "Completed all analyses" in last_line:
                parts = last_line.split()
                for i, part in enumerate(parts):
                    if part == "Successful:":
                        try:
                            successful = int(parts[i+1].rstrip(','))
                        except (IndexError, ValueError):
                            pass
                    elif part == "Failed:":
                        try:
                            failed = int(parts[i+1])
                        except (IndexError, ValueError):
                            pass
            
            total = successful + failed
            
            # Return success with counts
            return JSONResponse({
                "status": "success" if failed == 0 else "partial",
                "message": f"Processed {successful} of {total} metrics successfully. {failed} metrics failed.",
                "results": {
                    "total": total,
                    "successful": successful,
                    "failed": failed,
                    "output": output
                }
            })
        else:
            logger.error(f"Error running all metrics: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": "Error running all metrics",
                "error": result.stderr
            }, status_code=500)
    except Exception as e:
        logger.exception(f"Error running all metrics: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/query")
async def query_page(request: Request):
    """Serve the query interface page."""
    logger.debug("Query page route called")
    if templates is None:
        logger.error("Templates not initialized in backend router")
        raise RuntimeError("Templates not initialized")
    
    return templates.TemplateResponse("query.html", {
        "request": request
    })


@router.post("/execute-query")
async def execute_query(request: Request):
    """Execute a query and return results as HTML or Markdown table."""
    try:
        form_data = await request.form()
        endpoint = form_data.get('endpoint', '').strip()
        query = form_data.get('query', '').strip()
        format_type = form_data.get('format', 'html')
        
        if not endpoint or not query:
            return JSONResponse({
                'status': 'error',
                'message': 'Both endpoint and query are required'
            })
            
        # Execute query using existing data_fetcher
        result = fetch_data_from_api({'endpoint': endpoint, 'query': query})
        
        if 'error' in result:
            return JSONResponse({
                'status': 'error',
                'message': result['error'],
                'queryURL': result.get('queryURL')
            })
            
        # Convert data to DataFrame
        df = pd.DataFrame(result['data'])
        
        # Generate table based on format
        if format_type == 'markdown':
            table = df.to_markdown(index=False)
        else:  # html
            table = df.to_html(index=False, classes=['table', 'table-striped', 'table-hover'])
            
        return JSONResponse({
            'status': 'success',
            'table': table,
            'queryURL': result.get('queryURL'),
            'rowCount': len(df)
        })
        
    except Exception as e:
        logger.exception(f"Error executing query: {str(e)}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        })


@router.get("/logs/{filename}")
async def get_log_file(filename: str):
    """Serve a log file directly."""
    logger.info(f"Get log file called for: {filename}")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(script_dir, 'logs')
        file_path = os.path.join(logs_dir, filename)
        
        # Basic security check - ensure the file is within the logs directory
        if not os.path.abspath(file_path).startswith(os.path.abspath(logs_dir)):
            logger.error(f"Attempted to access file outside logs directory: {file_path}")
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not os.path.exists(file_path):
            logger.error(f"Log file not found: {file_path}")
            raise HTTPException(status_code=404, detail="File not found")
            
        return FileResponse(
            file_path,
            media_type="text/plain",
            filename=filename
        )
    except Exception as e:
        logger.exception(f"Error serving log file: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute-qdrant-query")
async def execute_qdrant_query(request: Request):
    """Execute a semantic search query using Qdrant."""
    try:
        form_data = await request.form()
        collection_name = form_data.get('collection', '').strip()
        query = form_data.get('query', '').strip()
        
        # Connect to Qdrant
        qdrant = QdrantClient(host='localhost', port=6333)
        
        # If no parameters provided, just return collections list
        if not collection_name and not query:
            collections = [c.name for c in qdrant.get_collections().collections]
            collections.sort()  # Sort alphabetically
            logger.info(f"Available collections: {collections}")
            
            # Get collection info
            collection_info = {}
            for coll in collections:
                try:
                    info = qdrant.get_collection(coll)
                    points_count = info.points_count
                    collection_info[coll] = {
                        "points_count": points_count,
                        "vector_size": info.config.params.vectors.size
                    }
                    logger.info(f"Collection {coll}: {points_count} points, vector size: {info.config.params.vectors.size}")
                except Exception as e:
                    logger.error(f"Error getting info for collection {coll}: {e}")
                    
            return JSONResponse({
                'status': 'success',
                'collections': collections,
                'collection_info': collection_info,
                'results': []
            })
        
        # For actual search, require both parameters
        if not collection_name or not query:
            return JSONResponse({
                'status': 'error',
                'message': 'Both collection and query are required for search'
            })
            
        limit = int(form_data.get('limit', '5'))
        logger.info(f"Searching collection '{collection_name}' for query: '{query}' (limit: {limit})")
            
        # Get embedding for query
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model="text-embedding-3-large",
            input=query
        )
        query_vector = response.data[0].embedding
        logger.debug(f"Generated query vector of size {len(query_vector)}")
        
        # Get collection info before search
        collection_info = qdrant.get_collection(collection_name)
        logger.info(f"Collection {collection_name} has {collection_info.points_count} points")
        
        # Query Qdrant with lower score threshold
        search_result = qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            score_threshold=0.05  # Lower threshold to catch more potential matches
        )
        
        logger.info(f"Found {len(search_result)} results")
        
        # Format results with score interpretation
        results = []
        for hit in search_result:
            score = round(hit.score, 3)
            # Add score interpretation
            if score >= 0.5:
                relevance = "Very High"
            elif score >= 0.3:
                relevance = "High"
            elif score >= 0.2:
                relevance = "Medium"
            elif score >= 0.1:
                relevance = "Low"
            elif score >= 0.05:
                relevance = "Very Low"
            else:
                relevance = "Minimal"
                
            result = {
                'score': score,
                'relevance': relevance,
                'filename': hit.payload.get('filename', 'N/A'),
                'content': hit.payload.get('content', 'No content')  # Show full content
            }
            logger.debug(f"Result: score={score} ({relevance}), file={result['filename']}")
            results.append(result)
            
        # Get list of available collections
        collections = [c.name for c in qdrant.get_collections().collections]
        collections.sort()  # Sort alphabetically
            
        return JSONResponse({
            'status': 'success',
            'results': results,
            'collections': collections,
            'query_info': {
                'collection': collection_name,
                'query': query,
                'vector_size': len(query_vector),
                'total_points': collection_info.points_count,
                'score_guide': {
                    '0.5+': 'Very High Relevance',
                    '0.3-0.5': 'High Relevance',
                    '0.2-0.3': 'Medium Relevance',
                    '0.1-0.2': 'Low Relevance',
                    '0.05-0.1': 'Very Low Relevance',
                    '<0.05': 'Minimal Relevance'
                }
            }
        })
        
    except Exception as e:
        logger.exception(f"Error executing Qdrant query: {str(e)}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        })


@router.delete("/delete-collection/{collection_name}")
async def delete_collection(collection_name: str):
    """Delete a Qdrant collection."""
    logger.debug(f"Delete collection called for: {collection_name}")
    try:
        # Connect to Qdrant
        qdrant = QdrantClient(host='localhost', port=6333)
        
        # Check if collection exists
        if not qdrant.collection_exists(collection_name):
            return JSONResponse({
                'status': 'error',
                'message': f'Collection {collection_name} does not exist'
            })
            
        # Delete collection
        qdrant.delete_collection(collection_name)
        time.sleep(2)  # Wait for deletion to complete
        
        # Verify deletion
        if not qdrant.collection_exists(collection_name):
            logger.info(f"Successfully deleted collection {collection_name}")
            return JSONResponse({
                'status': 'success',
                'message': f'Collection {collection_name} deleted successfully'
            })
        else:
            logger.error(f"Collection {collection_name} still exists after deletion attempt")
            return JSONResponse({
                'status': 'error',
                'message': f'Failed to delete collection {collection_name}'
            })
            
    except Exception as e:
        logger.exception(f"Error deleting collection {collection_name}: {str(e)}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        })


@router.get("/metric-control")
async def metric_control(request: Request):
    """Serve the metric control interface."""
    logger.debug("Metric control route called")
    if templates is None:
        logger.error("Templates not initialized in backend router")
        raise RuntimeError("Templates not initialized")
    
    return templates.TemplateResponse("metric_control.html", {
        "request": request
    })


@router.get("/get-endpoint-columns/{endpoint}")
async def get_endpoint_columns(endpoint: str):
    """Get available columns for an endpoint."""
    try:
        # Load the dataset file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dataset_file = os.path.join(script_dir, "data", "datasets", "fixed", f"{endpoint}.json")
        
        if not os.path.exists(dataset_file):
            raise HTTPException(status_code=404, detail=f"Dataset file not found for endpoint: {endpoint}")
        
        with open(dataset_file, 'r') as f:
            dataset_data = json.load(f)
        
        # Extract column names from the dataset
        columns = [col["fieldName"] for col in dataset_data.get("columns", [])]
        
        return JSONResponse({
            "status": "success",
            "columns": columns
        })
    except Exception as e:
        logger.exception(f"Error getting columns for endpoint '{endpoint}': {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/get-selected-columns/{endpoint}")
async def get_selected_columns(endpoint: str):
    """Get currently selected columns for an endpoint."""
    try:
        # Load the enhanced queries file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        enhanced_queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        
        if not os.path.exists(enhanced_queries_file):
            raise HTTPException(status_code=404, detail="Enhanced queries file not found")
        
        with open(enhanced_queries_file, 'r') as f:
            enhanced_queries = json.load(f)
        
        # Find the metric with this endpoint
        selected_columns = []
        for category in enhanced_queries.values():
            for subcategory in category.values():
                for metric in subcategory.get("queries", {}).values():
                    if metric.get("endpoint") == endpoint:
                        # Get selected columns from the metric's category_fields
                        category_fields = metric.get("category_fields", [])
                        selected_columns = [field["fieldName"] for field in category_fields]
                        break
        
        return JSONResponse({
            "status": "success",
            "columns": selected_columns
        })
    except Exception as e:
        logger.exception(f"Error getting selected columns for endpoint '{endpoint}': {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/update-selected-columns")
async def update_selected_columns(request: Request):
    """Update the selected columns for an endpoint."""
    try:
        # Get request data
        data = await request.json()
        endpoint = data.get("endpoint")
        columns = data.get("columns", [])
        metric_id = data.get("metric_id")  # Add metric_id to identify specific metric
        
        if not endpoint or not metric_id:
            raise HTTPException(status_code=400, detail="Both endpoint and metric_id are required")
        
        # Load the enhanced queries file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        enhanced_queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        
        if not os.path.exists(enhanced_queries_file):
            raise HTTPException(status_code=404, detail="Enhanced queries file not found")
        
        with open(enhanced_queries_file, 'r') as f:
            enhanced_queries = json.load(f)
        
        # Find and update the metric with this endpoint and metric_id
        metric_updated = False
        metric_found = False
        
        # Log the endpoint and metric_id we're looking for
        logger.info(f"Looking for endpoint: {endpoint}, metric_id: {metric_id}")
        
        for category in enhanced_queries.values():
            for subcategory in category.values():
                for metric_name, metric in subcategory.get("queries", {}).items():
                    metric_endpoint = metric.get("endpoint")
                    current_metric_id = metric.get("id")
                    logger.debug(f"Checking metric: {metric_name} with endpoint: {metric_endpoint}, id: {current_metric_id}")
                    
                    if metric_endpoint == endpoint and current_metric_id == metric_id:
                        metric_found = True
                        # Update the category_fields with selected columns
                        metric["category_fields"] = [
                            {
                                "name": col,
                                "fieldName": col,
                                "description": f"Selected column for enhanced dashboard queries"
                            }
                            for col in columns
                        ]
                        metric_updated = True
                        logger.info(f"Successfully updated category_fields for metric: {metric_name}")
                        break
        
        if not metric_found:
            logger.error(f"Metric with endpoint '{endpoint}' and id '{metric_id}' not found")
            raise HTTPException(status_code=404, detail=f"No metric found with endpoint: {endpoint} and id: {metric_id}")
        
        if not metric_updated:
            logger.error(f"Found metric but failed to update category_fields")
            raise HTTPException(status_code=500, detail=f"Failed to update category_fields for endpoint: {endpoint} and id: {metric_id}")
        
        # Save the updated enhanced queries
        with open(enhanced_queries_file, 'w') as f:
            json.dump(enhanced_queries, f, indent=4)
        
        return JSONResponse({
            "status": "success",
            "message": f"Updated category_fields for metric {metric_id} with endpoint {endpoint}"
        })
    except Exception as e:
        logger.exception(f"Error updating category_fields: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/run_specific_metric")
async def run_specific_metric(metric_id: int, district_id: int = 0, period_type: str = 'year'):
    """Run analysis for a specific metric."""
    logger.info(f"Run specific metric called for metric_id: {metric_id}, district_id: {district_id}, period_type: {period_type}")
    
    # Validate period_type
    period_folder_map = {
        'year': 'annual',
        'month': 'monthly',
        'day': 'daily',
        'ytd': 'ytd',
        'week': 'weekly'
    }
    
    if period_type not in period_folder_map:
        logger.error(f"Invalid period_type: {period_type}")
        return JSONResponse({
            "status": "error",
            "message": f"Invalid period_type: {period_type}. Must be one of: {', '.join(period_folder_map.keys())}"
        }, status_code=400)
    
    try:
        # Load the metric ID mapping to get the endpoint
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mapping_file = os.path.join(script_dir, "data", "dashboard", "metric_id_mapping.json")
        enhanced_queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        
        if not os.path.exists(mapping_file):
            logger.error(f"Metric ID mapping file not found: {mapping_file}")
            return JSONResponse({
                "status": "error",
                "message": "Metric ID mapping file not found"
            }, status_code=404)
        
        if not os.path.exists(enhanced_queries_file):
            logger.error(f"Enhanced queries file not found: {enhanced_queries_file}")
            return JSONResponse({
                "status": "error",
                "message": "Enhanced queries file not found"
            }, status_code=404)
        
        # Load both files
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
            
        with open(enhanced_queries_file, 'r') as f:
            enhanced_queries = json.load(f)
        
        # Check if the metric ID exists in the mapping
        metric_id_str = str(metric_id)
        if metric_id_str not in mapping:
            logger.error(f"Metric ID {metric_id} not found in mapping")
            return JSONResponse({
                "status": "error",
                "message": f"Metric ID {metric_id} not found"
            }, status_code=404)
        
        # Get the metric details from mapping
        metric_info = mapping[metric_id_str]
        metric_name = metric_info.get("name", "Unknown")
        category = metric_info.get("category", "Uncategorized")
        
        # Find the metric in enhanced queries
        from generate_metric_analysis import find_metric_in_queries
        enhanced_metric_info = find_metric_in_queries(enhanced_queries, metric_id)
        
        if not enhanced_metric_info:
            logger.error(f"Metric ID {metric_id} not found in enhanced queries")
            return JSONResponse({
                "status": "error",
                "message": f"Metric ID {metric_id} not found in enhanced queries"
            }, status_code=404)
        
        # Merge the metric info from mapping with the enhanced query info
        metric_info.update(enhanced_metric_info)
        
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(script_dir, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create period-specific output folder
        period_folder = period_folder_map[period_type]
        period_output_dir = os.path.join(output_dir, period_folder, str(district_id))
        os.makedirs(period_output_dir, exist_ok=True)
        
        # Determine which script to run based on period_type
        if period_type == 'ytd':
            # For YTD metrics, use generate_dashboard_metrics.py
            logger.info(f"Running YTD dashboard metrics generation for metric ID {metric_id}")
            
            # Check if we can import the specific metric generation function
            try:
                from generate_dashboard_metrics import generate_single_metric
                
                # Check if the function exists and call it
                if callable(generate_single_metric):
                    result = generate_single_metric(metric_id=metric_id, district_id=district_id)
                    logger.info(f"YTD metrics generation completed for metric ID {metric_id}")
                else:
                    # Fall back to the main function if generate_single_metric isn't available
                    from generate_dashboard_metrics import main as generate_all_metrics
                    generate_all_metrics()
                    logger.info(f"All YTD metrics generated (including metric ID {metric_id})")
            except (ImportError, AttributeError) as e:
                # If there's no generate_single_metric function, run the main function
                logger.warning(f"Could not import specific metric generation function: {str(e)}")
                from generate_dashboard_metrics import main as generate_all_metrics
                generate_all_metrics()
                logger.info(f"All YTD metrics generated (including metric ID {metric_id})")
        
        elif period_type == 'week':
            # For weekly analysis, use generate_weekly_analysis.py
            logger.info(f"Running weekly analysis for metric ID {metric_id}")
            
            try:
                from generate_weekly_analysis import run_weekly_analysis, generate_weekly_newsletter
                
                # Run the analysis for the specific metric
                results = run_weekly_analysis(
                    metrics_list=[str(metric_id)],
                    process_districts=(district_id == 0)  # Only process districts if district_id is 0 (citywide)
                )
                
                # Generate a newsletter
                newsletter_path = generate_weekly_newsletter(results)
                
                logger.info(f"Weekly analysis completed for metric ID {metric_id}")
                
            except ImportError as e:
                logger.error(f"Could not import generate_weekly_analysis module: {str(e)}")
                return JSONResponse({
                    "status": "error",
                    "message": f"Missing required module: {str(e)}"
                }, status_code=500)
            except Exception as e:
                logger.error(f"Error in generate_weekly_analysis: {str(e)}")
                return JSONResponse({
                    "status": "error",
                    "message": f"Error generating weekly analysis: {str(e)}"
                }, status_code=500)
                
        else:
            # For monthly/annual analysis, use generate_metric_analysis.py
            logger.info(f"Running {period_type} analysis for metric ID {metric_id}")
            
            try:
                from generate_metric_analysis import process_metric_analysis
                
                # Run the analysis for the specific metric
                result = process_metric_analysis(
                    metric_info=metric_info,
                    period_type=period_type,
                    process_districts=True  # Enable district processing
                )
                logger.info(f"{period_type.capitalize()} analysis completed for metric ID {metric_id}")
                
            except ImportError as e:
                logger.error(f"Could not import generate_metric_analysis module: {str(e)}")
                return JSONResponse({
                    "status": "error",
                    "message": f"Missing required module: {str(e)}"
                }, status_code=500)
            except Exception as e:
                logger.error(f"Error in generate_metric_analysis: {str(e)}")
                return JSONResponse({
                    "status": "error",
                    "message": f"Error generating metric analysis: {str(e)}"
                }, status_code=500)
        
        # Check if expected output files exist
        expected_file = os.path.join(output_dir, 'dashboard', str(district_id), f"{metric_id}.json") if period_type == 'ytd' else os.path.join(period_output_dir, f"{metric_id}.json")
        
        if os.path.exists(expected_file):
            logger.info(f"Output file confirmed at: {expected_file}")
        else:
            logger.warning(f"Expected output file not found at: {expected_file}")
        
        return JSONResponse({
            "status": "success",
            "message": f"{period_type.capitalize()} analysis for metric ID {metric_id} completed successfully",
            "details": {
                "metric_id": metric_id,
                "district_id": district_id,
                "period_type": period_type,
                "metric_name": metric_name,
                "category": category,
                "expected_file": expected_file
            }
        })
    except Exception as e:
        logger.exception(f"Error running specific metric analysis: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/get_output_files")
async def get_output_files(metric_id: str, district_id: str = '0', period_type: str = 'year'):
    """
    Get output files for a specific metric based on metric ID, district ID, and period type.
    Returns files organized by folder type (dashboard, monthly, annual, ytd, weekly).
    """
    try:
        logger.info(f"Fetching output files for metric ID: {metric_id}, district: {district_id}, period: {period_type}")
        
        # Define the directory structure
        output_folder = os.path.join(script_dir, 'output')
        district_dir = f"district_{district_id}"
        
        # Dictionary to store files by category
        result = {
            "dashboard": [],
            "monthly": [],
            "annual": [],
            "ytd": [],
            "weekly": []
        }
        
        # Check both directory patterns for each output type
        
        # Check dashboard folder - both directory structures
        dashboard_dir_new = os.path.join(output_folder, "dashboard", district_dir, metric_id)  # district_0/6/
        dashboard_dir_old = os.path.join(output_folder, "dashboard", district_id)  # 0/
        dashboard_file_old = os.path.join(dashboard_dir_old, f"{metric_id}.json")  # 0/6.json
        
        if os.path.exists(dashboard_dir_new):
            result["dashboard"] = [f for f in os.listdir(dashboard_dir_new) if os.path.isfile(os.path.join(dashboard_dir_new, f))]
            logger.debug(f"Found {len(result['dashboard'])} files in dashboard/district_{district_id}/{metric_id} folder")
        elif os.path.exists(dashboard_dir_old):
            # Check if the specific metric file exists in the old structure
            if os.path.exists(dashboard_file_old):
                result["dashboard"] = [f"{metric_id}.json"]
                logger.debug(f"Found metric file at dashboard/{district_id}/{metric_id}.json")
        
        # Check monthly folder - both directory structures
        monthly_dir_new = os.path.join(output_folder, "monthly", district_dir, metric_id)  # district_0/6/
        monthly_dir_old = os.path.join(output_folder, "monthly", district_id)  # 0/
        monthly_file_old = os.path.join(monthly_dir_old, f"{metric_id}.json")  # 0/6.json
        
        if os.path.exists(monthly_dir_new):
            result["monthly"] = [f for f in os.listdir(monthly_dir_new) if os.path.isfile(os.path.join(monthly_dir_new, f))]
            logger.debug(f"Found {len(result['monthly'])} files in monthly/district_{district_id}/{metric_id} folder")
        elif os.path.exists(monthly_dir_old):
            # Check if the specific metric file exists in the old structure
            if os.path.exists(monthly_file_old):
                result["monthly"] = [f"{metric_id}.json"]
                logger.debug(f"Found metric file at monthly/{district_id}/{metric_id}.json")
        
        # Check annual folder - both directory structures
        annual_dir_new = os.path.join(output_folder, "annual", district_dir, metric_id)  # district_0/6/
        annual_dir_old = os.path.join(output_folder, "annual", district_id)  # 0/
        annual_file_old = os.path.join(annual_dir_old, f"{metric_id}.json")  # 0/6.json
        
        if os.path.exists(annual_dir_new):
            result["annual"] = [f for f in os.listdir(annual_dir_new) if os.path.isfile(os.path.join(annual_dir_new, f))]
            logger.debug(f"Found {len(result['annual'])} files in annual/district_{district_id}/{metric_id} folder")
        elif os.path.exists(annual_dir_old):
            # Check if the specific metric file exists in the old structure
            if os.path.exists(annual_file_old):
                result["annual"] = [f"{metric_id}.json"]
                logger.debug(f"Found metric file at annual/{district_id}/{metric_id}.json")
        
        # Check ytd folder - both directory structures
        ytd_dir_new = os.path.join(output_folder, "ytd", district_dir, metric_id)  # district_0/6/
        ytd_dir_old = os.path.join(output_folder, "ytd", district_id)  # 0/
        ytd_file_old = os.path.join(ytd_dir_old, f"{metric_id}.json")  # 0/6.json
        
        if os.path.exists(ytd_dir_new):
            result["ytd"] = [f for f in os.listdir(ytd_dir_new) if os.path.isfile(os.path.join(ytd_dir_new, f))]
            logger.debug(f"Found {len(result['ytd'])} files in ytd/district_{district_id}/{metric_id} folder")
        elif os.path.exists(ytd_dir_old):
            # Check if the specific metric file exists in the old structure
            if os.path.exists(ytd_file_old):
                result["ytd"] = [f"{metric_id}.json"]
                logger.debug(f"Found metric file at ytd/{district_id}/{metric_id}.json")
        
        # Check weekly folder
        weekly_dir = os.path.join(output_folder, "weekly")
        
        # First try to find files with the metric ID in their filename
        if os.path.exists(weekly_dir):
            weekly_files = []
            for file in os.listdir(weekly_dir):
                # Look for files that start with the metric ID and have a date component
                # Format: metric_id_YYYY-MM-DD.md
                if file.startswith(f"{metric_id}_") and file.endswith(".md"):
                    weekly_files.append(file)
            result["weekly"] = weekly_files
            logger.debug(f"Found {len(result['weekly'])} files in weekly folder matching metric ID {metric_id}")
        
        # Log the total number of files found
        total_files = sum(len(files) for files in result.values())
        logger.info(f"Found a total of {total_files} output files for metric {metric_id}")
        
        return result
    except Exception as e:
        logger.error(f"Error fetching output files: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch output files: {str(e)}"}
        )


@router.post("/enhance_queries")
async def enhance_queries():
    """Enhance dashboard queries with IDs and category fields."""
    try:
        # Define file paths
        script_dir = os.path.dirname(os.path.abspath(__file__))
        queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries.json")
        datasets_dir = os.path.join(script_dir, "..", "ai", "data", "datasets", "fixed")  # Updated path to include 'fixed'
        output_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        
        # Ensure the datasets directory exists
        os.makedirs(datasets_dir, exist_ok=True)
        
        # Run the enhancement process
        enhance_dashboard_queries(queries_file, datasets_dir, output_file)
        
        return JSONResponse({
            "status": "success",
            "message": "Dashboard queries have been enhanced successfully"
        })
    except Exception as e:
        logger.exception(f"Error enhancing dashboard queries: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": f"Error enhancing dashboard queries: {str(e)}"
        }, status_code=500)
