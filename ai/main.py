from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from generate_dashboard_metrics import main as generate_metrics
from generate_weekly_analysis import run_weekly_analysis, generate_weekly_newsletter
from dotenv import load_dotenv
import re
import glob

# Import routers
from webChat import router as webchat_router
from backend import router as backend_router, set_templates, get_chart_by_metric, get_chart_data
from anomalyAnalyzer import router as anomaly_analyzer_router, set_templates as set_anomaly_templates

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

# Define a custom route for monthly files BEFORE mounting the static directory
@app.get("/output/monthly/{district}/{metric_id}.md", response_class=HTMLResponse)
async def get_monthly_file(district: str, metric_id: str):
    """
    Serve monthly analysis files only. No fallback to annual files.
    """
    # Try to get the monthly file only
    monthly_path = os.path.join(output_dir, "monthly", district, f"{metric_id}.md")
    if os.path.exists(monthly_path):
        logger.debug(f"Serving monthly file from {monthly_path}")
        with open(monthly_path, 'r') as f:
            return f.read()

    # If monthly doesn't exist, return a 404 error
    logger.error(f"Monthly file not found for district {district}, metric {metric_id}")
    raise HTTPException(status_code=404, detail=f"Monthly analysis file not found for metric {metric_id}")

# Now mount the static directory after defining our custom route
app.mount("/output", StaticFiles(directory=output_dir), name="output")
logger.debug(f"Output directory: {output_dir}")

# Define OUTPUT_DIR constant for compatibility with other modules
OUTPUT_DIR = output_dir

# Mount weekly output directory
weekly_dir = os.path.join(output_dir, "weekly")
if not os.path.exists(weekly_dir):
    os.makedirs(weekly_dir)
app.mount("/weekly", StaticFiles(directory=weekly_dir), name="weekly")
logger.debug(f"Weekly output directory: {weekly_dir}")

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
    try:
        districts = []
        dashboard_dir = os.path.join(output_dir, 'dashboard')
        
        # Look for numbered directories (0-11) representing districts
        for item in os.listdir(dashboard_dir):
            item_path = os.path.join(dashboard_dir, item)
            # Check if it's a directory and represents a district number
            if os.path.isdir(item_path) and item.isdigit() and 0 <= int(item) <= 11:
                district_id = item
                # Load the district's top-level data for its name
                top_level_path = os.path.join(item_path, 'top_level.json')
                try:
                    with open(top_level_path, 'r') as f:
                        top_level_data = json.load(f)
                        district_name = top_level_data.get('name', f"District {district_id}")
                        districts.append({
                            'id': district_id,
                            'name': district_name
                        })
                except:
                    # If we can't read top_level.json, just use the district number
                    districts.append({
                        'id': district_id,
                        'name': f"District {district_id}"
                    })
        
        # Sort districts by ID, ensuring numeric sorting (district 0 is first, then 1-11)
        districts.sort(key=lambda d: int(d['id']))
        return JSONResponse(content=districts)
    except Exception as e:
        logger.error(f"Error listing districts: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/weekly-analysis")
async def trigger_weekly_analysis(metrics: str = None, include_districts: bool = False):
    """
    Trigger weekly analysis on demand.
    
    Parameters:
    - metrics: Optional comma-separated list of metric IDs to analyze
    - include_districts: Whether to include district-level analysis
    
    Returns:
    - Analysis results summary
    """
    try:
        logger.info(f"Manually triggered weekly analysis with metrics={metrics}, include_districts={include_districts}")
        
        # Parse metrics list if provided
        metrics_list = metrics.split(",") if metrics else None
        
        # Run the analysis
        results = run_weekly_analysis(
            metrics_list=metrics_list,
            process_districts=include_districts
        )
        
        # Generate a newsletter
        newsletter_path = generate_weekly_newsletter(results)
        
        # Prepare the response
        analyzed_metrics = []
        for result in results:
            analyzed_metrics.append({
                'metric_id': result.get('metric_id', ''),
                'query_name': result.get('query_name', ''),
                'file_path': os.path.relpath(result.get('file_path', ''), OUTPUT_DIR) if result.get('file_path') else None
            })
        
        response = {
            'success': True,
            'analyzed_metrics': analyzed_metrics,
            'newsletter': os.path.relpath(newsletter_path, OUTPUT_DIR) if newsletter_path else None,
            'timestamp': datetime.now().isoformat()
        }
        
        return JSONResponse(content=response)
    except Exception as e:
        logger.error(f"Error running weekly analysis: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                'success': False,
                'error': str(e)
            }
        )

