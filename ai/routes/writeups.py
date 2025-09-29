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

# Import the background job manager
from background_jobs import job_manager

# Create router
router = APIRouter(prefix="/api/writeups", tags=["writeups"])

# Set up logging
logger = logging.getLogger(__name__)

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
    """Create a new write-up request as a background job."""
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
        generate_title = body.get("generate_title", True)  # Default to generating title
        
        if not original_prompt:
            return JSONResponse({
                "status": "error",
                "message": "Prompt is required"
            }, status_code=400)
        
        # Import the write-ups manager
        from tools.writeups_manager import WriteupsManager
        
        # Create the write-up in pending status (don't execute immediately)
        writeup_manager = WriteupsManager()
        
        # Create writeup record without executing (now async)
        result = await writeup_manager.create_writeup(
            title=title,
            original_prompt=original_prompt,
            output_format=output_format,
            output_destination=output_destination,
            frequency=frequency,
            scheduled_for=scheduled_for,
            model_key=model_key,
            generate_title=generate_title
        )
        
        if result.get("status") != "success":
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to create write-up")
            }, status_code=500)
        
        writeup_id = result.get("writeup_id")
        final_title = result.get("title", title)
        logger.info(f"Created pending write-up {writeup_id}: {final_title}")
        
        return JSONResponse({
            "status": "success",
            "message": "Write-up created and ready for execution",
            "writeup_id": writeup_id,
            "title": final_title,
            "content": f"Write-up '{final_title}' created successfully. Select a model and click Execute to begin generation."
        })
            
    except Exception as e:
        error_message = f"Error creating write-up: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)

async def _run_writeup_execution_job(job_id: str, writeup_id: int, model_key: str):
    """Run the writeup execution as a background job."""
    try:
        logger.info(f"=== STARTING WRITEUP EXECUTION JOB ===")
        logger.info(f"Job ID: {job_id}")
        logger.info(f"Writeup ID: {writeup_id}")
        logger.info(f"Model Key: {model_key}")
        
        # Get the job and mark it as running
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in job manager")
            logger.error(f"Available jobs: {list(job_manager.get_all_jobs().keys())}")
            return
            
        job.start()
        job.update_progress(5)
        logger.info(f"Job {job_id} started, progress: 5%")
        
        # Import the write-ups manager
        from tools.writeups_manager import WriteupsManager
        
        job.update_progress(10)
        logger.info(f"Job {job_id} progress: 10% - Initializing writeup manager")
        
        # Get writeup details first
        writeup_manager = WriteupsManager()
        writeup = writeup_manager.get_writeup(writeup_id)
        if not writeup:
            job.fail(f"Write-up {writeup_id} not found")
            return
            
        job.update_progress(20)
        logger.info(f"Job {job_id} progress: 20% - Writeup found, preparing execution")
        
        # Update job description with more details
        job.description = f"Execute writeup '{writeup.get('title', 'Untitled')}' (ID: {writeup_id})"
        
        # Update writeup status to in_progress
        def update_writeup_status_db(conn):
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE writeups 
                SET status = 'in_progress', 
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (writeup_id,))
            conn.commit()
            return "Write-up status updated to in_progress"
        
        from tools.db_utils import execute_with_connection
        execute_with_connection(update_writeup_status_db)
        
        # Execute the writeup with progress tracking
        logger.info(f"Executing writeup {writeup_id} with model {model_key}")
        job.update_progress(30)
        logger.info(f"Job {job_id} progress: 30% - Starting agent execution")
        
        # Execute the writeup directly with progress updates
        result = await _execute_writeup_with_progress(job, writeup_id, writeup_manager, model_key)
        
        logger.info(f"Writeup execution result: {result}")
        
        if result.get("status") == "success":
            job.update_progress(100)
            job.complete(f"Write-up '{writeup.get('title', 'Untitled')}' generated successfully.")
            logger.info(f"Job {job_id} completed successfully")
        else:
            job.fail(result.get("message", "Failed to execute write-up"))
            logger.error(f"Job {job_id} failed: {result.get('message', 'Unknown error')}")
            
    except Exception as e:
        error_message = f"Error in writeup execution job: {str(e)}"
        logger.error(error_message)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(error_message)


