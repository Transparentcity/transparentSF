from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import os
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Templates will be provided by main.py
templates = None

def set_templates(t):
    global templates
    templates = t

@router.get("/writeups", response_class=HTMLResponse)
async def writeups_page(request: Request):
    if templates is None:
        # Fallback to direct file response if templates not set
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(current_dir, "templates", "writeups.html")
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except Exception as e:
            logger.error(f"Error loading writeups.html: {e}")
            return HTMLResponse(content="Template not available", status_code=500)

    return templates.TemplateResponse("writeups.html", {"request": request})

