from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from generate_dashboard_metrics import main as generate_metrics
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the absolute path to the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))
logger.debug(f"Current directory: {current_dir}")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://transparentsf.com",
        "https://*.transparentsf.com",
        "https://*.replit.app",
        "http://localhost:8081",
        "https://*.replit.dev",
        "http://c8f21de5-50d5-4932-9f30-db3b51e8af74-00-3cldcjjo20ql9.riker.replit.dev",
        "https://0ea7c615-87bb-43af-8dc3-9d1816811571-00-3jirn7oguyp0q.picard.replit.dev/",
    ],  # Only allow specific origins
    allow_credentials=True,  # Required for cookies
    allow_methods=["*"],    # Allows all methods
    allow_headers=["*"],    # Allows all headers
    expose_headers=["Set-Cookie"],  # Expose Set-Cookie header
    max_age=3600,          # Cache preflight requests for 1 hour
)

# Initialize templates with absolute path
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
logger.debug(f"Templates directory: {os.path.join(current_dir, 'templates')}")

# Mount static files
static_dir = os.path.join(current_dir, "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
logger.debug(f"Static directory: {static_dir}")

# Mount output directory
output_dir = os.path.join(current_dir, "output")
if not os.path.exists(output_dir):
    os.makedirs(output_dir)
app.mount("/output", StaticFiles(directory=output_dir), name="output")
logger.debug(f"Output directory: {output_dir}")

# Mount logs directory
logs_dir = os.path.join(current_dir, "logs")
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)
app.mount("/logs", StaticFiles(directory=logs_dir), name="logs")
logger.debug(f"Logs directory: {logs_dir}")

# Define metrics data directory
metrics_dir = os.path.join(current_dir, "data/dashboard")
logger.debug(f"Metrics directory: {metrics_dir}")

@app.get("/api/metrics/{filename}")
async def get_metrics(filename: str):
    """Serve metrics data from JSON files."""
    # Ensure filename ends with .json
    if not filename.endswith('.json'):
        filename = f"{filename}.json"
    
    # Check if this is a district file request (district_X.json)
    if filename.startswith('district_') and '_' in filename:
        # Extract district number
        district_str = filename.split('_')[1].split('.')[0]
        
        # For backward compatibility, check if the file exists in the old location
        old_file_path = os.path.join(output_dir, 'dashboard', filename)
        if os.path.exists(old_file_path):
            logger.debug(f"Serving metrics from old location: {old_file_path}")
            try:
                with open(old_file_path, 'r') as f:
                    data = json.load(f)
                    return JSONResponse(content=data)
            except Exception as e:
                logger.error(f"Error reading metrics file {old_file_path}: {str(e)}")
                # Fall through to try the new location
        
        # Try to serve top_level.json from the district subfolder
        new_file_path = os.path.join(output_dir, 'dashboard', district_str, 'top_level.json')
        logger.debug(f"Attempting to read metrics from new location: {new_file_path}")
        try:
            with open(new_file_path, 'r') as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except FileNotFoundError:
            logger.error(f"Metrics file not found in new location: {new_file_path}")
            raise HTTPException(status_code=404, detail=f"Metrics file '{filename}' not found")
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in metrics file: {new_file_path}")
            raise HTTPException(status_code=500, detail=f"Invalid JSON in metrics file '{filename}'")
        except Exception as e:
            logger.error(f"Error reading metrics file {new_file_path}: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")
    
    # Check if this is a metric ID request (metric_id.json)
    # Format could be something like "crime_incidents_ytd.json"
    elif '_' in filename and not filename.startswith('district_'):
        # This is likely a metric file request
        # We need to determine which district it belongs to
        # First, try to find it in each district folder
        for district_num in range(12):  # 0-11 for citywide and districts 1-11
            district_str = str(district_num)
            metric_file_path = os.path.join(output_dir, 'dashboard', district_str, filename)
            if os.path.exists(metric_file_path):
                logger.debug(f"Found metric file in district {district_str}: {metric_file_path}")
                try:
                    with open(metric_file_path, 'r') as f:
                        data = json.load(f)
                        return JSONResponse(content=data)
                except Exception as e:
                    logger.error(f"Error reading metric file {metric_file_path}: {str(e)}")
                    # Continue to try other districts
        
        # If we get here, the metric file wasn't found in any district folder
        logger.error(f"Metric file not found in any district folder: {filename}")
        raise HTTPException(status_code=404, detail=f"Metric file '{filename}' not found")
    
    # For any other files, try the old location
    else:
        file_path = os.path.join(output_dir, 'dashboard', filename)
        logger.debug(f"Attempting to read metrics from: {file_path}")
        
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                return JSONResponse(content=data)
        except FileNotFoundError:
            logger.error(f"Metrics file not found: {file_path}")
            raise HTTPException(status_code=404, detail=f"Metrics file '{filename}' not found")
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in metrics file: {file_path}")
            raise HTTPException(status_code=500, detail=f"Invalid JSON in metrics file '{filename}'")
        except Exception as e:
            logger.error(f"Error reading metrics file {file_path}: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/enhanced-queries")
