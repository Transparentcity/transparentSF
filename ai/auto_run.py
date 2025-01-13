import os
import json
from tools.data_fetcher import set_dataset
from tools.genChart import generate_time_series_chart
from tools.anomaly_detection import anomaly_detection
import datetime
from swarm import Swarm, Agent
from urllib.parse import quote

# ------------------------------
# Initialization
# ------------------------------
# Initialize OpenAI API key
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

client = Swarm()

# GPT_MODEL = 'gpt-3.5-turbo-16k'
GPT_MODEL = 'gpt-4o'

# ------------------------------
# Helper Functions
# ------------------------------

def load_combined_data(datasets_folder, log_file):
    combined_data = []
    article_list = ['analysis_map.json']

    if not article_list:
        print(f"No JSON files found in {datasets_folder}")
        return combined_data

    print(f"Found {len(article_list)} JSON files to process.")

    for filename in article_list:
        article_path = os.path.join(datasets_folder, filename)
        print(f"\nProcessing file: {filename}")
        try:
            with open(article_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    combined_data.append(data)
                elif isinstance(data, list) and data:
                    combined_data.extend(data)
                else:
                    print(f"No valid data structure found in {filename}")
        except json.JSONDecodeError as e:
            print(f"JSON decode error in file {filename}: {e}")
            continue
        except Exception as e:
            print(f"Error reading file {filename}: {e}")
            continue

    if combined_data:
        print(f"Total records before sorting: {len(combined_data)}")
        combined_data.sort(key=lambda x: (str(x.get('report_category', 'Unknown')),
                                         str(x.get('periodic', 'Unknown'))))
    else:
        print("No valid data found in the files.")

    return combined_data

def save_combined_html(combined_data, output_folder):
    output_file = os.path.join(output_folder, 'all_data.html')
    with open(output_file, 'w', encoding='utf-8') as html_file:
        html_file.write("<table border='1'><tr><th>Index</th><th>Category</th><th>Title</th><th>Usefulness</th></tr>")
        for index, entry in enumerate(combined_data):
            category = entry.get('report_category', 'Unknown')
            title = entry.get('title', 'Unknown')
            usefulness = entry.get('usefulness', 'Unknown')
            html_file.write(f"<tr><td>{index}</td><td>{category}</td><td>{title}</td><td>{usefulness}</td></tr>")
        html_file.write("</table>")
    print(f"Combined data saved to {output_file}")

def process_entry(index, data_entry, output_folder, log_file, script_dir):
    title = data_entry.get('title', 'Unknown')
    noun = data_entry.get('item_noun', data_entry.get('table_metadata', {}).get('item_noun', 'Unknown'))
    category = data_entry.get('report_category', 'Unknown')
    endpoint = data_entry.get('endpoint', None)
    query = data_entry.get('query', None)
    date_fields = data_entry.get('DateFields', [])
    numeric_fields = data_entry.get('NumericFields', [])
    category_field = data_entry.get('CategoryFields', [])
    usefulness = data_entry.get('usefulness', data_entry.get('table_metadata', {}).get('usefulness', 0))

    # Check usefulness
    if usefulness == 0:
        log_file.write(f"{index}: {title} - Skipped due to zero usefulness.\n")
        return

    # Skip if mandatory fields are missing
    if not query or not endpoint or not date_fields or not numeric_fields:
        log_file.write(f"{index}: {title} - Missing required fields. Skipping.\n")
        return

    # Extract table and column metadata
    table_metadata = data_entry.get('table_metadata', {})
    column_metadata = data_entry.get('column_metadata', [])

    # Set up anomaly detection parameters
    recent_period = {
        'start': datetime.date(2024, 11, 1),
        'end': datetime.date(2024, 11, 30)
    }
    comparison_period = {
        'start': datetime.date(2022, 10, 1),
        'end': datetime.date(2024, 10, 31)
    }

    # Add these as filter conditions
    filter_conditions = [
        {'field': date_fields[0], 'operator': '<=', 'value': recent_period['end']},
        {'field': date_fields[0], 'operator': '>=', 'value': comparison_period['start']},
    ]

    try:
        context_variables = {}  # Initialize context_variables for each iteration
        # Set the dataset
        result = set_dataset(context_variables=context_variables, endpoint=endpoint, query=query)
        if 'error' in result:
            log_file.write(f"{index}: {title} - Error setting dataset: {result['error']}\n")
            return
        # Get the dataset from context_variables
        if 'dataset' in context_variables:
            summary = context_variables['dataset'].describe(include='all').to_dict()
        else:
            log_file.write(f"{index}: {title} - No dataset found in context.\n")
            return

        print(summary)
        all_markdown_contents = []
        all_html_contents = []
        print(f"Date fields from JSON: {date_fields}")
        print(f"Numeric fields from JSON: {numeric_fields}")
        print(f"Category field from JSON: {category_field}")
        print(f"Available columns in dataset: {context_variables['dataset'].columns.tolist()}")

        # Generate charts for each combination of date and numeric fields
        for date_field in date_fields:
            for numeric_field in numeric_fields:
                # Update chart title for this combination
                context_variables['chart_title'] = (
                    f"{title} <br> {'count' if numeric_fields[0] == 'item_count' else numeric_fields[0].replace('_', ' ')} by {date_fields[0]}"
                )
                context_variables['noun'] = (
                    f"{title}"
                )
                # Generate the chart
                chart_result = generate_time_series_chart(
                    context_variables=context_variables,
                    time_series_field=date_field,
                    numeric_fields=numeric_field,
                    aggregation_period='month',
                    max_legend_items=10,
                    filter_conditions=filter_conditions,
                    show_average_line=True
                )
                # Ensure we're adding strings, not tuples or dicts
                if isinstance(chart_result, tuple):
                    markdown_content, html_content = chart_result
                    all_markdown_contents.append(str(markdown_content))
                    all_html_contents.append(str(html_content))
                elif isinstance(chart_result, dict):
                    all_html_contents.append(str(chart_result.get('html', '')))
                else:
                    all_html_contents.append(str(chart_result))

        dataset = context_variables['dataset']
        data_records = dataset.to_dict('records')

        # Loop through each category field to detect anomalies
        for cat_field in category_field:
            try:
                context_variables['chart_title'] = (
                    f"{title} <br> {'count' if numeric_fields[0] == 'item_count' else numeric_fields[0].replace('_', ' ')} by {date_fields[0]} by {cat_field}"
                )
                chart_result = generate_time_series_chart(
                    context_variables=context_variables,
                    time_series_field=date_fields[0],
                    numeric_fields=numeric_fields[0],
                    aggregation_period='month',
                    max_legend_items=10,
                    group_field=cat_field,
                    filter_conditions=filter_conditions,
                    show_average_line=False
                )
                if isinstance(chart_result, tuple):
                    markdown_content, html_content = chart_result
                    all_markdown_contents.append(str(markdown_content))
                    all_html_contents.append(str(html_content))
                elif isinstance(chart_result, dict):
                    all_html_contents.append(str(chart_result.get('html', '')))
                else:
                    all_html_contents.append(str(chart_result))

                print(f"Detecting anomalies for category field: {cat_field}")

                # Check anomalies for each numeric field
                for numeric_field in numeric_fields:
                    print(f"Detecting anomalies for numeric field: {numeric_field}")

                    anomalies_result = anomaly_detection(
                        context_variables=context_variables,
                        group_field=cat_field,
                        filter_conditions=filter_conditions,
                        min_diff=2,
                        recent_period=recent_period,
                        comparison_period=comparison_period,
                        date_field=date_fields[0],
                        numeric_field=numeric_field,
                        y_axis_label=numeric_field,
                        title=context_variables['chart_title']
                    )

                    if anomalies_result and 'anomalies' in anomalies_result:
                        anomalies_html = anomalies_result['anomalies']
                        all_html_contents.append(anomalies_html)
                        anomalies_markdown = anomalies_result.get('anomalies_markdown', '')
                        all_markdown_contents.append(anomalies_markdown)
            except Exception as e:
                print(f"Error detecting anomalies for category {cat_field} in entry at index {index}: {str(e)}")
                import traceback
                print(traceback.format_exc())
                # Log the error and move on
                log_file.write(f"{index}: {title} - Error detecting anomalies: {str(e)}\n")
                continue

        # Add query URL information
        base_url = "https://data.sfgov.org/resource/"
        # URL encode the query parameter
        encoded_query = quote(query)
        query_url = f"{base_url}{endpoint}?$query={encoded_query}"

        processed_html_contents = []
        for content in all_html_contents:
            if content is None:
                continue
            if isinstance(content, dict):
                if 'html' in content:
                    processed_html_contents.append(str(content['html']))
                else:
                    processed_html_contents.append(str(content))
            else:
                processed_html_contents.append(str(content))

        combined_html = "\n\n".join(processed_html_contents)

        # Prepare metadata for HTML
        metadata_html = f"<head><title>{noun}</title></head><body><h1>November update for {noun}</h1>\n"
        metadata_html += f"<p><strong>Query URL:</strong> <a href='{query_url}'>LINK</a></p>\n"
        for key, value in table_metadata.items():
            metadata_html += f"<p><strong>{key.capitalize()}:</strong> {value}</p>"

        metadata_html += "<h2>Column Metadata</h2><table border='1'><tr><th>Field Name</th><th>Description</th><th>Data Type</th></tr>"
        for column in column_metadata:
            row = "<tr>"
            if isinstance(column, dict):
                # Assume fieldName, description, dataTypeName keys exist
                row += f"<td>{column.get('fieldName', 'Unknown')}</td>"
                row += f"<td>{column.get('description', 'No description')}</td>"
                row += f"<td>{column.get('dataTypeName', 'Unknown')}</td>"
            else:
                # If column is a string
                row += f"<td>{column}</td><td>No description</td><td>Unknown</td>"
            row += "</tr>"
            metadata_html += row
        metadata_html += "</table>"

        # Prepend metadata to combined_html
        full_html_content = metadata_html + "\n\n" + combined_html

        # Sanitize title for filename
        sanitized_title = endpoint if endpoint.endswith('.json') else endpoint + '.json'

        # Save combined HTML content
        html_filename = os.path.join(output_folder, f"{sanitized_title}.html")
# Delete existing file if it exists
        if os.path.exists(html_filename):
            os.remove(html_filename)
        with open(html_filename, 'w', encoding='utf-8') as f:
            f.write(full_html_content)

        # Process markdown content
        processed_markdown_contents = []
        for content in all_markdown_contents:
            if content is None:
                continue
            if isinstance(content, dict):
                if 'markdown' in content:
                    processed_markdown_contents.append(str(content['markdown']))
                else:
                    processed_markdown_contents.append(str(content))
            else:
                processed_markdown_contents.append(str(content))

        combined_markdown = "\n\n".join(processed_markdown_contents)

        # Prepare Markdown metadata
        metadata_md = f"# {table_metadata.get('item_noun', 'Item')}\n\n# Table Metadata\n"
      
        metadata_md += f"**Query URL:** {query_url}\n\n"

        for key, value in table_metadata.items():
            metadata_md += f"**{key.capitalize()}:** {value}\n\n"

        metadata_md += "## Column Metadata\n\n| Field Name | Description | Data Type |\n|------------|-------------|-----------|\n"
        for column in column_metadata:
            if isinstance(column, dict):
                field_name = column.get('fieldName', 'Unknown')
                description = column.get('description', 'No description')
                data_type = column.get('dataTypeName', 'Unknown')
                metadata_md += f"| {field_name} | {description} | {data_type} |\n"
            else:
                metadata_md += f"| {column} | No description | Unknown |\n"

        # Prepend metadata to combined_markdown
        full_markdown_content = metadata_md + "\n\n" + combined_markdown
        # Save markdown file to output folder
        markdown_filename = os.path.join(output_folder, f"{sanitized_title}.md")

        # Delete existing file if it exists
        if os.path.exists(markdown_filename):
            os.remove(markdown_filename)
        with open(markdown_filename, 'w', encoding='utf-8') as f:
            f.write(full_markdown_content)

        
        # Construct the prompt
        system_message = """
            Role & Objective:
            You are a data journalist examining city-level data for San Francisco. Your job is to identify and highlight notable trends and anomalies in the data without speculating on the reasons behind them. Your ultimate goal is to note changes or patterns that might reflect positively—or at times, negatively—on public officials, but always without assigning causation. You’re simply describing what appears to have changed, not why.

            Tone & Approach:
            Your voice should be factual, descriptive, and neutral. You may highlight a positive trend (e.g., a decrease in a certain type of crime) in a matter-of-fact way, but do not suggest causation or inject opinions about why it happened. Stick to the data: what it shows, how it compares to previous periods, what improvements or declines are evident.

            Task:

            Data Context: You will be provided with the output of an analysis from one specific table. This table focuses on a particular aspect of city life (e.g., crime data in a district, housing permits in a neighborhood, transit ridership stats, etc.).

            Identify Notable Trends or Anomalies:

            Look for unusual spikes, declines, or patterns over time.
            Spot improvements that might be considered good outcomes (e.g., reduced incidents, increased housing production, improved public service metrics).
            Note any negative shifts as well, but avoid speculation.
            
            Document them in charts and images.  There should be several static images referenced in the markdown file.  When you find a noteworthy trend (say police reports are down) then you should document that with a chart that supports it.  Just move the chart over in markdown to the final text file.
            No Explanation of Causes:

            Do not try to explain why these changes occurred.
            Do not assign direct credit or blame to specific officials or conditions. Just note what changed and who might be generally connected to these metrics (e.g., certain police districts, public agencies, or departments).
            If Nothing Remarkable:

            If the data is unremarkable—no visible trend, no anomaly—simply state that nothing significant stands out this period.
            
            Your output will be internal notes, not a final article. You can include as many details as you find interesting.
            Keep your language concise and data-focused, so you can easily pick and choose which insights to use later when writing an actual story.
            
            Outcome:
            A concise but thorough textual summary capturing what is notable (or not) in the provided markdown file. This summary should serve as a quick reference the journalist can review later when deciding which stories to highlight in a monthly report.
            """

        user_message = f"""
        Here is the markdown data:

        Data: {full_markdown_content}

        """

        # Define the anomaly finder agent
        analyst_agent = Agent(
            model=GPT_MODEL,
            name="Analyst",
            instructions=system_message,
            functions=[],
            debug=True,
        )
        # Call the OpenAI API
        messages = [{"role": "user", "content": user_message}]

        # Commenting out agent response to save on quota
        # response = client.run(agent=analyst_agent, messages=messages)
        # assistant_reply = response.messages[-1]["content"]
        # assistant_reply_filename = os.path.join(output_folder, f"{sanitized_title}_{index}_assistant_reply.txt")
        # with open(assistant_reply_filename, 'w', encoding='utf-8') as f:
        #     f.write(assistant_reply)

        # Log success
        relative_html_path = os.path.relpath(html_filename, start=script_dir)
        log_file.write(f"{index}: {title} - Success. Output HTML: {relative_html_path}\n")

    except Exception as e:
        import traceback
        traceback_str = traceback.format_exc()
        print(f"Error processing data from entry at index {index}: {e}")
        print(traceback_str)
        # Log the error
        log_file.write(f"{index}: {title} - Error: {str(e)}\n")

def process_entries(combined_data, num_start, num_end, output_folder, log_file, script_dir):
    for index in range(num_start, num_end):
        print(f"Length of the data: {len(combined_data)}")
        if index >= len(combined_data):
            break
        data_entry = combined_data[index]
        process_entry(index, data_entry, output_folder, log_file, script_dir)

def export_for_endpoint(endpoint, output_folder=None, log_file_path=None):
    """
    Export processing for a specific endpoint.

    Args:
        endpoint (str): The endpoint to process.
        output_folder (str, optional): The base output folder. Defaults to None.
        log_file_path (str, optional): Path to the log file. Defaults to None.
    """
    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_folder = os.path.join(script_dir, 'data')
    datasets_folder = os.path.join(data_folder, 'analysis_map')
    if not output_folder:
        output_folder = os.path.join(script_dir, 'output')

    # Ensure the output directory exists
    os.makedirs(output_folder, exist_ok=True)

    # Create a log file to track the processing results
    if not log_file_path:
        log_file_path = os.path.join(output_folder, "processing_log.txt")
    with open(log_file_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\nProcessing Export for Endpoint: {endpoint} at {datetime.datetime.now()}\n")
    
    print(f"Processing Export for Endpoint: {endpoint} at {datetime.datetime.now()}")
    # Load combined data
    combined_data = load_combined_data(datasets_folder, log_file_path)

    # Find records matching the endpoint
    matching_records = [entry for entry in combined_data if entry.get('endpoint') == endpoint or entry.get('endpoint') == endpoint + '.json']

    if not matching_records:
        print(f"No records found for endpoint: {endpoint}")
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"No records found for endpoint: {endpoint}\n")
        return

    print(f"Found {len(matching_records)} record(s) for endpoint: {endpoint}")

    # Process each matching record
    with open(log_file_path, "a", encoding="utf-8") as log_file:
        for record in matching_records:
            index = combined_data.index(record)
            process_entry(index, record, output_folder, log_file, script_dir)

    print(f"\nExport processing complete for endpoint: {endpoint}.")
    print(f"Log file location: {log_file_path}")

# ------------------------------
# Main Function
# ------------------------------

def main():
    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_folder = os.path.join(script_dir, 'data')
    datasets_folder = os.path.join(data_folder, 'analysis_map')
    output_folder = os.path.join(script_dir, 'output')

    # Ensure the output directory exists
    os.makedirs(output_folder, exist_ok=True)

    # Set the range of entries to process
    num_start = 102
    num_end = 103

    # Create a log file to track the processing results
    log_file_path = os.path.join(output_folder, "processing_log.txt")
    with open(log_file_path, "w", encoding="utf-8") as log_file:
        log_file.write("Processing Log:\n")

    # Load combined data
    combined_data = load_combined_data(datasets_folder, log_file_path)

    # Save combined data as HTML
    if combined_data:
        save_combined_html(combined_data, output_folder)
    else:
        print("No valid data found to save.")

    # Process entries from num_start to num_end
    process_entries(combined_data, num_start, num_end, output_folder, log_file_path, script_dir)

    print(f"\nProcessing complete for entries from index {num_start} to {num_end - 1}.")
    print(f"Log file location: {log_file_path}")

   

# ------------------------------
# Entry Point
# ------------------------------
if __name__ == '__main__':
    main()
