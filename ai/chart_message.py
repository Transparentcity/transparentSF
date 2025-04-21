import uuid
import logging
import json

logger = logging.getLogger(__name__)

def generate_anomaly_chart_html(anomaly_data):
    """
    Generate HTML for an anomaly chart.
    
    Args:
        anomaly_data: The data for the chart
        
    Returns:
        HTML string for the chart
    """
    logger.info(f"Generating anomaly chart HTML with data: {str(anomaly_data)[:200]}...")
    
    # Create a unique ID for the chart container
    chart_id = f"chart-{uuid.uuid4().hex[:8]}"
    
    # Generate a simple chart HTML
    chart_html = f"""
    <div id="{chart_id}" style="width: 100%; max-width: 800px; margin: 20px auto;">
        <div class="chart-wrapper" style="width: 100%; overflow: hidden; border-radius: 12px; border: 3px solid #4A7463; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);">
            <div id="chart-container-{chart_id}" style="width: 100%; padding: 20px; box-sizing: border-box;">
                <h3>Anomaly Chart</h3>
                <p>Chart data: {str(anomaly_data)[:100]}...</p>
            </div>
        </div>
    </div>
    """
    
    logger.info(f"Generated chart HTML with ID: {chart_id}")
    return chart_html

def generate_chart_message(chart_data, chart_type="anomaly"):
    """
    Generate a special message type for chart data that can be sent to the client.
    
    Args:
        chart_data: The data for the chart
        chart_type: The type of chart (default: "anomaly")
        
    Returns:
        A dictionary with the chart message
    """
    logger.info(f"Generating chart message of type: {chart_type}")
    logger.info(f"Chart data: {str(chart_data)[:200]}...")
    
    # Create a unique ID for the chart container
    chart_id = f"chart-{uuid.uuid4().hex[:8]}"
    
    # Ensure chart_data is a dictionary
    if isinstance(chart_data, str):
        try:
            # Try to parse as JSON if it's a string
            chart_data = json.loads(chart_data)
        except json.JSONDecodeError:
            # If it's not valid JSON, create a simple dict
            chart_data = {"raw_data": chart_data}
    
    # Generate the chart HTML based on the chart type
    if chart_type == "anomaly":
        chart_html = generate_anomaly_chart_html(chart_data)
    else:
        # Default to a simple chart if type is not recognized
        chart_html = f"""
        <div id="{chart_id}" style="width: 100%; max-width: 800px; margin: 20px auto;">
            <div class="chart-wrapper" style="width: 100%; overflow: hidden; border-radius: 12px; border: 3px solid #4A7463; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);">
                <div id="chart-container-{chart_id}" style="width: 100%; padding: 20px; box-sizing: border-box;"></div>
            </div>
        </div>
        """
    
    # Create the chart message
    chart_message = {
        "type": "chart",
        "chart_id": chart_id,
        "chart_type": chart_type,
        "chart_data": chart_data,
        "chart_html": chart_html
    }
    
    logger.info(f"Generated chart message with ID: {chart_id}")
    return chart_message 