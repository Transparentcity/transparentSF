from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import logging
import os
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import sqlite3
from pathlib import Path

# Create router
router = APIRouter(prefix="/api/writeups", tags=["writeups"])

# Set up logging
logger = logging.getLogger(__name__)

# Global templates variable
templates = None

def set_templates(template_instance: Jinja2Templates):
    """Set the templates instance for this router."""
    global templates
    templates = template_instance
    logging.info("Templates set in writeups router")

@router.get("", response_class=HTMLResponse)
async def writeups_page(request: Request):
    """Serve the write-ups page."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not initialized")
    
    return templates.TemplateResponse("writeups.html", {"request": request})

@router.post("/create")
async def create_writeup(request: Request):
    """Create a new write-up request."""
    logger.debug("Create write-up called")
    try:
        body = await request.json()
        
        title = body.get("title", "").strip()
        original_prompt = body.get("prompt", "").strip()
        output_format = body.get("output_format", "html")
        output_destination = body.get("output_destination", "")
        frequency = body.get("frequency", "one_time")
        scheduled_for = body.get("scheduled_for")
        model_key = body.get("model_key")
        
        if not title or not original_prompt:
            return JSONResponse({
                "status": "error",
                "message": "Title and prompt are required"
            }, status_code=400)
        
        # Import the write-ups manager
        from tools.writeups_manager import WriteupsManager
        
        # Create the write-up
        writeup_manager = WriteupsManager()
        result = writeup_manager.create_writeup(
            title=title,
            original_prompt=original_prompt,
            output_format=output_format,
            output_destination=output_destination,
            frequency=frequency,
            scheduled_for=scheduled_for,
            model_key=model_key
        )
        
        if result.get("status") == "success":
            return JSONResponse({
                "status": "success",
                "message": result.get("message", "Write-up created and executed successfully"),
                "writeup_id": result.get("writeup_id"),
                "content": result.get("content", "")
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to create write-up")
            }, status_code=500)
            
    except Exception as e:
        error_message = f"Error creating write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/list")
async def get_writeups():
    """Get a list of all write-ups."""
    logger.debug("Get write-ups called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        writeups = writeup_manager.get_all_writeups()
        
        return JSONResponse({
            "status": "success",
            "writeups": writeups
        })
    except Exception as e:
        error_message = f"Error getting write-ups: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/{writeup_id}")
async def get_writeup(writeup_id: int):
    """Get details of a specific write-up."""
    logger.debug(f"Get write-up {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        writeup = writeup_manager.get_writeup(writeup_id)
        
        if not writeup:
            return JSONResponse({
                "status": "error",
                "message": "Write-up not found"
            }, status_code=404)
        
        return JSONResponse({
            "status": "success",
            "writeup": writeup
        })
    except Exception as e:
        error_message = f"Error getting write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)


@router.post("/{writeup_id}/execute")
async def execute_writeup(writeup_id: int):
    """Execute a write-up directly."""
    logger.debug(f"Execute write-up {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        result = writeup_manager.execute_writeup(writeup_id)
        
        if result.get("status") == "success":
            return JSONResponse({
                "status": "success",
                "message": "Write-up executed successfully",
                "content": result.get("content", "")
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to execute write-up")
            }, status_code=500)
        
    except Exception as e:
        error_message = f"Error executing write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.post("/{writeup_id}/regenerate")
async def regenerate_writeup(writeup_id: int, request: Request):
    """Regenerate a write-up with new parameters."""
    logger.debug(f"Regenerate write-up {writeup_id} called")
    try:
        body = await request.json()
        model_key = body.get("model_key")
        
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        result = writeup_manager.regenerate_writeup(writeup_id, model_key)
        
        if result.get("status") == "success":
            return JSONResponse({
                "status": "success",
                "message": result.get("message", "Write-up regenerated successfully"),
                "content": result.get("content", "")
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to regenerate write-up")
            }, status_code=500)
        
    except Exception as e:
        error_message = f"Error regenerating write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.delete("/{writeup_id}")
async def delete_writeup(writeup_id: int):
    """Delete a write-up and all associated data."""
    logger.debug(f"Delete write-up {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        result = writeup_manager.delete_writeup(writeup_id)
        
        if result.get("status") == "success":
            return JSONResponse({
                "status": "success",
                "message": "Write-up deleted successfully"
            })
        else:
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to delete write-up")
            }, status_code=500)
            
    except Exception as e:
        error_message = f"Error deleting write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/{writeup_id}/steps")
async def get_writeup_steps(writeup_id: int):
    """Get the execution steps for a write-up."""
    logger.debug(f"Get write-up steps {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        steps = writeup_manager.get_writeup_steps(writeup_id)
        
        return JSONResponse({
            "status": "success",
            "steps": steps
        })
    except Exception as e:
        error_message = f"Error getting write-up steps: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/{writeup_id}/responses")
async def get_writeup_responses(writeup_id: int):
    """Get all responses for a write-up."""
    logger.debug(f"Get write-up responses {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        responses = writeup_manager.get_writeup_responses(writeup_id)
        
        return JSONResponse({
            "status": "success",
            "responses": responses
        })
    except Exception as e:
        error_message = f"Error getting write-up responses: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/{writeup_id}/content")
async def get_writeup_content(writeup_id: int):
    """Get the final content of a completed write-up."""
    logger.debug(f"Get write-up content {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        writeup = writeup_manager.get_writeup(writeup_id)
        
        if not writeup:
            return JSONResponse({
                "status": "error",
                "message": "Write-up not found"
            }, status_code=404)
        
        if writeup.get("status") != "completed":
            return JSONResponse({
                "status": "error",
                "message": f"Write-up is not completed (status: {writeup.get('status')})"
            }, status_code=400)
        
        final_content = writeup.get("final_content")
        if not final_content:
            return JSONResponse({
                "status": "error",
                "message": "No final content available"
            }, status_code=404)
        
        return JSONResponse({
            "status": "success",
            "content": final_content,
            "title": writeup.get("title"),
            "output_format": writeup.get("output_format", "html")
        })
    except Exception as e:
        error_message = f"Error getting write-up content: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.get("/permalink/{writeup_id}")
async def writeup_permalink(request: Request, writeup_id: int):
    """Serve a permalink page for a specific write-up."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not initialized")
    
    logger.debug(f"Permalink for write-up {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        writeup = writeup_manager.get_writeup(writeup_id)
        
        if not writeup:
            raise HTTPException(status_code=404, detail="Write-up not found")
        
        if writeup.get("status") != "completed":
            raise HTTPException(status_code=400, detail=f"Write-up is not completed (status: {writeup.get('status')})")
        
        final_content = writeup.get("final_content")
        if not final_content:
            raise HTTPException(status_code=404, detail="No content available for this write-up")
        
        return templates.TemplateResponse("writeup_permalink.html", {
            "request": request,
            "writeup": writeup,
            "content": final_content
        })
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"Error loading write-up permalink: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)

@router.get("/scheduled/list")
async def get_scheduled_writeups():
    """Get all scheduled write-ups that need to be executed."""
    logger.debug("Get scheduled write-ups called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        scheduled_writeups = writeup_manager.get_scheduled_writeups()
        
        return JSONResponse({
            "status": "success",
            "scheduled_writeups": scheduled_writeups
        })
    except Exception as e:
        error_message = f"Error getting scheduled write-ups: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)