@app.get("/weekly-report")
async def view_weekly_report(request: Request):
    """Serve the weekly analysis report."""
    try:
        # Find the most recent newsletter file
        newsletter_files = []
        for file in os.listdir(weekly_dir):
            if file.startswith("weekly_newsletter_") and file.endswith(".md"):
                file_path = os.path.join(weekly_dir, file)
                newsletter_files.append({
                    'path': file_path,
                    'modified': os.path.getmtime(file_path),
                    'filename': file
                })
        
        if not newsletter_files:
            return templates.TemplateResponse("weekly.html", {
                "request": request,
                "newsletter_content": "No weekly reports found. Generate one using the API.",
                "metrics": [],
                "start_date": "",
                "end_date": ""
            })
        
        # Sort by modified time (newest first)
        newsletter_files.sort(key=lambda x: x['modified'], reverse=True)
        latest_newsletter = newsletter_files[0]
        
        # Read the newsletter content
        with open(latest_newsletter['path'], 'r') as f:
            newsletter_content = f.read()
        
        # Extract date from filename (format: weekly_newsletter_YYYY-MM-DD.md)
        report_date_str = latest_newsletter['filename'].replace("weekly_newsletter_", "").replace(".md", "")
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d")
        
        # Calculate the start and end dates of the report period
        end_date = report_date - timedelta(days=1)  # Report is generated for period ending yesterday
        start_date = end_date - timedelta(days=6)  # 7-day period
        
        # Find all analysis files from the same time period
        metrics = []
        for file in os.listdir(weekly_dir):
            if file.endswith(".md") and not file.startswith("weekly_newsletter_"):
                # Extract the date from filename (format: metric_id_YYYY-MM-DD.md)
                parts = file.split("_")
                if len(parts) >= 2 and parts[-1].endswith(".md"):
                    file_date_str = parts[-1].replace(".md", "")
                    try:
                        file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                        # If the file was created on the same day as the newsletter
                        if file_date.date() == report_date.date():
                            metric_id = "_".join(parts[:-1])
                            
                            # Try to get metric details from the file
                            file_path = os.path.join(weekly_dir, file)
                            with open(file_path, 'r') as f:
                                file_content = f.read()
                                # Extract title from markdown (# Title)
                                title_match = re.search(r'^# (.*?)$', file_content, re.MULTILINE)
                                query_name = title_match.group(1) if title_match else metric_id
                                
                                # Extract summary if available
                                summary_match = re.search(r'\*\*Analysis Type:\*\* Weekly\s*\n\s*\n\*\*Period:\*\* (.*?)\s*\n\s*\n\*\*Filters:\*\* (.*?)$', 
                                                        file_content, re.MULTILINE)
                                summary = summary_match.group(0) if summary_match else ""
                            
                            metrics.append({
                                'metric_id': metric_id,
                                'query_name': query_name,
                                'file_path': os.path.relpath(file_path, weekly_dir),
                                'summary': summary
                            })
                    except:
                        # Skip files with invalid date format
                        pass
        
        # Render the template with the data
        return templates.TemplateResponse("weekly.html", {
            "request": request,
            "newsletter_content": newsletter_content,
            "metrics": metrics,
            "start_date": start_date.strftime("%B %d, %Y"),
            "end_date": end_date.strftime("%B %d, %Y")
        })
        
    except Exception as e:
        logger.error(f"Error serving weekly report: {str(e)}")
        return templates.TemplateResponse("weekly.html", {
            "request": request,
            "newsletter_content": f"Error loading weekly report: {str(e)}",
            "metrics": [],
            "start_date": "",
            "end_date": ""
        })

# Root route to serve index.html
@app.get("/")
async def root(request: Request):
    """Serve the main chat interface."""
    logger.debug("Serving index.html template")
    return templates.TemplateResponse("index.html", {"request": request})

# Set templates for backend router
set_templates(templates)
logger.debug("Set templates for backend router")

# Set templates for anomaly analyzer router
set_anomaly_templates(templates)
logger.debug("Set templates for anomaly analyzer router")

# Include routers
app.include_router(webchat_router, prefix="/chat")  # Chat router at /chat
logger.debug("Included chat router at /chat")

# Mount backend router without trailing slash
app.include_router(backend_router, prefix="/backend", tags=["backend"])
logger.debug("Included backend router at /backend")

# Mount anomaly analyzer router
app.include_router(anomaly_analyzer_router, prefix="/anomaly-analyzer", tags=["anomaly-analyzer"])
logger.debug("Included anomaly analyzer router at /anomaly-analyzer")

# Add redirect for anomaly analyzer without trailing slash
@app.get("/anomaly-analyzer")
async def redirect_to_anomaly_analyzer():
    return RedirectResponse(url="/anomaly-analyzer/")

@app.get("/api/chart-by-metric")
async def forward_chart_by_metric(
    metric_id: str,
    district: int = 0,
    period_type: str = 'year'
):
    """
    Forward requests from /api/chart-by-metric to /backend/api/chart-by-metric
    """
    return await get_chart_by_metric(metric_id, district, period_type)

