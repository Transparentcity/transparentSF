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
        "https://*.replit.app"
    ],  # Only allow specific origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
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
    
    file_path = os.path.join(output_dir, filename)
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