async def _execute_writeup_with_progress(job, writeup_id: int, writeup_manager, model_key: str = None):
    """Execute writeup with progress updates."""
    try:
        # Get writeup details
        writeup = writeup_manager.get_writeup(writeup_id)
        if not writeup:
            return {"status": "error", "message": "Write-up not found"}
        
        original_prompt = writeup.get('original_prompt', '')
        output_format = writeup.get('output_format', 'html')
        # Use provided model_key or fallback to database value
        if not model_key:
            model_key = writeup.get('model_key')
            logger.info(f"Using model_key from database: {model_key}")
        else:
            logger.info(f"Using model_key from request: {model_key}")
        
        job.update_progress(40)
        logger.info(f"Job {job.job_id} progress: 40% - Setting up agent with model: {model_key}")
        
        # Import the agent
        from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
        from agents.langchain_agent.config.tool_config import ToolGroup
        
        job.update_progress(50)
        logger.info(f"Job {job.job_id} progress: 50% - Creating agent instance")
        
        # Create agent instance
        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS, ToolGroup.ANALYSIS, ToolGroup.VISUALIZATION],
            include_all_sections=False,
            enable_session_logging=True
        )
        
        job.update_progress(60)
        logger.info(f"Job {job.job_id} progress: 60% - Agent created, starting execution")
        
        # Create comprehensive prompt
        writeup_prompt = f"""
        TASK: Create a comprehensive write-up based on the following request.
        
        REQUEST: "{original_prompt}"
        
        OUTPUT FORMAT: {output_format}
        
        INSTRUCTIONS:
        1. Use available tools strategically to gather relevant data - be targeted and specific in your queries
        2. ALWAYS include LIMIT clauses in DataSF queries (recommended: 500-1000 records max per query)
        3. For large datasets, make multiple focused queries rather than one large query
        4. Prioritize recent data and filter by relevant categories, districts, or time periods
        5. Analyze the data thoroughly to identify key insights and patterns
        6. Create maps and visualizations when geographic data is relevant using generate_map_with_query
        7. Create a well-structured, comprehensive write-up that addresses the request
        8. If data sampling was applied, acknowledge this limitation in your analysis
        9. Ensure the content is accurate, informative, and well-organized
        10. Format the output according to the specified format ({output_format})
        
        CONTEXT WINDOW MANAGEMENT:
        - The system will automatically limit data to prevent context overflow
        - If you receive sampling warnings, adjust your queries to be more targeted
        - Focus on quality insights from representative data rather than exhaustive data collection
        
        Please create a complete, professional write-up that thoroughly addresses the request.
        """
        
        job.update_progress(70)
        logger.info(f"Job {job.job_id} progress: 70% - Starting agent streaming")
        
        # Execute with streaming and progress updates
        response_content = ""
        session_id = None
        chunk_count = 0
        
        async for chunk in agent.explain_change_streaming(writeup_prompt, metric_details={}):
            chunk_count += 1
            
            # Update progress based on chunk count (rough estimate)
            if chunk_count % 10 == 0:  # Update every 10 chunks
                progress = min(85, 70 + (chunk_count * 2))  # Gradually increase from 70% to 85%
                job.update_progress(progress)
                logger.info(f"Job {job.job_id} progress: {progress}% - Processing chunk {chunk_count}")
            
            # Process streaming chunks
            if chunk.startswith("data: "):
                try:
                    import json
                    data = json.loads(chunk[6:])  # Remove "data: " prefix
                    
                    if 'content' in data:
                        response_content += data['content']
                    elif 'session_id' in data:
                        session_id = data['session_id']
                    elif 'completion' in data:
                        # Stream completed
                        break
                except json.JSONDecodeError:
                    continue
        
        job.update_progress(90)
        logger.info(f"Job {job.job_id} progress: 90% - Agent execution complete, saving results")
        
        # Extract content and clean it to remove agent thinking text
        if response_content:
            # Import the agent to use its cleaning method
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            temp_agent = LangChainExplainerAgent(model_key=model_key, tool_groups=[], include_all_sections=False, enable_session_logging=False)
            content = temp_agent._extract_clean_response(response_content)
            if not content or len(content.strip()) < 20:
                content = response_content  # Fallback to original if cleaning removes too much
        else:
            content = "No content generated"
        
        # Update the write-up with the final content
        def update_writeup_db(conn):
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE writeups 
                SET status = 'completed', 
                    final_content = %s, 
                    session_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (content, session_id, writeup_id))
            conn.commit()
            return "Write-up completed"
        
        from tools.db_utils import execute_with_connection
        result = execute_with_connection(update_writeup_db)
        
        if result['status'] == 'success':
            job.update_progress(95)
            logger.info(f"Job {job.job_id} progress: 95% - Writeup saved to database")
            return {
                "status": "success",
                "content": content,
                "session_id": session_id
            }
        else:
            logger.error(f"Error updating write-up: {result['message']}")
            return {"status": "error", "message": "Failed to save write-up content"}
            
    except Exception as e:
        logger.error(f"Error executing write-up with progress: {e}")
        
        # Mark the write-up as failed
        def mark_failed_db(conn):
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE writeups 
                SET status = 'failed', 
                    error_message = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (str(e), writeup_id))
            conn.commit()
            return "Write-up marked as failed"
        
        from tools.db_utils import execute_with_connection
        execute_with_connection(mark_failed_db)
        return {"status": "error", "message": str(e)}

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

