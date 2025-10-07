"""
Structured Metric Processor for TransparentSF

This module provides a structured approach to processing metrics without
relying on regex parsing. It uses the query configuration stored in the
metrics metadata field.
"""

import os
import sys
import json
import logging
import traceback
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date, timedelta

# Add the parent directory to the path so we can import from ai.tools
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.structured_query_parser import (
    get_structured_query_info,
    validate_query_config,
    extract_date_field_structured,
    extract_aggregation_function_structured,
    determine_date_field_name_structured,
    is_fiscal_year_query_structured,
    supports_districts_structured,
    is_ytd_query_structured
)

logger = logging.getLogger(__name__)

def get_period_field_name(period_type: str) -> str:
    """
    Get the field name used for the time period in structured queries.
    
    Args:
        period_type: 'month' or 'year'
        
    Returns:
        Field name used in the query (e.g., 'month_period', 'year_period')
    """
    if period_type == 'year':
        return 'year_period'
    else:  # month or default
        return 'month_period'

def get_time_ranges(period_type: str, is_fiscal_year: bool = False) -> Tuple[Dict[str, date], Dict[str, date]]:
    """
    Calculate recent and comparison periods based on period type.
    
    Args:
        period_type: 'month' or 'year'
        is_fiscal_year: Whether to use fiscal year logic
        
    Returns:
        Tuple of (recent_period, comparison_period) dictionaries
    """
    today = date.today()
    
    if is_fiscal_year:
        # Fiscal year logic (July 1 to June 30)
        if today.month >= 7:  # July or later
            current_fiscal_year = today.year + 1
        else:  # January to June
            current_fiscal_year = today.year
        
        if period_type == 'year':
            # Compare last fiscal year to 6 years before
            recent_period = {
                'start': date(current_fiscal_year - 1, 7, 1),
                'end': date(current_fiscal_year - 1, 6, 30)
            }
            comparison_period = {
                'start': date(current_fiscal_year - 7, 7, 1),
                'end': date(current_fiscal_year - 7, 6, 30)
            }
        else:  # month
            # Use current and previous fiscal years
            recent_period = {
                'start': date(current_fiscal_year - 1, 7, 1),
                'end': date(current_fiscal_year - 1, 6, 30)
            }
            comparison_period = {
                'start': date(current_fiscal_year - 2, 7, 1),
                'end': date(current_fiscal_year - 2, 6, 30)
            }
    else:
        # Calendar year logic
        if period_type == 'year':
            # Compare last year to 6 years before
            recent_period = {
                'start': date(today.year - 1, 1, 1),
                'end': date(today.year - 1, 12, 31)
            }
            comparison_period = {
                'start': date(today.year - 7, 1, 1),
                'end': date(today.year - 7, 12, 31)
            }
        else:  # month
            # Use the previous complete month (same logic as non-structured path)
            if today.month == 1:
                recent_month = 12
                recent_year = today.year - 1
            else:
                recent_month = today.month - 1
                recent_year = today.year
            
            # Calculate last day of the month
            if recent_month == 12:
                last_day = 31
            elif recent_month in [4, 6, 9, 11]:
                last_day = 30
            elif recent_month == 2:
                # Handle leap years
                if recent_year % 4 == 0 and (recent_year % 100 != 0 or recent_year % 400 == 0):
                    last_day = 29
                else:
                    last_day = 28
            else:
                last_day = 31
            
            recent_period = {
                'start': date(recent_year, recent_month, 1),
                'end': date(recent_year, recent_month, last_day)
            }
            
            # Compare to previous 24 months
            comparison_start_month = recent_month
            comparison_start_year = recent_year - 2
            
            comparison_period = {
                'start': date(comparison_start_year, comparison_start_month, 1),
                'end': date(recent_year, recent_month, 1) - timedelta(days=1)
            }
    
    return recent_period, comparison_period

