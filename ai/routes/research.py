"""
Research Routes - FastAPI routes for the research system.

This module provides API endpoints for creating, managing, and executing
research reports.
"""

import logging
import json
import re
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from background_jobs import job_manager
from ai.core.session_store import get_session_store, SessionData

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/research", tags=["research"])

# Templates will be set from main
templates = None


def set_templates(template_instance: Jinja2Templates):
    """Set the templates instance for this router."""
    global templates
    templates = template_instance
    logger.info("Templates set in research router")


async def save_agent_session_to_redis(agent, session_id: Optional[str] = None) -> bool:
    """
    Save agent session data to Redis for persistence.
    
    This ensures that agent sessions created during research are persisted
    even if the process dies or times out.
    
    Args:
        agent: LangChainExplainerAgent instance
        session_id: Optional session ID (will use agent's current session if not provided)
        
    Returns:
        True if session saved successfully, False otherwise
    """
    try:
        # Get session ID from agent if not provided
        if not session_id:
            if hasattr(agent, 'current_session') and agent.current_session:
                session_id = agent.current_session.session_id
            else:
                logger.warning("No session ID available to save to Redis")
                return False
        
        # Get session store (Redis with fallback to in-memory)
        session_store = get_session_store()
        
        # Get conversation history from agent
        conversation_history = []
        if hasattr(agent, 'get_conversation_history'):
            conversation_history = agent.get_conversation_history()
        elif hasattr(agent, 'memory'):
            # Try to extract from LangChain memory
            try:
                memory_vars = agent.memory.load_memory_variables({})
                chat_history = memory_vars.get("chat_history", [])
                # Convert LangChain messages to our format
                for msg in chat_history:
                    if hasattr(msg, 'content'):
                        conversation_history.append({
                            "role": "user" if msg.__class__.__name__ == "HumanMessage" else "assistant",
                            "content": msg.content,
                            "timestamp": datetime.utcnow().isoformat()
                        })
            except Exception as e:
                logger.debug(f"Could not extract conversation from memory: {e}")
        
        # Get tool groups
        tool_groups = []
        if hasattr(agent, 'tool_groups'):
            tool_groups = [g.value if hasattr(g, 'value') else str(g) for g in agent.tool_groups]
        
        # Get tool calls from agent session if available
        tool_calls = []
        if hasattr(agent, 'current_session') and agent.current_session:
            if hasattr(agent.current_session, 'tool_calls'):
                for tc in agent.current_session.tool_calls:
                    # Handle both ToolCall objects and dictionaries
                    if hasattr(tc, 'tool_name'):
                        # It's a ToolCall object
                        tool_calls.append({
                            "tool_name": getattr(tc, 'tool_name', ''),
                            "arguments": getattr(tc, 'arguments', {}),
                            "result": str(getattr(tc, 'result', ''))[:1000] if getattr(tc, 'result', None) else "",
                            "timestamp": getattr(tc, 'timestamp', datetime.utcnow().isoformat()),
                            "success": getattr(tc, 'success', True)
                        })
                    elif isinstance(tc, dict):
                        # It's already a dictionary
                        tool_calls.append({
                            "tool_name": tc.get("tool_name", ""),
                            "arguments": tc.get("arguments", {}),
                            "result": str(tc.get("result", ""))[:1000] if tc.get("result") else "",
                            "timestamp": tc.get("timestamp", datetime.utcnow().isoformat()),
                            "success": tc.get("success", True)
                        })
                    else:
                        # Fallback: try to convert to string
                        logger.warning(f"Unknown tool_call type: {type(tc)}")
                        tool_calls.append({
                            "tool_name": str(getattr(tc, 'tool_name', 'unknown')),
                            "arguments": {},
                            "result": str(tc)[:1000],
                            "timestamp": datetime.utcnow().isoformat(),
                            "success": True
                        })
        
        # Create session data
        session_data = SessionData(
            session_id=session_id,
            agent_type="langchain_explainer",
            model_key=getattr(agent, 'model_key', 'unknown'),
            tool_groups=tool_groups,
            conversation_history=conversation_history,
            tool_calls=tool_calls,
            metadata={
                "source": "research_system",
                "tools_count": len(getattr(agent, 'tools', [])),
                "available_tools": [tool.name for tool in getattr(agent, 'tools', [])]
            }
        )
        
        # Save with 24 hour TTL (research sessions should persist longer)
        ttl_seconds = 24 * 3600
        success = await session_store.save_session(session_data, ttl_seconds=ttl_seconds)
        
        if success:
            logger.info(f"Successfully saved research session {session_id} to Redis")
        else:
            logger.warning(f"Failed to save research session {session_id} to Redis")
        
        return success
        
    except Exception as e:
        # Don't fail the research if Redis save fails - just log it
        logger.error(f"Error saving research session to Redis: {e}", exc_info=True)
        return False