@router.get("/test-jobs")
async def test_jobs():
    """Test endpoint to check if job manager is working."""
    try:
        # Create a test job
        test_job_id = job_manager.create_job("test", "Test job for debugging")
        logger.info(f"Created test job: {test_job_id}")
        
        # Get all jobs
        all_jobs = job_manager.get_all_jobs()
        logger.info(f"All jobs: {list(all_jobs.keys())}")
        
        return JSONResponse({
            "status": "success",
            "test_job_id": test_job_id,
            "total_jobs": len(all_jobs),
            "jobs": [job.to_dict() for job in all_jobs.values()]
        })
    except Exception as e:
        logger.error(f"Error testing jobs: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
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

@router.get("/{writeup_id}/title")
async def get_writeup_title(writeup_id: int):
    """Get just the title of a write-up for polling updates."""
    logger.debug(f"Get write-up title {writeup_id} called")
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
            "title": writeup.get("title", ""),
            "updated_at": writeup.get("updated_at")
        })
        
    except Exception as e:
        logger.error(f"Error getting write-up title {writeup_id}: {e}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.post("/{writeup_id}/execute")
async def execute_writeup(writeup_id: int, request: Request):
    """Execute a write-up as a background job."""
    logger.info(f"Execute write-up {writeup_id} called")
    try:
        body = await request.json()
        model_key = body.get("model_key")
        
        logger.info(f"Creating job for writeup {writeup_id} with model {model_key}")
        
        # Create a background job for the writeup execution
        job_id = job_manager.create_job("writeup_execution", f"Execute writeup {writeup_id}")
        logger.info(f"Created job {job_id} for writeup {writeup_id}")
        
        # Verify job was created
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Failed to create job {job_id}")
            return JSONResponse({
                "status": "error",
                "message": "Failed to create background job"
            }, status_code=500)
        
        logger.info(f"Job {job_id} created successfully, starting execution task")
        
        # Start the writeup execution in the background
        task = asyncio.create_task(_run_writeup_execution_job(job_id, writeup_id, model_key))
        logger.info(f"Started background task for job {job_id}")
        
        # Add error handling for the task
        def task_done_callback(task):
            try:
                if task.exception():
                    logger.error(f"Background task failed: {task.exception()}")
            except Exception as e:
                logger.error(f"Error in task callback: {e}")
        
        task.add_done_callback(task_done_callback)
        
        return JSONResponse({
            "status": "success",
            "message": "Write-up execution started",
            "job_id": job_id
        })
        
    except Exception as e:
        error_message = f"Error executing write-up: {str(e)}"
        logger.error(error_message)
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
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
        result = await writeup_manager.regenerate_writeup(writeup_id, model_key)
        
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

@router.get("/stream/{writeup_id}")
async def stream_writeup_session(writeup_id: int, request: Request):
    """Stream the conversation session for a writeup."""
    logger.debug(f"Stream writeup session {writeup_id} called")
    try:
        from tools.writeups_manager import WriteupsManager
        
        writeup_manager = WriteupsManager()
        writeup = writeup_manager.get_writeup(writeup_id)
        
        if not writeup:
            return JSONResponse({
                "status": "error",
                "message": "Write-up not found"
            }, status_code=404)
        
        session_id = writeup.get("session_id")
        if not session_id:
            return JSONResponse({
                "status": "error",
                "message": "No session ID available for this write-up"
            }, status_code=404)
        
        # Serve the conversation viewer template directly with the session ID
        if not templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        
        return templates.TemplateResponse("conversation_viewer.html", {
            "request": request,
            "session_id": session_id
        })
        
    except Exception as e:
        error_message = f"Error streaming write-up session: {str(e)}"
        logger.error(error_message)
        return JSONResponse({
            "status": "error",
            "message": error_message
        }, status_code=500)
