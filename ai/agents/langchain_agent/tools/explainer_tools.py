"""
Clean, properly designed tools for the LangChain explainer agent.
These tools are designed to work with StructuredTool and use named arguments.
"""
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
import json
import pandas as pd
import requests
from urllib.parse import urljoin

# Add the parent directory to sys.path for absolute imports
current_dir = Path(__file__).parent
ai_dir = current_dir.parent.parent
sys.path.insert(0, str(ai_dir))

logger = logging.getLogger(__name__)

def set_dataset_tool(endpoint: str, query: str) -> Dict[str, Any]:
    """
    Set dataset for analysis by querying DataSF.
    
    NOTE: Due to LangChain tool limitations, this tool cannot store data in context_variables
    for other tools to access. Use generate_map_with_query instead for map generation.
    
    IMPORTANT: This tool automatically limits data to prevent context window overflow.
    For large datasets, it will sample data intelligently and warn about the limitation.
    
    Args:
        endpoint: The dataset identifier WITHOUT the .json extension (e.g., 'ubvf-ztfx')
        query: The complete SoQL query string using standard SQL syntax
        
    Returns:
        Dictionary with status, data, and optional error message
    """
    logger.info("=== Starting set_dataset_tool ===")
    logger.info(f"Endpoint: {endpoint}")
    logger.info(f"Query: {query}")

    try:
        # Validate required parameters
        if not endpoint:
            logger.error("Missing endpoint parameter")
            return {
                'status': 'error',
                'error': 'Endpoint is required', 
                'queryURL': None,
                'error_type': 'validation_error'
            }
        if not query:
            logger.error("Missing query parameter")
            return {
                'status': 'error',
                'error': 'Query is required', 
                'queryURL': None,
                'error_type': 'validation_error'
            }
            
        # Clean up endpoint - ensure it ends with .json
        if not endpoint.endswith('.json'):
            endpoint = f"{endpoint}.json"
            logger.info(f"Added .json to endpoint: {endpoint}")

        # CONTEXT WINDOW PROTECTION: Add automatic LIMIT if not present
        query_lower = query.lower()
        if 'limit' not in query_lower:
            # Add a reasonable default limit to prevent context overflow
            DEFAULT_LIMIT = 1000
            query = f"{query} LIMIT {DEFAULT_LIMIT}"
            logger.info(f"Added automatic LIMIT {DEFAULT_LIMIT} to prevent context window overflow")
        else:
            # Check if existing limit is too high
            import re
            limit_match = re.search(r'limit\s+(\d+)', query_lower)
            if limit_match:
                existing_limit = int(limit_match.group(1))
                MAX_SAFE_LIMIT = 2000
                if existing_limit > MAX_SAFE_LIMIT:
                    query = re.sub(r'limit\s+\d+', f'LIMIT {MAX_SAFE_LIMIT}', query, flags=re.IGNORECASE)
                    logger.warning(f"Reduced LIMIT from {existing_limit} to {MAX_SAFE_LIMIT} to prevent context window overflow")

        # Import the original function and call it
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        from tools.data_fetcher import fetch_data_from_api
        
        query_object = {'endpoint': endpoint, 'query': query}
        result = fetch_data_from_api(query_object)
        logger.info(f"API result status: {'success' if 'data' in result else 'error'}")
        
        if result and 'data' in result:
            data = result['data']
            if data:
                df = pd.DataFrame(data)
                logger.info(f"Dataset successfully created with shape: {df.shape}")
                
                # CONTEXT WINDOW PROTECTION: Smart sampling for very large datasets
                MAX_CONTEXT_ROWS = 1000  # Conservative limit to prevent context overflow
                original_rows = len(df)
                was_sampled = False
                sampling_message = ""
                
                if original_rows > MAX_CONTEXT_ROWS:
                    # Smart sampling: Try to get a representative sample
                    if 'date' in str(df.columns).lower() or 'time' in str(df.columns).lower():
                        # For time-series data, get recent data
                        date_cols = [col for col in df.columns if 'date' in col.lower() or 'time' in col.lower()]
                        if date_cols:
                            try:
                                df[date_cols[0]] = pd.to_datetime(df[date_cols[0]], errors='coerce')
                                df = df.sort_values(date_cols[0], ascending=False).head(MAX_CONTEXT_ROWS)
                                sampling_message = f"Sampled {MAX_CONTEXT_ROWS} most recent records from {original_rows} total records to prevent context overflow."
                                was_sampled = True
                            except:
                                # Fallback to random sampling
                                df = df.sample(n=MAX_CONTEXT_ROWS, random_state=42)
                                sampling_message = f"Randomly sampled {MAX_CONTEXT_ROWS} records from {original_rows} total records to prevent context overflow."
                                was_sampled = True
                    else:
                        # Random sampling for other data types
                        df = df.sample(n=MAX_CONTEXT_ROWS, random_state=42)
                        sampling_message = f"Randomly sampled {MAX_CONTEXT_ROWS} records from {original_rows} total records to prevent context overflow."
                        was_sampled = True
                
                # Prepare the return message
                base_message = 'Dataset loaded successfully. Use generate_map_with_query for map generation.'
                if was_sampled:
                    base_message = f"⚠️  DATA SAMPLING APPLIED: {sampling_message} {base_message}"
                    logger.warning(sampling_message)
                
                return {
                    'status': 'success', 
                    'data': df.to_dict('records'),
                    'shape': df.shape,
                    'original_rows': original_rows,
                    'was_sampled': was_sampled,
                    'columns': list(df.columns),
                    'queryURL': result.get('queryURL'),
                    'message': base_message
                }
            else:
                logger.warning("API returned empty data")
                return {
                    'status': 'error',
                    'error': 'No data returned from the API', 
                    'queryURL': result.get('queryURL'),
                    'error_type': 'empty_data'
                }
        elif 'error' in result:
            logger.error(f"API returned error: {result['error']}")
            return {
                'status': 'error',
                'error': result['error'], 
                'queryURL': result.get('queryURL'),
                'error_type': 'api_error'
            }
        else:
            logger.error("Unexpected API response format")
            return {
                'status': 'error',
                'error': 'Unexpected API response format', 
                'queryURL': result.get('queryURL'),
                'error_type': 'unexpected_format'
            }
            
    except Exception as e:
        logger.exception("Unexpected error in set_dataset_tool")
        return {
            'status': 'error',
            'error': f'Unexpected error: {str(e)}', 
            'queryURL': None,
            'error_type': 'unexpected_error'
        }


