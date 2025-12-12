import os
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

# Initialize APIRouter
router = APIRouter()

# Templates will be set by the main app
templates = None

def set_templates(t):
    """Set the templates instance for this router"""
    global templates
    templates = t
    logging.info("Templates set in conversation router")

def clean_json_for_api(obj):
    """Clean JSON data to ensure it's compatible with FastAPI serialization."""
    if isinstance(obj, dict):
        return {k: clean_json_for_api(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_json_for_api(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    else:
        return obj


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """
    Parse a timestamp that may be an ISO string or a unix timestamp (seconds/ms).

    Returns a timezone-aware datetime in UTC when possible.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # Heuristic: values > 1e12 are almost certainly ms since epoch.
        seconds = value / 1000.0 if value > 1e12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except Exception:
            return None

    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        # Normalize Zulu suffix to python's expected +00:00
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def _derive_session_status(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add a small, stable set of derived fields so the UI can tell whether a
    session likely finished cleanly vs ended in an incomplete state.
    """
    success = session_data.get("success")
    start_dt = _parse_timestamp(session_data.get("start_time") or session_data.get("timestamp"))
    end_dt = _parse_timestamp(session_data.get("end_time"))

    # Compute last_event_time from any timestamps we have.
    candidates: list[datetime] = []
    for dt in (start_dt, end_dt):
        if dt:
            candidates.append(dt)

    for msg in (session_data.get("conversation") or []):
        dt = _parse_timestamp(msg.get("timestamp"))
        if dt:
            candidates.append(dt)

    for resp in (session_data.get("intermediate_responses") or []):
        dt = _parse_timestamp(resp.get("timestamp"))
        if dt:
            candidates.append(dt)

    for tc in (session_data.get("tool_calls") or []):
        for key in ("start_time", "end_time"):
            dt = _parse_timestamp(tc.get(key))
            if dt:
                candidates.append(dt)

    last_event_dt = max(candidates) if candidates else None

    conversation = session_data.get("conversation") or []
    last_role = conversation[-1].get("role") if conversation else None
    last_content = conversation[-1].get("content") if conversation else None
    final_response = session_data.get("final_response") or ""

    # "Incomplete" here means: the session has an end time, but the final
    # conversation item is a tool call (or otherwise not an assistant message),
    # which typically indicates the run ended before the assistant produced its
    # post-tool narrative.
    looks_incomplete = bool(end_dt) and (last_role != "assistant")

    # Status bucket
    if end_dt and success is True:
        derived_status = "completed_incomplete" if looks_incomplete else "completed"
    elif end_dt and success is False:
        derived_status = "failed"
    else:
        derived_status = "running"

    now = datetime.now(tz=timezone.utc)
    last_event_age_seconds = (
        int((now - last_event_dt).total_seconds()) if last_event_dt else None
    )

    return {
        "derived_status": derived_status,
        "looks_incomplete": looks_incomplete,
        "last_event_time": last_event_dt.isoformat() if last_event_dt else None,
        "last_event_age_seconds": last_event_age_seconds,
        "last_role": last_role,
        "last_content_preview": (
            str(last_content)[:200] if last_content is not None else None
        ),
        "final_response_length": len(final_response) if isinstance(final_response, str) else None,
    }

@router.get("/api/sessions/{session_id}")
async def get_session_data(session_id: str):
    """Get session data for conversation viewer (from GCS or local storage)."""
    try:
        # Import GCS logger for cloud-enabled retrieval
        from tools.gcs_logger import get_gcs_logger
        
        # Use GCS logger which tries GCS first, then falls back to local storage
        gcs_logger = get_gcs_logger()
        session_data = gcs_logger.retrieve_session(session_id)
        
        if session_data:
            logging.info(f"Retrieved session {session_id} (from GCS or local)")
            # Add derived status fields for the UI/debugging.
            if isinstance(session_data, dict):
                session_data["derived"] = _derive_session_status(session_data)
            # Clean any problematic float values for FastAPI JSON serialization
            cleaned_data = clean_json_for_api(session_data)
            return JSONResponse(cleaned_data)
        
        logging.warning(f"Session {session_id} not found in GCS or local storage")
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        logging.exception(f"Error loading session {session_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversation")
async def conversation_viewer(request: Request):
    """Serve the conversation viewer page."""
    return templates.TemplateResponse("conversation_viewer.html", {"request": request})
