"""
Query Configuration Schema for TransparentSF Metrics

This module defines the structured schema for storing query configuration
data in the metrics table metadata field, replacing regex-based parsing
with explicit configuration.
"""

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum

class AggregationType(Enum):
    """Supported aggregation types for metrics."""
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"
    COUNT_DISTINCT = "COUNT_DISTINCT"

class DateTruncType(Enum):
    """Supported date truncation types."""
    YEAR = "y"
    MONTH = "ym"
    DAY = "ymd"

@dataclass
class DateFieldConfig:
    """Configuration for date field extraction and handling."""
    # The actual date field name in the database
    field_name: str
    
    # The field name to use in the query (may be different due to aliasing)
    query_field_name: str
    
    # The date truncation type used in the query
    trunc_type: DateTruncType
    
    # Whether this is a fiscal year field
    is_fiscal_year: bool = False
    
    # Fallback field name if the primary field is not available
    fallback_field: Optional[str] = None
    
    # Custom date logic (CASE statements, etc.)
    custom_logic: Optional[str] = None

@dataclass
class AggregationConfig:
    """Configuration for aggregation function."""
    # The type of aggregation
    type: AggregationType
    
    # The field being aggregated (None for COUNT(*))
    field: Optional[str] = None
    
    # Whether to use DISTINCT
    distinct: bool = False
    
    # Custom aggregation expression
    custom_expression: Optional[str] = None

@dataclass
class QueryTransformationConfig:
    """Configuration for query transformation."""
    # Date field configuration
    date_field: DateFieldConfig
    
    # Aggregation configuration
    aggregation: AggregationConfig
    
    # Whether this is a YTD (year-to-date) query
    is_ytd_query: bool = True
    
    # Whether this query supports district-level data
    supports_districts: bool = False
    
    # Whether this query supports time periods
    supports_time_periods: bool = True
    
    # Custom WHERE conditions to add
    custom_where_conditions: List[str] = None
    
    # Custom GROUP BY fields to add
    custom_group_by_fields: List[str] = None
    
    # Custom ORDER BY clause
    custom_order_by: Optional[str] = None

@dataclass
class QueryConfig:
    """Complete query configuration for a metric."""
    # Configuration for YTD query
    ytd_config: QueryTransformationConfig
    
    # Configuration for metric query (if different from YTD)
    metric_config: Optional[QueryTransformationConfig] = None
    
    # Whether to use the same config for both queries
    use_same_config: bool = True
    
    # Additional metadata
    description: Optional[str] = None
    version: str = "1.0"