def query_docs_tool(collection_name: str, query: str) -> Dict[str, Any]:
    """
    Search for additional context in documentation.
    
    Args:
        collection_name: The name of the document collection to search (e.g., "SFPublicData")
        query: The search query string describing what information you're looking for
        
    Returns:
        Dictionary with search results
    """
    logger.info("=== Starting query_docs_tool ===")
    logger.info(f"Collection: {collection_name}")
    logger.info(f"Query: {query}")
    
    try:
        # Import the original function and call it
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        from tools.vector_query import query_docs
        
        # Call the original function with the correct parameters
        # The original function expects (context_variables, collection_name, query)
        # Since we removed context_variables, we'll pass an empty dict
        result = query_docs({}, collection_name, query)
        
        logger.info(f"Query completed successfully")
        return {'status': 'success', 'results': result}
        
    except Exception as e:
        logger.exception("Error in query_docs_tool")
        return {'error': f'Error querying documentation: {str(e)}'}

def get_notes_tool() -> Dict[str, Any]:
    """
    Get summary of available analysis and documentation.
    
    Returns:
        Dictionary with notes information
    """
    logger.info("=== Starting get_notes_tool ===")
    
    try:
        # Import the original function and call it
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        from tools.notes_manager import get_notes
        
        # Call the original function with empty context
        result = get_notes({})
        
        logger.info("Notes retrieved successfully")
        return {'status': 'success', 'notes': result}
        
    except Exception as e:
        logger.exception("Error in get_notes_tool")
        return {'error': f'Error retrieving notes: {str(e)}'}

def get_dashboard_metric_tool(district_number: int = 0, metric_id: int = None) -> Dict[str, Any]:
    """
    Retrieve dashboard metric data containing anomalies.
    
    Args:
        district_number: District number (0 for citywide)
        metric_id: Metric ID to retrieve
        
    Returns:
        Dictionary with metric data
    """
    logger.info("=== Starting get_dashboard_metric_tool ===")
    logger.info(f"District: {district_number}")
    logger.info(f"Metric ID: {metric_id}")
    
    try:
        # Import the original function and call it
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        from tools.dashboard_metric_tool import get_dashboard_metric
        
        # Call the original function with empty context
        result = get_dashboard_metric({}, district_number=district_number, metric_id=metric_id)
        
        logger.info("Dashboard metric retrieved successfully")
        return {'status': 'success', 'metric_data': result}
        
    except Exception as e:
        logger.exception("Error in get_dashboard_metric_tool")
        return {'error': f'Error retrieving dashboard metric: {str(e)}'}

