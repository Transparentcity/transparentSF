from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
import logging
import os
import asyncio
from pathlib import Path
from background_jobs import job_manager

# Create router
router = APIRouter(prefix="/api/monthly-reports", tags=["monthly-reports"])

# Set up logging
logger = logging.getLogger(__name__)

@router.get("/available-models")
async def get_available_models_for_newsletter():
    """Get available models for newsletter generation."""
    logger.debug("Get available models called")
    try:
        # Import the necessary function
        from monthly_report import get_available_models_for_newsletter
        
        # Get the list of available models
        result = get_available_models_for_newsletter()
        
        return JSONResponse(result)
    except Exception as e:
        error_message = f"Error getting available models: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

@router.post("/generate")
async def generate_monthly_report_post(request: Request):
    """Generate monthly report with custom parameters including model selection."""
    logger.debug("Generate monthly report (POST) called")
    try:
        # Get parameters from request body
        body = await request.json()
        district = body.get("district", "0")
        period_type = body.get("period_type", "month")
        max_report_items = body.get("max_report_items", 10)
        model_key = body.get("model_key", None)  # New parameter for model selection
        
        logger.info(f"Generating monthly report with district={district}, period_type={period_type}, max_items={max_report_items}, model={model_key}")
        
        # Create a background job for newsletter generation
        job_description = f"Generate monthly newsletter (district={district}, period={period_type}, items={max_report_items})"
        job_id = job_manager.create_job("newsletter_generation", job_description)
        logger.info(f"Created newsletter generation job {job_id}")
        
        # Mark job as running immediately so badge updates faster
        job = job_manager.get_job(job_id)
        if job:
            job.start()
            job.update_progress(1)
        
        # Start the newsletter generation in the background
        asyncio.create_task(
            _run_newsletter_generation_job(
                job_id=job_id,
                district=district,
                period_type=period_type,
                max_report_items=max_report_items,
                model_key=model_key
            )
        )
        
        # Return immediately with job_id
        return JSONResponse({
            "status": "success",
            "message": "Monthly Newsletter is running",
            "job_id": job_id
        })
        
    except Exception as e:
        error_message = f"Error starting newsletter generation job: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)


async def _run_newsletter_generation_job(
    job_id: str,
    district: str = "0",
    period_type: str = "month",
    max_report_items: int = 10,
    model_key: str = None
):
    """Run the newsletter generation as a background job with progress updates."""
    try:
        from monthly_report import run_monthly_report_process
        
        logger.info(f"Starting newsletter generation job {job_id}")
        
        # Get the job (should already be started, but verify)
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in job manager")
            return
        
        # Job should already be started, but ensure it's running
        if job.status != "running":
            job.start()
        job.update_progress(5)
        logger.info(f"Job {job_id} running, progress: 5%")
        
        # Run the monthly report process in a thread pool
        def run_newsletter_process():
            try:
                # Get job to update progress
                job = job_manager.get_job(job_id)
                if job:
                    job.update_progress(10)
                logger.info(f"Job {job_id} progress: 10% - Starting newsletter process")
                
                result = run_monthly_report_process(
                    district=district,
                    period_type=period_type,
                    max_report_items=max_report_items,
                    model_key=model_key
                )
                
                return result
            except Exception as e:
                logger.error(f"Error in newsletter process for job {job_id}: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise
        
        # Execute in thread pool to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_newsletter_process)
        
        # Check result and complete job
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found when trying to complete")
            return
        
        if result.get("status") == "success":
            # Extract filename from result
            report_path = result.get("revised_report_path") or result.get("newsletter_path")
            filename = os.path.basename(report_path) if report_path else None
            
            job.update_progress(100)
            job.complete({
                "filename": filename,
                "report_path": report_path,
                "district": district,
                "period_type": period_type,
                "max_report_items": max_report_items
            })
            logger.info(f"✅ Newsletter generation job {job_id} completed successfully: {filename}")
        else:
            error_message = result.get("message", "Newsletter generation failed")
            job.fail(error_message)
            logger.error(f"❌ Newsletter generation job {job_id} failed: {error_message}")
            
    except Exception as e:
        logger.error(f"Error in newsletter generation job {job_id}: {e}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))

@router.get("/list")
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

@router.delete("/{report_id}")
async def delete_monthly_report(report_id: int):
    """Delete a monthly report and associated files."""
    try:
        from tools.db_utils import get_postgres_connection
        
        conn = get_postgres_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        cursor = conn.cursor()
        
        # Get the report details first (to get filenames for deletion)
        cursor.execute("SELECT original_filename, revised_filename, audio_file FROM reports WHERE id = %s", (report_id,))
        report = cursor.fetchone()
        
        if not report:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Delete the report from database
        cursor.execute("DELETE FROM reports WHERE id = %s", (report_id,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        # Delete associated files if they exist
        reports_dir = Path(__file__).parent.parent / 'output' / 'reports'
        for filename in [report[0], report[1]]:  # original_filename, revised_filename
            if filename:
                file_path = reports_dir / filename
                if file_path.exists():
                    file_path.unlink()
        
        # Delete audio file if it exists
        if report[2]:  # audio_file
            audio_dir = Path(__file__).parent.parent / 'output' / 'narration'
            audio_path = audio_dir / report[2]
            if audio_path.exists():
                audio_path.unlink()
        
        return JSONResponse({
            "status": "success",
            "message": "Report deleted successfully"
        })
    except Exception as e:
        logger.error(f"Error deleting report {report_id}: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": f"Error deleting report: {str(e)}"
        }, status_code=500)
