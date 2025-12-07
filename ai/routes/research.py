"""
Research Routes - FastAPI routes for the research system.

This module provides API endpoints for creating, managing, and executing
research reports.
"""

import logging
import json
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from background_jobs import job_manager

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
        
        # Convert datetime objects to ISO format
        for report in reports:
            for key, value in report.items():
                if hasattr(value, 'isoformat'):
                    report[key] = value.isoformat()
        
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
        
        # Convert datetime objects
        for key, value in report.items():
            if hasattr(value, 'isoformat'):
                report[key] = value.isoformat()
                
        for item in items:
            for key, value in item.items():
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
        
        report['items'] = items
        
        return JSONResponse({
            "status": "success",
            "report": report
        })
        
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
        
        result = manager.create_report(
            title=title,
            original_prompt=prompt,
            district=district,
            model_key=model_key
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
        
        # Run agenda generation in background
        asyncio.create_task(
            _run_agenda_generation_job(
                job_id, 
                report_id, 
                report.get('original_prompt'),
                report.get('district', '0'),
                model_key or report.get('model_key')
            )
        )
        
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
                builds_toward=item.get("builds_toward")
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
        
        # Update report status
        manager.update_report(report_id, status="running")
        
        # Run research in background
        asyncio.create_task(
            _run_research_execution_job(
                job_id,
                report_id,
                model_key or report.get('model_key')
            )
        )
        
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


@router.get("/{report_id}/items")
async def get_research_items(report_id: int):
    """Get all research items for a report."""
    try:
        from tools.research_manager import get_research_manager
        
        manager = get_research_manager()
        items = manager.get_research_items(report_id)
        
        # Convert datetime objects
        for item in items:
            for key, value in item.items():
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
        
        return JSONResponse({
            "status": "success",
            "items": items
        })
        
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
        
        title_prompt = f"""
            You are a title generator. Generate a concise, descriptive title for a research report based on this research question:
            
            "{prompt}"
            
            Requirements:
            - Maximum 60 characters
            - Clear and specific about the research topic
            - Use title case
            - Professional tone
            - Respond with ONLY the title, nothing else
            
            Title:
            """

        response = ""
        # Note: explain_change_streaming takes (prompt, metric_details) - prompt is first
        async for chunk in agent.explain_change_streaming(title_prompt, {}):
            if isinstance(chunk, dict):
                # Handle dict chunks with "type": "token"
                if chunk.get("type") == "token":
                    response += chunk.get("content", "")
                elif "content" in chunk:
                    response += chunk.get("content", "")
            elif isinstance(chunk, str):
                # Parse SSE format strings
                content = _parse_sse_chunk(chunk)
                if content:
                    response += content
        
        # Clean up the response
        cleaned = response.strip()
        
        if cleaned:
            # Take first line only in case there's extra output
            cleaned = cleaned.split('\n')[0].strip()
            # Remove quotes if present
            cleaned = cleaned.strip('"\'')
            # Truncate to 60 chars
            if len(cleaned) > 60:
                cleaned = cleaned[:57] + "..."
            return cleaned if cleaned else f"Research: {prompt[:50]}..."
        
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
        
        # Create agent for agenda generation
        agent = LangChainExplainerAgent(
            model_key=model_key,
            tool_groups=[ToolGroup.CORE, ToolGroup.METRICS, ToolGroup.ANALYSIS],  # Core + Metrics + Analysis for agenda planning
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
        
        # Generate agenda using AI - focused on iterative research with clear success criteria
        agenda_prompt = f"""You are creating a research agenda for investigating San Francisco public data. This agenda will drive an ITERATIVE research process where each question is investigated until fully answered.

## USER'S RESEARCH QUESTION
"{prompt}"

## CONTEXT
District: {district if district != "0" else "Citywide"}
{anomaly_context}

## YOUR TASK

Create a research agenda that:

1. **Clarifies the research question** - Restate it precisely with measurable success criteria
2. **Defines what a complete answer looks like** - Be specific about what data/findings would satisfy the question
3. **Breaks down into sub-questions** - Each sub-question should be independently answerable and build toward the main answer

## ITERATIVE RESEARCH PROCESS

Each research item in your agenda will be investigated iteratively:
- The AI researcher will gather data and attempt to answer the question
- It will evaluate: "Is this question fully answered with specific data?"
- If NO, it generates a follow-up question and continues researching
- This repeats until the question is satisfactorily answered (up to 3 iterations)

Therefore, each research item should be:
- **Specific and measurable** - Can we definitively say when it's answered?
- **Data-driven** - What specific metrics/datasets will provide the answer?
- **Building-block** - How does this contribute to the main research question?

## EXAMPLE OF A GOOD RESEARCH AGENDA

For the question "Is the Mission District getting safer?":

```json
{{
    "refined_question": "Has the Mission District (District 9) experienced a statistically significant decrease in reported crime incidents over the past 2 years compared to the citywide average?",
    "success_criteria": "The question is answered when we have: (1) Year-over-year crime trend data for District 9, (2) Comparison to citywide trends, (3) Breakdown by crime type to identify which categories are improving/worsening, (4) Statistical context on whether changes are significant or within normal variation.",
    "example_good_answer": "The Mission District has seen a 12% decrease in violent crime over the past 24 months (from 1,847 incidents to 1,625), outperforming the citywide average decrease of 7%. Property crime remains elevated, up 3% vs last year. The violent crime reduction is statistically significant (p<0.05) and driven primarily by a 23% drop in aggravated assaults. However, vehicle break-ins increased 8%, partially offsetting overall safety improvements.",
    "narrative_thread": "Examining whether safety improvements in the Mission are real, sustained, and broad-based across crime categories",
    "items": [
        {{
            "research_question": "What are the overall crime trends in District 9 over the past 24 months?",
            "success_criteria": "Have month-by-month incident counts with year-over-year comparison",
            "builds_toward": "Establishes baseline trend data",
            "metric_name": "Police Incidents",
            "priority": 1
        }},
        {{
            "research_question": "How does District 9's crime trend compare to the citywide average?",
            "success_criteria": "Have comparative percentage changes for District 9 vs citywide",
            "builds_toward": "Contextualizes whether District 9 is outperforming or underperforming",
            "metric_name": "Police Incidents",
            "priority": 2
        }},
        {{
            "research_question": "Which specific crime categories are driving the overall trend?",
            "success_criteria": "Have breakdown by crime type (violent, property, etc.) with individual trends",
            "builds_toward": "Identifies specific areas of improvement/concern",
            "metric_name": "Police Incidents by Category",
            "priority": 3
        }}
    ]
}}
```

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

Include 3-5 focused research items. Quality over quantity - each should be essential to answering the main question.
Respond with ONLY valid JSON, no additional text."""

        job.update_progress(50)
        
        response = ""
        # Note: explain_change_streaming takes (prompt, metric_details) - prompt is first
        async for chunk in agent.explain_change_streaming(agenda_prompt, {}):
            if isinstance(chunk, dict):
                if chunk.get("type") == "token":
                    response += chunk.get("content", "")
                elif "content" in chunk:
                    response += chunk.get("content", "")
            elif isinstance(chunk, str):
                # Parse SSE format strings
                content = _parse_sse_chunk(chunk)
                if content:
                    response += content
        
        job.update_progress(70)
        
        # Parse the JSON response
        try:
            # Find JSON in response
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                agenda = json.loads(json_str)
            else:
                raise ValueError("No JSON found in response")
        except Exception as e:
            logger.error(f"Failed to parse agenda JSON: {e}")
            job.fail(f"Failed to parse agenda: {str(e)}")
            manager.update_report(report_id, status="failed", error_message=str(e))
            return
        
        job.update_progress(80)
        
        # Update report with agenda
        result = manager.update_report(
            report_id,
            agenda=agenda,
            agenda_json=json.dumps(agenda, indent=2),
            status="agenda_ready"
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
        
        job.update_progress(5)
        logger.info(f"Research execution job {job_id} started for report {report_id}")
        
        # Get report and items
        report = manager.get_report(report_id)
        items = manager.get_research_items(report_id)
        
        if not items:
            job.fail("No research items found")
            manager.update_report(report_id, status="failed", error_message="No research items")
            return
        
        job.update_progress(10)
        
        total_items = len(items)
        completed_count = {"count": 0}  # Use dict for mutable counter in closure
        
        # Limit concurrent research items to avoid overwhelming LLM API
        max_concurrent = 3
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def research_single_item(item):
            """Research a single item with iteration loop - runs in parallel"""
            async with semaphore:  # Limit concurrency
                item_id = item.get('item_id')
                research_question = item.get('research_question', '')
                
                # Update item status
                manager.update_research_item(
                    report_id,
                    item_id,
                    status="in_progress",
                    started_at=datetime.now()
                )
                
                try:
                    # Build research context
                    metric_name = item.get('metric_name', '')
                    metric_id = item.get('metric_id')
                    anomaly_id = item.get('anomaly_id')
                    
                    # Get success criteria from metadata (where it's stored)
                    metadata = item.get('metadata', {})
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}
                    success_criteria = metadata.get('success_criteria', '') or item.get('success_criteria', '')
                    builds_toward = metadata.get('builds_toward', '') or item.get('builds_toward', '')
                    
                    context = ""
                    if metric_name:
                        context += f"Metric/Dataset: {metric_name}\n"
                    if metric_id:
                        context += f"Metric ID: {metric_id}\n"
                    if anomaly_id:
                        context += f"Anomaly ID: {anomaly_id}\n"
                    if builds_toward:
                        context += f"Purpose: {builds_toward}\n"
                    
                    # Execute research with iteration loop
                    iterations = []
                    max_iterations = 3
                    current_iteration = 0
                    combined_response = ""
                    session_id = None
                    is_complete = False
                    
                    while current_iteration < max_iterations and not is_complete:
                        current_iteration += 1
                        iteration_data = {
                            "iteration": current_iteration,
                            "type": "initial" if current_iteration == 1 else "follow_up",
                            "question": research_question if current_iteration == 1 else iterations[-1].get("follow_up_question", ""),
                            "started_at": datetime.now().isoformat()
                        }
                        
                        # Create agent for this iteration
                        agent = LangChainExplainerAgent(
                            model_key=model_key,
                            tool_groups=[
                                ToolGroup.CORE,
                                ToolGroup.DATA_ANALYSIS,
                                ToolGroup.ANALYSIS,
                                ToolGroup.METRICS,
                                ToolGroup.VISUALIZATION
                            ],
                            include_all_sections=False,
                            enable_session_logging=True
                        )
                        
                        # Build the prompt based on iteration
                        if current_iteration == 1:
                            success_section = f"\n\n## SUCCESS CRITERIA\nThis question is answered when: {success_criteria}" if success_criteria else ""
                            
                            research_prompt = f"""## RESEARCH QUESTION
{research_question}

## CONTEXT
{context}
{success_section}

## YOUR TASK
Investigate this question thoroughly using available data tools. Query relevant datasets, analyze patterns, and provide a data-driven answer.

This is part of an ITERATIVE research process. After your response, it will be evaluated against the success criteria. If incomplete, follow-up questions will be generated automatically.

## RESPONSE FORMAT
Structure your response EXACTLY as follows:

### Summary
[1-2 sentence direct answer with specific numbers. This should stand alone as a complete answer.]

### Key Findings
[3-5 bullet points with the most important data-driven discoveries]

### Analysis
[Detailed analysis with evidence, patterns, comparisons, and context]

### Data Sources
[List the specific datasets, queries, and tools you used]

Focus on SPECIFIC DATA. Avoid vague statements. Include actual numbers, percentages, time periods, and comparisons."""
                        else:
                            # Follow-up iteration
                            follow_up_q = iterations[-1].get("follow_up_question", "")
                            research_prompt = f"""## FOLLOW-UP INVESTIGATION

### Follow-up Question
{follow_up_q}

### Original Research Question
{research_question}

### Success Criteria
{success_criteria if success_criteria else "Provide specific, data-driven findings"}

### Previous Findings
{combined_response[:2000]}

## YOUR TASK
Continue investigating to answer this follow-up question. Use data tools to gather the specific evidence needed.

Provide your response in the same structured format:

## Summary
[Direct answer to the follow-up question]

## Key Findings
[New discoveries from this investigation]

## Analysis
[Additional analysis and evidence]"""

                        # Execute research for this iteration
                        iteration_response = ""
                        
                        async for chunk in agent.explain_change_streaming(
                            research_prompt,
                            {"metric_id": metric_id} if metric_id else {}
                        ):
                            if isinstance(chunk, dict):
                                if chunk.get("type") == "token":
                                    iteration_response += chunk.get("content", "")
                                elif chunk.get("type") == "session_id":
                                    session_id = chunk.get("session_id")
                                elif "content" in chunk:
                                    iteration_response += chunk.get("content", "")
                            elif isinstance(chunk, str):
                                content = _parse_sse_chunk(chunk)
                                if content:
                                    iteration_response += content
                        
                        iteration_data["response"] = iteration_response
                        iteration_data["completed_at"] = datetime.now().isoformat()
                        
                        # Add to combined response
                        if current_iteration == 1:
                            combined_response = iteration_response
                        else:
                            combined_response += f"\n\n---\n\n## Follow-up: {iterations[-1].get('follow_up_question', '')}\n\n{iteration_response}"
                        
                        # Evaluate if the question is fully answered (only if we haven't hit max iterations)
                        if current_iteration < max_iterations:
                            success_check = f"\n\nSUCCESS CRITERIA:\n{success_criteria}" if success_criteria else ""
                            
                            eval_prompt = f"""## EVALUATE RESEARCH COMPLETENESS

### Research Question
{research_question}
{success_check}

### Findings So Far
{combined_response[:3000]}

### Your Task
Evaluate whether the success criteria have been met. Check for:
1. **Specific data** - Are there actual numbers, percentages, time periods?
2. **Direct answer** - Does the Summary directly answer the question?
3. **Evidence** - Are findings supported by queried data?
4. **Completeness** - Are all aspects of the success criteria addressed?

### Response Format
COMPLETE: [YES or NO]
FOLLOW_UP: [If NO, one specific question to gather missing data. If YES, write "None"]
REASON: [1-2 sentences explaining what's complete or what's missing]"""

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
                            is_complete = "COMPLETE: YES" in eval_response.upper() or "COMPLETE:YES" in eval_response.upper()
                            
                            iteration_data["evaluation"] = eval_response.strip()
                            iteration_data["is_complete"] = is_complete
                            
                            # Extract follow-up question if not complete
                            if not is_complete:
                                follow_up_match = eval_response.split("FOLLOW_UP:")
                                if len(follow_up_match) > 1:
                                    follow_up_text = follow_up_match[1].split("REASON:")[0].strip()
                                    if follow_up_text.lower() != "none":
                                        iteration_data["follow_up_question"] = follow_up_text
                                    else:
                                        is_complete = True
                                else:
                                    is_complete = True  # Can't parse, assume complete
                        else:
                            is_complete = True  # Max iterations reached
                            iteration_data["is_complete"] = True
                            iteration_data["evaluation"] = "Max iterations reached"
                        
                        iterations.append(iteration_data)
                        
                        # Update metadata with iteration progress
                        manager.update_research_item(
                            report_id,
                            item_id,
                            metadata={"iterations": iterations, "total_iterations": current_iteration}
                        )
                    
                    # Update item with final result
                    manager.update_research_item(
                        report_id,
                        item_id,
                        status="completed",
                        result=combined_response,
                        session_id=session_id,
                        metadata={"iterations": iterations, "total_iterations": len(iterations)},
                        completed_at=datetime.now()
                    )
                    
                    # Update progress counter
                    completed_count["count"] += 1
                    progress = 10 + int((completed_count["count"] / total_items) * 70)
                    job.update_progress(progress)
                    
                    return {
                        "item_id": item_id,
                        "question": research_question,
                        "answer": combined_response,
                        "iterations": len(iterations)
                    }
                    
                except Exception as e:
                    logger.error(f"Error researching item {item_id}: {e}")
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
        
        # Run all research items in parallel
        results = await asyncio.gather(*[research_single_item(item) for item in items])
        
        job.update_progress(85)
        
        # Synthesize final report
        manager.update_report(report_id, status="synthesizing")
        
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

Create a comprehensive research report that:
1. Summarizes the key findings
2. Identifies patterns and connections between findings
3. Draws conclusions
4. Notes any areas that need further investigation

Format the report in clean markdown with clear sections."""

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
        
        job.update_progress(95)
        
        # Generate HTML version
        import markdown2
        final_report_html = markdown2.markdown(
            final_report,
            extras=['fenced-code-blocks', 'tables', 'header-ids']
        )
        
        # Update report with final content
        manager.update_report(
            report_id,
            status="completed",
            final_report=final_report,
            final_report_html=final_report_html
        )
        
        job.update_progress(100)
        job.complete({
            "report_id": report_id,
            "items_completed": completed_count["count"],
            "items_total": total_items
        })
        
        logger.info(f"Research execution completed for report {report_id}")
        
    except Exception as e:
        logger.error(f"Error in research execution job: {e}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))
        
        from tools.research_manager import get_research_manager
        manager = get_research_manager()
        manager.update_report(report_id, status="failed", error_message=str(e))