def query_anomalies_db_tool(query_type: str = 'by_metric_id', metric_id: int = None, district_filter: int = None, only_anomalies: bool = True) -> Dict[str, Any]:
    """
    Query anomalies directly from the PostgreSQL database.
    
    Args:
        query_type: Type of query ('by_metric_id', etc.)
        metric_id: Metric ID to query
        district_filter: District filter
        only_anomalies: Whether to return only anomalies
        
    Returns:
        Dictionary with anomaly data
    """
    logger.info("=== Starting query_anomalies_db_tool ===")
    logger.info(f"Query type: {query_type}")
    logger.info(f"Metric ID: {metric_id}")
    logger.info(f"District filter: {district_filter}")
    logger.info(f"Only anomalies: {only_anomalies}")
    
    try:
        # Import the original function and call it
        from anomalyAnalyzer import query_anomalies_db
        
        # Call the original function with empty context
        result = query_anomalies_db({}, query_type=query_type, metric_id=metric_id, district_filter=district_filter, only_anomalies=only_anomalies)
        
        logger.info("Anomalies query completed successfully")
        return {'status': 'success', 'anomalies': result}
        
    except Exception as e:
        logger.exception("Error in query_anomalies_db_tool")
        return {'error': f'Error querying anomalies: {str(e)}'}

def get_anomaly_details_tool(anomaly_id: int) -> Dict[str, Any]:
    """
    Get detailed information about a specific anomaly by ID.
    
    Args:
        anomaly_id: ID of the anomaly to retrieve
        
    Returns:
        Dictionary with anomaly details
    """
    logger.info("=== Starting get_anomaly_details_tool ===")
    logger.info(f"Anomaly ID: {anomaly_id}")
    
    try:
        # Import the original function and call it
        from anomalyAnalyzer import get_anomaly_details
        
        # Call the original function with empty context
        result = get_anomaly_details({}, anomaly_id=anomaly_id)
        
        logger.info("Anomaly details retrieved successfully")
        return {'status': 'success', 'anomaly_details': result}
        
    except Exception as e:
        logger.exception("Error in get_anomaly_details_tool")
        return {'error': f'Error retrieving anomaly details: {str(e)}'}

def get_dataset_columns_tool(endpoint: str) -> Dict[str, Any]:
    """
    Get column information for a dataset endpoint.
    
    Args:
        endpoint: Dataset endpoint to query
        
    Returns:
        Dictionary with column information
    """
    logger.info("=== Starting get_dataset_columns_tool ===")
    logger.info(f"Endpoint: {endpoint}")
    
    try:
        # Import the function from anomalyAnalyzer
        from anomalyAnalyzer import get_dataset_columns
        
        # Call the database version with empty context
        result = get_dataset_columns({}, endpoint=endpoint)
        
        logger.info("Dataset columns retrieved successfully from database")
        return {'status': 'success', 'columns': result}
        
    except Exception as e:
        logger.exception("Error in get_dataset_columns_tool")
        return {'error': f'Error retrieving dataset columns: {str(e)}'}

def get_charts_for_review_tool(
    limit: int = 8,  # Reduced from 20 to 8
    days_back: int = 7,  # Reduced from 30 to 7
    district_filter: str = None, 
    metric_id: str = None,
    only_recent: bool = True,  # New parameter
    max_total_charts: int = 20,  # New parameter
    include_metadata: bool = True,  # Changed to True to ensure caption extraction
    include_urls: bool = False,  # New parameter
    sort_by: str = "created_at"  # New parameter
) -> Dict[str, Any]:
    """
    Get available charts for newsletter inclusion review.
    
    Args:
        limit: Maximum number of charts to return per type (default: 8, reduced from 20)
        days_back: Number of days to look back (default: 7, reduced from 30)
        district_filter: District filter
        metric_id: Filter by specific metric ID/object_id (optional)
        only_recent: Only return charts from last 7 days (default: True)
        max_total_charts: Maximum total charts across all types (default: 20)
        include_metadata: Whether to include full metadata (default: True - ensures caption extraction)
        include_urls: Whether to include query URLs (default: False)
        sort_by: Sort order - created_at, out_of_bounds (default: created_at)
        
    Returns:
        Dictionary with chart information
    """
    logger.info("=== Starting get_charts_for_review_tool ===")
    logger.info(f"Limit: {limit}")
    logger.info(f"Days back: {days_back}")
    logger.info(f"District filter: {district_filter}")
    logger.info(f"Metric ID: {metric_id}")
    logger.info(f"Only recent: {only_recent}")
    logger.info(f"Max total charts: {max_total_charts}")
    logger.info(f"Include metadata: {include_metadata}")
    logger.info(f"Include URLs: {include_urls}")
    logger.info(f"Sort by: {sort_by}")
    
    try:
        # Import the original function and call it
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        from tools.get_charts_for_review import get_charts_for_review
        
        # Call the original function with new parameters
        result = get_charts_for_review(
            context_variables={}, 
            limit=limit, 
            days_back=days_back, 
            district_filter=district_filter, 
            metric_id=metric_id,
            only_recent=only_recent,
            max_total_charts=max_total_charts,
            include_metadata=include_metadata,
            include_urls=include_urls,
            sort_by=sort_by
        )
        
        logger.info("Charts for review retrieved successfully")
        return {'status': 'success', 'charts': result}
        
    except Exception as e:
        logger.exception("Error in get_charts_for_review_tool")
        return {'error': f'Error retrieving charts for review: {str(e)}'} 


