from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the absolute path to the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))
logger.debug(f"Current directory: {current_dir}")

app = FastAPI()

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

# Debug: Print all registered routes
@app.on_event("startup")
async def startup_event():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods"):
            # Regular routes
            routes.append(f"{route.path} [{route.methods}]")
        elif hasattr(route, "path"):
            # Mount routes
            routes.append(f"{route.path} [Mount]")
    
    logger.debug("Registered routes:")
    for route in routes:
        logger.debug(f"  {route}")

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting server...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 