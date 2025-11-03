"""
Active Businesses Analysis Route

This route provides monthly analysis of active, new, and closing businesses
based on the business_registrations_cache table.
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from tools.db_utils import get_pooled_connection

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Templates will be set by main app
templates = None

def set_templates(templates_instance):
    """Set templates instance from main app"""
    global templates
    templates = templates_instance

@router.get("/active-businesses")
async def active_businesses_page(request: Request, restaurant_only: bool = False):
    """Serve the active businesses analysis page"""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    return templates.TemplateResponse("active_businesses.html", {
        "request": request,
        "restaurant_only": restaurant_only
    })

@router.get("/api/active-businesses/data")
async def get_active_businesses_data(restaurant_only: bool = False):
    """
    Get monthly active, new, and closing business counts for the past 24 months.
    
    Args:
        restaurant_only: If True, filter to restaurants only (H24, H25, H26 license codes)
    
    Returns:
        JSON with monthly data including active, new, and closed counts
    """
    try:
        # Calculate date range - past 24 months
        end_date = datetime.now()
        start_date = end_date - relativedelta(months=24)
        
        # Generate list of months to analyze
        months = []
        current = start_date.replace(day=1)
        while current <= end_date:
            month_start = current
            # Get last day of month
            if current.month == 12:
                month_end = current.replace(year=current.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = current.replace(month=current.month + 1, day=1) - timedelta(days=1)
            
            months.append({
                'year': current.year,
                'month': current.month,
                'month_start': month_start,
                'month_end': month_end,
                'label': current.strftime('%Y-%m')
            })
            
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        
        # Query database for each month
        results = []
        previous_active = None  # Will be set from first month's query
        
        with get_pooled_connection() as conn:
            cursor = conn.cursor()
            
            # Build restaurant filter if needed
            restaurant_filter = ""
            if restaurant_only:
                # Filter for restaurants using license codes (H24, H25, H26)
                restaurant_filter = """
                    AND (
                        lic_code IS NOT NULL AND (
                            lic_code LIKE '%%H24%%' 
                            OR lic_code LIKE '%%H25%%' 
                            OR lic_code LIKE '%%H26%%'
                        )
                    )
                """
            
            for month_data in months:
                month_start = month_data['month_start']
                month_end = month_data['month_end']
                
                # Combined query: Get new and closed counts in a single query for better performance
                # This reduces 2 separate queries to 1 per month
                combined_query = f"""
                    SELECT 
                        -- New businesses (started within month AND (no end date OR end date after month end))
                        COUNT(*) FILTER (
                            WHERE location_start_date >= %s
                            AND location_start_date <= %s
                            AND (
                                location_end_date IS NULL 
                                OR location_end_date > %s
                            )
                        ) as new_count,
                        -- Closed businesses (closed within month)
                        COUNT(*) FILTER (
                            WHERE location_end_date >= %s
                            AND location_end_date <= %s
                        ) as closed_count
                    FROM business_registrations_cache
                    WHERE 1=1
                    {restaurant_filter}
                """
                try:
                    # Execute combined query
                    cursor.execute(
                        combined_query, 
                        (month_start, month_end, month_end,  # For new_count FILTER
                         month_start, month_end)              # For closed_count FILTER
                    )
                    result = cursor.fetchone()
                    
                    if result:
                        new_count = int(result[0]) if result[0] is not None else 0
                        closed_count = int(result[1]) if result[1] is not None else 0
                    else:
                        new_count = 0
                        closed_count = 0
                        
                except Exception as e:
                    logger.error(f"Error executing combined query for {month_data['label']}: {e}")
                    logger.error(f"Query: {combined_query}")
                    logger.error(f"Params: {(month_start, month_end, month_end, month_start, month_end, month_start, month_end, month_end, month_start, month_end)}")
                    new_count = 0
                    closed_count = 0
                
                # For the first month, calculate initial active businesses
                # (started before month start AND (no end date OR end date after month end))
                if previous_active is None:
                    initial_active_query = f"""
                        SELECT COUNT(*)
                        FROM business_registrations_cache
                        WHERE location_start_date < %s
                        AND (
                            location_end_date IS NULL 
                            OR location_end_date > %s
                        )
                        {restaurant_filter}
                    """
                    try:
                        cursor.execute(initial_active_query, (month_start, month_end))
                        initial_result = cursor.fetchone()
                        previous_active = int(initial_result[0]) if initial_result and initial_result[0] is not None else 0
                    except Exception as e:
                        logger.error(f"Error executing initial active query for {month_data['label']}: {e}")
                        logger.error(f"Query: {initial_active_query}")
                        logger.error(f"Params: {(month_start, month_end)}")
                        previous_active = 0
                
                # Calculate active as: previous active + new - closed
                active_count = previous_active + new_count - closed_count
                
                # Update previous_active for next iteration
                previous_active = active_count
                
                results.append({
                    'year': month_data['year'],
                    'month': month_data['month'],
                    'label': month_data['label'],
                    'active': int(active_count),
                    'new': int(new_count),
                    'closed': int(closed_count),
                    'net_change': int(new_count - closed_count)
                })
            
            cursor.close()
        
        return JSONResponse(content={
            'success': True,
            'data': results,
            'restaurant_only': restaurant_only,
            'total_months': len(results)
        })
        
    except Exception as e:
        logger.error(f"Error getting active businesses data: {str(e)}", exc_info=True)
        return JSONResponse(
            content={
                'success': False,
                'error': str(e)
            },
            status_code=500
        )