def search_web_tool(query: str, system_message: str = None) -> Dict[str, Any]:
    """
    Search the web for real-time information and context using Perplexity AI.
    Use this to find current events, recent news, explanations, or additional 
    context that might help explain data trends or anomalies.
    
    Args:
        query: The search query describing what information you're looking for
        system_message: Optional custom system message to guide the search (defaults to general research prompt)
        
    Returns:
        Dictionary with status, context content, citations, and other metadata
    """
    logger.info("=== Starting search_web_tool ===")
    logger.info(f"Query: {query}")
    
    try:
        # Import required modules
        import os
        import requests
        import json
        
        # Check if Perplexity API key is available
        PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
        if not PERPLEXITY_API_KEY:
            logger.warning("No Perplexity API key available")
            return {
                'status': 'error',
                'error': 'Perplexity API key not configured. Web search is not available.',
                'error_type': 'configuration_error'
            }
        
        # Default system message if not provided
        if not system_message:
            system_message = """You are a helpful research assistant. Provide accurate, 
            up-to-date information based on web search results. Include relevant citations 
            and focus on factual information that can help explain data trends and patterns."""
        
        # Make the API call to Perplexity
        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}"
        }
        
        payload = {
            "model": "sonar",
            "messages": [
                {
                    "role": "system",
                    "content": system_message
                },
                {
                    "role": "user",
                    "content": query
                }
            ]
        }
        
        # Make the API request
        logger.info(f"Sending request to Perplexity API")
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        # Check if the request was successful
        if response.status_code == 200:
            logger.info("Successfully received response from Perplexity API")
            result = response.json()
            
            # Extract the content from the response
            context_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not context_content:
                logger.warning("Received empty content from Perplexity API")
                return {
                    'status': 'error',
                    'error': 'Empty response from Perplexity API',
                    'error_type': 'empty_response'
                }
            
            # Extract citations and other metadata
            perplexity_response = {}
            
            # Extract citations from the top level of the response
            if "citations" in result:
                perplexity_response["citations"] = result["citations"]
                logger.info(f"Found top-level citations: {len(result['citations'])} citations")
            else:
                # Fallback: Check if citations are in the message
                if "choices" in result and len(result["choices"]) > 0:
                    message = result["choices"][0].get("message", {})
                    if "citations" in message:
                        perplexity_response["citations"] = message["citations"]
                        logger.info(f"Found message-level citations: {len(message['citations'])} citations")
            
            # Store other relevant fields from the top level
            for field in ["links", "search_queries", "attachments", "tool_calls"]:
                if field in result:
                    perplexity_response[field] = result[field]
                    logger.info(f"Found top-level {field} in Perplexity response")
            
            logger.info(f"Web search completed successfully (content length: {len(context_content)})")
            
            return {
                'status': 'success',
                'content': context_content,
                'citations': perplexity_response.get("citations", []),
                'metadata': perplexity_response,
                'query': query
            }
        else:
            error_text = response.text
            logger.error(f"Perplexity API error: {response.status_code} - {error_text}")
            return {
                'status': 'error',
                'error': f'Perplexity API error: {response.status_code}',
                'error_details': error_text,
                'error_type': 'api_error'
            }
            
    except requests.exceptions.Timeout:
        logger.error("Perplexity API request timed out")
        return {
            'status': 'error',
            'error': 'Request to Perplexity API timed out',
            'error_type': 'timeout'
        }
    except Exception as e:
        logger.exception("Error in search_web_tool")
        return {
            'status': 'error',
            'error': f'Error searching web: {str(e)}',
            'error_type': 'unexpected_error'
        }