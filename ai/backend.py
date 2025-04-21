from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote

import os
import json
from datetime import datetime, date
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
import psycopg2
import psycopg2.extras
import asyncio

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

def get_db_connection():
    """Helper function to create a database connection using environment variables."""
    try:
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None

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
    """
    Get the JSON data for a specific endpoint.
    """
    logger.debug(f"Get endpoint JSON called for '{endpoint}'")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'output')
        
        # Find the most recent JSON file for this endpoint
        json_files = []
        for root, _, files in os.walk(output_dir):
            for file in files:
                if file.startswith(endpoint) and file.endswith('.json'):
                    json_files.append(os.path.join(root, file))
        
        if not json_files:
            raise HTTPException(status_code=404, detail=f"No JSON files found for endpoint '{endpoint}'")
        
        # Sort by modification time, most recent first
        json_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        most_recent_file = json_files[0]
        
        try:
            with open(most_recent_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return JSONResponse(data)
        except IOError:
            raise HTTPException(status_code=500, detail="Error reading file")
            
    except Exception as e:
        logger.exception(f"Error serving endpoint JSON for '{endpoint}': {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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
        # Run the script directly since it handles defaults internally
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, "generate_dashboard_metrics.py")
        
        # Run the script
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("YTD metrics generated successfully")
            return JSONResponse({
                "status": "success",
                "message": "YTD metrics generated successfully"
            })
        else:
            logger.error(f"Error generating YTD metrics: {result.stderr}")
            return JSONResponse({
                "status": "error",
                "message": f"Error generating YTD metrics: {result.stderr}"
            }, status_code=500)
    except Exception as e:
        logger.exception(f"Error generating YTD metrics: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/generate_weekly_report")
async def generate_weekly_report():
    """Generate weekly report on demand."""
    logger.debug("Generate weekly report called")
    try:
        # Import the necessary functions
        from generate_weekly_analysis import run_weekly_analysis, generate_weekly_newsletter
        
        # Run the weekly analysis with specific metrics instead of empty list
        # Using metric IDs 1-3 as defaults, which are usually the most reliable metrics
        logger.info("Running weekly analysis for default metrics")
        metrics_to_process = ["1", "2", "3"]  # Convert to strings as the function expects string IDs
        results = run_weekly_analysis(metrics_list=metrics_to_process, process_districts=True)
        
        # Generate a newsletter
        newsletter_path = generate_weekly_newsletter(results)
        
        if results and len(results) > 0:
            successful = len(results)
            failed = 0
            
            # Get the metric IDs for the successful analyses
            metric_ids = [result.get('metric_id', 'unknown') for result in results]
            
            # After generating the report, redirect to the weekly-report page
            logger.info(f"Weekly report generated successfully with {successful} metrics")
            return RedirectResponse(url="/weekly-report", status_code=302)
        else:
            error_message = "Weekly analysis returned no results. Check logs for details."
            logger.error(error_message)
            return JSONResponse({
                "status": "error",
                "message": error_message
            }, status_code=500)
    except ImportError as e:
        error_message = f"Could not import weekly analysis functions: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)
    except Exception as e:
        error_message = f"Error generating weekly report: {str(e)}"
        logger.exception(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)


@router.get("/generate_monthly_report")
async def generate_monthly_report_get():
    """Generate monthly report on demand (GET method for backward compatibility)."""
    logger.debug("Generate monthly report (GET) called")
    try:
        # Import the necessary function
        from monthly_report import run_monthly_report_process
        
        # Run the monthly report process
        logger.info("Running monthly report process")
        result = run_monthly_report_process()
        
        if result.get("status") == "success":
            # After generating the report, redirect to the report path
            report_path = result.get("revised_report_path") or result.get("report_path")
            
            if report_path:
                # Extract the filename from the path
                filename = os.path.basename(report_path)
                logger.info(f"Monthly report generated successfully: {filename}")
                
                # Redirect to the report file
                return RedirectResponse(url=f"/logs/{filename}", status_code=302)
            else:
                logger.info("Monthly report generated but no file path returned")
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully"
                })
        else:
            error_message = result.get("message", "Monthly report generation failed. Check logs for details.")
            logger.error(error_message)
            return JSONResponse({
                "status": "error",
                "message": error_message
            }, status_code=500)
    except ImportError as e:
        error_message = f"Could not import monthly report function: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)