def process_metric_analysis_structured(metric_info: Dict[str, Any], period_type: str = 'month', process_districts: bool = False) -> Dict[str, Any]:
    """
    Process metric analysis using structured configuration instead of regex parsing.
    
    Args:
        metric_info: Dictionary containing metric information including metadata
        period_type: 'month' or 'year'
        process_districts: Whether to process district-level data
        
    Returns:
        Dictionary containing analysis results
    """
    # Extract basic metric information
    metric_id = metric_info.get('metric_id', '')
    query_name = metric_info.get('query_name', metric_id)
    definition = metric_info.get('definition', '')
    summary = metric_info.get('summary', '')
    endpoint = metric_info.get('endpoint', '')
    data_sf_url = metric_info.get('data_sf_url', '')
    
    # Determine period description
    period_desc = 'Monthly' if period_type == 'month' else 'Annual'
    
    # Get structured query information
    query_info = get_structured_query_info(metric_info)
    
    # Validate query configuration
    is_valid, errors = validate_query_config(metric_info)
    if not is_valid:
        logger.warning(f"Invalid query configuration for metric {query_name}: {errors}")
        # Fall back to basic processing
        return process_metric_analysis_fallback(metric_info, period_type, process_districts)
    
    # Extract configuration values
    date_field = query_info['date_field']
    aggregation_function = query_info['aggregation_function']
    date_field_name = query_info['date_field_name']
    is_fiscal_year = query_info['is_fiscal_year']
    supports_districts = query_info['supports_districts']
    is_ytd_query = query_info['is_ytd_query']
    
    # Get time ranges
    recent_period, comparison_period = get_time_ranges(period_type, is_fiscal_year)
    
    # Get the original query
    original_query = None
    if isinstance(metric_info.get('query_data'), dict):
        original_query = metric_info['query_data'].get('ytd_query', '')
        if not original_query:
            original_query = metric_info['query_data'].get('metric_query', '')
    else:
        original_query = metric_info.get('query_data', '')
    
    if not original_query:
        logger.error(f"No query found for metric {query_name}")
        return {
            'status': 'error',
            'message': f'No query found for metric {query_name}',
            'metric_id': metric_id
        }
    
    # Get category fields
    category_fields = metric_info.get('category_fields', [])
    
    # Set up filter conditions for date filtering
    # Use the actual field names from the query, not abstract field names
    # For structured queries, match the field name created by the query transformation
    actual_period_field = get_period_field_name(period_type)
    
    filter_conditions = []
    if period_type == 'year':
        # For year periods, make sure we're comparing strings with strings
        year_end = str(recent_period['end'].year)
        year_start = str(comparison_period['start'].year)
        filter_conditions = [
            {'field': actual_period_field, 'operator': '<=', 'value': year_end},
            {'field': actual_period_field, 'operator': '>=', 'value': year_start},
        ]
        logger.info(f"Year filter conditions: {filter_conditions}")
    else:
        # For other period types, use datetime objects to match the converted data
        from datetime import datetime
        # Convert date objects to datetime objects for proper comparison
        start_datetime = datetime.combine(comparison_period['start'], datetime.min.time())
        end_datetime = datetime.combine(recent_period['end'], datetime.min.time())
        
        filter_conditions = [
            {'field': actual_period_field, 'operator': '<=', 'value': end_datetime},
            {'field': actual_period_field, 'operator': '>=', 'value': start_datetime},
        ]
        logger.info(f"Standard filter conditions with datetime: {filter_conditions}")
    
    # Transform the query for the specified period type
    transformed_query = transform_query_for_period_structured(
        original_query,
        query_info,
        category_fields,
        period_type,
        recent_period,
        comparison_period,
        endpoint
    )
    
    # Process the query and generate analysis
    try:
        # Set the dataset
        from tools.data_fetcher import set_dataset
        context_variables = {}
        dataset_result = set_dataset(context_variables, endpoint=endpoint, query=transformed_query)
        
        if 'error' in dataset_result:
            logger.error(f"Error setting dataset for {query_name}: {dataset_result['error']}")
            return {
                'status': 'error',
                'message': f'Error setting dataset for {query_name}: {dataset_result["error"]}',
                'metric_id': metric_id
            }
        
        # Ensure numeric columns are properly typed and handle categorical columns
        if 'dataset' in context_variables:
            dataset = context_variables['dataset']
            import pandas as pd
            
            # Convert the value column to numeric and handle all data types properly
            if 'value' in dataset.columns:
                dataset['value'] = pd.to_numeric(dataset['value'], errors='coerce')
                # Drop any NaN values that might cause aggregation issues
                dataset = dataset.dropna(subset=['value'])
                logger.info(f"Converted value column to numeric and dropped NaN values. Data types: {dataset.dtypes.to_dict()}")
            
            # Convert date column to proper datetime if it exists
            if 'month_period' in dataset.columns:
                dataset['month_period'] = pd.to_datetime(dataset['month_period'], errors='coerce')
                logger.info(f"Converted month_period to datetime")
            
            # For categorical columns, convert to string type to avoid aggregation confusion
            categorical_columns = ['supervisor_district']  # Add other categorical columns as needed
            for col in categorical_columns:
                if col in dataset.columns:
                    # Convert to string type and then categorical to avoid pandas confusion
                    dataset[col] = dataset[col].astype(str).astype('category')
                    logger.info(f"Converted {col} to categorical type")
            
            context_variables['dataset'] = dataset
        
        # Import analysis functions from the main module
        import sys
        import os
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.append(parent_dir)
        
        from generate_metric_analysis import process_single_analysis, save_analysis_files
        
        # Determine period_field and value_field based on period_type
        # For structured queries, match the field name created by the query transformation
        period_field = get_period_field_name(period_type)
        value_field = 'value'
        
        # Determine if this uses averaging
        uses_avg = 'AVG' in aggregation_function.upper()
        
        # Map SQL aggregation functions to pandas methods for chart generation
        def sql_to_pandas_agg(sql_func):
            """Convert SQL aggregation function to pandas method name."""
            if 'AVG(' in sql_func.upper():
                return 'mean'
            elif 'SUM(' in sql_func.upper():
                return 'sum'
            elif 'COUNT(' in sql_func.upper():
                return 'count'
            elif 'MAX(' in sql_func.upper():
                return 'max'
            elif 'MIN(' in sql_func.upper():
                return 'min'
            else:
                return 'mean'  # Default fallback
        
        # Convert SQL aggregation function to pandas method
        pandas_agg_method = sql_to_pandas_agg(aggregation_function) if aggregation_function else 'count'
        agg_functions = {value_field: pandas_agg_method}
        
        # Get category fields from metric info
        category_fields = metric_info.get('category_fields', [])
        
        # Process the analysis using the existing logic
        analysis_result = process_single_analysis(
            context_variables=context_variables,
            category_fields=category_fields,
            period_type=period_type,
            period_field=period_field,
            filter_conditions=filter_conditions,
            query_name=f"{query_name} - Citywide",
            period_desc=f"{period_desc} - Citywide",
            value_field=value_field,
            recent_period=recent_period,
            comparison_period=comparison_period,
            uses_avg=uses_avg,
            agg_functions=agg_functions,
            district=0,
            metric_id=metric_id,
            base_metric_name=query_name
        )
        
        # Create the basic result structure
        result = {
            'status': 'success',
            'metric_id': metric_id,
            'query_name': f"{query_name} - Citywide",
            'period_type': period_type,
            'period_desc': f"{period_desc} - Citywide",
            'date_field': date_field,
            'aggregation_function': aggregation_function,
            'supports_districts': supports_districts,
            'is_fiscal_year': is_fiscal_year,
            'is_ytd_query': is_ytd_query,
            'transformed_query': transformed_query,
            'filter_conditions': filter_conditions,
            'recent_period': recent_period,
            'comparison_period': comparison_period
        }
        
        if analysis_result:
            # Save the analysis files
            save_analysis_files(analysis_result, metric_id, period_type, district=0)
            result.update(analysis_result)
            logger.info(f"Successfully generated and saved analysis for {query_name}")
        
        # Process district-level data if requested and if supervisor_district field exists
        if process_districts and supports_districts and 'dataset' in context_variables:
            dataset = context_variables['dataset']
            
            # Check that supervisor_district column actually exists
            if 'supervisor_district' in dataset.columns:
                logger.info("Processing district-level data using supervisor_district column")
                
                # Get all unique district values
                districts = dataset['supervisor_district'].dropna().unique()
                logger.info(f"Found {len(districts)} unique districts to process: {districts}")
                
                # Process each district separately
                for district in districts:
                    try:
                        # Skip non-numeric or invalid districts
                        try:
                            # Convert string floats like '2.00000' or '2.0' to int
                            if isinstance(district, str):
                                try:
                                    district_num = int(float(district))
                                except Exception:
                                    district_num = int(district)
                            else:
                                district_num = int(district)
                            
                            if district_num < 0 or district_num > 11:
                                logger.warning(f"Skipping invalid district number: {district}")
                                continue
                            
                            # Skip district 0 as it's already processed as citywide
                            if district_num == 0:
                                logger.info(f"Skipping district 0 as it's already processed as citywide")
                                continue
                            
                            logger.info(f"Processing district {district_num} (original: {district})")
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Skipping non-numeric district: {district} (error: {e})")
                            continue
                        
                        # Create district-specific filter conditions
                        district_filter_conditions = filter_conditions.copy()
                        district_filter_conditions.append({
                            'field': 'supervisor_district',
                            'operator': '=',
                            'value': str(district_num)
                        })
                        
                        # Process analysis for this district
                        district_result = process_single_analysis(
                            context_variables=context_variables.copy(),
                            category_fields=category_fields,
                            period_type=period_type,
                            period_field=period_field,
                            filter_conditions=district_filter_conditions,
                            query_name=f"{query_name} - District {district_num}",
                            period_desc=f"{period_desc} - District {district_num}",
                            value_field=value_field,
                            recent_period=recent_period,
                            comparison_period=comparison_period,
                            uses_avg=uses_avg,
                            agg_functions=agg_functions,
                            district=district_num,
                            metric_id=metric_id,
                            base_metric_name=query_name
                        )
                        
                        if district_result:
                            district_analysis_result = {
                                'query_name': f"{query_name} - District {district_num}",
                                'period_type': period_type,
                                'markdown': district_result.get('markdown', ''),
                                'html': district_result.get('html', ''),
                                'metric_id': metric_id
                            }
                            
                            # Save the analysis files for this district
                            save_analysis_files(district_analysis_result, metric_id, period_type, district=district_num)
                            logger.info(f"Successfully generated and saved analysis for district {district_num}")
                    except Exception as e:
                        logger.error(f"Error processing district {district}: {e}")
                        logger.error(traceback.format_exc())
            else:
                logger.warning("Cannot process districts: supervisor_district column not found in dataset")
        
        logger.info(f"Successfully processed metric {query_name} using structured configuration")
        return result
        
    except Exception as e:
        logger.error(f"Error processing metric {query_name}: {e}")
        return {
            'status': 'error',
            'message': f'Error processing metric {query_name}: {e}',
            'metric_id': metric_id
        }