async def get_enhanced_queries():
    """Serve the enhanced dashboard queries file."""
    file_path = os.path.join(current_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
    logger.debug(f"Attempting to read enhanced queries from: {file_path}")
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            return JSONResponse(content=data)
    except FileNotFoundError:
        logger.error(f"Enhanced queries file not found: {file_path}")
        raise HTTPException(status_code=404, detail="Enhanced queries file not found")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in enhanced queries file: {file_path}")
        raise HTTPException(status_code=500, detail="Invalid JSON in enhanced queries file")
    except Exception as e:
        logger.error(f"Error reading enhanced queries file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/metric-id-mapping")
async def get_metric_id_mapping():
    """Serve the metric ID mapping file."""
    file_path = os.path.join(current_dir, "data", "dashboard", "metric_id_mapping.json")
    logger.debug(f"Attempting to read metric ID mapping from: {file_path}")
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            return JSONResponse(content=data)
    except FileNotFoundError:
        logger.error(f"Metric ID mapping file not found: {file_path}")
        raise HTTPException(status_code=404, detail="Metric ID mapping file not found")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in metric ID mapping file: {file_path}")
        raise HTTPException(status_code=500, detail="Invalid JSON in metric ID mapping file")
    except Exception as e:
        logger.error(f"Error reading metric ID mapping file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/metric/{metric_id}")
async def get_metric_by_id(metric_id: int):
    """Get metric details by its numeric ID."""
    # First, load the metric ID mapping
    mapping_file = os.path.join(current_dir, "data", "dashboard", "metric_id_mapping.json")
    enhanced_queries_file = os.path.join(current_dir, "data", "dashboard", "dashboard_queries_enhanced.json")
    
    try:
        # Load the mapping file
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
        
        # Check if the metric ID exists in the mapping
        metric_id_str = str(metric_id)
        if metric_id_str not in mapping:
            logger.error(f"Metric ID {metric_id} not found in mapping")
            raise HTTPException(status_code=404, detail=f"Metric ID {metric_id} not found")
        
        # Get the metric details from the mapping
        metric_info = mapping[metric_id_str]
        category = metric_info["category"]
        subcategory = metric_info["subcategory"]
        metric_name = metric_info["name"]
        
        # Load the enhanced queries file
        with open(enhanced_queries_file, 'r') as f:
            queries = json.load(f)
        
        # Get the metric details from the enhanced queries
        if category in queries and subcategory in queries[category] and "queries" in queries[category][subcategory] and metric_name in queries[category][subcategory]["queries"]:
            metric_data = queries[category][subcategory]["queries"][metric_name]
            return JSONResponse(content=metric_data)
        else:
            logger.error(f"Metric {metric_name} not found in enhanced queries")
            raise HTTPException(status_code=404, detail=f"Metric {metric_name} not found in enhanced queries")
    
    except FileNotFoundError as e:
        logger.error(f"File not found: {str(e)}")
        raise HTTPException(status_code=404, detail="Required files not found")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {str(e)}")
        raise HTTPException(status_code=500, detail="Invalid JSON in required files")
    except Exception as e:
        logger.error(f"Error getting metric by ID: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/district/{district_id}/metric/{metric_id}")
async def get_district_metric(district_id: str, metric_id: str):
    """Serve a specific metric for a specific district."""
    # Ensure metric_id ends with .json
    if not metric_id.endswith('.json'):
        metric_id = f"{metric_id}.json"
    
    # Construct the file path
    file_path = os.path.join(output_dir, 'dashboard', district_id, metric_id)
    logger.debug(f"Attempting to read district metric from: {file_path}")
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            return JSONResponse(content=data)
    except FileNotFoundError:
        logger.error(f"District metric file not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"Metric '{metric_id}' for district '{district_id}' not found")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in district metric file: {file_path}")
        raise HTTPException(status_code=500, detail=f"Invalid JSON in metric file '{metric_id}' for district '{district_id}'")
    except Exception as e:
        logger.error(f"Error reading district metric file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/district/{district_id}")
async def get_district_top_level(district_id: str):
    """Serve the top-level metrics for a specific district."""
    # Construct the file path
    file_path = os.path.join(output_dir, 'dashboard', district_id, 'top_level.json')
    logger.debug(f"Attempting to read district top-level metrics from: {file_path}")
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            return JSONResponse(content=data)
    except FileNotFoundError:
        logger.error(f"District top-level file not found: {file_path}")
        raise HTTPException(status_code=404, detail=f"Top-level metrics for district '{district_id}' not found")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in district top-level file: {file_path}")
        raise HTTPException(status_code=500, detail=f"Invalid JSON in top-level file for district '{district_id}'")
    except Exception as e:
        logger.error(f"Error reading district top-level file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/district/{district_id}/metrics")
async def list_district_metrics(district_id: str):
    """List all available metrics for a specific district."""
    # Construct the directory path
    dir_path = os.path.join(output_dir, 'dashboard', district_id)
    logger.debug(f"Attempting to list metrics in directory: {dir_path}")
    
    try:
        if not os.path.exists(dir_path):
            logger.error(f"District directory not found: {dir_path}")
            raise HTTPException(status_code=404, detail=f"District '{district_id}' not found")
        
        # Get all JSON files in the directory except top_level.json
        metric_files = [f for f in os.listdir(dir_path) if f.endswith('.json') and f != 'top_level.json']
        
        # Extract metric IDs (remove .json extension)
        metric_ids = [f[:-5] for f in metric_files]
        
        # Return the list of metric IDs
        return JSONResponse(content={"metrics": metric_ids})
    except Exception as e:
        logger.error(f"Error listing district metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/districts")
async def list_districts():
    """List all available districts."""
    # Construct the directory path
    dir_path = os.path.join(output_dir, 'dashboard')
    logger.debug(f"Attempting to list districts in directory: {dir_path}")
    
    try:
        if not os.path.exists(dir_path):
            logger.error(f"Dashboard directory not found: {dir_path}")
            raise HTTPException(status_code=404, detail="Dashboard directory not found")
        
        # Get all subdirectories that are numeric (district IDs)
        district_dirs = [d for d in os.listdir(dir_path) if os.path.isdir(os.path.join(dir_path, d)) and d.isdigit()]
        
        # Sort district IDs numerically
        district_ids = sorted(district_dirs, key=int)
        
        # Return the list of district IDs
        return JSONResponse(content={"districts": district_ids})
    except Exception as e:
        logger.error(f"Error listing districts: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Root route to serve index.html
@app.get("/")
async def root(request: Request):
    """Serve the main chat interface."""
    logger.debug("Serving index.html template")
    return templates.TemplateResponse("index.html", {"request": request})

# Import routers
from webChat import router as chat_router
from backend import router as backend_router, set_templates

# Set templates for backend router
set_templates(templates)
logger.debug("Set templates for backend router")

# Include routers
app.include_router(chat_router, prefix="/chat")  # Chat router at /chat
logger.debug("Included chat router at /chat")

# Mount backend router without trailing slash
app.include_router(backend_router, prefix="/backend", tags=["backend"])
logger.debug("Included backend router at /backend")

async def schedule_metrics_generation():
    """Schedule metrics generation to run daily at 5 AM and 11 AM."""
    while True:
        try:
            # Calculate time until next run (either 5 AM or 11 AM)
            now = datetime.now()
            target_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
            target_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
            
            # If we're past 11 AM, set targets to next day
            if now.hour >= 11:
                target_5am += timedelta(days=1)
                target_11am += timedelta(days=1)
            # If we're past 5 AM but before 11 AM, only adjust the 5 AM target
            elif now.hour >= 5:
                target_5am += timedelta(days=1)
            
            # Find the next closest target time
            next_run = min(target_5am, target_11am)
            
            # Wait until the next scheduled time
            wait_seconds = (next_run - now).total_seconds()
            logger.info(f"Next metrics generation scheduled for {next_run} (in {wait_seconds/3600:.2f} hours)")
            await asyncio.sleep(wait_seconds)
            
            # Generate metrics
            logger.info("Starting scheduled metrics generation...")
            try:
                generate_metrics()
                logger.info("Metrics generation completed successfully")
            except Exception as e:
                logger.error(f"Error during metrics generation: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error in metrics generation scheduler: {str(e)}")
            await asyncio.sleep(300)  # Wait 5 minutes before retrying if there's an error

@app.on_event("startup")
async def startup_event():
    # Existing startup code
    routes = []
    for route in app.routes:
        if hasattr(route, "methods"):
            routes.append(f"{route.path} [{route.methods}]")
        elif hasattr(route, "path"):
            routes.append(f"{route.path} [Mount]")
    
    logger.debug("Registered routes:")
    for route in routes:
        logger.debug(f"  {route}")
        
    # Start the metrics generation scheduler
    asyncio.create_task(schedule_metrics_generation())
    logger.info("Started metrics generation scheduler")

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting server...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 