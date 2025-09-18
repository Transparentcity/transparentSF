import logging
import os
import json
import shutil
import subprocess
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

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


@router.get("/query", response_class=HTMLResponse)
async def query_page(request: Request):
    if templates is None:
        raise RuntimeError("Templates not initialized")
    return templates.TemplateResponse("query.html", {"request": request})


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    if templates is None:
        raise RuntimeError("Templates not initialized")
    return templates.TemplateResponse("jobs.html", {"request": request})


@router.get("/api/database-info")
async def get_database_info():
    """Get database status information"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metrics.db")
        db_exists = os.path.exists(db_path)
        db_size = os.path.getsize(db_path) if db_exists else 0
        
        # Get last backup info
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backup_history")
        last_backup = "Never"
        if os.path.exists(backup_dir):
            backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.sql')], reverse=True)
            if backups:
                last_backup = backups[0].replace('metrics_backup_', '').replace('.sql', '')
        
        return JSONResponse({
            "db_status": "Connected" if db_exists else "Not Found",
            "db_size": f"{db_size / 1024 / 1024:.2f} MB" if db_size > 0 else "0 MB",
            "last_backup": last_backup
        })
    except Exception as e:
        logger.error(f"Error getting database info: {str(e)}")
        return JSONResponse({"db_status": "Error", "db_size": "Unknown", "last_backup": "Unknown"})


@router.post("/api/database-backup")
async def create_database_backup():
    """Create database backup"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"metrics_backup_{timestamp}.sql"
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backup_history")
        
        # Create backup directory if it doesn't exist
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, backup_filename)
        
        # Copy database file (simple backup for SQLite)
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metrics.db")
        shutil.copy2(db_path, backup_path)
        
        return JSONResponse({
            "success": True,
            "filename": backup_filename,
            "download_url": f"/backend/api/download-backup/{backup_filename}"
        })
    except Exception as e:
        logger.error(f"Error creating backup: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/download-backup/{filename}")
async def download_backup(filename: str):
    """Download a backup file"""
    backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backup_history")
    file_path = os.path.join(backup_dir, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    return FileResponse(file_path, filename=filename)


@router.post("/api/database-restore")
async def restore_database(backup_file: UploadFile = File(...)):
    """Restore database from backup"""
    try:
        # Save uploaded file temporarily
        temp_path = f"/tmp/{backup_file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(backup_file.file, buffer)
        
        # Backup current database before restoring
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metrics.db")
        backup_current = db_path + ".backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_current)
        
        # Restore from uploaded file
        shutil.copy2(temp_path, db_path)
        os.remove(temp_path)
        
        return JSONResponse({"success": True, "message": "Database restored successfully"})
    except Exception as e:
        logger.error(f"Error restoring database: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/session-logs-info")
async def get_session_logs_info():
    """Get information about session logs"""
    try:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        
        if not os.path.exists(logs_dir):
            return JSONResponse({
                "total_sessions": 0,
                "latest_session": "None",
                "logs_directory": logs_dir
            })
        
        # Count log files
        log_files = [f for f in os.listdir(logs_dir) if f.endswith('.log')]
        
        # Get latest session
        latest_session = "None"
        if log_files:
            latest_session = sorted(log_files, reverse=True)[0]
        
        return JSONResponse({
            "total_sessions": len(log_files),
            "latest_session": latest_session,
            "logs_directory": logs_dir
        })
    except Exception as e:
        logger.error(f"Error getting session logs info: {str(e)}")
        return JSONResponse({"total_sessions": 0, "latest_session": "Error", "logs_directory": "Unknown"})


@router.get("/api/available-logs")
async def get_available_logs():
    """Get list of available log files"""
    try:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        
        if not os.path.exists(logs_dir):
            return JSONResponse({"logs": []})
        
        log_files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')], reverse=True)
        return JSONResponse({"logs": log_files[:50]})  # Return max 50 most recent logs
    except Exception as e:
        logger.error(f"Error getting available logs: {str(e)}")
        return JSONResponse({"logs": []})


@router.get("/api/download-session-logs")
async def download_session_logs():
    """Download all session logs as zip"""
    try:
        import zipfile
        from io import BytesIO
        
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        
        if not os.path.exists(logs_dir):
            raise HTTPException(status_code=404, detail="Logs directory not found")
        
        # Create zip file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(logs_dir):
                for file in files:
                    if file.endswith('.log'):
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, logs_dir)
                        zip_file.write(file_path, arcname)
        
        zip_buffer.seek(0)
        
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=session_logs.zip"}
        )
    except Exception as e:
        logger.error(f"Error downloading session logs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/delete-data")
async def delete_data():
    """Delete data from the system (danger zone)"""
    try:
        # This is a dangerous operation - implement with caution
        # For now, we'll just return a success message
        logger.warning("Delete data requested - not implemented for safety")
        return JSONResponse({"success": False, "message": "Delete operation not implemented for safety"})
    except Exception as e:
        logger.error(f"Error in delete data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/notes-count")
async def get_notes_count():
    """Get count of notes/tokens"""
    try:
        # Import the notes manager function
        from tools.notes_manager import get_notes
        
        # Create empty context_variables for the function
        context_variables = {}
        
        # Call the same function the explainer agent uses
        result = get_notes(context_variables)
        
        if "error" in result:
            logger.error(f"Notes manager error: {result['error']}")
            return JSONResponse({"count": 0})
        
        content = result.get("notes", "")
        
        # Simple token count approximation
        token_count = len(content.split()) if content else 0
        
        return JSONResponse({"count": token_count})
        
    except Exception as e:
        logger.error(f"Error getting notes count: {str(e)}")
        return JSONResponse({"count": 0})


@router.get("/api/view-log")
async def view_log_info(file: str):
    """Get information about a specific log file"""
    try:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        file_path = os.path.join(logs_dir, file)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Log file not found")
        
        file_size = os.path.getsize(file_path)
        return JSONResponse({
            "size": f"{file_size / 1024:.2f} KB" if file_size < 1024*1024 else f"{file_size / 1024 / 1024:.2f} MB"
        })
    except Exception as e:
        logger.error(f"Error getting log file info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