def transform_query_for_period_structured(
    original_query: str,
    query_info: Dict[str, Any],
    category_fields: List[Dict[str, Any]],
    period_type: str,
    recent_period: Dict[str, date],
    comparison_period: Dict[str, date],
    endpoint: str,
    district: Optional[int] = None
) -> str:
    """
    Transform a query for monthly or annual analysis using structured configuration.
    
    Args:
        original_query: The original SQL query
        query_info: Structured query information
        category_fields: List of category field definitions
        period_type: 'month' or 'year'
        recent_period: Recent period date range
        comparison_period: Comparison period date range
        endpoint: The DataSF endpoint to query
        district: Optional district number to filter by
        
    Returns:
        Transformed SQL query
    """
    # Extract configuration values
    date_field = query_info['date_field']
    aggregation_function = query_info['aggregation_function']
    is_fiscal_year = query_info['is_fiscal_year']
    supports_districts = query_info['supports_districts']
    is_ytd_query = query_info['is_ytd_query']
    
    # Format date strings for SQL
    recent_start = recent_period['start'].isoformat()
    recent_end = recent_period['end'].isoformat()
    comparison_start = comparison_period['start'].isoformat()
    comparison_end = comparison_period['end'].isoformat()
    
    # Start with the original query
    modified_query = original_query
    
    # Replace date placeholders
    replacements = {}
    if is_fiscal_year:
        # Fiscal year replacements
        today = date.today()
        if today.month >= 7:
            current_fiscal_year = today.year + 1
        else:
            current_fiscal_year = today.year
        
        if period_type == 'year':
            recent_fiscal_year = str(current_fiscal_year - 1)
            comparison_fiscal_year = str(current_fiscal_year - 7)
        else:
            recent_fiscal_year = str(current_fiscal_year - 1)
            comparison_fiscal_year = str(current_fiscal_year - 2)
        
        replacements = {
            'this_year_start': f"'{recent_fiscal_year}'",
            'this_year_end': f"'{recent_fiscal_year}'",
            'last_year_start': f"'{comparison_fiscal_year}'",
            'last_year_end': f"'{comparison_fiscal_year}'",
            'start_date': f"'{comparison_fiscal_year}'",
            'current_date': f"'{recent_fiscal_year}'"
        }
    else:
        # Calendar year replacements
        replacements = {
            'this_year_start': f"'{recent_start}'",
            'this_year_end': f"'{recent_end}'",
            'last_year_start': f"'{comparison_start}'",
            'last_year_end': f"'{comparison_end}'",
            'start_date': f"'{comparison_start}'",
            'current_date': f"'{recent_end}'"
        }
    
    # Apply replacements
    for placeholder, value in replacements.items():
        import re
        pattern = r'\b' + re.escape(placeholder) + r'\b'
        modified_query = re.sub(pattern, value, modified_query)
        logger.info(f"Replaced {placeholder} with {value} in query")
    
    # Handle YTD query transformation
    if is_ytd_query:
        # Generate appropriate date_trunc based on period_type
        period_field = get_period_field_name(period_type)
        if period_type == 'year':
            date_trunc = f"date_trunc_y({date_field})"
        else:  # month
            date_trunc = f"date_trunc_ym({date_field})"
        
        # Build category fields part
        category_select = ""
        group_by_fields = []
        
        for field in category_fields:
            if isinstance(field, dict):
                field_name = field.get('fieldName', '')
            else:
                field_name = field
            
            if field_name:
                # Handle supervisor_district specially
                if field_name == 'supervisor_district':
                    if 'vw6y-z8j6' in original_query or 'requested_datetime' in original_query:
                        category_select += f", floor(supervisor_district) as supervisor_district"
                        if "floor(supervisor_district)" not in group_by_fields:
                            group_by_fields.append("floor(supervisor_district)")
                    else:
                        category_select += f", supervisor_district"
                        if "supervisor_district" not in group_by_fields:
                            group_by_fields.append("supervisor_district")
                else:
                    # For complex CASE statements, use the field as is
                    if 'CASE' in field_name.upper():
                        if isinstance(field, dict):
                            alias = field.get('name', 'category_field')
                            alias = alias.replace(' ', '_').replace('-', '_').lower()
                        else:
                            alias = 'category_field'
                        category_select += f", ({field_name}) as {alias}"
                        group_by_clause = f"({field_name})"
                        if group_by_clause not in group_by_fields:
                            group_by_fields.append(group_by_clause)
                    else:
                        category_select += f", {field_name}"
                        if field_name not in group_by_fields:
                            group_by_fields.append(field_name)
        
        # Get custom WHERE conditions from query_info
        custom_where_conditions = []
        if 'query_config' in query_info and 'ytd_config' in query_info['query_config']:
            custom_where_conditions = query_info['query_config']['ytd_config'].get('custom_where_conditions', [])
        
        # Apply placeholder replacements to custom WHERE conditions
        import re
        replaced_where_conditions = []
        for condition in custom_where_conditions:
            replaced_condition = condition
            for placeholder, value in replacements.items():
                pattern = r'\b' + re.escape(placeholder) + r'\b'
                replaced_condition = re.sub(pattern, value, replaced_condition)
            replaced_where_conditions.append(replaced_condition)
            logger.info(f"Replaced placeholders in WHERE condition: {condition} -> {replaced_condition}")
        
        # Build WHERE clause
        where_conditions = [f"{date_field} >= '{comparison_start}'", f"{date_field} <= '{recent_end}'"]
        where_conditions.extend(replaced_where_conditions)
        where_clause = " AND ".join(where_conditions)
        
        # Build the transformed query (SOQL doesn't use FROM clauses - dataset specified in URL)
        if period_type == 'year':
            transformed_query = f"""
                SELECT 
                    {date_trunc} as {period_field},
                    {aggregation_function} as value{category_select}
                WHERE {where_clause}
                GROUP BY {period_field}{', ' + ', '.join(group_by_fields) if group_by_fields else ''}
                ORDER BY {period_field}
            """
        else:  # month
            transformed_query = f"""
                SELECT 
                    {date_trunc} as {period_field},
                    {aggregation_function} as value{category_select}
                WHERE {where_clause}
                GROUP BY {period_field}{', ' + ', '.join(group_by_fields) if group_by_fields else ''}
                ORDER BY {period_field}
            """
        
        # Add district filter if specified
        if district is not None and supports_districts:
            transformed_query = transformed_query.replace(
                "WHERE",
                f"WHERE supervisor_district = {district} AND"
            )
        
        return transformed_query
    
    # For non-YTD queries, build a new query from metadata instead of using the original query
    # This ensures we use the correct aggregation function and field names from the metadata
    
    # Get the date field and aggregation function from query_info
    date_field = query_info['date_field']
    aggregation_function = query_info['aggregation_function']
    
    # Build date truncation based on period type
    period_field = get_period_field_name(period_type)
    if period_type == 'year':
        date_trunc = f"date_trunc_y({date_field})"
    else:  # month
        date_trunc = f"date_trunc_ym({date_field})"
    
    # Build category fields for SELECT and GROUP BY
    category_select = ""
    group_by_fields = []
    if category_fields:
        for field in category_fields:
            if isinstance(field, dict):
                field_name = field.get('fieldName', '')
                if field_name:
                    category_select += f", {field_name}"
                    group_by_fields.append(field_name)
            else:
                category_select += f", {field}"
                group_by_fields.append(field)
    
    # Build WHERE clause
    where_conditions = [f"{date_field} >= '{comparison_start}'", f"{date_field} <= '{recent_end}'"]
    
    # Get custom where conditions from query_info
    custom_where_conditions = []
    if 'custom_where_conditions' in query_info:
        custom_where_conditions = query_info['custom_where_conditions']
    elif 'query_config' in query_info and 'metric_config' in query_info['query_config']:
        custom_where_conditions = query_info['query_config']['metric_config'].get('custom_where_conditions', [])
    
    # Apply placeholder replacements to custom WHERE conditions
    import re
    replaced_where_conditions = []
    for condition in custom_where_conditions:
        replaced_condition = condition
        for placeholder, value in replacements.items():
            pattern = r'\b' + re.escape(placeholder) + r'\b'
            replaced_condition = re.sub(pattern, value, replaced_condition)
        replaced_where_conditions.append(replaced_condition)
        logger.info(f"Replaced placeholders in WHERE condition: {condition} -> {replaced_condition}")
    
    where_conditions.extend(replaced_where_conditions)
    where_clause = " AND ".join(where_conditions)
    
    # Build the transformed query (SOQL doesn't use FROM clauses - dataset specified in URL)
    if period_type == 'year':
        transformed_query = f"""
            SELECT 
                {date_trunc} as {period_field},
                {aggregation_function} as value{category_select}
            WHERE {where_clause}
            GROUP BY {period_field}{', ' + ', '.join(group_by_fields) if group_by_fields else ''}
            ORDER BY {period_field}
        """
    else:  # month
        transformed_query = f"""
            SELECT 
                {date_trunc} as {period_field},
                {aggregation_function} as value{category_select}
            WHERE {where_clause}
            GROUP BY {period_field}{', ' + ', '.join(group_by_fields) if group_by_fields else ''}
            ORDER BY {period_field}
        """
    
    # Add district filter if specified
    if district is not None and supports_districts:
        transformed_query = transformed_query.replace(
            "WHERE",
            f"WHERE supervisor_district = {district} AND"
        )
    
    return transformed_query

def process_metric_analysis_fallback(metric_info: Dict[str, Any], period_type: str, process_districts: bool) -> Dict[str, Any]:
    """
    Fallback processing when structured configuration is not available.
    
    Args:
        metric_info: Dictionary containing metric information
        period_type: 'month' or 'year'
        process_districts: Whether to process district-level data
        
    Returns:
        Dictionary containing analysis results
    """
    logger.warning("Using fallback processing - structured configuration not available")
    
    # This would call the original regex-based processing
    # For now, return an error indicating fallback is needed
    return {
        'status': 'error',
        'message': 'Structured configuration not available, fallback processing needed',
        'metric_id': metric_info.get('metric_id', ''),
        'fallback_required': True
    }