@router.get("", response_class=HTMLResponse)
async def research_page(request: Request):
    """Serve the research page."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not initialized")
    return templates.TemplateResponse("research.html", {"request": request})


@router.get("/list")
async def list_research_reports(
    limit: int = 50,
    status: Optional[str] = None,
    district: Optional[str] = None
):
    """List research reports with optional filters."""
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        reports = manager.list_reports(limit=limit, status=status, district=district)
        
        # Convert datetime objects to ISO format and ensure metadata is properly formatted
        for report in reports:
            for key, value in report.items():
                if hasattr(value, 'isoformat'):
                    report[key] = value.isoformat()
                # Ensure metadata is a dict if it's JSONB from PostgreSQL
                if key == 'metadata' and value is not None:
                    if isinstance(value, str):
                        try:
                            report[key] = json.loads(value)
                        except:
                            report[key] = {}
                    elif not isinstance(value, dict):
                        report[key] = {}
        
        return JSONResponse({
            "status": "success",
            "reports": reports
        })
        
    except Exception as e:
        logger.error(f"Error listing research reports: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/{report_id}")
async def get_research_report(report_id: int):
    """Get a specific research report."""
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            return JSONResponse({
                "status": "error",
                "message": "Research report not found"
            }, status_code=404)
        
        # Get research items
        items = manager.get_research_items(report_id)
        
        # Convert datetime objects and ensure metadata is properly formatted
        for key, value in report.items():
            if hasattr(value, 'isoformat'):
                report[key] = value.isoformat()
            # Ensure metadata is a dict if it's JSONB from PostgreSQL
            if key == 'metadata' and value is not None:
                if isinstance(value, str):
                    try:
                        report[key] = json.loads(value)
                    except:
                        report[key] = {}
                elif not isinstance(value, dict):
                    report[key] = {}
                
        for item in items:
            for key, value in item.items():
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
                # Ensure metadata is a dict if it's JSONB from PostgreSQL
                if key == 'metadata' and value is not None:
                    if isinstance(value, str):
                        try:
                            item[key] = json.loads(value)
                        except Exception:
                            item[key] = {}
                    elif not isinstance(value, dict):
                        item[key] = {}
        
        report['items'] = items
        
        return JSONResponse(
            {"status": "success", "report": report},
            headers={"Cache-Control": "no-store"},
        )
        
    except Exception as e:
        logger.error(f"Error getting research report {report_id}: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/create")
async def create_research_report(request: Request):
    """Create a new research report."""
    try:
        body = await request.json()
        
        title = body.get("title", "").strip()
        prompt = body.get("prompt", "").strip()
        district = body.get("district", "0")
        model_key = body.get("model_key")
        generate_title = body.get("generate_title", True)
        enable_web_search = body.get("enable_web_search", False)
        
        if not prompt:
            return JSONResponse({
                "status": "error",
                "message": "Research prompt is required"
            }, status_code=400)
        
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        
        # Generate title if not provided
        if not title and generate_title:
            title = await _generate_research_title(prompt, model_key)
        elif not title:
            title = f"Research: {prompt[:50]}..."
        
        # Store web search option in metadata
        metadata = {"enable_web_search": enable_web_search}
        
        result = manager.create_report(
            title=title,
            original_prompt=prompt,
            district=district,
            model_key=model_key,
            metadata=metadata
        )
        
        if result.get("status") != "success":
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to create research report")
            }, status_code=500)
        
        report_id = result.get("report_id")
        
        logger.info(f"Created research report {report_id}: {title}")
        
        return JSONResponse({
            "status": "success",
            "report_id": report_id,
            "title": title
        })
        
    except Exception as e:
        logger.error(f"Error creating research report: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/generate-agenda")
async def generate_research_agenda(report_id: int, request: Request):
    """Generate a research agenda for a report using AI."""
    try:
        body = await request.json()
        model_key = body.get("model_key")
        
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            return JSONResponse({
                "status": "error",
                "message": "Research report not found"
            }, status_code=404)
        
        # Create background job for agenda generation
        job_id = job_manager.create_job(
            "research_agenda_generation",
            f"Generating research agenda for: {report.get('title', 'Research')}"
        )
        
        job = job_manager.get_job(job_id)
        job.start()

        # Store active job_id on report metadata for UI controls (e.g., Stop)
        md = report.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = {}
        if not isinstance(md, dict):
            md = {}
        md["job_id"] = job_id
        md["status_detail"] = md.get("status_detail") or "Generating agenda..."
        manager.update_report(report_id, metadata=md)
        
        # Run agenda generation in background
        task = asyncio.create_task(
            _run_agenda_generation_job(
                job_id, 
                report_id, 
                report.get('original_prompt'),
                report.get('district', '0'),
                model_key or report.get('model_key')
            )
        )
        job_manager.register_task(job_id, task)
        
        return JSONResponse({
            "status": "success",
            "job_id": job_id,
            "message": "Agenda generation started"
        })
        
    except Exception as e:
        logger.error(f"Error starting agenda generation: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/set-agenda")
async def set_research_agenda(report_id: int, request: Request):
    """Manually set or update the research agenda."""
    try:
        body = await request.json()
        agenda_json = body.get("agenda_json", "").strip()
        
        if not agenda_json:
            return JSONResponse({
                "status": "error",
                "message": "Agenda JSON is required"
            }, status_code=400)
        
        # Validate JSON
        try:
            agenda = json.loads(agenda_json)
        except json.JSONDecodeError as e:
            return JSONResponse({
                "status": "error",
                "message": f"Invalid JSON: {str(e)}"
            }, status_code=400)
        
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        
        # Update report with agenda
        result = manager.update_report(
            report_id,
            agenda=agenda,
            agenda_json=agenda_json,
            status="agenda_ready"
        )
        
        if result.get("status") != "success":
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to update agenda")
            }, status_code=500)
        
        # Delete existing research items before adding new ones
        manager.delete_research_items(report_id)
        
        # Create research items from agenda
        items = agenda.get("items", [])
        for item in items:
            manager.add_research_item(
                report_id=report_id,
                research_question=item.get("research_question", ""),
                metric_id=str(item.get("metric_id")) if item.get("metric_id") else None,
                metric_name=item.get("metric_name"),
                anomaly_id=str(item.get("anomaly_id")) if item.get("anomaly_id") else None,
                reason=item.get("reason") or item.get("builds_toward"),
                priority=item.get("priority", 1),
                success_criteria=item.get("success_criteria"),
                builds_toward=item.get("builds_toward"),
                metadata={"phase": "agenda", "added_by": "agenda"}
            )
        
        return JSONResponse({
            "status": "success",
            "message": "Research agenda set successfully",
            "items_count": len(items)
        })
        
    except Exception as e:
        logger.error(f"Error setting research agenda: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/run")
async def run_research(report_id: int, request: Request):
    """Run the research for a report."""
    try:
        body = await request.json()
        model_key = body.get("model_key")
        
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            return JSONResponse({
                "status": "error",
                "message": "Research report not found"
            }, status_code=404)
        
        if report.get('status') not in ['agenda_ready', 'failed']:
            return JSONResponse({
                "status": "error",
                "message": f"Cannot run research in status: {report.get('status')}"
            }, status_code=400)
        
        # Create background job
        job_id = job_manager.create_job(
            "research_execution",
            f"Executing research: {report.get('title', 'Research')}"
        )
        
        job = job_manager.get_job(job_id)
        job.start()
        
        # Update report status - merge metadata
        existing_metadata = report.get('metadata', {}) if report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = "Starting research..."
        existing_metadata["job_id"] = job_id
        manager.update_report(
            report_id,
            status="running",
            metadata=existing_metadata
        )
        
        # Run research in background
        task = asyncio.create_task(
            _run_research_execution_job(
                job_id,
                report_id,
                model_key or report.get('model_key')
            )
        )
        job_manager.register_task(job_id, task)
        
        return JSONResponse({
            "status": "success",
            "job_id": job_id,
            "message": "Research execution started"
        })
        
    except Exception as e:
        logger.error(f"Error starting research execution: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.delete("/{report_id}")
async def delete_research_report(report_id: int):
    """Delete a research report."""
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        result = manager.delete_report(report_id)
        
        if result.get("status") != "success":
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to delete report")
            }, status_code=500)
        
        return JSONResponse({
            "status": "success",
            "message": "Research report deleted"
        })
        
    except Exception as e:
        logger.error(f"Error deleting research report: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/regenerate")
async def regenerate_research(report_id: int, request: Request):
    """Regenerate a research report by re-running it with a new model."""
    try:
        body = await request.json()
        model_key = body.get("model_key")
        
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            return JSONResponse({
                "status": "error",
                "message": "Research report not found"
            }, status_code=404)
        
        if report.get('status') not in ['completed', 'failed']:
            return JSONResponse({
                "status": "error",
                "message": f"Cannot regenerate research in status: {report.get('status')}"
            }, status_code=400)

        # IMPORTANT: Remove non-agenda items (exploration + evaluation follow-ons) from prior runs.
        # Otherwise users see "extra" questions before the run even starts.
        cleanup_result = manager.delete_non_agenda_items(report_id)
        if cleanup_result.get("status") == "error":
            logger.warning(
                f"Non-agenda cleanup failed for report {report_id}: {cleanup_result.get('message')}"
            )
        
        # Reset research items to pending status
        items = manager.get_research_items(report_id)
        for item in items:
            # Preserve useful metadata (success criteria, phase tags), but clear iteration history.
            md = item.get("metadata") or {}
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except Exception:
                    md = {}
            if not isinstance(md, dict):
                md = {}
            md.pop("iterations", None)
            md.pop("total_iterations", None)

            manager.update_research_item(
                report_id,
                item.get('item_id'),
                status="pending",
                result=None,
                error_message=None,
                session_id=None,
                started_at=None,
                completed_at=None,
                metadata=md  # Keep core metadata, clear iteration history above
            )
        
        # Reset report status to agenda_ready and clear previous results
        final_model_key = model_key if model_key else report.get('model_key')
        manager.update_report(
            report_id,
            status="agenda_ready",
            final_report=None,
            final_report_html=None,
            error_message=None,
            model_key=final_model_key,
            total_items=len(items),
            completed_items=0,
            progress_percent=0,
        )
        
        logger.info(f"Reset research report {report_id} for regeneration with model {final_model_key}")
        
        # Create background job for research execution
        job_id = job_manager.create_job(
            "research_execution",
            f"Regenerating research: {report.get('title', 'Research')}"
        )
        
        job = job_manager.get_job(job_id)
        job.start()
        
        # Update report status to running (include status_detail like /run)
        latest_report = manager.get_report(report_id)
        existing_metadata = latest_report.get("metadata", {}) if latest_report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except Exception:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        existing_metadata["status_detail"] = "Starting research..."

        manager.update_report(
            report_id,
            status="running",
            metadata=existing_metadata,
        )
        
        # Run research in background
        task = asyncio.create_task(
            _run_research_execution_job(
                job_id,
                report_id,
                final_model_key
            )
        )
        job_manager.register_task(job_id, task)
        
        return JSONResponse({
            "status": "success",
            "job_id": job_id,
            "message": "Research regeneration started"
        })
        
    except Exception as e:
        logger.error(f"Error regenerating research: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/resynthesize")
async def resynthesize_research(report_id: int, request: Request):
    """Re-synthesize the final report from existing research item results."""
    try:
        body = await request.json()
        model_key = body.get("model_key")

        from tools.research_manager import get_research_manager

        manager = get_research_manager()
        report = manager.get_report(report_id)

        if not report:
            return JSONResponse(
                {"status": "error", "message": "Research report not found"},
                status_code=404,
            )

        # Only allow resynthesis when not actively running
        if report.get("status") in ["running"]:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Cannot re-synthesize while research is running",
                },
                status_code=400,
            )

        # Require at least one completed item with a result
        items = manager.get_research_items(report_id)
        completed_items = [
            i for i in items if i.get("status") == "completed" and i.get("result")
        ]
        if not completed_items:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "No completed research items with results to synthesize",
                },
                status_code=400,
            )

        final_model_key = model_key if model_key else report.get("model_key")

        # Update report status to synthesizing (merge metadata)
        existing_metadata = report.get("metadata", {}) if report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except Exception:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}

        existing_metadata["status_detail"] = "Re-synthesizing final report..."
        manager.update_report(
            report_id,
            status="synthesizing",
            metadata=existing_metadata,
            model_key=final_model_key,
        )

        job_id = job_manager.create_job(
            "research_resynthesis",
            f"Re-synthesizing final report: {report.get('title', 'Research')}",
        )
        job = job_manager.get_job(job_id)
        job.start()

        # Store active job_id on report metadata for UI controls (e.g., Stop)
        md = report.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = {}
        if not isinstance(md, dict):
            md = {}
        md["job_id"] = job_id
        md["status_detail"] = md.get("status_detail") or "Re-synthesizing..."
        manager.update_report(report_id, metadata=md)

        task = asyncio.create_task(
            _run_research_synthesis_only_job(job_id, report_id, final_model_key)
        )
        job_manager.register_task(job_id, task)

        return JSONResponse(
            {
                "status": "success",
                "job_id": job_id,
                "message": "Final report re-synthesis started",
            }
        )

    except Exception as e:
        logger.error(f"Error re-synthesizing research: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/{report_id}/items")
async def get_research_items(report_id: int):
    """Get all research items for a report."""
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        items = manager.get_research_items(report_id)
        
        # Convert datetime objects + normalize metadata
        for item in items:
            for key, value in item.items():
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
                if key == "metadata" and value is not None:
                    if isinstance(value, str):
                        try:
                            item[key] = json.loads(value)
                        except Exception:
                            item[key] = {}
                    elif not isinstance(value, dict):
                        item[key] = {}
        
        return JSONResponse(
            {"status": "success", "items": items},
            headers={"Cache-Control": "no-store"},
        )
        
    except Exception as e:
        logger.error(f"Error getting research items: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a background job."""
    try:
        job = job_manager.get_job(job_id)
        
        if not job:
            return JSONResponse({
                "status": "error",
                "message": "Job not found"
            }, status_code=404)
        
        return JSONResponse({
            "status": "success",
            "job": job.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error getting job status: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/{report_id}/cancel")
async def cancel_research(report_id: int):
    """Cancel an in-progress research run (Stop button)."""
    try:
        from tools.research_manager import get_research_manager

        manager = get_research_manager()
        report = manager.get_report(report_id)
        if not report:
            return JSONResponse(
                {"status": "error", "message": "Research report not found"},
                status_code=404,
            )

        if report.get("status") not in ["running", "synthesizing", "draft"]:
            return JSONResponse(
                {
                    "status": "error",
                    "message": f"Cannot cancel research in status: {report.get('status')}",
                },
                status_code=400,
            )

        md = report.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = {}
        if not isinstance(md, dict):
            md = {}

        job_id = md.get("job_id")
        if not job_id:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "No active job_id found for this report",
                },
                status_code=400,
            )

        # Mark job cancelled (and cancel associated asyncio task if present)
        ok = job_manager.cancel_job(job_id, reason="Cancelled by user")
        if not ok:
            return JSONResponse(
                {"status": "error", "message": "Job not found"},
                status_code=404,
            )

        md["status_detail"] = "Cancelled by user"
        md["cancelled_at"] = datetime.now().isoformat()
        manager.update_report(report_id, status="cancelled", metadata=md)

        # Mark any in-progress items as failed (cancelled); leave pending items as-is
        items = manager.get_research_items(report_id)
        for item in items:
            if item.get("status") == "in_progress":
                manager.update_research_item(
                    report_id,
                    item.get("item_id"),
                    status="failed",
                    error_message="Cancelled by user",
                    completed_at=datetime.now(),
                )

        return JSONResponse({"status": "success", "message": "Cancellation requested"})

    except Exception as e:
        logger.error(f"Error cancelling research: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/permalink/{report_id}", response_class=HTMLResponse)