def create_query_config(
    date_field: str,
    query_field_name: str = None,
    trunc_type: str = "ymd",
    aggregation_type: str = "COUNT",
    aggregation_field: str = None,
    is_fiscal_year: bool = False,
    supports_districts: bool = False,
    is_ytd_query: bool = True,
    custom_logic: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a query configuration dictionary for storage in the metrics metadata field.
    
    Args:
        date_field: The actual date field name in the database
        query_field_name: The field name used in queries (defaults to date_field)
        trunc_type: Date truncation type ("y", "ym", "ymd")
        aggregation_type: Type of aggregation ("COUNT", "SUM", "AVG", etc.)
        aggregation_field: Field being aggregated (None for COUNT(*))
        is_fiscal_year: Whether this uses fiscal year logic
        supports_districts: Whether this query supports district-level data
        is_ytd_query: Whether this is a year-to-date query
        custom_logic: Custom date logic (CASE statements, etc.)
        **kwargs: Additional configuration options
    
    Returns:
        Dictionary suitable for storage in metrics.metadata
    """
    if query_field_name is None:
        query_field_name = date_field
    
    # Create date field configuration
    date_config = {
        "field_name": date_field,
        "query_field_name": query_field_name,
        "trunc_type": trunc_type,
        "is_fiscal_year": is_fiscal_year,
        "fallback_field": kwargs.get("fallback_field"),
        "custom_logic": custom_logic
    }
    
    # Create aggregation configuration
    aggregation_config = {
        "type": aggregation_type,
        "field": aggregation_field,
        "distinct": kwargs.get("distinct", False),
        "custom_expression": kwargs.get("custom_expression")
    }
    
    # Create query transformation configuration
    query_transformation_config = {
        "date_field": date_config,
        "aggregation": aggregation_config,
        "is_ytd_query": is_ytd_query,
        "supports_districts": supports_districts,
        "supports_time_periods": kwargs.get("supports_time_periods", True),
        "custom_where_conditions": kwargs.get("custom_where_conditions", []),
        "custom_group_by_fields": kwargs.get("custom_group_by_fields", []),
        "custom_order_by": kwargs.get("custom_order_by")
    }
    
    # Create complete configuration
    config = {
        "query_config": {
            "ytd_config": query_transformation_config,
            "metric_config": kwargs.get("metric_config"),
            "use_same_config": kwargs.get("use_same_config", True),
            "description": kwargs.get("description"),
            "version": "1.0"
        }
    }
    
    return config

def get_date_field_from_config(metadata: Dict[str, Any]) -> Optional[str]:
    """
    Extract the date field from query configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        The date field name, or None if not found
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        date_field_config = ytd_config.get("date_field", {})
        return date_field_config.get("field_name")
    except (KeyError, TypeError):
        return None

def get_aggregation_from_config(metadata: Dict[str, Any]) -> Optional[str]:
    """
    Extract the aggregation function from query configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        The aggregation function string, or None if not found
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        aggregation_config = ytd_config.get("aggregation", {})
        
        agg_type = aggregation_config.get("type", "COUNT")
        field = aggregation_config.get("field")
        distinct = aggregation_config.get("distinct", False)
        custom_expression = aggregation_config.get("custom_expression")
        
        if custom_expression:
            return custom_expression
        
        if agg_type == "COUNT":
            if field is None:
                return "COUNT(*)"
            elif distinct:
                return f"COUNT(DISTINCT {field})"
            else:
                return f"COUNT({field})"
        elif field:
            return f"{agg_type}({field})"
        else:
            return f"{agg_type}(*)"
            
    except (KeyError, TypeError):
        return None

def get_trunc_type_from_config(metadata: Dict[str, Any]) -> Optional[str]:
    """
    Extract the date truncation type from query configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        The truncation type string, or None if not found
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        date_field_config = ytd_config.get("date_field", {})
        return date_field_config.get("trunc_type", "ymd")
    except (KeyError, TypeError):
        return None

def is_fiscal_year_from_config(metadata: Dict[str, Any]) -> bool:
    """
    Check if the query uses fiscal year logic from configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        True if fiscal year logic is used, False otherwise
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        date_field_config = ytd_config.get("date_field", {})
        return date_field_config.get("is_fiscal_year", False)
    except (KeyError, TypeError):
        return False

def supports_districts_from_config(metadata: Dict[str, Any]) -> bool:
    """
    Check if the query supports district-level data from configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        True if district data is supported, False otherwise
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        return ytd_config.get("supports_districts", False)
    except (KeyError, TypeError):
        return False

def is_ytd_query_from_config(metadata: Dict[str, Any]) -> bool:
    """
    Check if this is a year-to-date query from configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        True if this is a YTD query, False otherwise
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        return ytd_config.get("is_ytd_query", True)
    except (KeyError, TypeError):
        return True

def get_where_conditions_from_config(metadata: Dict[str, Any]) -> List[str]:
    """
    Extract custom WHERE conditions from query configuration metadata.
    
    Args:
        metadata: The metadata dictionary from the metrics table
        
    Returns:
        List of WHERE condition strings, or empty list if not found
    """
    try:
        query_config = metadata.get("query_config", {})
        ytd_config = query_config.get("ytd_config", {})
        
        return ytd_config.get("custom_where_conditions", [])

    except (KeyError, TypeError):
        return []

# Example configurations for common metric types
EXAMPLE_CONFIGS = {
    "crime_incident": create_query_config(
        date_field="Report_Datetime",
        trunc_type="ymd",
        aggregation_type="COUNT",
        supports_districts=True,
        description="Standard crime incident metric"
    ),
    
    "business_license": create_query_config(
        date_field="dba_start_date",
        query_field_name="CASE WHEN dba_start_date >= last_year_start THEN dba_start_date ELSE location_start_date END",
        trunc_type="ymd",
        aggregation_type="COUNT",
        supports_districts=True,
        custom_logic="CASE WHEN dba_start_date >= last_year_start THEN dba_start_date ELSE location_start_date END",
        fallback_field="location_start_date",
        description="Business license metric with complex date logic"
    ),
    
    "housing_permits": create_query_config(
        date_field="date_issued",
        trunc_type="ymd",
        aggregation_type="SUM",
        aggregation_field="number_of_units_certified",
        supports_districts=True,
        description="Housing permits metric with unit count aggregation"
    ),
    
    "fiscal_year_metric": create_query_config(
        date_field="fiscal_year",
        trunc_type="y",
        aggregation_type="COUNT",
        is_fiscal_year=True,
        supports_districts=False,
        description="Fiscal year-based metric"
    )
}
