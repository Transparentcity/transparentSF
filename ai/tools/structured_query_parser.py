"""
Structured Query Parser for TransparentSF Metrics

This module provides functions to extract query configuration information
from structured metadata instead of using regex parsing.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
try:
    from .query_config_schema import (
        get_date_field_from_config,
        get_aggregation_from_config,
        get_trunc_type_from_config,
        is_fiscal_year_from_config,
        supports_districts_from_config,
        is_ytd_query_from_config,
        get_where_conditions_from_config
    )
except ImportError:
    from query_config_schema import (
        get_date_field_from_config,
        get_aggregation_from_config,
        get_trunc_type_from_config,
        is_fiscal_year_from_config,
        supports_districts_from_config,
        is_ytd_query_from_config,
        get_where_conditions_from_config
    )

logger = logging.getLogger(__name__)

def extract_date_field_structured(metric_info: Dict[str, Any], query: str = None) -> Optional[str]:
    """
    Extract the date field from metric metadata using structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        query: Optional query string (for fallback to regex if needed)
        
    Returns:
        The date field name, or None if not found
    """
    # First try to get from structured configuration
    metadata = metric_info.get('metadata', {})
    date_field = get_date_field_from_config(metadata)
    
    if date_field:
        logger.info(f"Found date field from structured config: {date_field}")
        return date_field
    
    # Fallback to regex parsing if no structured config
    if query:
        logger.warning("No structured date field config found, falling back to regex parsing")
        return extract_date_field_regex_fallback(query)
    
    logger.warning("No date field found in structured config or query")
    return None

def extract_aggregation_function_structured(metric_info: Dict[str, Any], query: str = None) -> str:
    """
    Extract the aggregation function from metric metadata using structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        query: Optional query string (for fallback to regex if needed)
        
    Returns:
        The aggregation function string, defaults to 'COUNT(*)' if not found
    """
    # First try to get from structured configuration
    metadata = metric_info.get('metadata', {})
    aggregation_func = get_aggregation_from_config(metadata)
    
    if aggregation_func:
        logger.info(f"Found aggregation function from structured config: {aggregation_func}")
        return aggregation_func
    
    # Fallback to regex parsing if no structured config
    if query:
        logger.warning("No structured aggregation config found, falling back to regex parsing")
        return extract_aggregation_function_regex_fallback(query)
    
    logger.warning("No aggregation function found, using default COUNT(*)")
    return 'COUNT(*)'

def determine_date_field_name_structured(metric_info: Dict[str, Any], period_type: str) -> str:
    """
    Determine the appropriate date field name based on structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        period_type: The period type ('month' or 'year')
        
    Returns:
        The appropriate date field name for the period type
    """
    metadata = metric_info.get('metadata', {})
    trunc_type = get_trunc_type_from_config(metadata)
    
    if trunc_type == "ymd":
        return 'day'
    elif trunc_type == "ym":
        return 'month'
    elif trunc_type == "y":
        return 'year'
    else:
        # Default based on period type
        return period_type

def is_fiscal_year_query_structured(metric_info: Dict[str, Any]) -> bool:
    """
    Check if this is a fiscal year query using structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        
    Returns:
        True if this is a fiscal year query, False otherwise
    """
    metadata = metric_info.get('metadata', {})
    return is_fiscal_year_from_config(metadata)

def supports_districts_structured(metric_info: Dict[str, Any]) -> bool:
    """
    Check if this query supports district-level data using structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        
    Returns:
        True if district data is supported, False otherwise
    """
    metadata = metric_info.get('metadata', {})
    return supports_districts_from_config(metadata)

def is_ytd_query_structured(metric_info: Dict[str, Any]) -> bool:
    """
    Check if this is a year-to-date query using structured configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        
    Returns:
        True if this is a YTD query, False otherwise
    """
    metadata = metric_info.get('metadata', {})
    return is_ytd_query_from_config(metadata)

def get_query_config_structured(metric_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Get the complete query configuration from metric metadata.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        
    Returns:
        The query configuration dictionary, or None if not found
    """
    metadata = metric_info.get('metadata', {})
    return metadata.get('query_config')

