import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

# Router and templating pattern per repo rules
router = APIRouter()
templates = None

logger = logging.getLogger(__name__)

def set_templates(t):
    global templates
    templates = t
    logger.info("Templates set in settings router")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if templates is None:
        raise RuntimeError("Templates not initialized")
    return templates.TemplateResponse("settings.html", {"request": request})

