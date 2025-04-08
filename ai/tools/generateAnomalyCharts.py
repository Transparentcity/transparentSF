import os
import logging
from jinja2 import Environment, FileSystemLoader
import plotly.graph_objs as go
import plotly.io as pio
import datetime
import uuid
import pandas as pd

def generate_anomalies_summary_with_charts(results, metadata, output_dir='static'):
    """
    Generates an HTML page with charts for each detected anomaly and a summary table,
    along with a concise Markdown summary that references PNG images of the charts.

    Parameters:
    - results (list of dict): List containing anomaly details.
    - metadata (dict): Metadata containing period information.
    - output_dir (str): Directory to save the HTML page and chart images.

    Returns:
    - tuple: (html_content, markdown_summary)
        html_content (str): The full HTML as a string.
        markdown_summary (str): A concise Markdown summary of the anomalies referencing the PNG charts.
    """
    if 'title' not in metadata or 'y_axis_label' not in metadata:
        logging.warning("Metadata is missing 'title' or 'y_axis_label'. Default values will be used.")
    chart_counter=0
    
    # Get the aggregation function from metadata
    agg_function = metadata.get('agg_function', 'sum')
    agg_function_display = 'Average' if agg_function == 'mean' else 'Total'
    
    # Get script directory and set up output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_dir == 'static':
        output_dir = os.path.join(script_dir, '..', 'output')
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize a list to hold all chart HTML snippets
    all_charts_html = []
    table_data = []  # Prepare table data for the summary table
      
    # Sort anomalies by 'out_of_bounds' status
    sorted_anomalies = sorted(results, key=lambda x: x['out_of_bounds'], reverse=True)
    
    # Generate charts for each anomaly
    for item in sorted_anomalies:
        if item['out_of_bounds']:
            # Include aggregation function in chart title
            chart_title = f"Anomaly in {agg_function_display} {metadata['y_axis_label']} in {item['group_value']} "
            try:
                chart_html, chart_id = generate_chart_html(item, chart_title, metadata, chart_counter, output_dir)
                all_charts_html.append(chart_html)
                chart_counter += 1  # Increment only when chart is generated
            except Exception as e:
                logging.error(f"Failed to generate chart for group {item.get('group_value', 'Unknown')}: {e}")
                continue
        else:
            chart_id = None  # No chart generated for non-anomalous items
            
        # Prepare data for the table
        percent_difference = (
            (item['difference'] / item['comparison_mean']) * 100 if item['comparison_mean'] else 0
        )
        table_data.append({
            'group_value': item['group_value'],
            'comparison_mean': round(item['comparison_mean'], 1),
            'std_dev': round(item['stdDev'], 1),
            'recent_mean': round(item['recent_mean'], 1),
            'difference': round(item['difference'], 1),
            'percent_difference': round(abs(percent_difference), 1),
            'out_of_bounds': item['out_of_bounds'],
            'chart_id': chart_id  # Will be None for non-anomalous items
        })
    
    # Create title string for HTML and markdown
    title_str = f"{agg_function_display} {metadata.get('numeric_field', '')} by {metadata.get('group_field', '')}"
    title_str += f"<br>for {metadata['recent_period']['start'].strftime('%b-%y')} to {metadata['recent_period']['end'].strftime('%b-%y')} "
    title_str += f"<br>vs {metadata['comparison_period']['start'].strftime('%b-%y')} to {metadata['comparison_period']['end'].strftime('%b-%y')}"
    
    if metadata.get('filter_conditions'):
        title_str += " filtered by: "
        filters = []
        for filter in metadata['filter_conditions']:
            filters.append(f"{filter.get('field', '')} {filter.get('operator', '')} {filter.get('value', '')}")
        title_str += ", ".join(filters)
        
    # Use Jinja2 to create the final HTML page
    env = Environment(loader=FileSystemLoader('.'))
    template_str = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{{ metadata.get('title', 'Anomaly Detection Charts') }}</title>
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 20px;
            }
            .chart-container {
                margin-bottom: 50px;
            }
            .chart-caption{
                font-size: 12px;
                text-align: left;
                margin-bottom: 50px;
            }
            .caption {
                font-size: 12px;
                text-align: left;
                margin-bottom: 50px;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
            }
            th, td {
                border: 1px solid #ddd;
                text-align: left;
                padding: 8px;
            }
            th {
                background-color: #f2f2f2;
            }
            tr:nth-child(even) {
                background-color: #f9f9f9;
            }
            tr:hover {
                background-color: #f1f1f1;
            }
        </style>
    </head>
    <body>
        <b>{{ title_str }}</b>
        {% if table_data %}
        <h3>Summary Table</h3>
        <table>   
            <thead>
                <tr>
                    <th>Group</th>
                    <th>Recent Mean</th>
                    <th>Comparison Mean</th>
                    <th>Difference</th>
                    <th>% Difference</th>
                    <th>Std Dev</th>
                    <th>Anomaly Detected</th>
                </tr>
            </thead>
            {% else %}
            <p>No anomalies found.</p>
            {% endif %}
            <tbody>
                {% for row in table_data %}
                <tr style="background-color: {% if row.out_of_bounds %}rgba(255, 0, 0, 0.1){% else %}transparent{% endif %};">
                    <td>{% if row.out_of_bounds and row.chart_id %}<a href="#chart{{ loop.index0 }}" style="color: #d9534f; text-decoration: none;">{{ row.group_value }}</a>{% else %}{{ row.group_value }}{% endif %}</td>
                    <td>{{ "{:,.0f}".format(row.recent_mean) }}</td>
                    <td>{{ "{:,.0f}".format(row.comparison_mean) }}</td>
                    <td>{{ "{:,.0f}".format(row.difference) }}</td>
                    <td>{{ "{:,.0f}".format(row.percent_difference) }}%</td>
                    <td>{{ "{:,.0f}".format(row.std_dev) }}</td>
                    <td>{{ "Yes" if row.out_of_bounds else "No" }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% for chart in charts %}
            {{ chart|safe }}
        {% endfor %}
    </body>
    </html>
    """
    template = env.from_string(template_str)
    
    # Render the template with all charts and table data
    html_content = template.render(charts=all_charts_html, table_data=table_data, metadata=metadata, title_str=title_str)
    
    # Generate a unique filename for the HTML file
    unique_filename = f"anomaly_charts_{uuid.uuid4().hex}.html"
    html_file_path = os.path.join(output_dir, unique_filename)
    
    # Save the HTML content to a file
    # with open(html_file_path, 'w', encoding='utf-8') as f:
    #     f.write(html_content)
    
    logging.info(f"Anomaly charts HTML page commented out save generated at: {html_file_path}")

    # Generate a Markdown summary
    markdown_summary = generate_markdown_summary(table_data, metadata, output_dir)

    return html_content, markdown_summary


def generate_chart_html(item, chart_title, metadata, chart_counter, output_dir):
    """
    Generates an HTML snippet containing a Plotly chart for the given anomaly
    and saves the chart as a PNG image using Kaleido.

    Parameters:
    - item (dict): Anomaly data containing dates, counts, comparison_mean, etc.
    - metadata (dict): Contains comparison_period and recent_period information.
    - chart_counter (int): Index number of the chart.
    - output_dir (str): Directory to save the chart images.

    Returns:
    - tuple: (chart_html, chart_id)
        chart_html (str): HTML string for the chart.
        chart_id (str): The 8-char unique ID of this chart image.
    """
    
    # Extract data from the item
    dates = item['dates']
    counts = item['counts']
    combined_data = []

    # Get period type from metadata, default to month if not specified
    period_type = metadata.get('period_type', 'month')

    # Process dates based on period type
    for idx, date_entry in enumerate(dates):
        if isinstance(date_entry, str):
            try:
                if period_type == 'year':
                    # For year period, expect YYYY format
                    year = int(date_entry)
                    date_obj = datetime.date(year, 1, 1)
                else:
                    # For monthly data, expect YYYY-MM format
                    if len(date_entry.split('-')) == 2:
                        year, month = map(int, date_entry.split('-'))
                        date_obj = datetime.date(year, month, 1)
                    else:
                        # If we somehow get just a year in monthly mode, use January
                        year = int(date_entry)
                        date_obj = datetime.date(year, 1, 1)
                        logging.warning(f"Found year-only format in monthly mode for date: {date_entry}")
            except ValueError as ve:
                logging.warning(f"Record {idx}: Invalid date format '{date_entry}' for {period_type} period. Skipping this date.")
                continue
        elif isinstance(date_entry, datetime.date):
            date_obj = date_entry
        else:
            logging.warning(f"Record {idx}: Unexpected date type '{type(date_entry)}'. Skipping this date.")
            continue
        combined_data.append(date_obj)

    if not combined_data:
        raise ValueError("No valid dates available after processing.")

    # Create a unique chart container ID
    chart_container_id = f"chart{chart_counter}"

    # Prepare Plotly traces
    comparison_mean = float(round(item['comparison_mean'], 1))
    comparison_std_dev = float(round(item['stdDev'], 1))

    # Define date ranges using datetime.date objects directly
    try:
        comparison_start = metadata['comparison_period']['start']
        comparison_end = metadata['comparison_period']['end']
        recent_start = metadata['recent_period']['start']
        recent_end = metadata['recent_period']['end']
    except (KeyError, TypeError) as e:
        logging.error(f"Error accessing metadata dates: {e}")
        raise

    # Filter data within the date range
    filtered_data = [(date, count) for date, count in zip(combined_data, counts) if comparison_start <= date <= recent_end]

    # Split data into comparison and recent periods
    comparison_data = [(date, count) for date, count in filtered_data if comparison_start <= date <= comparison_end]
    recent_data = [(date, count) for date, count in filtered_data if recent_start <= date <= recent_end]

    # Extract dates and counts
    comparison_dates, comparison_counts = zip(*comparison_data) if comparison_data else ([], [])
    recent_dates, recent_counts = zip(*recent_data) if recent_data else ([], [])

    # Create Plotly traces
    comparison_trace = go.Scatter(
        x=comparison_dates,
        y=comparison_counts,
        mode='lines+markers',
        name='Historical',
        line=dict(color='grey'),
        marker=dict(color='grey')
    )

    recent_trace = go.Scatter(
        x=recent_dates,
        y=recent_counts,
        mode='lines+markers',
        name='Recent',
        line=dict(color='gold'),
        marker=dict(color='gold')
    )

    # Connector trace
    connector_trace = None
    if comparison_dates and recent_dates:
        connector_trace = go.Scatter(
            x=[comparison_dates[-1], recent_dates[0]],
            y=[comparison_counts[-1], recent_counts[0]],
            mode='lines',
            name='',
            line=dict(color='gold'),
            showlegend=False
        )

    # Normal range shaded area
    sigma_dates = [date for date, _ in filtered_data]
    upper_sigma_y = [comparison_mean + 2 * comparison_std_dev] * len(sigma_dates)
    lower_sigma_y = [max(comparison_mean - 2 * comparison_std_dev, 0)] * len(sigma_dates)

    normal_range_trace = go.Scatter(
        x=sigma_dates,
        y=upper_sigma_y,
        fill='tonexty',
        fillcolor='rgba(128, 128, 128, 0.2)',
        mode='none',
        name='Normal Range',
        showlegend=True
    )

    lower_sigma_trace = go.Scatter(
        x=sigma_dates,
        y=lower_sigma_y,
        mode='lines',
        line=dict(color='rgba(0,0,0,0)'),
        showlegend=False
    )

    # Assemble all traces
    plot_data = [lower_sigma_trace, normal_range_trace, comparison_trace, recent_trace]
    if connector_trace:
        plot_data.append(connector_trace)

    # Create layout
    annotation = []
    if recent_dates and recent_counts:
        annotation.append(
            dict(
                text=f"{recent_dates[-1].strftime('%B %Y')}:<br>{recent_counts[-1]:,.0f} {metadata.get('y_axis_label', 'credits').lower()}",
                x=recent_dates[-1],
                y=recent_counts[-1],
                arrowhead=2,
                ax=-75,
                ay=20,
                bgcolor='rgba(255, 255, 0, 0.7)',
                bordercolor='gold',
                borderwidth=1,
            )
        )

    # Update the xaxis configuration based on period_type
    xaxis_config = {}
    if period_type == 'year':
        xaxis_config = dict(
            tickformat='%Y',  # Show only year
            dtick='M12',      # One tick per year
            title=metadata.get('date_field', 'Value'),
            ticklabelmode='period'
        )
    else:
        xaxis_config = dict(
            tickformat='%b %Y',
            dtick='M1',
            title=metadata.get('date_field', 'Value'),
            ticklabelmode='period'
        )

    layout = go.Layout(
        title=dict(text=chart_title, font=dict(size=14)),
        xaxis=xaxis_config,
        yaxis=dict(
            title=metadata.get('y_axis_label', 'Value'),
            rangemode='tozero'
        ),
        showlegend=True,
        height=400,
        legend=dict(
            orientation="h",
            x=0.1,
            y=-0.15,
            xanchor='left',
            yanchor='top',
            font=dict(size=10)
        ),
        margin=dict(t=50, b=30, l=50, r=20),
        annotations=annotation,
        autosize=True
    )

    fig = go.Figure(data=plot_data, layout=layout)

    # Generate caption
    percent_difference = abs((item['difference'] / item['comparison_mean']) * 100) if item['comparison_mean'] else 0
    action = 'increase' if item['difference'] > 0 else 'drop' if item['difference'] < 0 else 'no change'

    comparison_period_label = f"{comparison_start.strftime('%B %Y')} to {comparison_end.strftime('%B %Y')}"
    recent_period_label = f"{recent_start.strftime('%B %Y')}"

    y_axis_labels = metadata['y_axis_label'].lower()
    caption = (
        f"In {recent_period_label}, there were {item['recent_mean']:,.0f} {item['group_value']} {y_axis_labels} per month, "
        f"compared to an average of {comparison_mean:,.0f} per month over {comparison_period_label}, "
        f"a {percent_difference:.1f}% {action}."
    )

    # Save chart as PNG using Kaleido
    chart_id = uuid.uuid4().hex[:8]
    chart_filename = f"chart_{chart_id}.png"
    chart_path = os.path.join(output_dir, chart_filename)
    fig.write_image(chart_path, engine="kaleido")
    logging.info(f"Chart image saved as {chart_path}")

    # For markdown summary, we just need the filename since images are in same directory
    chart_reference = chart_filename

    # Assemble the HTML snippet for the chart and its caption
    chart_html = f"""
    <div id="{chart_container_id}" class="chart-container" style="margin-bottom: 50px;">
        {fig.to_html(full_html=False)}
    </div>
    <div class="chart-caption" style="font-size: 12px; text-align: left; margin-bottom: 50px;">
        {caption}
    </div>
    """

    return chart_html, chart_id


def generate_markdown_summary(table_data, metadata, output_dir):
    """
    Generates a Markdown summary of the anomalies with links to chart images.

    Parameters:
    - table_data (list of dict): Data for all groups with anomaly status and chart_ids.
    - metadata (dict): Contains comparison_period and recent_period information.
    - output_dir (str): Directory to save the chart images.

    Returns:
    - str: Markdown summary of the anomalies.
    """
    # Get the aggregation function from metadata
    agg_function = metadata.get('agg_function', 'sum')
    agg_function_display = 'Average' if agg_function == 'mean' else 'Total'
    
    summary = "## Anomaly Detection Summary\n\n"
    
    # Add metadata information
    title = metadata.get('title', 'Anomaly Detection Results')
    if title:
        summary += f"### {title} ({agg_function_display})\n\n"
    
    # Periods information
    summary += "**Period Information:**\n\n"
    
    # Format dates based on period_type
    period_type = metadata.get('period_type', 'month')
    if period_type == 'year':
        date_format = '%Y'
    else:  # month, day
        date_format = '%b %Y'
    
    if 'recent_period' in metadata and 'comparison_period' in metadata:
        recent_start = metadata['recent_period']['start'].strftime(date_format)
        recent_end = metadata['recent_period']['end'].strftime(date_format)
        comp_start = metadata['comparison_period']['start'].strftime(date_format)
        comp_end = metadata['comparison_period']['end'].strftime(date_format)
        
        summary += f"- Recent Period: {recent_start} to {recent_end}\n"
        summary += f"- Comparison Period: {comp_start} to {comp_end}\n\n"
    
    # Count anomalies
    anomalies = [row for row in table_data if row.get('out_of_bounds', False)]
    
    if anomalies:
        summary += f"**{len(anomalies)} Anomalies Detected:**\n\n"
        
        # Sort anomalies by percent difference
        anomalies.sort(key=lambda x: abs(x.get('percent_difference', 0)), reverse=True)
        
        for i, anomaly in enumerate(anomalies, 1):
            group = anomaly.get('group_value', 'Unknown')
            recent = anomaly.get('recent_mean', 0)
            comp = anomaly.get('comparison_mean', 0)
            pct_diff = anomaly.get('percent_difference', 0)
            direction = "increase" if recent > comp else "decrease"
            
            # Add chart reference if available
            chart_id = anomaly.get('chart_id')
            if chart_id:
                # Get the chart image file path
                img_path = os.path.join(output_dir, f"chart_{chart_id}.png")
                rel_path = os.path.basename(img_path)
                
                # Add the anomaly description with link to chart
                summary += f"{i}. **{group}**: {agg_function_display} {metadata.get('y_axis_label', 'Value')} {direction}d by **{abs(pct_diff):.1f}%** "
                summary += f"(from {comp:.1f} to {recent:.1f})\n"
                summary += f"   ![Anomaly Chart for {group}](/output/{os.path.basename(output_dir)}/{rel_path})\n\n"
            else:
                # Just add the anomaly description without chart
                summary += f"{i}. **{group}**: {agg_function_display} {metadata.get('y_axis_label', 'Value')} {direction}d by **{abs(pct_diff):.1f}%** "
                summary += f"(from {comp:.1f} to {recent:.1f})\n\n"
    else:
        summary += "**No anomalies were detected.**\n\n"
    
    # Add summary table
    if table_data:
        summary += "### Summary Table\n\n"
        summary += "| Group | Recent | Comparison | % Change | Anomaly |\n"
        summary += "|-------|--------|------------|----------|--------|\n"
        
        # Sort table data by percent difference
        sorted_data = sorted(table_data, key=lambda x: abs(x.get('percent_difference', 0)), reverse=True)
        
        for row in sorted_data:
            group = row.get('group_value', 'Unknown')
            recent = row.get('recent_mean', 0)
            comp = row.get('comparison_mean', 0)
            pct_diff = row.get('percent_difference', 0)
            anomaly = "Yes" if row.get('out_of_bounds', False) else "No"
            
            summary += f"| {group} | {recent:.1f} | {comp:.1f} | {pct_diff:.1f}% | {anomaly} |\n"
    
    return summary