@router.post("/generate_monthly_report")
async def generate_monthly_report_post(request: Request):
    """Generate monthly report with custom parameters."""
    logger.debug("Generate monthly report (POST) called")
    try:
        # Get parameters from request body
        body = await request.json()
        district = body.get("district", "0")
        period_type = body.get("period_type", "month")
        max_report_items = body.get("max_report_items", 10)
        
        logger.info(f"Generating monthly report with district={district}, period_type={period_type}, max_items={max_report_items}")
        
        # Import the necessary function
        from monthly_report import run_monthly_report_process
        import asyncio
        
        # Run the monthly report process in a separate thread to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_monthly_report_process(
                district=district,
                period_type=period_type,
                max_report_items=max_report_items
            )
        )
        
        if result.get("status") == "success":
            # Return the report path
            report_path = result.get("revised_report_path") or result.get("newsletter_path")
            
            if report_path:
                # Extract the filename from the path
                filename = os.path.basename(report_path)
                logger.info(f"Monthly report generated successfully: {filename}")
                
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully",
                    "filename": filename
                })
            else:
                logger.info("Monthly report generated but no file path returned")
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully"
                })
        else:
            error_message = result.get("message", "Monthly report generation failed. Check logs for details.")
            logger.error(error_message)
            return JSONResponse({
                "status": "error",
                "message": error_message
            }, status_code=500)
    except Exception as e:
        error_message = f"Error generating monthly report: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
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
    """
    Serve a log file directly.
    """
    logger.info(f"Request for log file: {filename}")
    
    try:
        # Get the script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logger.info(f"Script directory: {script_dir}")
        
        # Check if this is a monthly report file
        if filename.startswith("monthly_report_") and filename.endswith(".md"):
            # Construct the path to the reports directory
            reports_dir = os.path.join(script_dir, 'output', 'reports')
            file_path = os.path.join(reports_dir, filename)
            
            logger.info(f"Monthly report file path: {file_path}")
            logger.info(f"Reports directory: {reports_dir}")
            logger.info(f"File exists: {os.path.exists(file_path)}")
            
            # Security check to ensure the file is within the reports directory
            if not os.path.abspath(file_path).startswith(os.path.abspath(reports_dir)):
                logger.error(f"Security check failed: {file_path} is not within {reports_dir}")
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Check if the file exists
            if os.path.exists(file_path):
                logger.info(f"Serving monthly report file: {file_path}")
                return FileResponse(file_path, media_type="text/markdown")
            else:
                # Try alternative path resolution
                alt_path = os.path.join(os.getcwd(), 'ai', 'output', 'reports', filename)
                logger.info(f"Trying alternative path: {alt_path}")
                logger.info(f"Alternative path exists: {os.path.exists(alt_path)}")
                
                if os.path.exists(alt_path):
                    logger.info(f"Serving monthly report file from alternative path: {alt_path}")
                    return FileResponse(alt_path, media_type="text/markdown")
                else:
                    logger.error(f"Monthly report file not found: {file_path} or {alt_path}")
                    raise HTTPException(status_code=404, detail=f"Monthly report file not found: {filename}")
        
        # For regular log files, check the logs directory
        file_path = os.path.join(script_dir, 'logs', filename)
        
        # Security check to ensure the file is within the logs directory
        logs_dir = os.path.join(script_dir, 'logs')
        if not os.path.abspath(file_path).startswith(os.path.abspath(logs_dir)):
            logger.error(f"Security check failed: {file_path} is not within {logs_dir}")
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if the file exists
        if os.path.exists(file_path):
            logger.info(f"Serving log file: {file_path}")
            return FileResponse(file_path)
        else:
            logger.error(f"Log file not found: {file_path}")
            raise HTTPException(status_code=404, detail=f"Log file not found: {filename}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving file {filename}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving file: {str(e)}")


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
                
            # Handle SFPublicData collection differently
            if collection_name == 'SFPublicData':
                # Format content from dataset fields
                content_parts = []
                
                title = hit.payload.get('title', '')
                if title:
                    content_parts.append(f"Title: {title}")
                
                description = hit.payload.get('description', '')
                if description:
                    content_parts.append(f"Description: {description}")
                
                url = hit.payload.get('url', '')
                if url:
                    content_parts.append(f"URL: {url}")
                
                endpoint = hit.payload.get('endpoint', '')
                if endpoint:
                    content_parts.append(f"Endpoint: {endpoint}")
                
                category = hit.payload.get('category', '')
                if category:
                    content_parts.append(f"Category: {category}")
                
                publishing_department = hit.payload.get('publishing_department', '')
                if publishing_department:
                    content_parts.append(f"Publishing Department: {publishing_department}")
                
                # Format columns information
                columns = hit.payload.get('columns', {})
                if columns:
                    column_list = []
                    for col_name, col_details in columns.items():
                        col_type = col_details.get('dataTypeName', '')
                        col_info = f"{col_name} ({col_type})"
                        column_list.append(col_info)
                    
                    if column_list:
                        content_parts.append(f"Columns: {', '.join(column_list)}")
                
                # Use endpoint as filename for SFPublicData
                filename = endpoint if endpoint else 'N/A'
                content = '\n'.join(content_parts)
            else:
                # For other collections, use the standard format
                filename = hit.payload.get('filename', 'N/A')
                content = hit.payload.get('content', 'No content')  # Show full content
                
            result = {
                'score': score,
                'relevance': relevance,
                'filename': filename,
                'content': content
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
async def get_selected_columns(endpoint: str, metric_id: str = None):
    """Get currently selected columns for an endpoint and optionally a specific metric ID."""
    try:
        print(f"DEBUG: get_selected_columns called for endpoint {endpoint}, metric_id {metric_id}")
        
        # Load the enhanced queries file - ensure we use the absolute path to avoid path confusion
        script_dir = os.path.dirname(os.path.abspath(__file__))
        enhanced_queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        logger.debug(f"Using dashboard_queries_enhanced.json at path: {enhanced_queries_file}")
        
        if not os.path.exists(enhanced_queries_file):
            logger.warning(f"Enhanced queries file not found at: {enhanced_queries_file}")
            # Try finding it in the parent directory structure
            alternate_path = os.path.join(script_dir, "..", "ai", "data", "dashboard", "dashboard_queries_enhanced.json")
            if os.path.exists(alternate_path):
                logger.info(f"Found enhanced queries file at alternate path: {alternate_path}")
                enhanced_queries_file = alternate_path
            else:
                raise HTTPException(status_code=404, detail="Enhanced queries file not found")
        
        with open(enhanced_queries_file, 'r') as f:
            enhanced_queries = json.load(f)
        
        # Find the metric with this endpoint and (if provided) metric_id
        selected_columns = []
        for category in enhanced_queries.values():
            for subcategory in category.values():
                for metric in subcategory.get("queries", {}).values():
                    current_endpoint = metric.get("endpoint")
                    current_metric_id = metric.get("id")
                    
                    # If metric_id is provided, match on both endpoint and ID
                    # Otherwise, just match on endpoint (for backward compatibility)
                    if metric_id and current_endpoint == endpoint and current_metric_id == int(metric_id):
                        # Get selected columns from the metric's category_fields
                        category_fields = metric.get("category_fields", [])
                        selected_columns = [field["fieldName"] for field in category_fields]
                        logger.info(f"Found selected columns for endpoint {endpoint} and metric_id {metric_id}: {selected_columns}")
                        return JSONResponse({
                            "status": "success",
                            "columns": selected_columns
                        })
                    elif not metric_id and current_endpoint == endpoint:
                        # Get selected columns from the metric's category_fields
                        category_fields = metric.get("category_fields", [])
                        selected_columns = [field["fieldName"] for field in category_fields]
                        logger.debug(f"Found selected columns for endpoint {endpoint}: {selected_columns}")
                        break
        
        logger.info(f"Returning selected columns: {selected_columns}")
        return JSONResponse({
            "status": "success",
            "columns": selected_columns
        })
    except Exception as e:
        logger.exception(f"Error getting selected columns for endpoint '{endpoint}'" + 
                        (f" and metric_id '{metric_id}'" if metric_id else "") + 
                        f": {str(e)}")
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
        
        # Load the enhanced queries file - ensure we use the absolute path to avoid path confusion
        script_dir = os.path.dirname(os.path.abspath(__file__))
        enhanced_queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        logger.info(f"Using dashboard_queries_enhanced.json at path: {enhanced_queries_file}")
        
        if not os.path.exists(enhanced_queries_file):
            logger.error(f"Enhanced queries file not found at: {enhanced_queries_file}")
            # Try finding it in the parent directory structure
            alternate_path = os.path.join(script_dir, "..", "ai", "data", "dashboard", "dashboard_queries_enhanced.json")
            if os.path.exists(alternate_path):
                logger.info(f"Found enhanced queries file at alternate path: {alternate_path}")
                enhanced_queries_file = alternate_path
            else:
                raise HTTPException(status_code=404, detail="Enhanced queries file not found")
        
        with open(enhanced_queries_file, 'r') as f:
            enhanced_queries = json.load(f)
            logger.info(f"Successfully loaded enhanced queries file with {len(enhanced_queries)} categories")
        
        # Find and update the metric with this endpoint and metric_id
        metric_updated = False
        metric_found = False
        
        # Log the endpoint and metric_id we're looking for
        logger.info(f"Looking for endpoint: {endpoint}, metric_id: {metric_id}")
        
        for category_name, category in enhanced_queries.items():
            logger.debug(f"Checking category: {category_name}")
            for subcategory_name, subcategory in category.items():
                logger.debug(f"Checking subcategory: {subcategory_name}")
                for metric_name, metric in subcategory.get("queries", {}).items():
                    metric_endpoint = metric.get("endpoint")
                    current_metric_id = metric.get("id")
                    logger.debug(f"Checking metric: {metric_name} with endpoint: {metric_endpoint}, id: {current_metric_id}")
                    
                    if metric_endpoint == endpoint and current_metric_id == int(metric_id):
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
        try:
            with open(enhanced_queries_file, 'w') as f:
                json.dump(enhanced_queries, f, indent=4)
                logger.info(f"Successfully saved updated enhanced queries to: {enhanced_queries_file}")
        except Exception as write_error:
            logger.error(f"Error writing to file {enhanced_queries_file}: {str(write_error)}")
            # Try to save to a different location if there's a permission issue
            alt_save_path = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced_updated.json")
            logger.info(f"Attempting to save to alternate location: {alt_save_path}")
            with open(alt_save_path, 'w') as f:
                json.dump(enhanced_queries, f, indent=4)
                logger.info(f"Successfully saved to alternate location: {alt_save_path}")
        
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
        
        # Determine which script to run based on period_type
        if period_type == 'ytd':
            # For YTD metrics, use generate_dashboard_metrics.py
            logger.info(f"Running YTD dashboard metrics generation for metric ID {metric_id}")
            
            # Check if we can import the specific metric generation function
            try:
                from generate_dashboard_metrics import process_single_metric
                
                # Check if the function exists and call it
                if callable(process_single_metric):
                    # Call process_single_metric with just the metric_id and period_type
                    result = process_single_metric(metric_id=metric_id, period_type=period_type)
                    logger.info(f"YTD metrics generation completed for metric ID {metric_id}")
                else:
                    # Fall back to the main function if process_single_metric isn't available
                    from generate_dashboard_metrics import main as generate_all_metrics
                    generate_all_metrics()
                    logger.info(f"All YTD metrics generated (including metric ID {metric_id})")
            except (ImportError, AttributeError) as e:
                # If there's no process_single_metric function, run the main function
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
        period_folder = period_folder_map[period_type]
        expected_file = os.path.join(script_dir, 'output', period_folder, str(district_id), f"{metric_id}.json")
        
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
        # Define file paths - ensure we use absolute paths to avoid confusion
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logger.info(f"Script directory: {script_dir}")
        
        # Set up paths for dashboard queries
        queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries.json")
        logger.info(f"Queries file path: {queries_file}")
        
        # Check if the queries file exists, if not try alternative paths
        if not os.path.exists(queries_file):
            logger.warning(f"Queries file not found at: {queries_file}")
            alt_queries_path = os.path.join(script_dir, "..", "ai", "data", "dashboard", "dashboard_queries.json")
            if os.path.exists(alt_queries_path):
                logger.info(f"Found queries file at alternate path: {alt_queries_path}")
                queries_file = alt_queries_path
            else:
                raise FileNotFoundError(f"Dashboard queries file not found at {queries_file} or {alt_queries_path}")
        
        # Set up paths for datasets directory
        datasets_dir = os.path.join(script_dir, "data", "datasets", "fixed")
        logger.info(f"Datasets directory path: {datasets_dir}")
        
        # Check if the datasets directory exists, if not try alternative paths
        if not os.path.exists(datasets_dir):
            logger.warning(f"Datasets directory not found at: {datasets_dir}")
            alt_datasets_dir = os.path.join(script_dir, "..", "ai", "data", "datasets", "fixed")
            if os.path.exists(alt_datasets_dir):
                logger.info(f"Found datasets directory at alternate path: {alt_datasets_dir}")
                datasets_dir = alt_datasets_dir
            else:
                raise FileNotFoundError(f"Datasets directory not found at {datasets_dir} or {alt_datasets_dir}")
        
        # Set up path for output file
        output_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        logger.info(f"Output file path: {output_file}")
        
        # Make sure output directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        # Run the enhancement process
        enhance_dashboard_queries(queries_file, datasets_dir, output_file)
        
        # Verify the output file was created
        if os.path.exists(output_file):
            logger.info(f"Successfully created enhanced queries file at: {output_file}")
            
            # Check file size to ensure it's not empty
            file_size = os.path.getsize(output_file)
            logger.info(f"Enhanced queries file size: {file_size} bytes")
            
            if file_size == 0:
                logger.error("Enhanced queries file is empty!")
                return JSONResponse({
                    "status": "error",
                    "message": "Dashboard queries enhancement completed but produced an empty file"
                }, status_code=500)
        else:
            logger.error(f"Enhanced queries file was not created at: {output_file}")
            return JSONResponse({
                "status": "error",
                "message": f"Failed to create enhanced queries file at: {output_file}"
            }, status_code=500)
        
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


@router.post("/execute-postgres-query")
async def execute_postgres_query(request: Request):
    """Execute a PostgreSQL query and return results."""
    try:
        data = await request.json()
        query = data.get('query', '').strip()
        parameters = data.get('parameters', {})
        
        if not query:
            return JSONResponse({
                'status': 'error',
                'message': 'Query is required'
            })
        
        # Connect to PostgreSQL
        conn = get_db_connection()
        if not conn:
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to connect to database"}
            )
        
        try:
            # Create a cursor with dictionary-like results
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            # Execute the query with parameters
            cursor.execute(query, parameters)
            
            # For non-SELECT queries, commit the transaction
            if not query.strip().upper().startswith('SELECT'):
                conn.commit()
                return JSONResponse({
                    'status': 'success',
                    'message': 'Query executed successfully',
                    'rowCount': cursor.rowcount
                })
            
            # For SELECT queries, fetch and return results
            results = cursor.fetchall()
            
            # Convert results to list of dictionaries and handle datetime serialization
            results_list = []
            for row in results:
                row_dict = dict(row)
                # Convert datetime objects to ISO format strings
                for key, value in row_dict.items():
                    if isinstance(value, (datetime, date)):
                        row_dict[key] = value.isoformat()
                results_list.append(row_dict)
            
            return JSONResponse({
                'status': 'success',
                'rowCount': len(results_list),
                'query': query,
                'results': results_list
            })
            
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        logger.exception(f"Error executing PostgreSQL query: {str(e)}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)

@router.post("/get-biggest-deltas")
async def get_biggest_deltas_api(request: Request):
    """Get metrics with the biggest deltas between time periods."""
    try:
        data = await request.json()
        current_period = data.get('current_period')
        comparison_period = data.get('comparison_period')
        limit = data.get('limit', 10)
        district = data.get('district')
        object_type = data.get('object_type')
        
        # Import function here to avoid circular imports
        from tools.store_time_series import get_biggest_deltas
        
        result = get_biggest_deltas(
            current_period=current_period,
            comparison_period=comparison_period,
            limit=limit,
            district=district,
            object_type=object_type
        )
        
        return JSONResponse(result)
        
    except Exception as e:
        logger.exception(f"Error getting biggest deltas: {str(e)}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)

@router.post("/clear-postgres-data")
async def clear_postgres_data():
    """Clear all data from PostgreSQL database tables."""
    logger.debug("Clear PostgreSQL data called")
    try:
        # Connect to PostgreSQL
        conn = get_db_connection()
        if not conn:
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to connect to database"}
            )
        
        table_count = 0
        
        try:
            # Create a cursor
            cursor = conn.cursor()
            
            # Get list of all tables
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
            tables = [row[0] for row in cursor.fetchall()]
            
            # Truncate each table (clear all data)
            for table in tables:
                try:
                    cursor.execute(f'TRUNCATE TABLE "{table}" CASCADE;')
                    table_count += 1
                    logger.debug(f"Truncated table: {table}")
                except Exception as e:
                    logger.error(f"Error truncating table {table}: {str(e)}")
            
            # Commit the transaction
            conn.commit()
            
        finally:
            cursor.close()
            conn.close()
        
        logger.info(f"Successfully cleared data from {table_count} tables in PostgreSQL database")
        return JSONResponse({
            "status": "success", 
            "message": f"Successfully cleared data from {table_count} tables in PostgreSQL database"
        })
        
    except Exception as e:
        logger.exception(f"Error clearing PostgreSQL data: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })

@router.post("/clear-all-output")
async def clear_all_output():
    """Delete all files from the output directory."""
    logger.debug("Clear all output files called")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'output')
        
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
        
        # Recreate output directory structure
        os.makedirs(os.path.join(output_dir, 'annual'), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'monthly'), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'ytd'), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'weekly'), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'dashboard'), exist_ok=True)
        
        logger.info(f"Successfully deleted {deleted_count} files from output directory")
        return JSONResponse({
            "status": "success", 
            "message": f"Successfully deleted {deleted_count} files from output directory"
        })
        
    except Exception as e:
        logger.exception(f"Error clearing all output files: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })


@router.post("/clear-vector-db")
async def clear_vector_db():
    """Clear all collections from the vector database except SFPublicData."""
    logger.debug("Clear vector database called")
    try:
        # Connect to Qdrant
        qdrant = QdrantClient(host='localhost', port=6333)
        
        # Get all collections
        collections = qdrant.get_collections().collections
        collection_names = [c.name for c in collections]
        
        # Count of deleted collections
        deleted_count = 0
        
        # Delete all collections except SFPublicData
        for name in collection_names:
            if name != "SFPublicData":
                try:
                    qdrant.delete_collection(name)
                    deleted_count += 1
                    logger.debug(f"Deleted collection: {name}")
                except Exception as e:
                    logger.error(f"Error deleting collection {name}: {str(e)}")
        
        logger.info(f"Successfully deleted {deleted_count} collections from vector database")
        return JSONResponse({
            "status": "success", 
            "message": f"Successfully deleted {deleted_count} collections from vector database"
        })
        
    except Exception as e:
        logger.exception(f"Error clearing vector database: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })


@router.get("/get-notes")
async def get_notes_file():
    """
    Serves the combined notes file from the output/notes directory.
    """
    logger.info("Backend get-notes route called")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    notes_file = os.path.join(script_dir, 'output', 'notes', 'combined_notes.txt')
    
    if not os.path.exists(notes_file):
        logger.error("Notes file not found")
        return JSONResponse({
            "success": False, 
            "error": "Notes file not found"
        }, status_code=404)
    
    try:
        with open(notes_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Simple token count approximation
        token_count = len(content.split())
        
        return JSONResponse({
            "success": True,
            "content": content,
            "token_count": token_count
        })
    except Exception as e:
        logger.exception(f"Error reading notes file: {str(e)}")
        return JSONResponse({
            "success": False, 
            "error": f"Error reading notes file: {str(e)}"
        }, status_code=500)

# Import evals functionality
from evals import run_and_get_tool_calls, agent as analyst_agent

@router.get("/run-evals")
async def run_evals_endpoint(query: str):
    """Run evals with the specified query and return results."""
    logger.info(f"Running evals with query: {query}")
    
    try:
        # Run the query through the evals system
        tool_calls = run_and_get_tool_calls(analyst_agent, query)
        
        # Get the log filename that was used
        from evals import log_filename
        
        # Check if the log file exists
        if not os.path.exists(log_filename):
            return JSONResponse({
                "status": "error",
                "message": "Log file not found after running eval"
            }, status_code=500)
        
        # Read the log file content
        with open(log_filename, 'r') as log_file:
            log_content = log_file.read()
        
        return JSONResponse({
            "status": "success",
            "message": f"Eval completed successfully for query: {query}",
            "tool_calls_count": len(tool_calls),
            "log_filename": os.path.basename(log_filename),
            "log_content": log_content
        })
    except Exception as e:
        logger.exception(f"Error running evals: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/evals-interface")
async def evals_interface(request: Request):
    """Serve the evals interface."""
    logger.debug("Evals interface route called")
    if templates is None:
        logger.error("Templates not initialized in backend router")
        raise RuntimeError("Templates not initialized")
    
    return templates.TemplateResponse("evals.html", {
        "request": request
    })

@router.get("/list-eval-logs")
async def list_eval_logs():
    """List all eval log files."""
    logger.debug("List eval logs route called")
    
    try:
        log_folder = 'logs/evals'
        if not os.path.exists(log_folder):
            return JSONResponse({
                "status": "success",
                "files": []
            })
        
        files = []
        for filename in os.listdir(log_folder):
            if filename.endswith('.log'):
                file_path = os.path.join(log_folder, filename)
                files.append({
                    "name": filename,
                    "size": os.path.getsize(file_path),
                    "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                })
        
        # Sort files by modification time, most recent first
        files.sort(key=lambda x: x["modified"], reverse=True)
        
        return JSONResponse({
            "status": "success",
            "files": files
        })
    except Exception as e:
        logger.exception(f"Error listing eval logs: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/eval-log/{filename}")
async def get_eval_log(filename: str):
    """Get the content of an eval log file."""
    logger.debug(f"Get eval log content for {filename}")
    
    try:
        log_folder = 'logs/evals'
        file_path = os.path.join(log_folder, filename)
        
        if not os.path.exists(file_path):
            return JSONResponse({
                "status": "error",
                "message": f"Log file {filename} not found"
            }, status_code=404)
        
        with open(file_path, 'r') as log_file:
            content = log_file.read()
        
        return JSONResponse({
            "status": "success",
            "filename": filename,
            "content": content
        })
    except Exception as e:
        logger.exception(f"Error getting eval log content: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/dashboard")
async def dashboard_page(request: Request):
    """Serve the dashboard page."""
    logger.debug("Dashboard page route called")
    if templates is None:
        logger.error("Templates not initialized in backend router")
        raise RuntimeError("Templates not initialized")
    
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/api/datasets-count")
async def get_datasets_count():
    """Get the count of dataset files in the datasets directory."""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        datasets_dir = os.path.join(current_dir, 'data', 'datasets')
        fixed_datasets_dir = os.path.join(datasets_dir, 'fixed')
        
        # Count JSON files in the main datasets directory (excluding analysis_map.json)
        regular_json_files = [f for f in glob.glob(os.path.join(datasets_dir, '*.json')) 
                             if os.path.basename(f) != 'analysis_map.json']
        
        # Count JSON files in the fixed datasets directory
        fixed_json_files = glob.glob(os.path.join(fixed_datasets_dir, '*.json')) if os.path.exists(fixed_datasets_dir) else []
        
        # Total count
        total_count = len(regular_json_files) + len(fixed_json_files)
        
        # Log the counts for debugging
        logger.info(f"Dataset counts - Regular: {len(regular_json_files)}, Fixed: {len(fixed_json_files)}, Total: {total_count}")
        
        # Determine change from previous week (mock data for now)
        # In a real implementation, this would compare with historical data
        change = 0.0
        
        return JSONResponse(content={
            "count": total_count,
            "change": float(change)
        })
    except Exception as e:
        logger.error(f"Error getting datasets count: {str(e)}")
        # Return default value in case of error
        return JSONResponse(content={
            "count": 0,
            "change": 0.0
        })

@router.get("/api/time-series-count")
async def get_time_series_count():
    """Get the count of rows in the time_series_metadata table."""
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
            
        cursor = conn.cursor()
            
        # Query to count rows in the time_series_metadata table
        cursor.execute("SELECT COUNT(*) FROM time_series_metadata")
            
        result = cursor.fetchone()
        count = int(result[0]) if result else 0
            
        cursor.close()
        conn.close()
            
        # Mock change value for now
        # In a real implementation, this would be calculated from historical data
        change = 0
        
        logger.info(f"Time series metadata count: {count}")
        
        return JSONResponse(content={
            "count": count,
            "change": change
        })
    except Exception as e:
        logger.error(f"Error getting time series metadata count: {str(e)}")
        # Return default value in case of error
        return JSONResponse(content={
            "count": 0,
            "change": 0
        })

@router.get("/api/anomalies-count")
async def get_anomalies_count():
    """Get the count of rows in the anomalies table."""
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
            
        cursor = conn.cursor()
            
        # Query to count rows in the anomalies table
        cursor.execute("SELECT COUNT(*) FROM anomalies")
            
        result = cursor.fetchone()
        count = int(result[0]) if result else 0
            
        cursor.close()
        conn.close()
            
        # Mock change value for now
        # In a real implementation, this would be calculated from historical data
        change = 0
        
        logger.info(f"Anomalies count: {count}")
        
        return JSONResponse(content={
            "count": count,
            "change": change
        })
    except Exception as e:
        logger.error(f"Error getting anomalies count: {str(e)}")
        # Return default value in case of error
        return JSONResponse(content={
            "count": 0,
            "change": 0
        })

@router.get("/api/postgres-size")
async def get_postgres_size():
    """Get the size of the PostgreSQL database in MB."""
    try:
        # Connect to PostgreSQL and get the database size
        # This requires the database connection to be set up
        try:
            conn = psycopg2.connect(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                database=os.environ.get("POSTGRES_DB", "transparentsf"),
                user=os.environ.get("POSTGRES_USER", "postgres"),
                password=os.environ.get("POSTGRES_PASSWORD", "postgres")
            )
            
            cursor = conn.cursor()
            
            # Query to get the database size
            cursor.execute("""
                SELECT pg_database_size(current_database()) / 1024.0 / 1024.0 as size_mb
            """)
            
            result = cursor.fetchone()
            # Convert Decimal to float for JSON serialization
            size_mb = float(result[0]) if result else 0.0
            
            cursor.close()
            conn.close()
            
        except Exception as db_err:
            logger.error(f"Database error: {str(db_err)}")
            # If can't connect to DB, return estimated size
            size_mb = 0.0
        
        # Mock change value for now
        # In a real implementation, this would be calculated from historical data
        change = 0
        
        return JSONResponse(content={
            "size_mb": size_mb,
            "change": change
        })
    except Exception as e:
        logger.error(f"Error getting PostgreSQL size: {str(e)}")
        raise HTTPException(status_code=500, detail="Error getting PostgreSQL size")

@router.get("/api/vectordb-size")
async def get_vectordb_size():
    """Get the size of the Vector DB in MB using metrics API and filesystem fallback."""
    try:
        # Method 1: Try to get size from Qdrant metrics API
        try:
            import requests
            logger.info("Attempting to get Qdrant size from metrics API")
            response = requests.get("http://localhost:6333/metrics", timeout=5)
            
            if response.ok:
                metrics = response.text
                # Look for disk usage metrics in the response
                disk_metrics = {}
                
                # Parse metrics for disk usage
                for line in metrics.splitlines():
                    if line.startswith("qdrant_storage_total_") and not line.startswith("#"):
                        try:
                            parts = line.split()
                            if len(parts) >= 2:
                                key = parts[0].split("{")[0]  # Get metric name without labels
                                value = float(parts[1])
                                disk_metrics[key] = value
                        except Exception as parse_err:
                            logger.warning(f"Error parsing metric line '{line}': {str(parse_err)}")
                
                # Look for total bytes metric
                if "qdrant_storage_total_bytes" in disk_metrics:
                    storage_bytes = disk_metrics["qdrant_storage_total_bytes"]
                    storage_mb = storage_bytes / (1024 * 1024)  # Convert bytes to MB
                    logger.info(f"Got Qdrant size from metrics API: {storage_mb:.2f} MB")
                    return JSONResponse(content={
                        "size_mb": float(storage_mb),
                        "change": 0.0,
                        "source": "metrics_api"
                    })
                else:
                    logger.warning("Storage metrics not found in Qdrant metrics API response")
            else:
                logger.warning(f"Metrics API request failed with status {response.status_code}")
        
        except Exception as metrics_err:
            logger.warning(f"Error getting size from metrics API: {str(metrics_err)}")
        
        # Method 2: Try filesystem measurement
        try:
            # Get Qdrant storage path - default is './storage' in Qdrant's working directory
            # This needs to be adjusted based on your Qdrant configuration
            logger.info("Attempting to measure Qdrant size from filesystem")
            
            # Try to get storage path from environment variable or use default
            qdrant_storage_path = os.environ.get("QDRANT_STORAGE_PATH", "/var/lib/qdrant")
            
            if not os.path.exists(qdrant_storage_path):
                # Try alternate paths
                alternate_paths = [
                    "./storage",  # Default in local development
                    "/var/lib/qdrant/storage",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage")
                ]
                
                for path in alternate_paths:
                    if os.path.exists(path):
                        qdrant_storage_path = path
                        break
            
            if os.path.exists(qdrant_storage_path):
                total_size = 0
                for dirpath, _, filenames in os.walk(qdrant_storage_path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            total_size += os.path.getsize(fp)
                        except (FileNotFoundError, PermissionError) as e:
                            logger.warning(f"Error getting size of {fp}: {str(e)}")
                
                storage_mb = total_size / (1024 * 1024)  # Convert bytes to MB
                logger.info(f"Got Qdrant size from filesystem: {storage_mb:.2f} MB")
                return JSONResponse(content={
                    "size_mb": float(storage_mb),
                    "change": 0.0,
                    "source": "filesystem"
                })
            else:
                logger.warning(f"Qdrant storage path not found at {qdrant_storage_path}")
        except Exception as fs_err:
            logger.warning(f"Error measuring size from filesystem: {str(fs_err)}")
        
        # Method 3: Fallback to collection-based estimation if both methods above fail
        total_points = 0
        collections_count = 0
        
        try:
            # Initialize Qdrant client
            logger.info("Falling back to collection-based size estimation")
            qdrant_client = QdrantClient(os.environ.get("QDRANT_URL", "localhost"), port=6333)
            
            # Get list of collections
            collections_response = qdrant_client.get_collections()
            collections_count = len(collections_response.collections)
            
            # Iterate through each collection to get statistics
            for collection_info in collections_response.collections:
                collection_name = collection_info.name
                
                try:
                    # Get collection info with points count
                    collection_detail = qdrant_client.get_collection(collection_name=collection_name)
                    points_count = collection_detail.points_count
                    
                    if points_count is not None:
                        total_points += points_count
                except Exception as coll_err:
                    logger.error(f"Error getting details for collection {collection_name}: {str(coll_err)}")
                    continue
                    
        except Exception as qdrant_err:
            logger.error(f"Error querying Qdrant: {str(qdrant_err)}")
        
        # Estimate size based on points (more realistic estimate: ~2KB per vector on average)
        estimated_size_mb = (total_points * 2.0) / 1024
        
        # If it's a very small number, set a minimum
        if estimated_size_mb < 0.1 and total_points > 0:
            estimated_size_mb = 0.1
            
        logger.info(f"Estimated size from collections: {estimated_size_mb:.2f} MB")
        return JSONResponse(content={
            "size_mb": float(estimated_size_mb),
            "change": 0.0,
            "source": "estimate"
        })
    except Exception as e:
        logger.error(f"Error getting Vector DB size: {str(e)}")
        # Return a default value in case of error
        return JSONResponse(content={
            "size_mb": 0.0,
            "change": 0.0,
            "source": "error"
        })

@router.get("/api/system-status")
async def get_system_status():
    """Get system status for various components."""
    try:
        status_items = []
        
        # Check PostgreSQL status
        postgres_status = "error"
        postgres_value = "Offline"
        try:
            conn = psycopg2.connect(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                database=os.environ.get("POSTGRES_DB", "transparentsf"),
                user=os.environ.get("POSTGRES_USER", "postgres"),
                password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
                connect_timeout=3
            )
            cursor = conn.cursor()
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            
            postgres_status = "healthy"
            postgres_value = "Online"
        except Exception as db_err:
            logger.error(f"PostgreSQL status check failed: {str(db_err)}")
        
        status_items.append({
            "name": "PostgreSQL Database",
            "status": postgres_status,
            "value": postgres_value
        })
        
        # Check Vector DB (Qdrant) status
        vectordb_status = "error"
        vectordb_value = "Offline"
        try:
            qdrant_client = QdrantClient(os.environ.get("QDRANT_URL", "localhost"), port=6333)
            collections = qdrant_client.get_collections()
            vectordb_status = "healthy"
            vectordb_value = f"{len(collections.collections)} collections"
        except Exception as vdb_err:
            logger.error(f"Vector DB status check failed: {str(vdb_err)}")
        
        status_items.append({
            "name": "Vector Database",
            "status": vectordb_status,
            "value": vectordb_value
        })
        
        # Check disk space
        disk_status = "healthy"
        disk_value = ""
        try:
            total, used, free = shutil.disk_usage("/")
            percent_used = (used / total) * 100
            
            if percent_used > 90:
                disk_status = "error"
            elif percent_used > 70:
                disk_status = "warning"
            
            disk_value = f"{round(percent_used, 1)}% used"
        except Exception as disk_err:
            logger.error(f"Disk status check failed: {str(disk_err)}")
            disk_status = "error"
            disk_value = "Unknown"
        
        status_items.append({
            "name": "Disk Space",
            "status": disk_status,
            "value": disk_value
        })
        
        # Add server uptime
        uptime_status = "healthy"
        uptime_value = ""
        try:
            if os.name == 'posix':  # Linux or MacOS
                try:
                    # Try using uptime command (works on both Linux and MacOS)
                    uptime_output = subprocess.check_output(['uptime']).decode('utf-8')
                    # Extract the uptime information
                    if 'day' in uptime_output:
                        # Format with days
                        days_part = uptime_output.split('up ')[1].split(' day')[0].strip()
                        days = int(days_part)
                        uptime_value = f"{days} days"
                    else:
                        # Less than a day
                        uptime_value = "Less than a day"
                except:
                    # Fallback for Linux only
                    if os.path.exists('/proc/uptime'):
                        with open('/proc/uptime', 'r') as f:
                            uptime_seconds = float(f.readline().split()[0])
                            uptime_days = uptime_seconds / 86400  # Convert to days
                            uptime_value = f"{round(uptime_days, 1)} days"
                    else:
                        uptime_value = "Unknown"
            else:
                # Default value for non-posix systems
                uptime_value = "Unknown"
        except Exception as uptime_err:
            logger.error(f"Uptime check failed: {str(uptime_err)}")
            uptime_status = "warning"
            uptime_value = "Unknown"
        
        status_items.append({
            "name": "Server Uptime",
            "status": uptime_status,
            "value": uptime_value
        })
        
        return JSONResponse(content=status_items)
    except Exception as e:
        logger.error(f"Error getting system status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error getting system status")

@router.get("/api/time-series-data-count")
async def get_time_series_data_count():
    """
    Returns the count of time series data points in the system.
    Used by the dashboard to display time series data metrics.
    """
    try:
        # Connect to the database
        conn = None
        try:
            conn = psycopg2.connect(
                dbname=os.environ.get("POSTGRES_DB", "transparentsf"),
                user=os.environ.get("POSTGRES_USER", "postgres"),
                password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=os.environ.get("POSTGRES_PORT", "5432")
            )
            
            cursor = conn.cursor()
            
            # Query to count time series data points
            query = """
                SELECT COUNT(*) 
                FROM time_series_data;
            """
            
            cursor.execute(query)
            result = cursor.fetchone()
            count = result[0] if result else 0
            
            # Get change from yesterday (last 24 hours)
            change_query = """
                SELECT COUNT(*) 
                FROM time_series_data 
                WHERE created_at >= NOW() - INTERVAL '24 hours';
            """
            
            cursor.execute(change_query)
            change_result = cursor.fetchone()
            change = change_result[0] if change_result else 0
            
            return {
                "count": count,
                "change": change,
                "change_period": "24 hours"
            }
            
        except Exception as e:
            logger.error(f"Database error when counting time series data: {str(e)}")
            return {"count": "DB Error", "change": 0}
        finally:
            if conn:
                conn.close()
                
    except Exception as e:
        logger.exception(f"Error getting time series data count: {str(e)}")
        return {"count": "Error", "change": 0}

@router.get("/api/chart/{chart_id}")
@router.get("/backend/api/chart/{chart_id}")  # Add an extra route
async def get_chart_data(chart_id: int):
    """
    Get chart data for a specific chart_id.
    Accessible via both /api/chart/{chart_id} and /backend/api/chart/{chart_id}
    
    Returns:
        ChartResponse with metadata and data points
    """
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Query to get chart metadata
        cursor.execute("""
            SELECT 
                chart_id, 
                chart_title, 
                y_axis_label, 
                period_type, 
                object_type, 
                object_id, 
                object_name, 
                field_name, 
                district, 
                group_field
            FROM time_series_metadata 
            WHERE chart_id = %s
        """, (chart_id,))
        
        metadata_result = cursor.fetchone()
        
        if not metadata_result:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail=f"Chart with ID {chart_id} not found")
        
        # Query to get chart data points
        cursor.execute("""
            SELECT 
                time_period, 
                group_value, 
                numeric_value
            FROM time_series_data 
            WHERE chart_id = %s
            ORDER BY time_period
        """, (chart_id,))
        
        data_results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Map database period type to frontend period type for the response
        frontend_period_type_map = {
            'year': 'annual',
            'month': 'monthly',
            'week': 'weekly',
            'day': 'daily'
        }
        
        # Format the response according to the requested structure
        response = {
            "metadata": {
                "chart_id": metadata_result["chart_id"],
                "chart_title": metadata_result["chart_title"],
                "y_axis_label": metadata_result["y_axis_label"],
                "period_type": frontend_period_type_map.get(metadata_result["period_type"], metadata_result["period_type"]),
                "object_type": metadata_result["object_type"],
                "object_id": metadata_result["object_id"],
                "object_name": metadata_result["object_name"],
                "field_name": metadata_result["field_name"],
                "district": metadata_result["district"]
            }
        }
        
        # Add group_field if it exists
        if metadata_result["group_field"]:
            response["metadata"]["group_field"] = metadata_result["group_field"]
        
        # Format the data points
        response["data"] = []
        for row in data_results:
            data_point = {
                "time_period": row["time_period"].isoformat(),
                "numeric_value": float(row["numeric_value"])
            }
            
            # Add group_value if it exists
            if row["group_value"]:
                data_point["group_value"] = row["group_value"]
                
            response["data"].append(data_point)
        
        logger.info(f"Retrieved chart data for chart_id: {chart_id}")
        return JSONResponse(content=response)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chart data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving chart data: {str(e)}")

@router.get("/api/chart-by-metric")
@router.get("/backend/api/chart-by-metric")  # Add an extra route
async def get_chart_by_metric(
    metric_id: str,
    district: int = 0,
    period_type: str = 'year',
    group_field: str = None,
    groups: str = None
):
    """
    Get chart data for a specific metric, district, and period type.
    Accessible via both /api/chart-by-metric and /backend/api/chart-by-metric
    
    Parameters:
        metric_id: The ID of the metric (matches object_id in the database)
        district: The district ID (default: 0)
        period_type: The period type (default: 'year')
        group_field: Optional group field (default: null)
        groups: Comma-separated list of group values to include (default: null)
    
    Returns:
        ChartResponse with metadata and data points
    """
    try:
        # Map frontend period types to database period types if needed
        period_type_map = {
            'annual': 'year',
            'monthly': 'month',
            'weekly': 'week',
            'daily': 'day'
        }
        
        # Convert period type if it's one of the frontend values
        db_period_type = period_type_map.get(period_type, period_type)
        
        # Parse groups parameter if provided
        group_values = None
        if groups:
            group_values = [g.strip() for g in groups.split(',')]
            logger.info(f"Using specified group values: {group_values}")
        
        logger.info(f"Looking for chart with object_id={metric_id}, district={district}, period_type={db_period_type}, group_field={group_field}, groups={groups}")
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # DEBUGGING: Check for available metrics
        cursor.execute("SELECT DISTINCT object_id FROM time_series_metadata")
        available_metrics = [row[0] for row in cursor.fetchall()]
        logger.info(f"Available metrics in database: {available_metrics}")
        
        # DEBUGGING: Check for available period types
        cursor.execute("SELECT DISTINCT period_type FROM time_series_metadata")
        available_period_types = [row[0] for row in cursor.fetchall()]
        logger.info(f"Available period types in database: {available_period_types}")
        
        # DEBUGGING: Check if specific metric exists
        cursor.execute("SELECT COUNT(*) FROM time_series_metadata WHERE object_id = %s", (metric_id,))
        metric_count = cursor.fetchone()[0]
        logger.info(f"Found {metric_count} entries with object_id={metric_id}")
        
        # Build the query with proper handling of group_field NULL vs value
        if group_field is None:
            group_field_condition = "AND group_field IS NULL"
            params = (metric_id, district, db_period_type)
        else:
            group_field_condition = "AND group_field = %s"
            params = (metric_id, district, db_period_type, group_field)
        
        # Special handling for 'month' period type - try both 'month' directly and with LOWER comparison
        if db_period_type == 'month':
            query = f"""
                SELECT 
                    chart_id, 
                    chart_title, 
                    y_axis_label, 
                    period_type, 
                    object_type, 
                    object_id, 
                    object_name, 
                    field_name, 
                    district, 
                    group_field,
                    executed_query_url,
                    caption,
                    filter_conditions
                FROM time_series_metadata 
                WHERE object_id = %s AND district = %s 
                AND (period_type = %s OR LOWER(period_type) = LOWER(%s))
                {group_field_condition}
                ORDER BY created_at DESC
                LIMIT 1
            """
            
            if group_field is None:
                cursor.execute(query, (metric_id, district, db_period_type, db_period_type))
            else:
                cursor.execute(query, (metric_id, district, db_period_type, db_period_type, group_field))
        else:
            # Original query for other period types
            query = f"""
                SELECT 
                    chart_id, 
                    chart_title, 
                    y_axis_label, 
                    period_type, 
                    object_type, 
                    object_id, 
                    object_name, 
                    field_name, 
                    district, 
                    group_field,
                    executed_query_url,
                    caption,
                    filter_conditions
                FROM time_series_metadata 
                WHERE object_id = %s AND district = %s AND period_type = %s
                {group_field_condition}
                ORDER BY created_at DESC
                LIMIT 1
            """
            
            if group_field is None:
                cursor.execute(query, (metric_id, district, db_period_type))
            else:
                cursor.execute(query, (metric_id, district, db_period_type, group_field))
        
        metadata_result = cursor.fetchone()
        
        if not metadata_result:
            # Fallback 1: Try without group_field constraint
            logger.info(f"No chart found with group_field constraint, trying without group_field constraint")
            cursor.execute("""
                SELECT 
                    chart_id, 
                    chart_title, 
                    y_axis_label, 
                    period_type, 
                    object_type, 
                    object_id, 
                    object_name, 
                    field_name, 
                    district, 
                    group_field,
                    executed_query_url,
                    caption,
                    filter_conditions
                FROM time_series_metadata 
                WHERE object_id = %s AND district = %s AND period_type = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (metric_id, district, db_period_type))
            
            metadata_result = cursor.fetchone()
            
            if not metadata_result:
                # Fallback 2: Try without period_type constraint
                logger.info(f"No chart found with period_type constraint, trying without period_type constraint")
                cursor.execute("""
                    SELECT 
                        chart_id, 
                        chart_title, 
                        y_axis_label, 
                        period_type, 
                        object_type, 
                        object_id, 
                        object_name, 
                        field_name, 
                        district, 
                        group_field,
                        executed_query_url,
                        caption,
                        filter_conditions
                    FROM time_series_metadata 
                    WHERE object_id = %s AND district = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (metric_id, district))
                
                metadata_result = cursor.fetchone()
                
                if not metadata_result:
                    # Fallback 3: Try with just the metric_id
                    logger.info(f"No chart found with district constraint, trying with just object_id")
                    cursor.execute("""
                        SELECT 
                            chart_id, 
                            chart_title, 
                            y_axis_label, 
                            period_type, 
                            object_type, 
                            object_id, 
                            object_name, 
                            field_name, 
                            district, 
                            group_field,
                            executed_query_url,
                            caption,
                            filter_conditions
                        FROM time_series_metadata 
                        WHERE object_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (metric_id,))
                    
                    metadata_result = cursor.fetchone()
                    
                    if not metadata_result:
                        # DEBUGGING: Get a sample of what's actually in the database
                        cursor.execute("""
                            SELECT 
                                chart_id, 
                                chart_title, 
                                object_id, 
                                field_name, 
                                period_type, 
                                district,
                                group_field
                            FROM time_series_metadata 
                            LIMIT 5
                        """)
                        
                        sample_data = cursor.fetchall()
                        logger.info(f"Sample data from time_series_metadata: {[dict(row) for row in sample_data]}")
                        
                        cursor.close()
                        conn.close()
                        
                        # Provide more detailed error response
                        return JSONResponse(
                            status_code=404,
                            content={
                                "detail": f"Chart not found for object_id: {metric_id}, district: {district}, period_type: {period_type}, group_field: {group_field}",
                                "available_metrics": available_metrics,
                                "available_period_types": available_period_types,
                                "sample_data": [dict(row) for row in sample_data] if sample_data else []
                            }
                        )
                    else:
                        logger.info(f"Found chart with object_id only: {metadata_result['object_id']}, period_type: {metadata_result['period_type']}, district: {metadata_result['district']}")
                else:
                    logger.info(f"Found chart with different period_type: {metadata_result['period_type']}")
        
        chart_id = metadata_result["chart_id"]
        
        # Query to get chart data points
        if group_field:
            if group_values:
                # Create placeholders for the IN clause
                placeholders = ', '.join(['%s'] * len(group_values))
                
                # Query for specific group values
                query = f"""
                    SELECT 
                        time_period, 
                        group_value, 
                        numeric_value
                    FROM time_series_data 
                    WHERE chart_id = %s
                    AND group_value IN ({placeholders})
                    ORDER BY time_period, group_value
                """
                
                # Combine parameters: chart_id + group_values
                params = [chart_id] + group_values
                cursor.execute(query, params)
            else:
                # When group_field is provided but no specific groups,
                # get all data with non-null group_value
                cursor.execute("""
                    SELECT 
                        time_period, 
                        group_value, 
                        numeric_value
                    FROM time_series_data 
                    WHERE chart_id = %s
                    AND group_value IS NOT NULL
                    ORDER BY time_period, group_value
                """, (chart_id,))
        else:
            # Original query without group filtering
            cursor.execute("""
                SELECT 
                    time_period, 
                    group_value, 
                    numeric_value
                FROM time_series_data 
                WHERE chart_id = %s
                ORDER BY time_period
            """, (chart_id,))
        
        data_results = cursor.fetchall()
        
        # DEBUGGING: Check data count
        logger.info(f"Found {len(data_results)} data points for chart_id {chart_id}")
        
        # If no data found, try debugging
        if not data_results:
            cursor.execute("SELECT COUNT(*) FROM time_series_data")
            total_data = cursor.fetchone()[0]
            logger.info(f"Total data points in time_series_data: {total_data}")
        
        cursor.close()
        conn.close()
        
        # Map database period type back to frontend period type for the response
        frontend_period_type_map = {
            'year': 'annual',
            'month': 'monthly',
            'week': 'weekly',
            'day': 'daily'
        }
        
        # Format the response according to the requested structure
        response = {
            "metadata": {
                "chart_id": metadata_result["chart_id"],
                "chart_title": metadata_result["chart_title"],
                "y_axis_label": metadata_result["y_axis_label"],
                "period_type": frontend_period_type_map.get(metadata_result["period_type"], metadata_result["period_type"]),
                "object_type": metadata_result["object_type"],
                "object_id": metadata_result["object_id"],
                "object_name": metadata_result["object_name"],
                "field_name": metadata_result["field_name"],
                "district": metadata_result["district"],
                "executed_query_url": metadata_result["executed_query_url"],
                "caption": metadata_result["caption"],
                "filter_conditions": metadata_result["filter_conditions"]
            }
        }
        
        # Add group_field if it exists
        if metadata_result["group_field"]:
            response["metadata"]["group_field"] = metadata_result["group_field"]
        
        # Format the data points
        response["data"] = []
        for row in data_results:
            data_point = {
                "time_period": row["time_period"].isoformat(),
                "numeric_value": float(row["numeric_value"])
            }
            
            # Add group_value if it exists
            if row["group_value"]:
                data_point["group_value"] = row["group_value"]
                
            response["data"].append(data_point)
        
        logger.info(f"Retrieved chart data for object_id: {metric_id}, district: {district}, period_type: {period_type}, group_field: {group_field}")
        return JSONResponse(content=response)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chart data by metric: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving chart data: {str(e)}")

@router.get("/monthly-reports")
async def monthly_reports_page():
    """Serve the monthly reports interface page."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(current_dir, "templates", "monthly_reports.html")
    return FileResponse(template_path)

@router.get("/get_monthly_reports")
async def get_monthly_reports():
    """Get a list of all monthly reports."""
    logger.debug("Get monthly reports called")
    try:
        # Import the necessary function
        from monthly_report import get_monthly_reports_list
        
        # Get the list of reports
        reports = get_monthly_reports_list()
        
        return JSONResponse({
            "status": "success",
            "reports": reports
        })
    except Exception as e:
        error_message = f"Error getting monthly reports: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.delete("/delete_monthly_report/{report_id}")
async def delete_monthly_report(report_id: int):
    """Delete a specific monthly report."""
    logger.debug(f"Delete monthly report {report_id} called")
    try:
        # Import the necessary function
        from monthly_report import delete_monthly_report
        
        # Delete the report
        result = delete_monthly_report(report_id)
        
        if result.get("status") == "success":
            return JSONResponse({
                "status": "success",
                "message": "Report deleted successfully"
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to delete report")
            }, status_code=500)
    except Exception as e:
        error_message = f"Error deleting monthly report: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/generate_monthly_report")
async def generate_monthly_report_get():
    """Generate monthly report on demand (GET method for backward compatibility)."""
    logger.debug("Generate monthly report (GET) called")
    try:
        # Import the necessary function
        from monthly_report import run_monthly_report_process
        
        # Run the monthly report process
        logger.info("Running monthly report process")
        result = run_monthly_report_process()
        
        if result.get("status") == "success":
            # After generating the report, redirect to the report path
            report_path = result.get("revised_report_path") or result.get("report_path")
            
            if report_path:
                # Extract the filename from the path
                filename = os.path.basename(report_path)
                logger.info(f"Monthly report generated successfully: {filename}")
                
                # Redirect to the report file
                return RedirectResponse(url=f"/logs/{filename}", status_code=302)
            else:
                logger.info("Monthly report generated but no file path returned")
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully"
                })
        else:
            error_message = result.get("message", "Monthly report generation failed. Check logs for details.")
            logger.error(error_message)
            return JSONResponse({
                "status": "error",
                "message": error_message
            }, status_code=500)
    except ImportError as e:
        error_message = f"Could not import monthly report function: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.post("/generate_monthly_report")
async def generate_monthly_report_post(request: Request):
    """Generate monthly report with custom parameters."""
    logger.debug("Generate monthly report (POST) called")
    try:
        # Get parameters from request body
        body = await request.json()
        district = body.get("district", "0")
        period_type = body.get("period_type", "month")
        max_report_items = body.get("max_report_items", 10)
        
        logger.info(f"Generating monthly report with district={district}, period_type={period_type}, max_items={max_report_items}")
        
        # Import the necessary function
        from monthly_report import run_monthly_report_process
        import asyncio
        
        # Run the monthly report process in a separate thread to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_monthly_report_process(
                district=district,
                period_type=period_type,
                max_report_items=max_report_items
            )
        )
        
        if result.get("status") == "success":
            # Return the report path
            report_path = result.get("revised_report_path") or result.get("newsletter_path")
            
            if report_path:
                # Extract the filename from the path
                filename = os.path.basename(report_path)
                logger.info(f"Monthly report generated successfully: {filename}")
                
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully",
                    "filename": filename
                })
            else:
                logger.info("Monthly report generated but no file path returned")
                return JSONResponse({
                    "status": "success",
                    "message": "Monthly report generated successfully"
                })
        else:
            error_message = result.get("message", "Monthly report generation failed. Check logs for details.")
            logger.error(error_message)
            return JSONResponse({
                "status": "error",
                "message": error_message
            }, status_code=500)
    except Exception as e:
        error_message = f"Error generating monthly report: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)


