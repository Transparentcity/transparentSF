"""
Explainer Chat Routes

This module contains all the explainer agent and chat-related endpoints,
moved from backend.py for better organization.

Uses Redis-backed session storage for persistence across server restarts.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import json
import logging
import uuid
from typing import Dict, Any

# Import the LangChain explainer agent
from agents.langchain_agent.explainer_agent import create_explainer_agent
from agents.langchain_agent.config.tool_config import ToolGroup

# Import the necessary function for available models
from agents.config.models import get_available_models, get_default_model, get_default_token_limit

# Import tiktoken for token counting
import tiktoken

# Import Redis-backed session store
from core.session_store import (
    get_session_store,
    SessionData,
    generate_session_id as gen_session_id,
)

# Initialize router and logger
router = APIRouter()
logger = logging.getLogger(__name__)

# Templates will be set by main.py
templates = None

def set_templates(t):
    """Set the templates instance for this router"""
    global templates
    templates = t
    logger.info("Templates set in explainer chat router")

# Session store for explainer agents
# This stores ExplainerAgent instances by session_id to maintain conversation history
# Note: The agent instances are still stored in memory, but session data is persisted to Redis
explainer_sessions: Dict[str, Any] = {}

# Get the session store (Redis with fallback to in-memory)
def get_store():
    """Get the session store instance."""
    return get_session_store()

# Track session creation and destruction for debugging
def log_session_event(event: str, session_id: str, details: str = ""):
    """Log session lifecycle events for debugging."""
    session_count = len(explainer_sessions)
    active_sessions = list(explainer_sessions.keys())[:3]  # First 3 session IDs
    logger.info(f"SESSION {event}: {session_id} | Total sessions: {session_count} | Active: {active_sessions} | {details}")


async def save_agent_session_data(session_id: str, agent, model_key: str, tool_groups: list):
    """Save agent session data to the persistent store."""
    try:
        store = get_store()
        
        # Get conversation history from agent
        conversation_history = []
        if hasattr(agent, 'get_conversation_history'):
            conversation_history = agent.get_conversation_history()
        
        session_data = SessionData(
            session_id=session_id,
            agent_type="langchain_explainer",
            model_key=model_key,
            tool_groups=[g.value if hasattr(g, 'value') else str(g) for g in tool_groups],
            conversation_history=conversation_history,
        )
        
        # Save with 1 hour TTL (can be configured via env var)
        import os
        ttl = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
        await store.save_session(session_data, ttl_seconds=ttl)
        logger.debug(f"Saved session data for {session_id}")
    except Exception as e:
        logger.warning(f"Failed to save session data for {session_id}: {e}")


async def load_agent_session_data(session_id: str) -> tuple:
    """Load agent session data from the persistent store.
    
    Returns:
        Tuple of (session_data, conversation_history) or (None, []) if not found
    """
    try:
        store = get_store()
        session_data = await store.get_session(session_id)
        if session_data:
            logger.debug(f"Loaded session data for {session_id}")
            return session_data, session_data.conversation_history or []
        return None, []
    except Exception as e:
        logger.warning(f"Failed to load session data for {session_id}: {e}")
        return None, []

@router.get("/available-models")
async def get_available_models_endpoint():
    """Get available models for frontend dropdowns."""
    try:
        available_models = get_available_models()
        model_list = []
        
        for model_key, model_config in available_models.items():
            model_list.append({
                "key": model_key,
                "name": model_config.full_name,
                "provider": model_config.provider.value,
                "available": model_config.is_available(),
                "context_window": model_config.context_window,
                "input_price_per_million": model_config.input_price_per_million,
                "output_price_per_million": model_config.output_price_per_million
            })
        
        # Sort by provider and then by name
        model_list.sort(key=lambda x: (x["provider"], x["name"]))
        
        return JSONResponse({
            "status": "success",
            "models": model_list,
            "default_model": get_default_model()
        })
    except Exception as e:
        logger.error(f"Error getting available models: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error getting available models: {str(e)}"
        }, status_code=500)

@router.post("/api/clear-explainer-sessions")
async def clear_explainer_sessions():
    """Clear all explainer agent sessions to force fresh agent creation."""
    global explainer_sessions
    
    # Clear persistent sessions for all agents before removing them
    for session_key, agent in explainer_sessions.items():
        if hasattr(agent, 'clear_session'):
            agent.clear_session()
            logger.info(f"Cleared persistent session for agent: {session_key}")
    
    explainer_sessions.clear()
    logger.info("Cleared all explainer agent sessions")
    return JSONResponse(content={"status": "success", "message": "All sessions cleared"})

@router.get("/api/agent-config/{session_id}")
async def get_agent_config(session_id: str):
    """Get the configuration of a specific agent session."""
    session_key = f"langchain_{session_id}"
    if session_key in explainer_sessions:
        agent = explainer_sessions[session_key]
        config = agent.get_configuration_info()
        return JSONResponse(content={"status": "success", "config": config})
    else:
        return JSONResponse(content={"status": "error", "message": "Session not found"})

@router.post("/api/explain-change")
async def explain_change_api(request: Request):
    """
    API endpoint to explain data changes using the explainer agent with session management.

    Expected JSON payload:
    {
        "prompt": "Explain the change in metric X for district Y",
        "metric_id": 123,
        "district_id": 0,
        "period_type": "month",
        "session_id": "optional_session_id",  // Optional - will create new if not provided
        "return_json": true
    }
    """
    try:
        data = await request.json()
        prompt = data.get("prompt")
        metric_id = data.get("metric_id")
        district_id = data.get("district_id", 0)
        period_type = data.get("period_type", "month")
        session_id = data.get("session_id")
        return_json = data.get("return_json", True)

        if not prompt:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "No prompt provided"
                }
            )

        logger.info(f"Explaining change with prompt: {prompt}")

        # Get or create explainer agent for this session
        if session_id and session_id in explainer_sessions:
            agent = explainer_sessions[session_id]
            logger.info(f"Using existing explainer agent for session: {session_id}")
        else:
            # Create new agent and session
            agent = create_explainer_agent()
            if not session_id:
                session_id = str(uuid.uuid4())
            explainer_sessions[session_id] = agent
            logger.info(f"Created new explainer agent for session: {session_id}")

        # Prepare metric details for the agent
        metric_details = {}
        if metric_id is not None:
            metric_details = {
                "metric_id": metric_id,
                "district_id": district_id,
                "period_type": period_type
            }
            enhanced_prompt = f"""
            {prompt}

            Please analyze metric {metric_id} for district {district_id} over the {period_type} period.
            """
        else:
            enhanced_prompt = prompt

        # Get explanation using the LangChain agent interface with session_id
        result = agent.explain_change_sync(enhanced_prompt, metric_details, session_id=session_id)

        # Add session_id to the result
        if isinstance(result, dict):
            result["session_id"] = session_id

        return JSONResponse(
            content={
                "status": "success" if result.get("success", True) else "error",
                "result": result,
                "session_id": session_id
            }
        )

    except Exception as e:
        logger.error(f"Error in explain_change_api: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error explaining change: {str(e)}"
            }
        )

@router.post("/api/explain-change-streaming")
async def explain_change_streaming_api(request: Request):
    """
    Streaming API endpoint for explainer agent chat functionality.
    
    Expected JSON payload:
    {
        "prompt": "Explain the change in metric X for district Y",
        "session_data": {
            "session_id": "unique_session_id"  // Optional, will create new if not provided
        }
    }
    """
    try:
        data = await request.json()
        prompt = data.get("prompt")
        session_data = data.get("session_data", {})
        session_id = session_data.get("session_id")
        
        if not prompt:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "No prompt provided"
                }
            )
        
        logger.info(f"Starting streaming explanation with prompt: {prompt}")
        
        # Get or create explainer agent for this session
        if session_id and session_id in explainer_sessions:
            agent = explainer_sessions[session_id]
            logger.info(f"Using existing explainer agent for session: {session_id}")
        else:
            # Create new agent and session
            agent = create_explainer_agent()
            if not session_id:
                session_id = str(uuid.uuid4())
            explainer_sessions[session_id] = agent
            logger.info(f"Created new explainer agent for session: {session_id}")
        
        async def generate_stream():
            """Generate streaming response"""
            try:
                # Send session ID first so frontend can track it
                yield f"data: {json.dumps({'session_id': session_id})}\n\n"
                
                # Prepare metric details for the agent
                metric_details = {}
                if session_data and 'metric_id' in session_data:
                    metric_details = {
                        "metric_id": session_data.get('metric_id'),
                        "district_id": session_data.get('district_id', 0),
                        "period_type": session_data.get('period_type', 'month')
                    }
                
                async for chunk in agent.explain_change_streaming(prompt, metric_details, session_id=session_id):
                    if chunk:
                        # The agent already yields properly formatted SSE data, so pass it through directly
                        yield chunk
                
                # Send completion signal
                yield f"data: {json.dumps({'completed': True})}\n\n"
                
            except Exception as e:
                logger.error(f"Error in streaming generation: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        logger.error(f"Error in explain_change_streaming_api: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error in streaming explanation: {str(e)}"
            }
        )

@router.post("/api/langchain-explainer-streaming")
async def langchain_explainer_streaming_api(request: Request):
    """
    LangChain-based streaming API endpoint for explainer agent chat functionality.
    
    Expected JSON payload:
    {
        "prompt": "Explain the change in metric X for district Y",
        "model_key": "gpt-5",
        "tool_groups": ["core", "analysis", "metrics"],
        "session_data": {
            "session_id": "unique_session_id"  // Optional, will create new if not provided
        }
    }
    """
    try:
        data = await request.json()
        prompt = data.get("prompt")
        model_key = data.get("model_key", "gpt-5")
        tool_groups = data.get("tool_groups", ["core", "analysis", "visualization"])
        session_data = data.get("session_data", {})
        session_id = session_data.get("session_id")
        
        if not prompt:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "No prompt provided"
                }
            )
        
        logger.info(f"Starting LangChain streaming explanation with prompt: {prompt}")
        logger.info(f"Model: {model_key}, Tool groups: {tool_groups}")
        logger.info(f"Session data received: {session_data}")
        logger.info(f"Session ID from request: {session_id} (type: {type(session_id)})")
        logger.info(f"Tool groups type: {type(tool_groups)}, Tool groups content: {tool_groups}")
        
        # Convert tool group strings to ToolGroup enums
        tool_group_enums = []
        for group_name in tool_groups:
            try:
                tool_group_enums.append(ToolGroup(group_name))
            except ValueError:
                logger.warning(f"Unknown tool group: {group_name}")
        
        if not tool_group_enums:
            tool_group_enums = [ToolGroup.CORE, ToolGroup.ANALYSIS, ToolGroup.VISUALIZATION]
        
        # Get or create LangChain explainer agent for this session
        logger.info(f"SESSION LOGIC: session_id is {'None' if session_id is None else 'provided'}: {session_id}")
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.info(f"SESSION LOGIC: Created new session_id: {session_id}")
        else:
            logger.info(f"SESSION LOGIC: Using existing session_id: {session_id}")
        session_key = f"langchain_{session_id}"
        logger.info(f"SESSION LOGIC: Full session key: {session_key}")
        
        logger.info(f"SESSION LOOKUP: Checking if session exists: {session_key} in {list(explainer_sessions.keys())[:3]}")
        
        if session_key in explainer_sessions:
            agent = explainer_sessions[session_key]
            logger.info(f"SESSION LOOKUP: Found existing agent for session: {session_key}")
            
            # Update agent configuration if needed
            if hasattr(agent, 'model_key') and agent.model_key != model_key:
                # Preserve conversation history when recreating agent with new model
                old_agent = agent  # Keep reference to old agent
                agent = create_explainer_agent(model_key=model_key, tool_groups=tool_group_enums, enable_session_logging=True)
                
                # Transfer memory from old agent to new agent
                agent.transfer_memory_from(old_agent)
                
                explainer_sessions[session_key] = agent
                logger.info(f"Recreated agent with new model {model_key}, transferred memory from old agent")
            elif hasattr(agent, 'tool_groups') and agent.tool_groups != tool_group_enums:
                agent.update_tool_groups(tool_groups)
            logger.info(f"Using existing LangChain explainer agent for session: {session_key}")
        else:
            logger.info(f"SESSION LOOKUP: No existing agent found, creating new one for: {session_key}")
            
            # Try to restore session from persistent store (Redis)
            persisted_session, conversation_history = await load_agent_session_data(session_id)
            
            # Create new LangChain agent and session with session logging enabled
            agent = create_explainer_agent(model_key=model_key, tool_groups=tool_group_enums, enable_session_logging=True)
            
            # Restore conversation history if we have persisted data
            if persisted_session and conversation_history:
                logger.info(f"SESSION RESTORE: Restoring {len(conversation_history)} messages from Redis for {session_key}")
                if hasattr(agent, 'restore_conversation_history'):
                    agent.restore_conversation_history(conversation_history)
                elif hasattr(agent, 'messages'):
                    # Fallback: manually restore messages if method doesn't exist
                    from langchain_core.messages import HumanMessage, AIMessage
                    for msg in conversation_history:
                        if msg.get('role') == 'user':
                            agent.messages.append(HumanMessage(content=msg.get('content', '')))
                        elif msg.get('role') == 'assistant':
                            agent.messages.append(AIMessage(content=msg.get('content', '')))
            
            explainer_sessions[session_key] = agent
            log_session_event("CREATED", session_key, f"model={model_key}, tools={tool_groups}, restored={persisted_session is not None}")
            logger.info(f"Created new LangChain explainer agent for session: {session_key}")
        
        async def generate_stream():
            """Generate streaming response using LangChain agent with session logging"""
            try:
                # Send session ID first so frontend can track it
                yield f"data: {json.dumps({'session_id': session_id})}\n\n"
                
                # Prepare metric details for the agent
                metric_details = {}
                if session_data and 'metric_id' in session_data:
                    district_id = session_data.get('district_id', 0)
                    metric_details = {
                        "metric_id": session_data.get('metric_id'),
                        "district_id": district_id,
                        "district": str(district_id),  # Add district for research context lookup
                        "period_type": session_data.get('period_type', 'month'),
                        "city_id": 1  # San Francisco
                    }
                    
                    # Get research context for this district
                    try:
                        from services.research_service import get_research_service
                        research_service = get_research_service()
                        
                        research_context = research_service.get_research_context(
                            city_id=1,  # San Francisco
                            district=str(district_id),
                            max_agendas=3
                        )
                        
                        if research_context:
                            metric_details["research_context"] = research_context
                            logger.info(f"📋 Including research context in agent prompt")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to get research context: {e}")
                
                # Use the agent's explain_change_streaming method which includes real-time tool call logging
                async for chunk in agent.explain_change_streaming(prompt, metric_details, session_id=session_id):
                    if chunk:
                        # The agent already yields properly formatted SSE data, so pass it through directly
                        yield chunk
                
                # Save session data to Redis after streaming completes
                try:
                    await save_agent_session_data(session_id, agent, model_key, tool_group_enums)
                    logger.debug(f"Session data saved to Redis for {session_id}")
                except Exception as save_err:
                    logger.warning(f"Failed to save session data after streaming: {save_err}")
                
                # Don't send manual completion signal - the agent handles this with session_id
                
            except Exception as e:
                logger.error(f"Error in LangChain streaming generation: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        logger.error(f"Error in langchain_explainer_streaming_api: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error in LangChain streaming explanation: {str(e)}"
            }
        )

# Remove the backend prefix since the entire router is mounted at /backend
# @router.post("/backend/api/langchain-explainer-streaming")
# async def langchain_explainer_streaming_api_backend(request: Request):
#     """Backend prefix version of the LangChain streaming endpoint."""
#     return await langchain_explainer_streaming_api(request)

@router.post("/api/explain-metric-change")
async def explain_metric_change_api(request: Request):
    """
    Convenience API endpoint to explain a specific metric change.
    
    Expected JSON payload:
    {
        "metric_id": 123,
        "district_id": 0,
        "period_type": "month"
    }
    """
    try:
        data = await request.json()
        metric_id = data.get("metric_id")
        district_id = data.get("district_id", 0)
        period_type = data.get("period_type", "month")
        
        if metric_id is None:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "metric_id is required"
                }
            )
        
        logger.info(f"Explaining metric {metric_id} change for district {district_id}")
        
        # This functionality has been moved to the LangChain-based explainer agent
        # Use the /api/explain-change endpoint instead
        return JSONResponse(
            content={
                "status": "info",
                "message": "This functionality has been moved to the LangChain-based explainer agent. Use the /api/explain-change endpoint instead."
            }
        )
        
    except Exception as e:
        logger.error(f"Error in explain_metric_change_api: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error explaining metric change: {str(e)}"
            }
        )

@router.post("/api/explainer-cancel")
async def cancel_explainer_session(request: Request):
    """Cancel the current explainer session."""
    try:
        data = await request.json()
        session_id = data.get("session_id")
        
        if session_id:
            session_key = f"langchain_{session_id}"
            if session_key in explainer_sessions:
                agent = explainer_sessions[session_key]
                # Clear the agent's persistent session if it has the method
                if hasattr(agent, 'clear_session'):
                    agent.clear_session()
                # Remove the session to cancel any ongoing operations
                log_session_event("DELETED", session_key, "via cancel endpoint")
                del explainer_sessions[session_key]
                
                # Also delete from Redis
                try:
                    store = get_store()
                    await store.delete_session(session_id)
                    logger.info(f"Deleted session from Redis: {session_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete session from Redis: {e}")
                
                logger.info(f"Cancelled explainer session: {session_key}")
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success",
                        "message": "Session cancelled successfully"
                    }
                )
            else:
                return JSONResponse(
                    status_code=404,
                    content={
                        "status": "error",
                        "message": "Session not found"
                    }
                )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "No session ID provided"
                }
            )
    except Exception as e:
        logger.error(f"Error cancelling explainer session: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error cancelling session: {str(e)}"
            }
        )

# Remove backend prefix since the entire router is mounted at /backend
# @router.post("/backend/api/explainer-cancel") 
# async def cancel_explainer_session_backend(request: Request):
#     """Backend prefix version of the cancel endpoint."""
#     return await cancel_explainer_session(request)

@router.post("/api/explainer-clear-session")
async def clear_explainer_session(request: Request):
    """
    Clear a specific explainer session.
    
    Expected JSON payload:
    {
        "session_id": "session_id_to_clear"
    }
    """
    try:
        data = await request.json()
        session_id = data.get("session_id")
        
        if not session_id:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "session_id is required"
                }
            )
        
        # Check for both regular session and langchain prefixed session
        session_keys_to_check = [session_id, f"langchain_{session_id}"]
        session_cleared = False
        
        for session_key in session_keys_to_check:
            if session_key in explainer_sessions:
                agent = explainer_sessions[session_key]
                # Clear the agent's persistent session if it has the method
                if hasattr(agent, 'clear_session'):
                    agent.clear_session()
                log_session_event("DELETED", session_key, "via clear endpoint")
                del explainer_sessions[session_key]
                logger.info(f"Cleared explainer session: {session_key}")
                session_cleared = True
        
        # Also delete from Redis (use the original session_id without prefix)
        try:
            store = get_store()
            await store.delete_session(session_id)
            logger.info(f"Deleted session from Redis: {session_id}")
            # Consider session cleared even if only Redis had it
            session_cleared = True
        except Exception as e:
            logger.warning(f"Failed to delete session from Redis: {e}")
        
        if session_cleared:
            return JSONResponse(content={"status": "success", "message": f"Session {session_id} cleared"})
        else:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": f"Session {session_id} not found"
                }
            )
        
    except Exception as e:
        logger.error(f"Error clearing explainer session: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error clearing session: {str(e)}"
            }
        )

# Remove backend prefix since the entire router is mounted at /backend
# @router.post("/backend/api/explainer-clear-session")
# async def clear_explainer_session_backend(request: Request):
#     """Backend prefix version of the clear session endpoint.""" 
#     return await clear_explainer_session(request)

@router.post("/api/explainer-clear-all-sessions")
async def clear_all_explainer_sessions():
    """Clear all explainer sessions."""
    try:
        session_count = len(explainer_sessions)
        
        # Clear all agent sessions from memory
        for session_key, agent in list(explainer_sessions.items()):
            if hasattr(agent, 'clear_session'):
                agent.clear_session()
        
        log_session_event("ALL_CLEARED", "all", f"cleared {session_count} sessions")
        explainer_sessions.clear()
        
        # Also clear all sessions from Redis
        redis_cleared = 0
        try:
            store = get_store()
            session_ids = await store.list_sessions()
            for session_id in session_ids:
                await store.delete_session(session_id)
                redis_cleared += 1
            logger.info(f"Cleared {redis_cleared} sessions from Redis")
        except Exception as e:
            logger.warning(f"Failed to clear sessions from Redis: {e}")
        
        logger.info(f"Cleared all {session_count} memory sessions and {redis_cleared} Redis sessions")
        return JSONResponse(content={
            "status": "success", 
            "message": f"Cleared {session_count} memory sessions and {redis_cleared} Redis sessions"
        })
        
    except Exception as e:
        logger.error(f"Error clearing all explainer sessions: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error clearing sessions: {str(e)}"
            }
        )

@router.get("/api/explainer-sessions")
async def get_explainer_sessions():
    """Get information about active explainer sessions."""
    try:
        sessions_info = {}
        for session_id, agent in explainer_sessions.items():
            sessions_info[session_id] = {
                "message_count": len(agent.get_conversation_history()) if hasattr(agent, 'get_conversation_history') else 0,
                "agent_name": getattr(agent, 'agent', {}).get('name', 'Unknown') if hasattr(agent, 'agent') else 'LangChain Agent',
                "has_context": bool(getattr(agent, 'context_variables', {}))
            }
        
        return JSONResponse(content={
            "status": "success",
            "active_sessions": len(explainer_sessions),
            "sessions": sessions_info
        })
        
    except Exception as e:
        logger.error(f"Error getting explainer sessions: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error getting sessions: {str(e)}"
            }
        )


@router.get("/api/session-store-status")
async def get_session_store_status():
    """Get information about the session store (Redis or in-memory)."""
    try:
        store = get_store()
        store_type = type(store).__name__
        
        # Get list of persisted sessions
        try:
            persisted_sessions = await store.list_sessions()
        except Exception as e:
            persisted_sessions = []
            logger.warning(f"Failed to list persisted sessions: {e}")
        
        # Get Redis-specific info if applicable
        redis_info = None
        if store_type == "RedisSessionStore":
            try:
                if hasattr(store, '_redis_client') and store._redis_client:
                    info = store._redis_client.info("clients")
                    redis_info = {
                        "connected_clients": info.get("connected_clients"),
                        "redis_version": store._redis_client.info("server").get("redis_version"),
                    }
            except Exception as e:
                logger.warning(f"Failed to get Redis info: {e}")
        
        return JSONResponse(content={
            "status": "success",
            "store_type": store_type,
            "is_redis": store_type == "RedisSessionStore",
            "is_fallback": store_type == "InMemorySessionStore",
            "memory_sessions": len(explainer_sessions),
            "persisted_sessions": len(persisted_sessions),
            "persisted_session_ids": persisted_sessions[:10],  # First 10
            "redis_info": redis_info
        })
        
    except Exception as e:
        logger.error(f"Error getting session store status: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error getting session store status: {str(e)}"
            }
        )

@router.post("/api/explainer/context-status")
async def get_context_status(request: Request):
    """Get context window status for a session."""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        
        if not session_id:
            return JSONResponse(
                status_code=400,
                content={"error": "Session ID is required"}
            )
        
        # Get the agent for this session (using proper session key format)
        session_key = f"langchain_{session_id}"
        agent = explainer_sessions.get(session_key)
        log_session_event("ACCESSED", session_key, f"for context status - found={agent is not None}")
        logger.info(f"Context status request for session {session_id} (key: {session_key}): agent found = {agent is not None}")
        
        if not agent:
            # No session yet, return zero tokens
            logger.info(f"No agent found for session {session_id} (key: {session_key}), returning zero tokens")
            return JSONResponse(content={
                "current_tokens": 0,
                "max_tokens": 8192,
                "percentage": 0
            })
        
        # Calculate current token usage from conversation history
        current_tokens = 0
        max_tokens = 8192  # Default
        
        if hasattr(agent, 'get_conversation_history'):
            try:
                # Get the model being used to determine max tokens
                if hasattr(agent, 'model_key') and agent.model_key:
                    max_tokens = get_default_token_limit(agent.model_key)
                    logger.info(f"Using max tokens {max_tokens} for model {agent.model_key}")
                else:
                    logger.info(f"No model_key found on agent, using default max_tokens: {max_tokens}")
                
                conversation_history = agent.get_conversation_history()
                logger.info(f"Conversation history has {len(conversation_history)} messages")
                
                # Log first few messages for debugging
                for i, message in enumerate(conversation_history[:3]):  # Only log first 3
                    content = message.get('content', '')
                    role = message.get('role', 'unknown')
                    content_preview = content[:100] + '...' if len(content) > 100 else content
                    logger.info(f"Message {i} ({role}): {len(content)} chars - '{content_preview}'")
                
                # Use tiktoken to count tokens accurately
                encoding = tiktoken.get_encoding("cl100k_base")  # Used by most GPT models
                
                for i, message in enumerate(conversation_history):
                    content = message.get('content', '')
                    if content:
                        tokens_in_message = len(encoding.encode(str(content)))
                        current_tokens += tokens_in_message
                        logger.debug(f"Message {i}: {tokens_in_message} tokens")
                
                logger.info(f"Total calculated tokens: {current_tokens} from {len(conversation_history)} messages")
                
            except Exception as e:
                logger.warning(f"Error calculating token count with tiktoken: {str(e)}")
                # Fallback to simple word-based estimation
                try:
                    conversation_history = agent.get_conversation_history()
                    logger.info(f"Fallback: conversation history has {len(conversation_history)} messages")
                    
                    for i, message in enumerate(conversation_history):
                        content = message.get('content', '')
                        if content:
                            # Rough estimation: ~4 characters per token
                            tokens_in_message = len(content) // 4
                            current_tokens += tokens_in_message
                            if i < 3:  # Log details for first 3 messages
                                logger.info(f"Fallback message {i}: {len(content)} chars = {tokens_in_message} tokens")
                            
                    logger.info(f"Fallback total calculated tokens: {current_tokens}")
                except Exception as e2:
                    logger.error(f"Even fallback token counting failed: {str(e2)}")
        else:
            logger.warning(f"Agent does not have get_conversation_history method")
            # Let's check what methods the agent has
            agent_methods = [method for method in dir(agent) if not method.startswith('_')]
            logger.info(f"Agent methods available: {agent_methods[:10]}...")  # Log first 10 methods
        
        percentage = round((current_tokens / max_tokens) * 100) if max_tokens > 0 else 0
        
        logger.info(f"Returning context status: {current_tokens}/{max_tokens} tokens ({percentage}%)")
        
        return JSONResponse(content={
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "percentage": percentage
        })
        
    except Exception as e:
        logger.error(f"Error getting context status: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Error getting context status: {str(e)}"}
        )

@router.post("/api/explainer/debug-session")
async def debug_session_info(request: Request):
    """Debug endpoint to get detailed information about a session."""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        
        if not session_id:
            return JSONResponse(
                status_code=400,
                content={"error": "Session ID is required"}
            )
        
        # Get the agent for this session (using proper session key format)
        session_key = f"langchain_{session_id}"
        agent = explainer_sessions.get(session_key)
        debug_info = {
            "session_id": session_id,
            "session_key": session_key,
            "agent_exists": agent is not None,
            "total_sessions": len(explainer_sessions),
            "session_keys": list(explainer_sessions.keys())[:5]  # First 5 session keys
        }
        
        if agent:
            debug_info.update({
                "agent_type": type(agent).__name__,
                "has_get_conversation_history": hasattr(agent, 'get_conversation_history'),
                "has_messages": hasattr(agent, 'messages'),
                "has_model_key": hasattr(agent, 'model_key')
            })
            
            if hasattr(agent, 'model_key'):
                debug_info["model_key"] = getattr(agent, 'model_key', None)
            
            if hasattr(agent, 'get_conversation_history'):
                try:
                    history = agent.get_conversation_history()
                    debug_info.update({
                        "conversation_length": len(history),
                        "message_types": [msg.get('role', 'unknown') for msg in history[:10]],
                        "message_lengths": [len(msg.get('content', '')) for msg in history[:10]]
                    })
                except Exception as e:
                    debug_info["conversation_history_error"] = str(e)
            
            if hasattr(agent, 'messages'):
                try:
                    messages = getattr(agent, 'messages', [])
                    debug_info.update({
                        "raw_messages_count": len(messages),
                        "raw_message_types": [type(msg).__name__ for msg in messages[:5]]
                    })
                except Exception as e:
                    debug_info["raw_messages_error"] = str(e)
        
        return JSONResponse(content=debug_info)
        
    except Exception as e:
        logger.error(f"Error in debug session info: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Error getting debug info: {str(e)}"}
        )

@router.get("/api/test-explainer-session")
async def test_explainer_session():
    """Test endpoint to verify explainer session management is working."""
    try:
        # Create a test session
        test_session_id = str(uuid.uuid4())
        agent = create_explainer_agent()
        explainer_sessions[test_session_id] = agent
        
        # Add some test messages
        agent.add_message("user", "Hello, this is a test message")
        agent.add_message("assistant", "Hello! I received your test message.")
        
        # Get session info
        session_info = {
            "test_session_id": test_session_id,
            "message_count": len(agent.get_conversation_history()) if hasattr(agent, 'get_conversation_history') else 0,
            "total_sessions": len(explainer_sessions),
            "conversation_history": agent.get_conversation_history() if hasattr(agent, 'get_conversation_history') else []
        }
        
        return JSONResponse(content={
            "status": "success",
            "message": "Session management test completed",
            "session_info": session_info
        })
        
    except Exception as e:
        logger.error(f"Error in test explainer session: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Test failed: {str(e)}"
            }
        )

@router.post("/api/langchain-explainer-continue")
async def langchain_explainer_continue_api(request: Request):
    """
    Continue LangChain-based streaming API endpoint when agent hits stop condition.
    
    Expected JSON payload:
    {
        "session_id": "unique_session_id",  // Required - existing session to continue
        "continuation_prompt": "Additional context or question",  // Optional
        "model_key": "gpt-5",  // Optional - will use existing session's model if not provided
        "tool_groups": ["core", "analysis", "metrics"]  // Optional - will use existing session's tools if not provided
    }
    """
    try:
        data = await request.json()
        session_id = data.get("session_id")
        continuation_prompt = data.get("continuation_prompt", "")
        model_key = data.get("model_key")
        tool_groups = data.get("tool_groups")
        
        if not session_id:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "No session_id provided. Cannot continue without existing session."
                }
            )
        
        logger.info(f"Continuing LangChain streaming analysis for session: {session_id}")
        logger.info(f"Continuation prompt: {continuation_prompt}")
        
        # Get existing LangChain explainer agent for this session
        session_key = f"langchain_{session_id}"
        
        if session_key not in explainer_sessions:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": f"Session {session_id} not found. Please start a new conversation."
                }
            )
        
        agent = explainer_sessions[session_key]
        logger.info(f"Found existing LangChain explainer agent for session: {session_key}")
        
        # Update agent configuration if new parameters are provided
        if model_key and hasattr(agent, 'model_key') and agent.model_key != model_key:
            # Convert tool group strings to ToolGroup enums if provided
            tool_group_enums = []
            if tool_groups:
                for group_name in tool_groups:
                    try:
                        tool_group_enums.append(ToolGroup(group_name))
                    except ValueError:
                        logger.warning(f"Unknown tool group: {group_name}")
            else:
                tool_group_enums = getattr(agent, 'tool_groups', [ToolGroup.CORE, ToolGroup.ANALYSIS, ToolGroup.VISUALIZATION])
            
            agent = create_explainer_agent(model_key=model_key, tool_groups=tool_group_enums, enable_session_logging=True)
            explainer_sessions[session_key] = agent
            logger.info(f"Updated agent with new model: {model_key}")
        elif tool_groups and hasattr(agent, 'tool_groups'):
            # Convert tool group strings to ToolGroup enums
            tool_group_enums = []
            for group_name in tool_groups:
                try:
                    tool_group_enums.append(ToolGroup(group_name))
                except ValueError:
                    logger.warning(f"Unknown tool group: {group_name}")
            
            if tool_group_enums and agent.tool_groups != tool_group_enums:
                agent.update_tool_groups(tool_group_enums)
                logger.info(f"Updated agent with new tool groups: {tool_groups}")
        
        async def generate_continue_stream():
            """Generate streaming response for continuation using LangChain agent"""
            try:
                # Send session ID first so frontend can track it
                yield f"data: {json.dumps({'session_id': session_id, 'continued': True})}\n\n"
                
                # Use the agent's continuation streaming method
                async for chunk in agent.continue_analysis_streaming(continuation_prompt, metric_details={}):
                    yield chunk
                
            except Exception as e:
                logger.error(f"Error in LangChain continuation streaming generation: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return StreamingResponse(
            generate_continue_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        logger.error(f"Error in langchain_explainer_continue_api: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Error in LangChain continuation: {str(e)}"
            }
        )

@router.post("/api/test-session-logging")
async def test_session_logging():
    """Test endpoint to verify session logging is working."""
    try:
        # Create a test LangChain agent with session logging enabled
        agent = create_explainer_agent(
            model_key="gpt-5",
            tool_groups=[ToolGroup.CORE, ToolGroup.ANALYSIS, ToolGroup.VISUALIZATION],
            enable_session_logging=True
        )
        
        # Verify the agent has session logging enabled
        session_logging_enabled = getattr(agent, 'enable_session_logging', False)
        session_logger = getattr(agent, 'session_logger', None)
        logs_dir = None
        if session_logger:
            logs_dir = str(session_logger.logs_dir)
        
        # Try to run a simple sync explanation to generate a session log
        test_result = agent.explain_change_sync(
            "Test prompt for session logging verification",
            metric_details={}
        )
        
        # Check if any session files were created
        import os
        from pathlib import Path
        session_logs_dir = Path(__file__).parent.parent / 'logs' / 'sessions'
        session_files = list(session_logs_dir.glob('*.json')) if session_logs_dir.exists() else []
        
        return JSONResponse(content={
            "status": "success",
            "session_logging_enabled": session_logging_enabled,
            "session_logger_available": session_logger is not None,
            "logs_directory": logs_dir,
            "logs_directory_exists": session_logs_dir.exists() if session_logs_dir else False,
            "session_files_count": len(session_files),
            "recent_session_files": [f.name for f in session_files[-5:]] if session_files else [],
            "test_result_session_id": test_result.get('session_id') if isinstance(test_result, dict) else None,
            "agent_type": type(agent).__name__
        })
        
    except Exception as e:
        logger.error(f"Error testing session logging: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Test failed: {str(e)}",
                "error_type": type(e).__name__
            }
        )