@app.get("/api/chart/{chart_id}")
async def forward_chart_data(chart_id: int):
    """
    Forward requests from /api/chart/{chart_id} to /backend/api/chart/{chart_id}
    """
    return await get_chart_data(chart_id)

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

async def schedule_weekly_analysis():
    """Schedule weekly analysis to run every Thursday at 11 AM."""
    while True:
        try:
            # Calculate time until next Thursday at 11 AM
            now = datetime.now()
            days_until_thursday = (3 - now.weekday()) % 7  # Thursday is weekday 3
            
            # If it's Thursday and already past 11 AM, schedule for next week
            if days_until_thursday == 0 and now.hour >= 11:
                days_until_thursday = 7
                
            target_run = now.replace(hour=11, minute=0, second=0, microsecond=0)
            target_run += timedelta(days=days_until_thursday)
            
            # Wait until the scheduled time
            wait_seconds = (target_run - now).total_seconds()
            logger.info(f"Next weekly analysis scheduled for {target_run} (in {wait_seconds/3600:.2f} hours)")
            await asyncio.sleep(wait_seconds)
            
            # Run weekly analysis
            logger.info("Starting scheduled weekly analysis...")
            try:
                # Run analysis for default metrics with district processing
                results = run_weekly_analysis(process_districts=True)
                
                # Generate the newsletter
                newsletter_path = generate_weekly_newsletter(results)
                
                if newsletter_path:
                    logger.info(f"Weekly analysis completed successfully, newsletter saved to {newsletter_path}")
                else:
                    logger.warning("Weekly analysis completed but no newsletter was generated")
            except Exception as e:
                logger.error(f"Error during weekly analysis: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error in weekly analysis scheduler: {str(e)}")
            await asyncio.sleep(300)  # Wait 5 minutes before retrying if there's an error

async def cleanup_logs():
    """Trim log files to keep only the last 7 days of logs. Runs daily."""
    while True:
        try:
            # Schedule to run daily at 11:30 AM
            now = datetime.now()
            
            # Calculate time until tomorrow at 11:30 AM
            target_run = now.replace(hour=11, minute=30, second=0, microsecond=0)
            if now.hour > 11 or (now.hour == 11 and now.minute >= 30):
                target_run += timedelta(days=1)  # If it's already past 11:30 AM, schedule for tomorrow
            
            # Wait until the scheduled time
            wait_seconds = (target_run - now).total_seconds()
            logger.info(f"Next log cleanup scheduled for {target_run} (in {wait_seconds/3600:.2f} hours)")
            await asyncio.sleep(wait_seconds)
            
            # Trim log files
            logger.info("Starting scheduled log cleanup...")
            try:
                # Get all log files
                log_files = glob.glob(os.path.join(logs_dir, "*.log"))
                trimmed_count = 0
                
                # Calculate the cutoff date (7 days ago)
                cutoff_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                
                for log_file in log_files:
                    try:
                        # Create a temporary file to store recent logs
                        temp_file = log_file + ".temp"
                        with open(log_file, 'r') as original, open(temp_file, 'w') as temp:
                            # For each line, check if it's newer than the cutoff date
                            kept_lines = 0
                            for line in original:
                                # Try to extract the date from the line (assuming standard log format)
                                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                                if date_match and date_match.group(1) >= cutoff_date:
                                    # Keep lines newer than or equal to the cutoff date
                                    temp.write(line)
                                    kept_lines += 1
                            
                        # If we kept any lines or the original file is empty, replace the original with the temp file
                        if kept_lines > 0 or os.path.getsize(log_file) == 0:
                            os.replace(temp_file, log_file)
                            trimmed_count += 1
                            logger.debug(f"Trimmed log file: {log_file}, kept {kept_lines} lines")
                        else:
                            # If we didn't keep any lines, don't replace the file (it might be a special case)
                            os.remove(temp_file)
                            logger.debug(f"No recent logs found in {log_file}, file not modified")
                            
                    except Exception as e:
                        logger.error(f"Error trimming log file {log_file}: {str(e)}")
                        # Clean up temp file if it exists
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                
                logger.info(f"Log cleanup completed. Trimmed {trimmed_count} log files to keep entries from the last 7 days.")
            except Exception as e:
                logger.error(f"Error during log cleanup: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error in log cleanup scheduler: {str(e)}")
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
    
    # Start the weekly analysis scheduler
    asyncio.create_task(schedule_weekly_analysis())
    logger.info("Started weekly analysis scheduler")
    
    # Start the log cleanup scheduler
    asyncio.create_task(cleanup_logs())
    logger.info("Started log cleanup scheduler")

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting server...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 