@router.get("/api/total-metrics-count")
async def get_total_metrics_count():
    """Get the total count of metrics from dashboard_queries_enhanced.json."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        queries_file = os.path.join(script_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
        
        if not os.path.exists(queries_file):
            logger.error(f"dashboard_queries_enhanced.json not found at {queries_file}")
            return JSONResponse({
                "count": "Error",
                "change": 0
            })
        
        with open(queries_file, 'r') as f:
            queries_data = json.load(f)
        
        # Count total metrics across all categories and subcategories
        total_metrics = 0
        for category in queries_data.values():
            for subcategory in category.values():
                if "queries" in subcategory:
                    total_metrics += len(subcategory["queries"])
        
        return JSONResponse({
            "count": total_metrics,
            "change": 0
        })
        
    except Exception as e:
        logger.exception(f"Error getting total metrics count: {str(e)}")
        return JSONResponse({
            "count": "Error",
            "change": 0
        }, status_code=500)

@router.get("/api/anomalies-count-by-status")
async def get_anomalies_count_by_status():
    """Get the count of anomalies grouped by out_of_bounds status."""
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
            
        cursor = conn.cursor()
            
        # Query to count rows in the anomalies table grouped by out_of_bounds
        cursor.execute("""
            SELECT out_of_bounds, COUNT(*) as count 
            FROM anomalies 
            GROUP BY out_of_bounds
        """)
            
        results = cursor.fetchall()
        
        # Initialize counts
        out_of_bounds_count = 0
        in_bounds_count = 0
        
        # Process results
        for row in results:
            if row[0]:  # out_of_bounds is True
                out_of_bounds_count = int(row[1])
            else:  # out_of_bounds is False
                in_bounds_count = int(row[1])
            
        cursor.close()
        conn.close()
            
        logger.info(f"Anomalies count by status - Out of bounds: {out_of_bounds_count}, In bounds: {in_bounds_count}")
        
        return JSONResponse(content={
            "out_of_bounds": out_of_bounds_count,
            "in_bounds": in_bounds_count,
            "total": out_of_bounds_count + in_bounds_count
        })
    except Exception as e:
        logger.error(f"Error getting anomalies count by status: {str(e)}")
        # Return default values in case of error
        return JSONResponse(content={
            "out_of_bounds": 0,
            "in_bounds": 0,
            "total": 0
        })

@router.get("/monthly-report/{report_id}")
async def get_monthly_report_by_id(report_id: int):
    """
    Get a monthly report by its ID from the database.
    """
    try:
        logger.info(f"Requesting monthly report by ID: {report_id}")
        
        # Connect to database
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get the report details
        cur.execute("""
            SELECT id, district, period_type, max_items, created_at, updated_at, 
                   original_filename, revised_filename
            FROM reports
            WHERE id = %s
        """, (report_id,))
        
        report = cur.fetchone()
        
        if not report:
            logger.error(f"Monthly report not found with ID: {report_id}")
            return JSONResponse(
                status_code=404,
                content={"detail": f"Monthly report not found with ID: {report_id}"}
            )
        
        # Get the metrics for this report
        cur.execute("""
            SELECT *
            FROM monthly_reporting
            WHERE report_id = %s
            ORDER BY priority
        """, (report_id,))
        
        metrics = cur.fetchall()
        
        # Format the report data
        report_data = {
            "id": report["id"],
            "district": report["district"],
            "district_name": f"District {report['district']}" if report["district"] != "0" else "Citywide",
            "period_type": report["period_type"],
            "max_items": report["max_items"],
            "created_at": report["created_at"].isoformat() if report["created_at"] else None,
            "updated_at": report["updated_at"].isoformat() if report["updated_at"] else None,
            "original_filename": report["original_filename"],
            "revised_filename": report["revised_filename"],
            "metrics": []
        }
        
        # Format the metrics data
        for metric in metrics:
            report_data["metrics"].append({
                "id": metric["id"],
                "report_id": metric["report_id"],
                "item_title": metric["item_title"],
                "metric_name": metric["metric_name"],
                "group_value": metric["group_value"],
                "group_field_name": metric["group_field_name"],
                "period_type": metric["period_type"],
                "comparison_mean": metric["comparison_mean"],
                "recent_mean": metric["recent_mean"],
                "difference": metric["difference"],
                "std_dev": metric["std_dev"],
                "percent_change": metric["percent_change"],
                "explanation": metric["explanation"],
                "priority": metric["priority"],
                "report_text": metric["report_text"],
                "district": metric["district"],
                "chart_data": metric["chart_data"],
                "metadata": metric["metadata"],
                "created_at": metric["created_at"].isoformat() if metric["created_at"] else None
            })
        
        cur.close()
        conn.close()
        
        return JSONResponse(content={
            "status": "success",
            "report": report_data
        })
    except Exception as e:
        logger.error(f"Error getting monthly report by ID: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error getting monthly report by ID: {str(e)}"}
        )

@router.get("/time-series-chart")
async def time_series_chart_page(request: Request):
    """
    Serve the time series chart page.
    
    Query Parameters:
        metric_id: ID of the metric to display
        district: District ID (default: 0 for citywide)
        period_type: Period type (year, month, etc.)
        groups: Comma-separated list of group values to include (optional)
    """
    return templates.TemplateResponse("time_series_chart.html", {"request": request})

@router.get("/monthly-report/file/{filename}")
async def get_monthly_report_file(filename: str):
    """
    Serve a monthly report file directly by filename.
    """
    try:
        logger.info(f"Requesting monthly report file: {filename}")
        
        # Connect to database
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get the report details
        cur.execute("""
            SELECT id, district, period_type, max_items, created_at, updated_at, 
                   original_filename, revised_filename
            FROM reports
            WHERE original_filename = %s OR revised_filename = %s
        """, (filename, filename))
        
        report = cur.fetchone()
        
        if not report:
            logger.error(f"Monthly report not found with filename: {filename}")
            return JSONResponse(
                status_code=404,
                content={"detail": f"Monthly report not found with filename: {filename}"}
            )
        
        # Get the report ID
        report_id = report['id']
        
        # Get the metrics for this report
        cur.execute("""
            SELECT *
            FROM monthly_reporting
            WHERE report_id = %s
            ORDER BY priority ASC
        """, (report_id,))
        
        metrics = cur.fetchall()
        
        if not metrics:
            logger.error(f"No metrics found for report ID: {report_id}")
            return JSONResponse(
                status_code=404,
                content={"detail": f"No metrics found for report ID: {report_id}"}
            )
        
        # Format the report data
        report_data = {
            "id": report_id,
            "district": report['district'],
            "district_name": "Citywide" if report['district'] == "0" else f"District {report['district']}",
            "period_type": report['period_type'],
            "max_items": report['max_items'],
            "created_at": report['created_at'].isoformat(),
            "updated_at": report['updated_at'].isoformat(),
            "metrics": []
        }
        
        for metric in metrics:
            report_data["metrics"].append({
                "id": metric['id'],
                "metric_name": metric['metric_name'],
                "group_value": metric['group_value'],
                "group_field_name": metric['group_field_name'],
                "period_type": metric['period_type'],
                "comparison_mean": metric['comparison_mean'],
                "recent_mean": metric['recent_mean'],
                "difference": metric['difference'],
                "std_dev": metric['std_dev'],
                "percent_change": metric['percent_change'],
                "explanation": metric['explanation'],
                "priority": metric['priority'],
                "report_text": metric['report_text'],
                "chart_data": metric['chart_data'],
                "metadata": metric['metadata']
            })
        
        # Close database connection
        cur.close()
        conn.close()
        
        # Check if the file exists in the output directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(current_dir, "output")
        reports_dir = os.path.join(output_dir, "reports")
        file_path = os.path.join(reports_dir, filename)
        
        if os.path.exists(file_path):
            # Return the file directly
            return FileResponse(file_path)
        else:
            # If the file doesn't exist, generate it from the database data
            # This would require implementing a function to generate the HTML from the database data
            logger.error(f"Monthly report file not found at: {file_path}")
            return JSONResponse(
                status_code=404,
                content={"detail": f"Monthly report file not found at: {file_path}"}
            )
            
    except Exception as e:
        error_message = f"Error getting monthly report file: {str(e)}"
        logger.error(error_message, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": error_message}
        )

@router.post("/rerun_monthly_report_generation")
async def rerun_monthly_report_generation(request: Request):
    """
    Re-run the monthly report generation process
    
    This endpoint allows users to manually trigger the monthly report generation process
    """
    try:
        from monthly_report import run_monthly_report_process, generate_monthly_report
        import asyncio
        
        # Get request data
        data = await request.json()
        district = data.get("district", "0")
        period_type = data.get("period_type", "month")
        max_report_items = data.get("max_report_items", 10)
        only_generate = data.get("only_generate", False)
        
        logger.info(f"Re-running monthly report generation for district {district}, period_type {period_type}, only_generate={only_generate}")
        
        # Run the monthly report process in a separate thread to prevent blocking
        loop = asyncio.get_event_loop()
        
        if only_generate:
            # Only run the generate_monthly_report function
            result = await loop.run_in_executor(
                None,
                lambda: generate_monthly_report(district=district)
            )
        else:
            # Run the full monthly report process
            result = await loop.run_in_executor(
                None,
                lambda: run_monthly_report_process(
                    district=district,
                    period_type=period_type,
                    max_report_items=max_report_items
                )
            )
        
        if result.get("status") == "success":
            return JSONResponse(
                content={
                    "status": "success",
                    "message": "Monthly report generation completed successfully",
                    "report_path": result.get("revised_report_path") or result.get("newsletter_path")
                }
            )
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": result.get("message", "Unknown error occurred during monthly report generation")
                }
            )
            
    except Exception as e:
        logger.error(f"Error re-running monthly report generation: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error re-running monthly report generation: {str(e)}"
            }
        )

@router.post("/rerun_monthly_report_proofreading")
async def rerun_monthly_report_proofreading(request: Request):
    """
    Re-run the proofreading process for a monthly report
    
    This endpoint allows users to manually trigger the proofreading process for a specific report
    """
    try:
        from monthly_report import proofread_and_revise_report
        import asyncio
        
        # Get request data
        data = await request.json()
        report_path = data.get("report_path")
        
        if not report_path:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Missing report_path parameter"
                }
            )
        
        logger.info(f"Re-running proofreading for report at {report_path}")
        
        # Run the proofreading process in a separate thread to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: proofread_and_revise_report(report_path)
        )
        
        if result.get("status") == "success":
            return JSONResponse(
                content={
                    "status": "success",
                    "message": "Proofreading completed successfully",
                    "revised_report_path": result.get("revised_report_path")
                }
            )
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": result.get("message", "Unknown error occurred during proofreading")
                }
            )
            
    except Exception as e:
        logger.error(f"Error re-running proofreading: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error re-running proofreading: {str(e)}"
            }
        )

@router.post("/update-chart-groups/{chart_id}")
async def update_chart_groups(chart_id: int, request: Request):
    """
    Update the group values for a specific chart.
    This is useful when the chart data exists but doesn't have group values set.
    It fetches the actual data from the source endpoint and adds group values to the time_series_data rows.
    """
    try:
        data = await request.json()
        group_field = data.get("group_field")
        source_query_modification = data.get("source_query_modification", "")
        
        if not group_field:
            return JSONResponse(
                status_code=400,
                content={"detail": "group_field parameter is required"}
            )
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            database=os.environ.get("POSTGRES_DB", "transparentsf"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres")
        )
        
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # First, update the metadata to include the group_field
        cursor.execute("""
            UPDATE time_series_metadata
            SET group_field = %s
            WHERE chart_id = %s
            RETURNING *
        """, (group_field, chart_id))
        
        metadata_result = cursor.fetchone()
        
        if not metadata_result:
            conn.rollback()
            cursor.close()
            conn.close()
            return JSONResponse(
                status_code=404,
                content={"detail": f"Chart with ID {chart_id} not found"}
            )
        
        # Get the source query URL from the metadata
        executed_query_url = metadata_result["executed_query_url"]
        
        if not executed_query_url:
            conn.rollback()
            cursor.close()
            conn.close()
            return JSONResponse(
                status_code=400,
                content={"detail": "No source query URL found in metadata"}
            )
        
        # Modify the query if needed to include the group_field
        if source_query_modification:
            executed_query_url = executed_query_url.replace(
                "GROUP+BY+month_period", 
                f"GROUP+BY+month_period%2C+{group_field}"
            )
        
        # Fetch data from the source URL
        import requests
        import urllib.parse
        
        # Decode the URL for better readability of errors
        decoded_url = urllib.parse.unquote(executed_query_url)
        logger.info(f"Fetching data from: {decoded_url}")
        
        try:
            response = requests.get(executed_query_url)
            response.raise_for_status()
            source_data = response.json()
            
            logger.info(f"Fetched {len(source_data)} rows from source data")
            
            # Create a mapping of time periods to group values and numeric values
            # For example: {'2023-01-31': {'Group A': 10, 'Group B': 20}, '2023-02-28': {...}}
            grouped_data = {}
            
            for row in source_data:
                # Parse the time period from the data
                # Note: This might need adjustment based on the actual data format
                time_period_str = row.get("month_period", "")
                group_value = row.get(group_field, "")
                
                if not time_period_str or not group_value:
                    continue
                
                # Convert the time period string to a date object
                from datetime import datetime
                try:
                    time_period = datetime.strptime(time_period_str, "%Y-%m-%dT%H:%M:%S.%f")
                    # Format to match the format in the database (YYYY-MM-DD)
                    time_period_formatted = time_period.strftime("%Y-%m-%d")
                except ValueError:
                    try:
                        # Try another format if the first one fails
                        time_period = datetime.strptime(time_period_str, "%Y-%m-%d")
                        time_period_formatted = time_period_str
                    except ValueError:
                        logger.warning(f"Could not parse time period: {time_period_str}")
                        continue
                
                # Get the numeric value (might be called "value" or something else)
                numeric_value = float(row.get("value", 0))
                
                # Initialize the time period entry if it doesn't exist
                if time_period_formatted not in grouped_data:
                    grouped_data[time_period_formatted] = {}
                
                # Add the group value and numeric value
                if group_value in grouped_data[time_period_formatted]:
                    # If the group already exists for this time period, add to the value
                    grouped_data[time_period_formatted][group_value] += numeric_value
                else:
                    # Otherwise, initialize it
                    grouped_data[time_period_formatted][group_value] = numeric_value
            
            # Now, update or insert the data in the time_series_data table
            updated_count = 0
            inserted_count = 0
            
            for time_period, groups in grouped_data.items():
                for group_value, numeric_value in groups.items():
                    # Check if a row already exists for this combination
                    cursor.execute("""
                        SELECT id
                        FROM time_series_data
                        WHERE chart_id = %s AND time_period = %s AND group_value = %s
                    """, (chart_id, time_period, group_value))
                    
                    existing_row = cursor.fetchone()
                    
                    if existing_row:
                        # Update existing row
                        cursor.execute("""
                            UPDATE time_series_data
                            SET numeric_value = %s
                            WHERE id = %s
                        """, (numeric_value, existing_row["id"]))
                        updated_count += 1
                    else:
                        # Insert new row
                        cursor.execute("""
                            INSERT INTO time_series_data
                            (chart_id, time_period, group_value, numeric_value)
                            VALUES (%s, %s, %s, %s)
                        """, (chart_id, time_period, group_value, numeric_value))
                        inserted_count += 1
            
            # Commit the transaction
            conn.commit()
            
            logger.info(f"Updated {updated_count} rows and inserted {inserted_count} rows")
            
            return JSONResponse({
                "status": "success",
                "message": f"Updated chart groups successfully. Updated {updated_count} rows and inserted {inserted_count} rows.",
                "details": {
                    "chart_id": chart_id,
                    "group_field": group_field,
                    "updated_count": updated_count,
                    "inserted_count": inserted_count,
                    "total_time_periods": len(grouped_data),
                    "total_groups": sum(len(groups) for groups in grouped_data.values())
                }
            })
            
        except requests.RequestException as e:
            conn.rollback()
            logger.error(f"Error fetching data from source URL: {e}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Error fetching data from source URL: {str(e)}", "url": decoded_url}
            )
            
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Database error: {str(e)}"}
        )
    except Exception as e:
        logger.error(f"Error updating chart groups: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error updating chart groups: {str(e)}"}
        )
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()