def extract_date_field_regex_fallback(query: str) -> Optional[str]:
    """
    Fallback regex-based date field extraction (for backwards compatibility).
    
    Args:
        query: The SQL query string
        
    Returns:
        The date field name, or None if not found
    """
    import re
    
    # First, look for any field being aliased as 'date'
    date_alias_match = re.search(r'(\w+)\s+as\s+date\b', query, re.IGNORECASE)
    if date_alias_match:
        field = date_alias_match.group(1).strip()
        logger.info(f"Found date field from alias (regex fallback): {field} as date")
        return field
    
    # Try to find date_trunc patterns
    date_trunc_match = re.search(r'date_trunc_[ymd]+ *\( *([^\)]+) *\)', query)
    if date_trunc_match:
        field = date_trunc_match.group(1).strip()
        logger.info(f"Found date field from date_trunc (regex fallback): {field}")
        return field
    
    # Fallback to checking common date field names
    date_fields_to_check = [
        'date', 'incident_date', 'report_date', 'arrest_date', 'received_datetime', 
        'Report_Datetime', 'disposition_date', 'dba_start_date'
    ]
    
    for field in date_fields_to_check:
        if field in query:
            logger.info(f"Found date field in query (regex fallback): {field}")
            return field
    
    logger.warning("No date field found in query (regex fallback)")
    return None

def extract_aggregation_function_regex_fallback(query: str) -> str:
    """
    Fallback regex-based aggregation function extraction (for backwards compatibility).
    
    Args:
        query: The SQL query string
        
    Returns:
        The aggregation function string, defaults to 'COUNT(*)' if not found
    """
    import re
    
    # Look for common aggregation patterns
    aggregation_patterns = [
        r'(SUM\s*\(\s*count\s*\))',          # SUM(count) - most specific first
        r'(COUNT\s*\(\s*DISTINCT\s+[^)]+\))', # COUNT(DISTINCT field)
        r'(COUNT\s*\(\s*\*\s*\))',            # COUNT(*)
        r'(SUM\s*\([^)]+\))',                 # SUM(field)
        r'(AVG\s*\([^)]+\))',                 # AVG(field)
        r'(MAX\s*\([^)]+\))',                 # MAX(field)
        r'(MIN\s*\([^)]+\))',                 # MIN(field)
    ]
    
    for pattern in aggregation_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            aggregation_func = match.group(1)
            logger.info(f"Extracted aggregation function (regex fallback): {aggregation_func}")
            return aggregation_func
    
    # Default fallback to COUNT(*)
    logger.warning("Could not extract aggregation function (regex fallback), using default COUNT(*)")
    return 'COUNT(*)'

def get_structured_query_info(metric_info: Dict[str, Any], query: str = None) -> Dict[str, Any]:
    """
    Get all structured query information for a metric.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        query: Optional query string (for fallback to regex if needed)
        
    Returns:
        Dictionary containing all query configuration information
    """
    return {
        'date_field': extract_date_field_structured(metric_info, query),
        'aggregation_function': extract_aggregation_function_structured(metric_info, query),
        'date_field_name': determine_date_field_name_structured(metric_info, 'month'),
        'is_fiscal_year': is_fiscal_year_query_structured(metric_info),
        'supports_districts': supports_districts_structured(metric_info),
        'is_ytd_query': is_ytd_query_structured(metric_info),
        'query_config': get_query_config_structured(metric_info)
    }

def validate_query_config(metric_info: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate that a metric has proper query configuration.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Check if metadata exists
    metadata = metric_info.get('metadata', {})
    if not metadata:
        errors.append("No metadata found in metric_info")
        return False, errors
    
    # Check if query_config exists
    query_config = metadata.get('query_config')
    if not query_config:
        errors.append("No query_config found in metadata")
        return False, errors
    
    # Check if ytd_config exists
    ytd_config = query_config.get('ytd_config')
    if not ytd_config:
        errors.append("No ytd_config found in query_config")
        return False, errors
    
    # Check if date_field config exists
    date_field_config = ytd_config.get('date_field')
    if not date_field_config:
        errors.append("No date_field config found in ytd_config")
        return False, errors
    
    # Check if aggregation config exists
    aggregation_config = ytd_config.get('aggregation')
    if not aggregation_config:
        errors.append("No aggregation config found in ytd_config")
        return False, errors
    
    # Check required fields
    if not date_field_config.get('field_name'):
        errors.append("date_field.field_name is required")
    
    if not aggregation_config.get('type'):
        errors.append("aggregation.type is required")
    
    return len(errors) == 0, errors