async def research_permalink(request: Request, report_id: int):
    """Serve a permalink page for a specific research report."""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not initialized")
    
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            raise HTTPException(status_code=404, detail="Research report not found")
        
        # Check if report is public (if is_public flag exists)
        is_public = report.get("is_public", False)
        if not is_public:
            raise HTTPException(
                status_code=403,
                detail="This research report is not publicly available"
            )
        
        if report.get("status") != "completed":
            raise HTTPException(
                status_code=400, 
                detail=f"Research report is not completed (status: {report.get('status')})"
            )
        
        final_report = report.get("final_report") or report.get("final_report_html")
        if not final_report:
            raise HTTPException(status_code=404, detail="No content available for this report")
        
        # Parse agenda if available
        agenda = report.get("agenda")
        if isinstance(agenda, str):
            try:
                agenda = json.loads(agenda)
            except:
                agenda = None
        
        return templates.TemplateResponse("research_permalink.html", {
            "request": request,
            "report": report,
            "agenda": agenda,
            "content": report.get("final_report_html") or report.get("final_report", "")
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading research permalink {report_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{report_id}/toggle-public")
async def toggle_public_flag(report_id: int, request: Request):
    """Toggle the is_public flag for a research report and generate permalink slug."""
    try:
        body = await request.json()
        is_public = body.get("is_public", False)
        
        from tools.research_manager import get_research_manager, generate_slug
        
        manager = get_research_manager()
        report = manager.get_report(report_id)
        
        if not report:
            return JSONResponse({
                "status": "error",
                "message": "Research report not found"
            }, status_code=404)
        
        if report.get("status") != "completed":
            return JSONResponse({
                "status": "error",
                "message": f"Cannot make report public: status is {report.get('status')} (must be 'completed')"
            }, status_code=400)
        
        # Generate permalink slug if making public and slug doesn't exist
        permalink_slug = report.get("permalink_slug")
        if is_public and not permalink_slug:
            title = report.get("title", f"Research {report_id}")
            permalink_slug = generate_slug(title, report_id)
            
            # Ensure uniqueness by checking if slug exists
            def check_slug_exists(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) FROM research_reports 
                    WHERE permalink_slug = %s AND id != %s
                """, (permalink_slug, report_id))
                count = cursor.fetchone()[0]
                cursor.close()
                return count > 0
            
            from tools.db_utils import execute_with_connection
            result = execute_with_connection(check_slug_exists)
            if result.get("status") == "success" and result.get("result"):
                # Slug exists, append report_id
                permalink_slug = f"{permalink_slug}-{report_id}"
        
        # If making private, clear the slug
        if not is_public:
            permalink_slug = None
        
        result = manager.update_report(
            report_id,
            is_public=is_public,
            permalink_slug=permalink_slug
        )
        
        if result.get("status") != "success":
            return JSONResponse({
                "status": "error",
                "message": result.get("message", "Failed to update public flag")
            }, status_code=500)
        
        return JSONResponse({
            "status": "success",
            "is_public": is_public,
            "permalink_slug": permalink_slug,
            "permalink_url": f"/api/research/permalink/{report_id}" if is_public else None
        })
        
    except Exception as e:
        logger.error(f"Error toggling public flag for report {report_id}: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


# ============================================================================
# Helper Functions
# ============================================================================

def _parse_sse_chunk(chunk: str) -> str:
    """Parse SSE chunk and extract content."""
    if not chunk or not isinstance(chunk, str):
        return ""
    
    # Strip whitespace
    chunk = chunk.strip()
    
    # Skip empty chunks
    if not chunk:
        return ""
    
    # Handle SSE format: "data: {"content": "text"}" or "data:{"content": "text"}"
    if chunk.startswith("data:"):
        # Remove "data:" prefix (with or without space)
        json_str = chunk[5:].strip() if chunk.startswith("data: ") else chunk[5:].strip()
        
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                # Return content if present
                if "content" in data:
                    return data["content"]
                # Skip tool calls and other non-content chunks
                if any(key in data for key in ["tool_call_start", "tool_call_complete", "agent_stopped", "error", "done"]):
                    return ""
            return ""
        except json.JSONDecodeError:
            # If JSON parsing fails, maybe it's raw text after "data:"
            return ""
    
    # Handle raw JSON format (without data: prefix)
    if chunk.startswith("{"):
        try:
            data = json.loads(chunk)
            if isinstance(data, dict) and "content" in data:
                return data["content"]
            return ""
        except json.JSONDecodeError:
            pass
    
    # Return raw text if it's not SSE format
    return chunk


def _parse_sse_json(chunk: str) -> Dict[str, Any]:
    """Parse SSE chunk into JSON dict (if possible)."""
    if not chunk or not isinstance(chunk, str):
        return {}
    chunk = chunk.strip()
    if not chunk:
        return {}
    if chunk.startswith("data:"):
        json_str = chunk[5:].strip() if chunk.startswith("data: ") else chunk[5:].strip()
        try:
            data = json.loads(json_str)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    if chunk.startswith("{"):
        try:
            data = json.loads(chunk)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


async def _generate_research_title(prompt: str, model_key: str = None) -> str:
    """Generate a title for the research report using AI."""
    try:
        from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
        from agents.langchain_agent.config.tool_config import ToolGroup
        
        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=[],  # No tools needed for title generation
            include_all_sections=False,
            enable_session_logging=False
        )
        
        # Use a very direct prompt that overrides persona
        title_prompt = f"""Generate a title for this research question. Return ONLY the title, nothing else.

Research question: {prompt}

Title (max 60 characters, title case):"""

        response = ""
        api_error = None
        # Note: explain_change_streaming takes (prompt, metric_details) - prompt is first
        async for chunk in agent.explain_change_streaming(title_prompt, {}):
            if isinstance(chunk, dict):
                # Check for error in dict chunks
                if "error" in chunk:
                    api_error = chunk.get("error", "Unknown API error")
                    logger.error(f"API error detected in title generation: {api_error}")
                    break
                # Handle dict chunks with "type": "token"
                elif chunk.get("type") == "token":
                    response += chunk.get("content", "")
                elif "content" in chunk:
                    response += chunk.get("content", "")
            elif isinstance(chunk, str):
                # Check for error in SSE format
                if chunk.strip().startswith("data:"):
                    try:
                        json_str = chunk.strip()[5:].strip()
                        data = json.loads(json_str)
                        if isinstance(data, dict) and "error" in data:
                            api_error = data.get("error", "Unknown API error")
                            logger.error(f"API error detected in title generation SSE: {api_error}")
                            break
                    except json.JSONDecodeError:
                        pass
                
                # Parse SSE format strings
                content = _parse_sse_chunk(chunk)
                if content:
                    response += content
        
        # Handle API errors
        if api_error:
            error_message = f"API Error: {api_error}"
            if "credit balance" in api_error.lower() or "too low" in api_error.lower():
                error_message = "API Error: Insufficient API credits. Please check your API account balance."
            logger.error(f"Title generation failed: {error_message}")
            raise Exception(error_message)
        
        # Clean up the response - extract just the title
        cleaned = response.strip()
        
        if cleaned:
            # Remove common conversational prefixes
            prefixes_to_remove = [
                "i need to clarify",
                "i'm seymour",
                "i am seymour",
                "sure,",
                "of course,",
                "let me",
                "i'll",
                "i will",
                "here's",
                "here is",
                "the title is:",
                "title:",
                "title is",
            ]
            
            cleaned_lower = cleaned.lower()
            for prefix in prefixes_to_remove:
                if cleaned_lower.startswith(prefix):
                    # Remove the prefix and any following punctuation/whitespace
                    cleaned = cleaned[len(prefix):].strip(' ,:.-')
                    break
            
            # Take first line only in case there's extra output
            cleaned = cleaned.split('\n')[0].strip()
            # Remove quotes if present
            cleaned = cleaned.strip('"\'')
            
            # If it still looks like conversational text, try to extract a title from it
            # Look for patterns like "Title: ..." or quoted text
            if len(cleaned) > 100 or any(word in cleaned_lower for word in ["i'm", "i am", "i need", "let me", "sure"]):
                # Try to find quoted text
                import re
                quoted = re.findall(r'"([^"]+)"', cleaned)
                if quoted and len(quoted[0]) <= 60:
                    cleaned = quoted[0]
                else:
                    # Try to find text after "Title:" or "title:"
                    title_match = re.search(r'(?:title|title is)[:\s]+(.+?)(?:\.|$)', cleaned, re.IGNORECASE)
                    if title_match:
                        cleaned = title_match.group(1).strip()
            
            # Final cleanup
            cleaned = cleaned.strip('"\'.,:;')
            
            # Truncate to 60 chars
            if len(cleaned) > 60:
                cleaned = cleaned[:57] + "..."
            
            # If we still have a reasonable title, return it
            if cleaned and len(cleaned) > 5 and len(cleaned) <= 60:
                return cleaned
        
        # Fallback: generate a simple title from the prompt
        return f"Research: {prompt[:50]}..."
        
    except Exception as e:
        logger.error(f"Error generating research title: {e}")
        return f"Research: {prompt[:50]}..."


async def _run_agenda_generation_job(
    job_id: str,
    report_id: int,
    prompt: str,
    district: str,
    model_key: str = None
):
    """Run the research agenda generation as a background job."""
    try:
        from tools.research_manager import get_research_manager
        from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
        from agents.langchain_agent.config.tool_config import ToolGroup
        from tools.store_anomalies import get_anomalies
        
        job = job_manager.get_job(job_id)
        manager = get_research_manager()
        
        job.update_progress(10)
        logger.info(f"Agenda generation job {job_id} started for report {report_id}")
        
        # Update status to show we're generating the research plan
        # Get existing metadata and merge
        existing_report = manager.get_report(report_id)
        existing_metadata = existing_report.get('metadata', {}) if existing_report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = "Generating research plan..."
        manager.update_report(
            report_id,
            status="draft",
            metadata=existing_metadata
        )

        enable_web_search = bool(existing_metadata.get("enable_web_search", False))
        
        # Get recent anomalies and metric changes for context
        job.update_progress(20)
        
        anomalies_result = get_anomalies(
            query_type='recent',
            limit=30,
            only_anomalies=True,
            district_filter=district if district != "0" else None
        )
        
        anomalies = anomalies_result.get("items", []) if anomalies_result.get("status") == "success" else []
        
        job.update_progress(30)
        
        # Create agent for agenda generation (full tool set for planning)
        tool_groups = [
            ToolGroup.CORE,
            ToolGroup.DATA_ANALYSIS,
            ToolGroup.ANALYSIS,
            ToolGroup.METRICS,
            ToolGroup.VISUALIZATION,
        ]
        if enable_web_search:
            tool_groups.append(ToolGroup.WEB_SEARCH)

        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=tool_groups,
            include_all_sections=False,
            enable_session_logging=True
        )
        
        job.update_progress(40)
        
        # Build context about available data
        anomaly_context = ""
        if anomalies:
            anomaly_context = "\n\nRecent Anomalies:\n"
            for a in anomalies[:15]:
                metadata = a.get('metadata', {})
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                metric_name = metadata.get('object_name', a.get('group_value', 'Unknown'))
                anomaly_context += f"- {metric_name}: {a.get('difference', 0):.1f} change (ID: {a.get('id')})\n"
        
        # Generate agenda using AI - focused on clear success criteria and research-question iteration
        agenda_prompt = f"""You are creating a research agenda for investigating San Francisco public data. This agenda will drive a research process where each item is processed INDEPENDENTLY in parallel by different workers.

## USER'S RESEARCH QUESTION
"{prompt}"

## CONTEXT
District: {district if district != "0" else "Citywide"}
{anomaly_context}

## YOUR TASK

Create a research agenda that:

1. **Clarifies the research question** - Restate it precisely with measurable success criteria
2. **Defines what a complete answer looks like** - Be specific about what data/findings would satisfy the question
3. **Breaks down into MUTUALLY EXCLUSIVE sub-questions** - Each sub-question must be independently answerable WITHOUT needing results from other items

## CRITICAL REQUIREMENT: MUTUAL EXCLUSIVITY

Each research item will be processed by a SEPARATE worker thread that cannot see results from other workers. Therefore:

❌ **DO NOT CREATE:**
- Items that require ranking "top 5" or "most significant" across ALL metrics (workers can't know what's "top" without analyzing everything)
- Items that depend on results from other items (e.g., "For each top change..." requires knowing what the top changes are first)
- Items that analyze "All Dashboard Metrics" or "All Metrics" - these are too broad and create dependencies
- Items that need to compare results across items (e.g., "How do these changes vary..." when "these" refers to results from another item)

✅ **DO CREATE:**
- Items focused on SPECIFIC metrics or datasets (e.g., "Police Incidents in District 9", "311 Calls for Street Cleaning")
- Items that can be answered independently with their own data sources
- Items that analyze specific time periods, districts, or categories without needing cross-comparison
- Items that can be synthesized LATER after all items complete (synthesis is a separate step)

## RESEARCH PROCESS

The system will:
1. Process each agenda item INDEPENDENTLY in parallel
2. Each worker analyzes its specific metric/dataset without knowing other results
3. After all items complete, results can be synthesized/ranked/compared in a separate step

Therefore, each research item must be:
- **Mutually Exclusive** - Can be answered completely independently, no dependencies on other items
- **Specific and Measurable** - Focuses on a specific metric, dataset, or category (not "all metrics")
- **Data-driven** - What specific metrics/datasets will provide the answer?
- **Self-contained** - Contains all context needed to answer without other items' results
- **Data-efficient** - Requests focused, specific data (one metric, limited time range, essential breakdowns only)
- **Prioritized** - These are the most important independent lines of inquiry

## EXAMPLE OF A GOOD RESEARCH AGENDA (Mutually Exclusive Items)

For the question "Is the Mission District getting safer?":

```json
{{
    "refined_question": "Has the Mission District (District 9) experienced a statistically significant decrease in reported crime incidents over the past 2 years compared to the citywide average?",
    "success_criteria": "The question is answered when we have: (1) Year-over-year crime trend data for District 9, (2) Comparison to citywide trends, (3) Breakdown by crime type to identify which categories are improving/worsening, (4) Statistical context on whether changes are significant or within normal variation.",
    "example_good_answer": "The Mission District has seen a 12% decrease in violent crime over the past 24 months (from 1,847 incidents to 1,625), outperforming the citywide average decrease of 7%. Property crime remains elevated, up 3% vs last year. The violent crime reduction is statistically significant (p<0.05) and driven primarily by a 23% drop in aggravated assaults. However, vehicle break-ins increased 8%, partially offsetting overall safety improvements.",
    "narrative_thread": "Examining whether safety improvements in the Mission are real, sustained, and broad-based across crime categories",
    "items": [
        {{
            "research_question": "What are the violent crime trends in District 9 over the past 24 months?",
            "success_criteria": "Have month-by-month violent crime incident counts for District 9 with year-over-year comparison (focused data request: one metric, one district, 24-month period)",
            "builds_toward": "Establishes baseline trend data for violent crime specifically",
            "metric_name": "Police Incidents - Violent Crime",
            "priority": 1
        }},
        {{
            "research_question": "What are the property crime trends in District 9 over the past 24 months?",
            "success_criteria": "Have month-by-month property crime incident counts for District 9 with year-over-year comparison",
            "builds_toward": "Establishes baseline trend data for property crime specifically",
            "metric_name": "Police Incidents - Property Crime",
            "priority": 2
        }},
        {{
            "research_question": "What are the citywide violent crime trends over the past 24 months?",
            "success_criteria": "Have month-by-month violent crime incident counts citywide with year-over-year comparison",
            "builds_toward": "Provides citywide baseline for comparison with District 9",
            "metric_name": "Police Incidents - Violent Crime",
            "priority": 3
        }},
        {{
            "research_question": "What are the citywide property crime trends over the past 24 months?",
            "success_criteria": "Have month-by-month property crime incident counts citywide with year-over-year comparison",
            "builds_toward": "Provides citywide baseline for comparison with District 9",
            "metric_name": "Police Incidents - Property Crime",
            "priority": 4
        }}
    ]
}}
```

Note: Each item focuses on a SPECIFIC metric and location, can be answered independently, and comparison/synthesis happens AFTER all items complete.

## BAD EXAMPLE (Items with Dependencies - DO NOT DO THIS)

❌ For the question "What are the top metric changes in 2025?":

```json
{{
    "items": [
        {{
            "research_question": "What are the top 5-7 most significant citywide metric changes in 2025?",
            "metric_name": "All Dashboard Metrics",  // ❌ Too broad, requires analyzing everything
            "priority": 1
        }},
        {{
            "research_question": "For each top change, what policy mechanisms drove the shifts?",  // ❌ Depends on item 1's results
            "metric_name": "All Dashboard Metrics",  // ❌ Worker can't know what "top changes" are
            "priority": 2
        }},
        {{
            "research_question": "How do these top changes vary across districts?",  // ❌ Depends on item 1's results
            "metric_name": "All Dashboard Metrics",  // ❌ Worker doesn't know what "these" refers to
            "priority": 3
        }}
    ]
}}
```

✅ BETTER APPROACH (Mutually Exclusive):

```json
{{
    "items": [
        {{
            "research_question": "What are the violent crime trends citywide in 2025?",
            "metric_name": "Police Incidents - Violent Crime",  // ✅ Specific metric, focused request
            "priority": 1
        }},
        {{
            "research_question": "What are the property crime trends citywide in 2025?",
            "metric_name": "Police Incidents - Property Crime",  // ✅ Specific metric, focused request
            "priority": 2
        }},
        {{
            "research_question": "What are the 311 call volume trends citywide in 2025?",
            "metric_name": "311 Calls",  // ✅ Specific metric, focused request
            "priority": 3
        }},
        {{
            "research_question": "What are the building permit trends citywide in 2025?",
            "metric_name": "Building Permits",  // ✅ Specific metric, focused request
            "priority": 4
        }}
    ]
}}
```

After all items complete, a synthesis step can rank/compare them. But each item is independently answerable.

## YOUR RESPONSE

You MUST respond with a valid JSON object in this exact format:

{{
    "refined_question": "A precise, measurable restatement of the user's question",
    "success_criteria": "Specific data points and findings that would fully answer the question",
    "example_good_answer": "A 2-3 sentence example of what a complete, data-rich answer would look like",
    "narrative_thread": "The story we're trying to tell with this research",
    "items": [
        {{
            "research_question": "Specific sub-question to investigate",
            "success_criteria": "What data/findings would answer this sub-question",
            "builds_toward": "How this contributes to the main answer",
            "metric_id": "optional - specific metric ID if known",
            "metric_name": "Name of relevant metric/dataset",
            "anomaly_id": "optional - specific anomaly ID if investigating an anomaly",
            "priority": 1
        }}
    ]
}}

## DATA EFFICIENCY REQUIREMENTS

⚠️ **CRITICAL: Limit Data Requests**

Each research item will query San Francisco data sources. To avoid overwhelming the system and ensure timely results:

- **Focus on specific metrics** - Request data for ONE specific metric at a time, not broad categories
- **Limit time ranges** - Use focused time periods (e.g., "past 24 months" not "all historical data")
- **Avoid excessive breakdowns** - Don't request data broken down by every possible category simultaneously
- **Prioritize essential data** - Only request data that directly answers the research question
- **Avoid "all" queries** - Never request "all categories", "all districts", "all time periods" in a single item

❌ **BAD: "Analyze all business registrations across all districts, all categories, all time periods"**
✅ **GOOD: "Analyze business registrations in District 1 for the past 2 years"**

❌ **BAD: "Get all breakdowns by NAIC code, license code, corridor, and tax status"**
✅ **GOOD: "Get business registrations by NAIC code in District 1 for 2024"**

## FINAL INSTRUCTIONS

- Include 3-7 focused research items
- Each item MUST be mutually exclusive and independently answerable
- Each item MUST focus on a SPECIFIC metric/dataset (never "All Dashboard Metrics" or "All Metrics")
- Items should NOT depend on results from other items
- If ranking/comparison is needed, create separate items for each metric, then synthesis happens later
- **Limit data scope** - Each item should request focused, specific data, not broad exploratory queries
- Quality over quantity - each should be essential to answering the main question

## CRITICAL: JSON VALIDITY

⚠️ **You MUST return valid JSON. Common errors to avoid:**
- ❌ `"priority": 2"` (extra quote) → ✅ `"priority": 2`
- ❌ Missing commas between properties
- ❌ Trailing commas before closing braces/brackets
- ❌ Unescaped quotes in string values
- ❌ Numbers quoted as strings when they should be numbers

**Test your JSON before responding. Every property must be properly formatted with correct quotes, commas, and brackets.**

Respond with ONLY valid JSON, no additional text, no markdown code blocks, just the raw JSON object."""

        job.update_progress(50)
        
        response = ""
        api_error = None
        # Note: explain_change_streaming takes (prompt, metric_details) - prompt is first
        async for chunk in agent.explain_change_streaming(agenda_prompt, {}):
            if isinstance(chunk, dict):
                # Check for error in dict chunks
                if "error" in chunk:
                    api_error = chunk.get("error", "Unknown API error")
                    logger.error(f"API error detected in streaming response: {api_error}")
                    break
                elif chunk.get("type") == "token":
                    response += chunk.get("content", "")
                elif "content" in chunk:
                    response += chunk.get("content", "")
            elif isinstance(chunk, str):
                # Parse SSE format strings
                # Check for error in SSE format
                if chunk.strip().startswith("data:"):
                    try:
                        json_str = chunk.strip()[5:].strip()  # Remove "data:" prefix
                        data = json.loads(json_str)
                        if isinstance(data, dict) and "error" in data:
                            api_error = data.get("error", "Unknown API error")
                            logger.error(f"API error detected in SSE chunk: {api_error}")
                            break
                    except json.JSONDecodeError:
                        pass  # Not JSON, continue parsing as content
                
                content = _parse_sse_chunk(chunk)
                if content:
                    response += content
        
        # Check if we encountered an API error
        if api_error:
            error_message = f"API Error: {api_error}"
            # Provide user-friendly error message for common issues
            if "credit balance" in api_error.lower() or "too low" in api_error.lower():
                error_message = "API Error: Insufficient API credits. Please check your API account balance and add credits to continue."
            elif "rate limit" in api_error.lower():
                error_message = "API Error: Rate limit exceeded. Please wait a moment and try again."
            elif "invalid" in api_error.lower() and "key" in api_error.lower():
                error_message = "API Error: Invalid API key. Please check your API configuration."
            
            logger.error(f"Agenda generation failed due to API error: {api_error}")
            job.fail(error_message)
            manager.update_report(report_id, status="failed", error_message=error_message)
            return
        
        job.update_progress(70)
        
        # Save agent session to Redis for persistence
        agenda_session_id = None
        if hasattr(agent, 'current_session') and agent.current_session:
            agenda_session_id = agent.current_session.session_id
            await save_agent_session_to_redis(agent, agenda_session_id)
        
        # Check if we have any response content
        if not response or not response.strip():
            error_message = "No response received from API. The API may be unavailable or returned an empty response."
            logger.error(f"Agenda generation failed: {error_message}")
            job.fail(error_message)
            manager.update_report(report_id, status="failed", error_message=error_message)
            return
        
        # Parse the JSON response with error recovery
        agenda = None
        json_str = None
        
        try:
            # Find JSON in response
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                agenda = json.loads(json_str)
            else:
                raise ValueError(f"No JSON found in response. Response length: {len(response)}, Preview: {response[:200]}")
        except json.JSONDecodeError as e:
            # Try to fix common JSON errors
            logger.warning(f"Initial JSON parse failed: {e}. Attempting to fix common errors...")
            
            if json_str:
                # Fix common JSON errors
                fixed_json = json_str
                
                # Fix: "priority": 2" -> "priority": 2
                fixed_json = re.sub(r'"priority":\s*(\d+)"', r'"priority": \1', fixed_json)
                
                # Fix: Missing comma before closing brace in objects
                fixed_json = re.sub(r'(\d+)"\s*\n\s*}', r'\1\n    }', fixed_json)
                
                # Fix: Trailing commas before closing braces/brackets
                fixed_json = re.sub(r',(\s*[}\]])', r'\1', fixed_json)
                
                # Fix: Extra quotes around numbers
                fixed_json = re.sub(r':\s*"(\d+)"', r': \1', fixed_json)
                
                # Fix: Missing comma between object properties
                fixed_json = re.sub(r'"\s*\n\s*"', r'",\n        "', fixed_json)
                
                try:
                    agenda = json.loads(fixed_json)
                    logger.info("Successfully fixed JSON parsing errors")
                except json.JSONDecodeError as e2:
                    # Log the problematic section for debugging
                    error_line = getattr(e2, 'lineno', 'unknown')
                    error_col = getattr(e2, 'colno', 'unknown')
                    lines = fixed_json.split('\n')
                    problematic_line = lines[error_line - 1] if error_line <= len(lines) else "N/A"
                    
                    error_message = (
                        f"Failed to parse agenda JSON after error recovery: {str(e2)}. "
                        f"Error at line {error_line}, column {error_col}. "
                        f"Problematic line: {problematic_line[:100]}. "
                        f"Response preview: {response[:1000]}"
                    )
                    logger.error(error_message)
                    job.fail(f"Failed to parse agenda: {str(e2)}")
                    manager.update_report(report_id, status="failed", error_message=str(e2))
                    return
            else:
                error_message = f"Failed to parse agenda JSON: {str(e)}. Response preview: {response[:500]}"
                logger.error(error_message)
                job.fail(f"Failed to parse agenda: {str(e)}")
                manager.update_report(report_id, status="failed", error_message=str(e))
                return
        except Exception as e:
            error_message = f"Failed to parse agenda: {str(e)}"
            logger.error(f"Failed to parse agenda JSON: {e}")
            job.fail(error_message)
            manager.update_report(report_id, status="failed", error_message=error_message)
            return
        
        job.update_progress(80)
        
        # Validate agenda items for mutual exclusivity
        items = agenda.get("items", [])
        validation_warnings = []
        
        for i, item in enumerate(items):
            metric_name = item.get("metric_name", "").lower()
            research_question = item.get("research_question", "").lower()
            
            # Check for overly broad metrics
            if any(phrase in metric_name for phrase in ["all dashboard metrics", "all metrics", "all data", "pending"]):
                validation_warnings.append(
                    f"Item {i+1} uses overly broad metric '{item.get('metric_name')}'. "
                    f"Each item should focus on a SPECIFIC metric/dataset for parallel processing."
                )
            
            # Check for dependencies on other items
            dependency_phrases = [
                "for each top", "for each of the", "these top", "these changes", 
                "the top", "most significant", "ranked by", "the following",
                "based on the above", "from the previous", "as identified in"
            ]
            if any(phrase in research_question for phrase in dependency_phrases):
                validation_warnings.append(
                    f"Item {i+1} appears to depend on results from other items: "
                    f"'{item.get('research_question')}'. Items must be independently answerable."
                )
        
        # Log warnings but don't fail - let the LLM fix it if regenerated
        if validation_warnings:
            logger.warning(f"Agenda validation warnings for report {report_id}:")
            for warning in validation_warnings:
                logger.warning(f"  - {warning}")
            # Store warnings in metadata for potential regeneration
            agenda["_validation_warnings"] = validation_warnings
        
        # Update report with agenda - merge metadata
        existing_report = manager.get_report(report_id)
        existing_metadata = existing_report.get('metadata', {}) if existing_report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = "Research plan ready"
        result = manager.update_report(
            report_id,
            agenda=agenda,
            agenda_json=json.dumps(agenda, indent=2),
            status="agenda_ready",
            metadata=existing_metadata
        )
        
        # Create research items from agenda
        items = agenda.get("items", [])
        for item in items:
            manager.add_research_item(
                report_id=report_id,
                research_question=item.get("research_question", ""),
                metric_id=str(item.get("metric_id")) if item.get("metric_id") else None,
                metric_name=item.get("metric_name"),
                anomaly_id=str(item.get("anomaly_id")) if item.get("anomaly_id") else None,
                reason=item.get("reason") or item.get("builds_toward"),
                priority=item.get("priority", 1),
                success_criteria=item.get("success_criteria"),
                builds_toward=item.get("builds_toward")
            )
        
        job.update_progress(100)
        job.complete({
            "report_id": report_id,
            "items_count": len(items),
            "agenda": agenda
        })
        
        logger.info(f"Agenda generation completed for report {report_id}")
        
    except Exception as e:
        logger.error(f"Error in agenda generation job: {e}", exc_info=True)
        
        # Try to save session to Redis even on failure
        try:
            if 'agent' in locals() and (hasattr(agent, 'current_session') and agent.current_session):
                await save_agent_session_to_redis(agent, None)
        except Exception as save_error:
            logger.warning(f"Failed to save agenda session to Redis after error: {save_error}")
        
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))
        
        from tools.research_manager import get_research_manager
        manager = get_research_manager()
        manager.update_report(report_id, status="failed", error_message=str(e))


async def _run_research_execution_job(
    job_id: str,
    report_id: int,
    model_key: str = None
):
    """Run the research execution as a background job."""
    try:
        from tools.research_manager import get_research_manager
        from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
        from agents.langchain_agent.config.tool_config import ToolGroup
        
        job = job_manager.get_job(job_id)
        manager = get_research_manager()

        if job and getattr(job, "cancel_requested", False):
            raise asyncio.CancelledError()
        
        job.update_progress(5)
        logger.info(f"Research execution job {job_id} started for report {report_id}")
        
        # Get report and items
        report = manager.get_report(report_id)
        items = manager.get_research_items(report_id)
        
        if not items:
            job.fail("No research items found")
            manager.update_report(report_id, status="failed", error_message="No research items")
            return
        
        # Check if web search is enabled from report metadata
        report_metadata = report.get('metadata') or {}
        if isinstance(report_metadata, str):
            try:
                report_metadata = json.loads(report_metadata)
            except:
                report_metadata = {}
        elif not isinstance(report_metadata, dict):
            report_metadata = {}
        
        enable_web_search = bool(report_metadata.get('enable_web_search', False))
        # Breadth-first exploration ("research angles" scouting) is expensive; keep it opt-in.
        enable_breadth_exploration = bool(
            report_metadata.get("enable_breadth_exploration", False)
        )
        
        job.update_progress(10)
        
        total_items = len(items)
        completed_count = {"count": 0}  # Use dict for mutable counter in closure
        
        # Limit concurrent research items to avoid overwhelming LLM API
        max_concurrent = 3
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def research_single_item(item):
            """Research a single item with iteration loop - runs in parallel"""
            async with semaphore:  # Limit concurrency
                if job and getattr(job, "cancel_requested", False):
                    raise asyncio.CancelledError()
                # Defensive check for item
                if not item or not isinstance(item, dict):
                    logger.error(f"Invalid research item: {item}")
                    return {"item_id": "unknown", "question": "Unknown", "error": "Invalid item data"}
                
                item_id = item.get('item_id') or 'unknown'
                research_question = item.get('research_question') or ''
                
                if not research_question:
                    logger.error(f"Research item {item_id} has no research question")
                    return {"item_id": item_id, "question": "", "error": "No research question"}
                
                # Update item status
                manager.update_research_item(
                    report_id,
                    item_id,
                    status="in_progress",
                    started_at=datetime.now()
                )
                
                try:
                    if job and getattr(job, "cancel_requested", False):
                        raise asyncio.CancelledError()
                    # Build research context
                    metric_name = item.get('metric_name', '') or ''
                    metric_id = item.get('metric_id')
                    anomaly_id = item.get('anomaly_id')
                    
                    # Get success criteria from metadata (where it's stored)
                    metadata = item.get('metadata') or {}
                    if isinstance(metadata, str):
                        try:
                            parsed = json.loads(metadata)
                            metadata = parsed if isinstance(parsed, dict) else {}
                        except:
                            metadata = {}
                    elif not isinstance(metadata, dict):
                        metadata = {}
                    
                    success_criteria = (metadata.get('success_criteria') or item.get('success_criteria') or '')
                    builds_toward = (metadata.get('builds_toward') or item.get('builds_toward') or '')
                    
                    context = ""
                    if metric_name:
                        context += f"Metric/Dataset: {metric_name}\n"
                    if metric_id:
                        context += f"Metric ID: {metric_id}\n"
                    if anomaly_id:
                        context += f"Anomaly ID: {anomaly_id}\n"
                    if builds_toward:
                        context += f"Purpose: {builds_toward}\n"
                    
                    # Execute research - single pass (no per-item looping)
                    # Looping evaluation now happens at research question level
                    research_response = ""
                    session_id = None
                    
                    # Build tool groups list - include web search if enabled
                    tool_groups = [
                        ToolGroup.CORE,
                        ToolGroup.DATA_ANALYSIS,
                        ToolGroup.ANALYSIS,
                        ToolGroup.METRICS,
                        ToolGroup.VISUALIZATION
                    ]
                    if enable_web_search:
                        tool_groups.append(ToolGroup.WEB_SEARCH)
                        logger.info(f"Web search enabled for research item {item_id}")
                    
                    # Try to restore session from Redis if we have a session_id from previous attempts
                    # This allows recovery if the process was interrupted
                    conversation_history = []
                    if session_id:
                        try:
                            session_store = get_session_store()
                            session_data = await session_store.get_session(session_id)
                            if session_data and session_data.conversation_history:
                                conversation_history = session_data.conversation_history
                                logger.info(f"Restoring {len(conversation_history)} messages from Redis for research item {item_id}")
                        except Exception as restore_error:
                            logger.warning(f"Failed to restore session from Redis: {restore_error}")
                    
                    # Create agent for research
                    agent = LangChainExplainerAgent(
                        model_key=model_key,
                        tool_groups=tool_groups,
                        include_all_sections=False,
                        enable_session_logging=True
                    )
                    
                    # Restore conversation history if we loaded it from Redis
                    if conversation_history and hasattr(agent, 'restore_conversation_history'):
                        agent.restore_conversation_history(conversation_history)
                        logger.info(f"Restored conversation history for research item {item_id}")
                    
                    # Build research prompt
                    success_section = f"\n\n## SUCCESS CRITERIA\nThis question is answered when: {success_criteria}" if success_criteria else ""
                    
                    research_prompt = f"""## RESEARCH QUESTION
{research_question}

## CONTEXT
{context}
{success_section}

## YOUR TASK
Investigate this question thoroughly using available data tools. Query relevant datasets, analyze patterns, and provide a data-driven answer.

## VISUAL AIDS
You have access to visualization tools that can create helpful charts and maps. When appropriate, use these tools to enhance your findings:

1. **Time Series Charts**: Use `generate_time_series_chart` or `create_chart` tools to visualize trends over time
   - Format: `[CHART:time_series:{{metric_id}}:{{district}}:{{period_type}}]` or `[CHART:time_series_id:{{chart_id}}]`
   - Use for: Showing trends, patterns, seasonal variations, year-over-year comparisons

2. **Anomaly Charts**: Use `generate_anomaly_chart` or `create_anomaly_chart` tools to visualize detected anomalies
   - Format: `[CHART:anomaly:{{anomaly_id}}]`
   - Use for: Highlighting significant changes, outliers, or unusual patterns

3. **Maps**: Use `generate_map` or `create_datawrapper_map` tools to show geographic patterns
   - Format: `[CHART:map:{{map_id}}]`
   - Use for: District comparisons, geographic distributions, spatial patterns

**Important**: When you generate charts or maps, include the placeholder in your response where the visual should appear. The placeholder will be automatically replaced with the actual chart/map in the final report. Place placeholders immediately after the text they illustrate.

## RESPONSE FORMAT
Structure your response EXACTLY as follows:

### Summary
[1-2 sentence direct answer with specific numbers. This should stand alone as a complete answer.]

### Key Findings
[3-5 bullet points with the most important data-driven discoveries. Include chart placeholders here if visuals support the findings.]

### Analysis
[Detailed analysis with evidence, patterns, comparisons, and context. Include chart/map placeholders where visuals enhance understanding.]

### Data Sources
[List the specific datasets, queries, and tools you used]

Focus on SPECIFIC DATA. Avoid vague statements. Include actual numbers, percentages, time periods, and comparisons. Use visual aids (charts, maps, anomaly visualizations) when they help communicate your findings."""

                    # Execute research (single pass)
                    # Accumulate all content from the stream - the final response should be the complete answer
                    chunk_count = 0
                    agent_stopped = False
                    last_save_time = datetime.now()
                    save_interval_seconds = 60  # Save to Redis every 60 seconds during execution
                    
                    async for chunk in agent.explain_change_streaming(
                        research_prompt,
                        {"metric_id": metric_id} if metric_id else {},
                        session_id=session_id  # Pass session_id so agent can track it
                    ):
                        if job and getattr(job, "cancel_requested", False):
                            raise asyncio.CancelledError()
                        chunk_count += 1
                        
                        # Periodically save session to Redis during long-running tasks
                        # This prevents data loss if the process dies mid-execution
                        current_time = datetime.now()
                        time_since_last_save = (current_time - last_save_time).total_seconds()
                        if time_since_last_save >= save_interval_seconds:
                            try:
                                if session_id or (hasattr(agent, 'current_session') and agent.current_session):
                                    await save_agent_session_to_redis(agent, session_id)
                                    last_save_time = current_time
                                    logger.debug(f"Periodic save: Saved session to Redis during research item {item_id} execution")
                            except Exception as save_error:
                                logger.warning(f"Failed periodic save during execution: {save_error}")
                        
                        if chunk is None:
                            continue
                        if isinstance(chunk, dict):
                            if chunk.get("type") == "token":
                                content = chunk.get("content") or ""
                                research_response += content
                            elif chunk.get("type") == "session_id":
                                session_id = chunk.get("session_id")
                            elif chunk.get("agent_stopped"):
                                agent_stopped = True
                            elif chunk.get("completed"):
                                logger.info(f"Received completion signal for item {item_id}")
                                if chunk.get("session_id") and not session_id:
                                    session_id = chunk.get("session_id")
                            elif "content" in chunk:
                                content = chunk.get("content") or ""
                                research_response += content
                        elif isinstance(chunk, str):
                            # Check for completion signal
                            if '"completed": true' in chunk.lower() or '"completed":true' in chunk.lower():
                                logger.info(f"Received completion signal in SSE format for item {item_id}")
                            sse = _parse_sse_json(chunk)
                            if sse:
                                if sse.get("session_id") and not session_id:
                                    session_id = sse.get("session_id")
                                if sse.get("agent_stopped"):
                                    agent_stopped = True

                            content = _parse_sse_chunk(chunk)
                            if content:
                                research_response += content
                    
                    logger.info(f"Research streaming completed for item {item_id}: {chunk_count} chunks processed, response length: {len(research_response)}")
                    
                    # Save agent session to Redis for persistence
                    if session_id or (hasattr(agent, 'current_session') and agent.current_session):
                        await save_agent_session_to_redis(agent, session_id)
                    
                    # Clean up the response
                    final_response = research_response.strip()

                    # If the agent hit a stop condition, do a sync fallback to get a full answer.
                    if agent_stopped:
                        logger.warning(
                            f"Agent stopped early for item {item_id}; retrying with sync invoke"
                        )
                        retry_prompt = (
                            research_prompt
                            + "\n\nIMPORTANT: Provide the COMPLETE final answer now. "
                            "Do not narrate your steps. Use the required headings."
                        )
                        retry_agent = LangChainExplainerAgent(
                            model_key=model_key,
                            tool_groups=tool_groups,
                            include_all_sections=False,
                            enable_session_logging=True,
                        )
                        retry = retry_agent.explain_change_sync(
                            retry_prompt,
                            {"metric_id": metric_id} if metric_id else {},
                            session_id=session_id,
                        )
                        if retry.get("success") and retry.get("explanation"):
                            final_response = str(retry.get("explanation")).strip()
                            session_id = retry.get("session_id") or session_id
                            # Save retry agent session to Redis
                            await save_agent_session_to_redis(retry_agent, session_id)
                    
                    # If the response is very short (< 150 chars) and looks like just an acknowledgment,
                    # log a warning (this shouldn't happen if the agent completed properly)
                    if len(final_response) < 150:
                        acknowledgment_patterns = [
                            "sure, i'll", "sure i'll", "i'll look into", "i will look into",
                            "i'll investigate", "i will investigate", "let me look", "let me check"
                        ]
                        response_lower = final_response.lower()
                        if any(pattern in response_lower for pattern in acknowledgment_patterns):
                            logger.error(f"⚠️ Research item {item_id} only returned an acknowledgment, not the actual answer!")
                            logger.error(f"Acknowledgment text: {final_response[:200]}")
                            logger.error(f"This suggests the agent may not have completed the research properly")
                            logger.error(f"Total chunks received: {chunk_count}, Total response length: {len(final_response)}")
                            # Fallback to sync invoke to force a full answer
                            retry_prompt = (
                                research_prompt
                                + "\n\nIMPORTANT: Provide the COMPLETE final answer now. "
                                "Do not narrate your steps. Use the required headings."
                            )
                            retry_agent = LangChainExplainerAgent(
                                model_key=model_key,
                                tool_groups=tool_groups,
                                include_all_sections=False,
                                enable_session_logging=True,
                            )
                            retry = retry_agent.explain_change_sync(
                                retry_prompt,
                                {"metric_id": metric_id} if metric_id else {},
                                session_id=session_id,
                            )
                            if retry.get("success") and retry.get("explanation"):
                                final_response = str(retry.get("explanation")).strip()
                                session_id = retry.get("session_id") or session_id
                                # Save retry agent session to Redis
                                await save_agent_session_to_redis(retry_agent, session_id)
                    
                    # Log the response length for debugging
                    logger.info(f"Research item {item_id} completed with final response length: {len(final_response)}")
                    if len(final_response) > 0:
                        logger.info(f"First 200 chars of response: {final_response[:200]}...")
                    
                    # Save agent session to Redis one final time before completing
                    # This ensures we have the latest state even if something fails after this
                    if session_id or (hasattr(agent, 'current_session') and agent.current_session):
                        await save_agent_session_to_redis(agent, session_id)
                    
                    # Update item with result (preserve existing metadata fields)
                    merged_metadata = dict(metadata) if isinstance(metadata, dict) else {}
                    merged_metadata["research_question"] = research_question
                    # Persist session_id in metadata too (UI/back-compat: some views look there)
                    merged_metadata["session_id"] = session_id
                    manager.update_research_item(
                        report_id,
                        item_id,
                        status="completed",
                        result=final_response,
                        session_id=session_id,
                        metadata=merged_metadata,
                        completed_at=datetime.now()
                    )
                    
                    # Update progress counter
                    completed_count["count"] += 1
                    progress = 10 + int((completed_count["count"] / total_items) * 70)
                    job.update_progress(progress)
                    
                    return {
                        "item_id": item_id,
                        "question": research_question,
                        "answer": research_response
                    }
                    
                except asyncio.CancelledError:
                    manager.update_research_item(
                        report_id,
                        item_id,
                        status="failed",
                        error_message="Cancelled by user",
                        completed_at=datetime.now(),
                    )
                    raise
                except Exception as e:
                    logger.error(f"Error researching item {item_id}: {e}", exc_info=True)
                    
                    # Try to save session to Redis even on failure
                    # This helps with debugging - we can see what the agent was doing when it failed
                    try:
                        if 'agent' in locals() and (session_id or (hasattr(agent, 'current_session') and agent.current_session)):
                            await save_agent_session_to_redis(agent, session_id)
                    except Exception as save_error:
                        logger.warning(f"Failed to save session to Redis after error: {save_error}")
                    
                    manager.update_research_item(
                        report_id,
                        item_id,
                        status="failed",
                        error_message=str(e),
                        completed_at=datetime.now()
                    )
                    
                    # Update progress counter even on failure
                    completed_count["count"] += 1
                    progress = 10 + int((completed_count["count"] / total_items) * 70)
                    job.update_progress(progress)
                    
                    return {
                        "item_id": item_id,
                        "question": research_question,
                        "error": str(e)
                    }
        
        # ========================================================================
        # BREADTH-FIRST EXPLORATION PHASE (Deep Research style) - OPTIONAL
        # ========================================================================
        # This is intentionally opt-in because it can be slow + token-heavy.
        if enable_breadth_exploration:
            logger.info(f"Starting breadth-first exploration for report {report_id}")
            # Merge metadata
            existing_metadata = report.get('metadata', {})
            if isinstance(existing_metadata, str):
                try:
                    existing_metadata = json.loads(existing_metadata)
                except:
                    existing_metadata = {}
            elif not isinstance(existing_metadata, dict):
                existing_metadata = {}
            
            existing_metadata["status_detail"] = "Exploring research angles..."
            manager.update_report(
                report_id,
                metadata=existing_metadata
            )
            job.update_progress(15)
            
            agenda = report.get('agenda', {})
            if isinstance(agenda, str):
                try:
                    agenda = json.loads(agenda)
                except:
                    agenda = {}
            
            refined_question = agenda.get('refined_question', report.get('original_prompt', ''))
            success_criteria = agenda.get('success_criteria', '')
            
            # Generate breadth-first exploration questions
            exploration_prompt = f"""## BREADTH-FIRST EXPLORATION

### Main Research Question
{refined_question}

### Success Criteria
{success_criteria if success_criteria else "Provide comprehensive, data-driven findings"}

### Your Task
Generate 8-12 diverse exploration questions that cover different angles, perspectives, and approaches to this research question. Think broadly:

1. **Different data sources** - What datasets might be relevant?
2. **Different timeframes** - Historical trends, recent changes, seasonal patterns?
3. **Different geographic scopes** - District comparisons, citywide context, neighborhood-level?
4. **Different analytical angles** - Causal factors, correlations, comparisons, trends?
5. **Different stakeholder perspectives** - Who is affected? What are the implications?
6. **Different metrics** - What measurements would help answer this?

These should be QUICK exploration questions (not deep dives) to map the research landscape. Each should be answerable with a brief investigation.

### Response Format
Provide a JSON array of exploration questions (8-12 questions):

```json
[
  "Question 1: Brief exploration question covering angle A",
  "Question 2: Brief exploration question covering angle B",
  ...
]
```

Focus on BREADTH - cover many angles quickly, not depth. Respond with ONLY the JSON array, no additional text."""

            exploration_agent = LangChainExplainerAgent(
                model_key=model_key,
                tool_groups=[ToolGroup.CORE, ToolGroup.METRICS],  # Minimal tools for exploration planning
                include_all_sections=False,
                enable_session_logging=False
            )
        
            exploration_response = ""
            async for chunk in exploration_agent.explain_change_streaming(exploration_prompt, {}):
                if isinstance(chunk, dict):
                    if chunk.get("type") == "token":
                        exploration_response += chunk.get("content", "")
                    elif "content" in chunk:
                        exploration_response += chunk.get("content", "")
                elif isinstance(chunk, str):
                    content = _parse_sse_chunk(chunk)
                    if content:
                        exploration_response += content
        
            # Parse exploration questions
            exploration_questions = []
            try:
                import re
                json_match = re.search(r'\[(.*?)\]', exploration_response, re.DOTALL)
                if json_match:
                    # Try to parse as JSON array
                    try:
                        exploration_questions = json.loads(exploration_response)
                    except:
                        # Extract quoted strings
                        quoted = re.findall(r'"([^"]+)"', exploration_response)
                        exploration_questions = quoted[:12]  # Limit to 12
            except Exception as e:
                logger.warning(f"Could not parse exploration questions: {e}, using original agenda items")
                exploration_questions = []
        
            # If we got exploration questions, do quick research on them
            exploration_results = []
            if exploration_questions and len(exploration_questions) > 0:
                logger.info(f"Generated {len(exploration_questions)} exploration questions, doing quick research")
                job.update_progress(20)
            
                # Create quick exploration items (shallow research)
                exploration_items = []
                for i, eq in enumerate(exploration_questions[:12]):  # Limit to 12
                    if eq and isinstance(eq, str) and len(eq.strip()) > 10:
                        # Strip "Question X:" prefix if present
                        cleaned_question = eq.strip()
                        # Remove patterns like "Question 1:", "Question 2:", etc.
                        cleaned_question = re.sub(r'^Question\s+\d+:\s*', '', cleaned_question, flags=re.IGNORECASE)
                        cleaned_question = cleaned_question.strip()
                        
                        if len(cleaned_question) > 10:
                            result = manager.add_research_item(
                                report_id=report_id,
                                research_question=cleaned_question,
                                metric_id=None,
                                metric_name=None,
                                anomaly_id=None,
                                reason=f"Breadth-first exploration - angle {i+1}",
                                priority=100 + i,  # Lower priority (will be replaced by prioritized items)
                                success_criteria="Quick exploration to identify if this angle is promising",
                                builds_toward=refined_question,
                                metadata={
                                    "phase": "exploration",
                                    "added_by": "exploration",
                                    "is_exploration": True,
                                    "exploration_angle": i + 1,
                                },
                            )
                        if result.get("status") == "success":
                            item_id = result.get("item_id")
                            exploration_items.append({
                                'item_id': item_id,
                                'research_question': cleaned_question,
                                'exploration_angle': i + 1,
                                'is_exploration': True
                            })
            
            # Do quick research on exploration items (shallow pass)
            async def quick_exploration(item):
                """Quick exploration pass - shallow research to map the landscape"""
                async with semaphore:  # Limit concurrency (shared with deep research)
                    item_id = item.get('item_id')
                    research_question = item.get('research_question', '')
                    exploration_angle = item.get('exploration_angle')
                
                    manager.update_research_item(
                        report_id,
                        item_id,
                        status="in_progress",
                        started_at=datetime.now()
                    )
                
                    try:
                        # Quick exploration prompt - shorter, focused on getting a sense of the angle
                        quick_prompt = f"""## QUICK EXPLORATION

### Question
{research_question}

### Context
This is a breadth-first exploration to map the research landscape. Provide a brief (2-3 paragraph) response that:
1. Indicates whether this angle is promising for the main research question
2. Identifies what data/metrics would be needed for deeper investigation
3. Notes any initial findings or patterns

Keep it concise - this is scouting, not deep research."""

                        agent = LangChainExplainerAgent(
                            model_key=model_key,
                            tool_groups=[ToolGroup.CORE, ToolGroup.METRICS],  # Minimal tools for quick exploration
                            include_all_sections=False,
                            enable_session_logging=False
                        )
                    
                        quick_response = ""
                        async for chunk in agent.explain_change_streaming(quick_prompt, {}):
                            if isinstance(chunk, dict):
                                if chunk.get("type") == "token":
                                    quick_response += chunk.get("content", "")
                                elif "content" in chunk:
                                    quick_response += chunk.get("content", "")
                            elif isinstance(chunk, str):
                                content = _parse_sse_chunk(chunk)
                                if content:
                                    quick_response += content
                    
                        manager.update_research_item(
                            report_id,
                            item_id,
                            status="completed",
                            result=quick_response,
                            metadata={
                                "phase": "exploration",
                                "added_by": "exploration",
                                "is_exploration": True,
                                "exploration_angle": exploration_angle,
                                "research_question": research_question,
                            },
                            completed_at=datetime.now()
                        )
                    
                        return {
                            "item_id": item_id,
                            "question": research_question,
                            "answer": quick_response,
                            "is_exploration": True
                        }
                    except Exception as e:
                        logger.error(f"Error in quick exploration {item_id}: {e}")
                        manager.update_research_item(
                            report_id,
                            item_id,
                            status="failed",
                            error_message=str(e),
                            completed_at=datetime.now()
                        )
                        return {
                            "item_id": item_id,
                            "question": research_question,
                            "error": str(e),
                            "is_exploration": True
                        }
            
                # Run exploration in parallel
                exploration_results = await asyncio.gather(*[quick_exploration(item) for item in exploration_items])
                job.update_progress(35)
            
                # Analyze exploration results and prioritize
                logger.info(f"Analyzing {len(exploration_results)} exploration results to prioritize deep research")
            
                exploration_summary = ""
                for er in exploration_results:
                    if er.get('answer'):
                        exploration_summary += f"\n### {er.get('question')}\n{er.get('answer')[:500]}\n"
            
                prioritization_prompt = f"""## PRIORITIZE RESEARCH ANGLES

### Main Research Question
{refined_question}

### Success Criteria
{success_criteria if success_criteria else "Provide comprehensive, data-driven findings"}

### Exploration Results
{exploration_summary[:3000]}

### Your Task
Based on the breadth-first exploration, identify which angles are most promising and should receive deep research. Consider:
1. **Relevance** - Which angles directly address the main research question?
2. **Data availability** - Which angles have accessible, relevant data?
3. **Impact** - Which angles would provide the most valuable insights?
4. **Uniqueness** - Which angles offer distinct perspectives not covered elsewhere?

### Response Format
PRIORITIZED_ANGLES: [JSON array of 3-5 research questions that should receive deep investigation, ordered by priority]
REASONING: [Brief explanation of why these angles were prioritized]

Respond with ONLY this format, no additional text."""

                prioritization_response = ""
                prioritization_agent = LangChainExplainerAgent(
                    model_key=model_key,
                    tool_groups=[],  # No tools for prioritization
                    include_all_sections=False,
                    enable_session_logging=False
                )
            
                async for chunk in prioritization_agent.explain_change_streaming(prioritization_prompt, {}):
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "token":
                            prioritization_response += chunk.get("content", "")
                        elif "content" in chunk:
                            prioritization_response += chunk.get("content", "")
                    elif isinstance(chunk, str):
                        content = _parse_sse_chunk(chunk)
                        if content:
                            prioritization_response += content
            
                # Extract prioritized angles (optional - we'll use the original agenda items as prioritized)
                # The exploration helps inform the deep research, but we proceed with the agenda items
                logger.info("Breadth-first exploration complete, proceeding to deep research phase")
                # Merge metadata
                existing_metadata = report.get('metadata', {})
                if isinstance(existing_metadata, str):
                    try:
                        existing_metadata = json.loads(existing_metadata)
                    except:
                        existing_metadata = {}
                elif not isinstance(existing_metadata, dict):
                    existing_metadata = {}
                
                existing_metadata["status_detail"] = "Exploration complete, starting deep research..."
                manager.update_report(
                    report_id,
                    metadata=existing_metadata
                )
        else:
            logger.info(
                f"Skipping breadth-first exploration for report {report_id} "
                "(enable_breadth_exploration=false)"
            )
            existing_metadata = report.get('metadata', {})
            if isinstance(existing_metadata, str):
                try:
                    existing_metadata = json.loads(existing_metadata)
                except:
                    existing_metadata = {}
            elif not isinstance(existing_metadata, dict):
                existing_metadata = {}

            existing_metadata["status_detail"] = (
                "Skipping exploration, starting deep research..."
            )
            manager.update_report(report_id, metadata=existing_metadata)
            job.update_progress(15)
        
        job.update_progress(40)
        
        # ========================================================================
        # DEEP RESEARCH PHASE
        # ========================================================================
        # Now do deep research on the prioritized agenda items
        logger.info(f"Starting deep research phase for report {report_id}")
        # Merge metadata
        existing_metadata = report.get('metadata', {})
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = f"Researching {len(items)} key questions..."
        manager.update_report(
            report_id,
            metadata=existing_metadata
        )
        
        # Run all research items in parallel (deep research)
        results = await asyncio.gather(*[research_single_item(item) for item in items])
        
        job.update_progress(70)
        
        # Research question-level evaluation loop
        # Evaluate whether we need NEW research items (not just repeating/deepening existing answers)
        max_question_iterations = 3
        question_iteration = 0
        all_research_items = items.copy()
        
        while question_iteration < max_question_iterations:
            question_iteration += 1
            logger.info(f"Research question evaluation iteration {question_iteration} for report {report_id}")
            
            # Update status to show evaluation phase - merge metadata
            current_report = manager.get_report(report_id)
            existing_metadata = current_report.get('metadata', {}) if current_report else {}
            if isinstance(existing_metadata, str):
                try:
                    existing_metadata = json.loads(existing_metadata)
                except:
                    existing_metadata = {}
            elif not isinstance(existing_metadata, dict):
                existing_metadata = {}
            
            existing_metadata["status_detail"] = f"Evaluating findings (iteration {question_iteration})..."
            manager.update_report(
                report_id,
                metadata=existing_metadata
            )
            
            # Get current state of all research items
            current_items = manager.get_research_items(report_id)
            completed_items = [item for item in current_items if item.get('status') == 'completed']
            
            if not completed_items:
                break  # No completed items to evaluate
            
            # Get agenda for context
            agenda = report.get('agenda', {})
            if isinstance(agenda, str):
                try:
                    agenda = json.loads(agenda)
                except:
                    agenda = {}
            
            refined_question = agenda.get('refined_question', report.get('original_prompt', ''))
            success_criteria = agenda.get('success_criteria', '')
            
            # Build summary of all completed research
            research_summary = ""
            for item in completed_items:
                research_summary += f"\n### {item.get('research_question', 'Unknown')}\n"
                research_summary += f"{item.get('result', '')[:1000]}\n"
            
            # Evaluate at research question level
            eval_prompt = f"""## EVALUATE RESEARCH QUESTION COMPLETENESS

### Main Research Question
{refined_question}

### Success Criteria
{success_criteria if success_criteria else "Provide comprehensive, data-driven findings that fully answer the research question"}

### Current Research Findings
{research_summary[:4000]}

### Your Task
Evaluate whether the answers we have suggest NEW research questions that should be added as new research items, as opposed to essentially repeating or deepening the existing answers.

Consider:
1. **New angles** - Are there different aspects of the research question we haven't explored?
2. **Missing context** - Are there related questions that would provide important context?
3. **Gaps in understanding** - Do the current answers reveal gaps that need separate investigation?
4. **Avoid repetition** - Don't suggest questions that would just deepen existing answers

### Response Format
NEED_NEW_ITEMS: [YES or NO]
NEW_ITEMS: [If YES, provide 1-3 new research questions as a JSON array of strings. Each should be a distinct, new angle on the research question. If NO, write "[]"]
REASON: [1-2 sentences explaining why new items are needed or why the research is complete]"""

            eval_response = ""
            eval_agent = LangChainExplainerAgent(
                model_key=model_key,
                tool_groups=[],  # No tools for evaluation
                include_all_sections=False,
                enable_session_logging=False
            )
            
            async for chunk in eval_agent.explain_change_streaming(eval_prompt, {}):
                if isinstance(chunk, dict):
                    if chunk.get("type") == "token":
                        eval_response += chunk.get("content", "")
                    elif "content" in chunk:
                        eval_response += chunk.get("content", "")
                elif isinstance(chunk, str):
                    content = _parse_sse_chunk(chunk)
                    if content:
                        eval_response += content
            
            # Parse evaluation response
            need_new_items = "NEED_NEW_ITEMS: YES" in eval_response.upper() or "NEED_NEW_ITEMS:YES" in eval_response.upper()
            
            if not need_new_items:
                logger.info(f"Research question evaluation complete - no new items needed (iteration {question_iteration})")
                break
            
            # Extract new items
            new_items = []
            try:
                # Try to find JSON array in response
                import re
                json_match = re.search(r'NEW_ITEMS:\s*(\[.*?\])', eval_response, re.DOTALL)
                if json_match:
                    new_items_json = json_match.group(1)
                    new_items = json.loads(new_items_json)
                else:
                    # Fallback: look for array-like structure
                    array_match = re.search(r'\[(.*?)\]', eval_response, re.DOTALL)
                    if array_match:
                        items_text = array_match.group(1)
                        # Try to parse as JSON array
                        try:
                            new_items = json.loads(f"[{items_text}]")
                        except:
                            # Extract quoted strings
                            quoted = re.findall(r'"([^"]+)"', items_text)
                            new_items = quoted
            except Exception as e:
                logger.warning(f"Could not parse new items from evaluation response: {e}")
                break
            
            if not new_items or len(new_items) == 0:
                logger.info(f"No new items extracted from evaluation (iteration {question_iteration})")
                break
            
            logger.info(f"Adding {len(new_items)} new research items (iteration {question_iteration})")
            
            # Add new research items
            for new_question in new_items[:3]:  # Limit to 3 new items per iteration
                if new_question and isinstance(new_question, str) and len(new_question.strip()) > 10:
                    # Store iteration number in metadata
                    item_metadata = {
                        "phase": "evaluation",
                        "evaluation_iteration": question_iteration,
                        "added_by": "evaluation",
                        "reason": f"Suggested by research question evaluation (iteration {question_iteration})",
                    }
                    
                    manager.add_research_item(
                        report_id=report_id,
                        research_question=new_question.strip(),
                        metric_id=None,
                        metric_name=None,
                        anomaly_id=None,
                        reason=f"Suggested by research question evaluation (iteration {question_iteration})",
                        priority=len(current_items) + 1,  # Lower priority than original items
                        success_criteria=success_criteria,
                        builds_toward=refined_question,
                        metadata=item_metadata
                    )
            
            # Research the new items
            new_items_list = manager.get_research_items(report_id)
            new_items_to_research = [
                item for item in new_items_list 
                if item.get('status') == 'pending' and item.get('item_id') not in [i.get('item_id') for i in all_research_items]
            ]
            
            if new_items_to_research:
                all_research_items.extend(new_items_to_research)
                new_results = await asyncio.gather(*[research_single_item(item) for item in new_items_to_research])
                results.extend(new_results)
                total_items = len(all_research_items)
            else:
                break
        
        job.update_progress(85)
        
        # Synthesize final report - merge metadata
        current_report = manager.get_report(report_id)
        existing_metadata = current_report.get('metadata', {}) if current_report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = "Synthesizing final report..."
        manager.update_report(
            report_id,
            status="synthesizing",
            metadata=existing_metadata
        )
        
        agenda = report.get('agenda', {})
        if isinstance(agenda, str):
            agenda = json.loads(agenda)
        
        narrative = agenda.get('narrative_thread', report.get('original_prompt', ''))
        
        synthesis_prompt = f"""Synthesize the following research findings into a cohesive report.

RESEARCH FOCUS: {narrative}

ORIGINAL QUESTION: {report.get('original_prompt', '')}

RESEARCH FINDINGS:
"""
        
        for r in results:
            if r.get('answer'):
                synthesis_prompt += f"\n### {r.get('question')}\n{r.get('answer')}\n"
            elif r.get('error'):
                synthesis_prompt += f"\n### {r.get('question')}\n[Research failed: {r.get('error')}]\n"
        
        synthesis_prompt += """

## YOUR TASK

Transform these research findings into a high-impact, informative, and factually grounded blog post. Your goal is to create content that is:

- **Easy to read**: Use clear, accessible language. Avoid jargon. Write for a general audience interested in civic data.
- **Visually appealing**: Strategically place charts, maps, and visualizations to illustrate key points and make the content engaging.
- **Factually grounded**: Every claim must be supported by the research findings. Use specific numbers, percentages, and data points.
- **Compelling**: Tell a story with the data. Make it interesting and relevant to readers' lives.

## WRITING STYLE

- Use active voice and engaging prose
- Break up long paragraphs with subheadings, bullet points, and visuals
- Start with a hook that draws readers in
- Use transitions to connect ideas smoothly
- End with clear takeaways or implications
- Write in a conversational but professional tone

## VISUAL AIDS

The research findings may include chart placeholders that should be preserved and strategically placed. These placeholders will be automatically converted to actual charts/maps in the final report:

- **Time Series Charts**: `[CHART:time_series:{{metric_id}}:{{district}}:{{period_type}}]` or `[CHART:time_series_id:{{chart_id}}]`
- **Anomaly Charts**: `[CHART:anomaly:{{anomaly_id}}]`
- **Maps**: `[CHART:map:{{map_id}}]`

**Handling Placeholders:**
- **PRESERVE** all chart/map placeholders from the research findings
- Place them immediately after the text they illustrate - visuals should support and enhance the narrative
- Use visuals to break up text and make the post more scannable
- If multiple findings reference the same chart, consolidate to a single placeholder
- Add brief, descriptive context around placeholders to help readers understand what they're seeing
- Don't modify the placeholder format - keep them exactly as `[CHART:...]`

## BLOG POST STRUCTURE

Return the blog post as clean, valid HTML (NOT markdown). Use semantic HTML tags:
- Use <h3> for section headings (Introduction, Key Findings, etc.)
- Use <p> for paragraphs
- Use <ul><li> for bullet lists
- Keep chart placeholders exactly as raw text like [CHART:...]
- Do not wrap the entire document in <html> or <body>; return only the HTML fragment for the article.

Suggested structure:

<h3>Introduction</h3>
[Engaging opening that hooks the reader. What question are we answering? Why does it matter?]

<h3>Key Findings</h3>
[Main discoveries presented clearly with supporting data. Use visuals liberally here - charts and maps make findings more compelling and easier to understand.]

<h3>The Story in the Data</h3>
[Weave together the findings into a coherent narrative. Show patterns, connections, and what the data reveals. Integrate charts/maps where they illustrate your points.]

<h3>What This Means</h3>
[Clear conclusions and implications. What should readers take away?]

<h3>Looking Ahead</h3>
[Optional: Areas for further investigation or questions that remain]

Remember: This is a blog post, not a dry research paper. Make it engaging, visual, and accessible while maintaining factual accuracy. Preserve all chart placeholders and position them where they enhance understanding and visual appeal."""

        # Generate synthesis
        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=[],  # No tools needed for synthesis
            include_all_sections=False,
            enable_session_logging=True
        )
        
        final_report = ""
        # Note: explain_change_streaming takes (prompt, metric_details) - prompt is first
        async for chunk in agent.explain_change_streaming(synthesis_prompt, {}):
            if isinstance(chunk, dict):
                if chunk.get("type") == "token":
                    final_report += chunk.get("content", "")
                elif "content" in chunk:
                    final_report += chunk.get("content", "")
            elif isinstance(chunk, str):
                # Parse SSE format strings
                content = _parse_sse_chunk(chunk)
                if content:
                    final_report += content
        
        job.update_progress(90)
        
        # Generate HTML version (prefer model-provided HTML; fallback if markdown returned)
        final_report_html = final_report
        if not (isinstance(final_report_html, str) and "<" in final_report_html and ">" in final_report_html):
            import markdown2

            final_report_html = markdown2.markdown(
                final_report,
                extras=["fenced-code-blocks", "tables", "header-ids"],
            )
        
        # Generate social media content
        job.update_progress(92)
        social_media_prompt = f"""Based on the following research report, create social media content for sharing.

RESEARCH TITLE: {report.get('title', '')}
RESEARCH FOCUS: {narrative}

FULL REPORT:
{final_report[:3000]}

## YOUR TASK

Create engaging social media content that highlights the key findings. Your response must be valid JSON in this exact format:

{{
    "title": "A snappy, attention-grabbing title (max 80 characters)",
    "text": "2-3 engaging sentences that summarize the most interesting finding (max 280 characters). Make it compelling and shareable.",
    "visual_elements": [
        {{
            "type": "chart|map",
            "placeholder": "[CHART:time_series:123:0:month] or [CHART:map:456]",
            "description": "Brief description of what this visual shows"
        }}
    ]
}}

**Requirements:**
- Title: Catchy, clear, and under 80 characters
- Text: 2-3 sentences that make people want to read more. Include a key statistic or finding. Max 280 characters.
- Visual elements: Include 1-3 chart/map placeholders from the report that best illustrate the findings. Extract the exact placeholder format like [CHART:...] from the report.
- Focus on the most surprising or important finding

Respond with ONLY valid JSON, no additional text."""
        
        social_media_response = ""
        async for chunk in agent.explain_change_streaming(social_media_prompt, {}):
            if isinstance(chunk, dict):
                if chunk.get("type") == "token":
                    social_media_response += chunk.get("content", "")
                elif "content" in chunk:
                    social_media_response += chunk.get("content", "")
            elif isinstance(chunk, str):
                content = _parse_sse_chunk(chunk)
                if content:
                    social_media_response += content
        
        # Parse social media content
        social_media_content = None
        try:
            json_start = social_media_response.find('{')
            json_end = social_media_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = social_media_response[json_start:json_end]
                social_media_content = json.loads(json_str)
            else:
                logger.warning(f"Could not parse social media content JSON for report {report_id}")
        except Exception as e:
            logger.warning(f"Error parsing social media content: {e}")
        
        job.update_progress(95)
        
        # Update report with final content - clear status detail on completion
        current_report = manager.get_report(report_id)
        existing_metadata = current_report.get('metadata', {}) if current_report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        
        existing_metadata["status_detail"] = "Completed"
        manager.update_report(
            report_id,
            status="completed",
            final_report=final_report,
            final_report_html=final_report_html,
            social_media_content=social_media_content,
            metadata=existing_metadata
        )
        
        job.update_progress(100)
        job.complete({
            "report_id": report_id,
            "items_completed": completed_count["count"],
            "items_total": total_items
        })
        
        logger.info(f"Research execution completed for report {report_id}")
        
    except asyncio.CancelledError:
        # Job cancelled: mark report + job and exit early
        logger.warning(f"Research execution job {job_id} cancelled for report {report_id}")
        job = job_manager.get_job(job_id)
        if job:
            job.cancel(reason="Cancelled by user")
        from tools.research_manager import get_research_manager
        manager = get_research_manager()
        report = manager.get_report(report_id) or {}
        md = report.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = {}
        if not isinstance(md, dict):
            md = {}
        md["status_detail"] = "Cancelled by user"
        md["cancelled_at"] = datetime.now().isoformat()
        manager.update_report(report_id, status="cancelled", metadata=md)
        return
    except Exception as e:
        logger.error(f"Error in research execution job: {e}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))
        
        from tools.research_manager import get_research_manager
        manager = get_research_manager()
        manager.update_report(report_id, status="failed", error_message=str(e))


async def _run_research_synthesis_only_job(
    job_id: str, report_id: int, model_key: str = None
) -> None:
    """Background job: only rebuild final report from existing item results."""
    try:
        from tools.research_manager import get_research_manager
        from agents.langchain_agent.explainer_agent import LangChainExplainerAgent

        job = job_manager.get_job(job_id)
        manager = get_research_manager()

        report = manager.get_report(report_id)
        if not report:
            if job:
                job.fail("Research report not found")
            return

        items = manager.get_research_items(report_id)
        completed_items = [
            i for i in items if i.get("status") == "completed" and i.get("result")
        ]
        if not completed_items:
            if job:
                job.fail("No completed items to synthesize")
            manager.update_report(
                report_id,
                status="failed",
                error_message="No completed items to synthesize",
            )
            return

        if job:
            job.update_progress(10)

        # Build narrative context
        agenda = report.get("agenda", {})
        if isinstance(agenda, str):
            try:
                agenda = json.loads(agenda)
            except Exception:
                agenda = {}
        narrative = agenda.get("narrative_thread", report.get("original_prompt", ""))

        synthesis_prompt = f"""Synthesize the following research findings into a cohesive report.

RESEARCH FOCUS: {narrative}

ORIGINAL QUESTION: {report.get('original_prompt', '')}

RESEARCH FINDINGS:
"""

        for item in completed_items:
            synthesis_prompt += f"\n### {item.get('research_question', 'Unknown')}\n{item.get('result', '')}\n"

        synthesis_prompt += """

## YOUR TASK

Return a high-impact, informative, and factually grounded blog post as clean, valid HTML (NOT markdown).

HTML rules:
- Use <h3> for section headings
- Use <p> for paragraphs
- Use <ul><li> for bullet lists
- Keep chart placeholders exactly as raw text like [CHART:...]
- Do not wrap the entire document in <html> or <body>; return only the HTML fragment for the article.
"""

        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=[],
            include_all_sections=False,
            enable_session_logging=True,
        )

        final_report = ""
        async for chunk in agent.explain_change_streaming(synthesis_prompt, {}):
            if isinstance(chunk, dict):
                if chunk.get("type") == "token":
                    final_report += chunk.get("content", "")
                elif "content" in chunk:
                    final_report += chunk.get("content", "")
            elif isinstance(chunk, str):
                content = _parse_sse_chunk(chunk)
                if content:
                    final_report += content

        if job:
            job.update_progress(85)

        final_report_html = final_report
        if not (isinstance(final_report_html, str) and "<" in final_report_html and ">" in final_report_html):
            import markdown2

            final_report_html = markdown2.markdown(
                final_report,
                extras=["fenced-code-blocks", "tables", "header-ids"],
            )

        # Generate social media content
        if job:
            job.update_progress(90)
        
        social_media_prompt = f"""Based on the following research report, create social media content for sharing.

RESEARCH TITLE: {report.get('title', '')}
RESEARCH FOCUS: {narrative}

FULL REPORT:
{final_report[:3000]}

## YOUR TASK

Create engaging social media content that highlights the key findings. Your response must be valid JSON in this exact format:

{{
    "title": "A snappy, attention-grabbing title (max 80 characters)",
    "text": "2-3 engaging sentences that summarize the most interesting finding (max 280 characters). Make it compelling and shareable.",
    "visual_elements": [
        {{
            "type": "chart|map",
            "placeholder": "[CHART:time_series:123:0:month] or [CHART:map:456]",
            "description": "Brief description of what this visual shows"
        }}
    ]
}}

**Requirements:**
- Title: Catchy, clear, and under 80 characters
- Text: 2-3 sentences that make people want to read more. Include a key statistic or finding. Max 280 characters.
- Visual elements: Include 1-3 chart/map placeholders from the report that best illustrate the findings. Extract the exact placeholder format like [CHART:...] from the report.
- Focus on the most surprising or important finding

Respond with ONLY valid JSON, no additional text."""
        
        social_media_response = ""
        async for chunk in agent.explain_change_streaming(social_media_prompt, {}):
            if isinstance(chunk, dict):
                if chunk.get("type") == "token":
                    social_media_response += chunk.get("content", "")
                elif "content" in chunk:
                    social_media_response += chunk.get("content", "")
            elif isinstance(chunk, str):
                content = _parse_sse_chunk(chunk)
                if content:
                    social_media_response += content
        
        # Parse social media content
        social_media_content = None
        try:
            json_start = social_media_response.find('{')
            json_end = social_media_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = social_media_response[json_start:json_end]
                social_media_content = json.loads(json_str)
            else:
                logger.warning(f"Could not parse social media content JSON for report {report_id}")
        except Exception as e:
            logger.warning(f"Error parsing social media content: {e}")

        # Merge metadata and mark completed
        existing_metadata = report.get("metadata", {}) if report else {}
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except Exception:
                existing_metadata = {}
        elif not isinstance(existing_metadata, dict):
            existing_metadata = {}
        existing_metadata["status_detail"] = "Completed"

        if job:
            job.update_progress(95)

        manager.update_report(
            report_id,
            status="completed",
            final_report=final_report,
            final_report_html=final_report_html,
            social_media_content=social_media_content,
            metadata=existing_metadata,
        )

        if job:
            job.update_progress(100)
            job.complete({"report_id": report_id})

    except Exception as e:
        logger.error(f"Error in research resynthesis job: {e}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))
        from tools.research_manager import get_research_manager

        manager = get_research_manager()
        manager.update_report(report_id, status="failed", error_message=str(e))
