import datetime
from datetime import date
import logging
import pandas as pd
from tools.generateAnomalyCharts import generate_anomalies_summary_with_charts 
from dateutil import parser  # Import this library for robust date parsing

# set logging level to INFO
logging.basicConfig(level=logging.INFO)
def find_key(item, field_name):
    if field_name in item:
        return field_name
    for key in item.keys():
        if key.lower() == field_name.lower():
            return key
    return None

def get_item_value_case_insensitive(item, field_name):
    for key in item.keys():
        if key.lower() == field_name.lower():
            return item[key]
    return None

def calculate_stats(values):
    n = len(values)
    if n == 0:
        return {'mean': None, 'stdDev': None}
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std_dev = variance ** 0.5
    return {'mean': float(mean), 'stdDev': float(std_dev)}

def get_date_ranges():
    from datetime import date, timedelta

    today = date.today()

    # Calculate recent period: last full month (previous month)
    first_day_of_current_month = date(today.year, today.month, 1)
    recent_end = first_day_of_current_month - timedelta(days=1)  # Last day of previous month
    recent_start = date(recent_end.year, recent_end.month, 1)    # First day of previous month

    # Calculate comparison period: 12 months before recentStart
    comparison_end = recent_start - timedelta(days=1)            # Day before recentStart
    comparison_start = date(recent_start.year - 1, recent_start.month, 1)  # Same month and day, previous year

    print("Recent period:", recent_start, "to", recent_end)
    print("Comparison period:", comparison_start, "to", comparison_end)
    
    return {
        'recentPeriod': {
            'start': recent_start,
            'end': recent_end,
        },
        'comparisonPeriod': {
            'start': comparison_start,
            'end': comparison_end,
        },
    }

def generate_anomalies_summary(results):
    anomalies = []
    normal_items = []
    for item in results:
        group = item.get('group_value', 'Unknown Group')
        recent_mean = item.get('recent_mean')
        comparison_mean = item.get('comparison_mean')
        difference = item.get('difference')
        std_dev = item.get('stdDev', 0)
        out_of_bounds = item.get('out_of_bounds', False)

        # Determine the direction of the difference
        if difference > 0:
            direction = "higher"
        elif difference < 0:
            direction = "lower"
        else:
            direction = "the same as"

        # Absolute value of difference for reporting
        abs_difference = abs(difference)

        # Format the message based on whether it's an anomaly or normal
        if out_of_bounds:
            message = (
                f"Anomaly detected in '{group}': "
                f"Recent mean (RM: {recent_mean:.2f}) is {direction} than the comparison mean (CM: {comparison_mean:.2f}) "
                f"by {abs_difference:.2f} units. "
                f"Standard Deviation: {std_dev:.2f}."
            )
            anomalies.append(message)
        else:
            message = (
                f"'{group}': RM: {recent_mean:.2f}, CM: {comparison_mean:.2f}, Diff: {difference:.2f}"
            )
            normal_items.append(message)

    # Create the markdown summary
    markdown_summary = "## Anomalies Summary\n\n"
    
    if anomalies:
        markdown_summary += "### Anomalies Detected\n"
        for anomaly in anomalies:
            markdown_summary += f"- {anomaly}\n"
    else:
        markdown_summary += "No anomalies detected.\n"
    
    if normal_items:
        markdown_summary += "\n### Items Within Normal Range\n"
        for item in normal_items:
            markdown_summary += f"- {item}\n"

    # Convert to HTML for backward compatibility
    html_summary = f"<html><body>{markdown_summary}</body></html>"

    return {"anomalies": html_summary}

def group_data_by_field_and_date(data_array, group_field, numeric_field, date_field, period_type='month'):
    logging.info("=== Starting Data Grouping ===")
    logging.info(f"First 5 records of raw data:")
    for i, item in enumerate(data_array[:5]):
        logging.info(f"Record {i + 1}:")
        logging.info(f"  {group_field}: {item.get(group_field)}")
        logging.info(f"  {date_field}: {item.get(date_field)} (type: {type(item.get(date_field))})")
        logging.info(f"  {numeric_field}: {item.get(numeric_field)}")
        logging.info("---")

    # Create a set to track all unique dates we see
    all_dates = set()

    grouped = {}
    for item in data_array:
        group_value = item.get(group_field)
        date_obj = item.get(date_field)
        
        if not date_obj:
            logging.warning(f"Missing date for record: {item}")
            continue

        # Convert string dates to datetime objects
        if isinstance(date_obj, str):
            date_obj = custom_parse_date(date_obj)
            if not date_obj:
                logging.warning(f"Could not parse date for record: {item}")
                continue
        elif isinstance(date_obj, pd.Timestamp):
            date_obj = date_obj.to_pydatetime().date()
        elif isinstance(date_obj, datetime.datetime):
            date_obj = date_obj.date()
        elif not isinstance(date_obj, datetime.date):
            logging.warning(f"Unrecognized date type for record: {item}")
            continue

        # Use appropriate date format based on period_type
        if period_type == 'year':
            date_key = date_obj.strftime("%Y")
        elif period_type == 'month':
            date_key = date_obj.strftime("%Y-%m")
        else:  # day
            date_key = date_obj.strftime("%Y-%m-%d")
            
        all_dates.add(date_key)
        
        if group_value not in grouped:
            grouped[group_value] = {}
        if date_key not in grouped[group_value]:
            grouped[group_value][date_key] = 0

         # Convert numeric value to int, with error handling
        try:
            numeric_value = int(item.get(numeric_field, 0))
        except (ValueError, TypeError):
            logging.warning(f"Invalid numeric value for record: {item}")
            numeric_value = 0
            
        grouped[group_value][date_key] += numeric_value

    logging.info("=== Grouping Complete ===")
    logging.info(f"Total groups found: {len(grouped)}")
    logging.info(f"All unique dates found in data: {sorted(list(all_dates))}")
    for key in list(grouped.keys())[:3]:  # Show first 3 groups
        logging.info(f"Sample group '{key}' dates: {list(grouped[key].keys())}")

    return grouped

def custom_parse_date(date_str):
    # Handle YYYY-MM format first
    if isinstance(date_str, str) and len(date_str.split('-')) == 2:
        try:
            year, month = map(int, date_str.split('-'))
            return datetime.date(year, month, 1)
        except ValueError:
            pass

    # For other formats, try explicit parsing with known format
    date_formats = [
        "%Y-%m-%d",    # YYYY-MM-DD
        "%Y%m%d",      # YYYYMMDD
        "%m/%d/%Y",    # MM/DD/YYYY
        "%d/%m/%Y"     # DD/MM/YYYY
    ]
    
    for fmt in date_formats:
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    # Use dateutil.parser as last resort, with explicit dayfirst=False
    try:
        return parser.parse(date_str, dayfirst=False).date()
    except (ValueError, parser.ParserError):
        return None

def filter_data_by_date_and_conditions(data, filter_conditions, start_date=None, end_date=None, date_field=None, period_type='month'):
    # Initialize error log
    error_log = []
    # Convert date strings to datetime.date objects
    def parse_filter_date(date_val):
        if isinstance(date_val, str):
            try:
                if len(date_val.split('-')) == 2:
                    # For YYYY-MM format
                    year, month = map(int, date_val.split('-'))
                    return datetime.date(year, month, 1)
                else:
                    # Try parsing as YYYY-MM-DD
                    return datetime.datetime.strptime(date_val, "%Y-%m-%d").date()
            except ValueError:
                pass
        return date_val

    # Function to format date based on period_type
    def format_date(date_obj, period_type):
        if period_type == 'year':
            return date_obj.strftime("%Y")
        elif period_type == 'month':
            return date_obj.strftime("%Y-%m")
        else:  # day
            return date_obj.strftime("%Y-%m-%d")

    if start_date:
        start_date = parse_filter_date(start_date)
    if end_date:
        end_date = parse_filter_date(end_date)

    # Update the date comparison in filter conditions
    for condition in filter_conditions:
        value = condition['value']
        if isinstance(value, (datetime.date, datetime.datetime)):
            condition['is_date'] = True
        elif isinstance(value, str):
            parsed_date = parse_filter_date(value)
            if isinstance(parsed_date, datetime.date):
                condition['value'] = parsed_date
                condition['is_date'] = True
            else:
                condition['is_date'] = False
        else:
            condition['is_date'] = False

    # Add logging for date range parameters
    logging.info(f"Date filtering parameters:")
    logging.info(f"  start_date: {start_date} ({type(start_date)})")
    logging.info(f"  end_date: {end_date} ({type(end_date)})")
    logging.info(f"  date_field: {date_field}")
    logging.info(f"  period_type: {period_type}")
    logging.info(f"  filter_conditions: {filter_conditions}")

    filtered_data = []
    for idx, item in enumerate(data):
        # Initialize meets_conditions to True
        meets_conditions = True

        # If date_field and date range are provided, perform date filtering
        if date_field and start_date and end_date:
            key_date_field = find_key(item, date_field)
            if key_date_field is None:
                error_log.append(f"Record {idx} missing {date_field} information. Skipping.")
                continue
            date_value = item[key_date_field]
            
            # Preserve the original date value
            original_date = date_value
            
            # Convert date_field value to datetime.date if necessary
            if isinstance(date_value, str):
                if len(date_value.split('-')) == 2:
                    # If it's YYYY-MM format, keep it as is
                    item_date = date_value
                else:
                    item_date = custom_parse_date(date_value)
            elif isinstance(date_value, pd.Timestamp):
                # Format based on period_type
                item_date = format_date(date_value, period_type)
            elif isinstance(date_value, (datetime.date, datetime.datetime)):
                # Format based on period_type
                item_date = format_date(date_value, period_type)
            else:
                error_log.append(f"Record {idx}: Unrecognized {date_field} type: {type(date_value)}. Skipping.")
                continue

            # Update item with original date value
            item[date_field] = original_date

            # Add logging for date value parsing
            logging.debug(f"Record {idx}: Processing date value: {date_value} ({type(date_value)})")

            # Convert date_field value to datetime.date if necessary
            if isinstance(date_value, str):
                item_date = custom_parse_date(date_value)
                logging.debug(f"Record {idx}: Parsed string date to: {item_date}")
            elif isinstance(date_value, pd.Timestamp):
                item_date = date_value.date()
                logging.debug(f"Record {idx}: Converted Timestamp to date: {item_date}")
            elif isinstance(date_value, (datetime.date, datetime.datetime)):
                item_date = date_value.date() if isinstance(date_value, datetime.datetime) else date_value
                logging.debug(f"Record {idx}: Using existing date/datetime: {item_date}")
            elif isinstance(date_value, (int, float)):
                try:
                    date_str = str(int(date_value))
                    item_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                    logging.debug(f"Record {idx}: Converted numeric date to: {item_date}")
                except (ValueError, TypeError) as e:
                    error_log.append(f"Record {idx}: Invalid numeric date for {date_field} {e}. Skipping.")
                    logging.debug(f"Record {idx}: Failed to parse numeric date: {date_value}")
                    continue
            else:
                error_log.append(f"Record {idx}: Unrecognized {date_field} type: {type(date_value)}. Skipping.")
                logging.debug(f"Record {idx}: Unhandled date type: {type(date_value)}")
                continue

            if item_date is None:
                error_log.append(f"Record {idx}: Invalid date format for {date_field}. Skipping.")
                continue

            # Add logging for date comparison
            logging.debug(f"Record {idx}: Comparing dates - Item: {item_date}, Range: {start_date} to {end_date}")
            
            # Convert start_date and end_date to first/last day of month if they're year-month dates
            if isinstance(start_date, str) and len(start_date.split('-')) == 2:
                start_date = datetime.datetime.strptime(f"{start_date}-01", "%Y-%m-%d").date()
            if isinstance(end_date, str) and len(end_date.split('-')) == 2:
                # Set to last day of month
                next_month = datetime.datetime.strptime(f"{end_date}-01", "%Y-%m-%d").date().replace(day=28) + datetime.timedelta(days=4)
                end_date = next_month - datetime.timedelta(days=next_month.day)

            # Ensure item_date is a datetime.date object for comparison
            if isinstance(item_date, str):
                if period_type == 'year':
                    item_date = datetime.datetime.strptime(item_date, "%Y").date()
                elif period_type == 'month':
                    item_date = datetime.datetime.strptime(f"{item_date}-01", "%Y-%m").date()
                else:  # day
                    item_date = datetime.datetime.strptime(item_date, "%Y-%m-%d").date()

            # Perform date filtering based on actual date objects
            if not (start_date <= item_date <= end_date):
                logging.debug(f"Record {idx}: Date outside range - skipping")
                continue  # Skip this record

            # Update item with formatted date string based on period_type
            item[date_field] = format_date(item_date, period_type)
        
        # Now, check the filter_conditions
        for condition in filter_conditions:
            field = condition['field']
            operator = condition['operator']
            value = condition['value']
            is_date = condition.get('is_date', False)
            key_field = find_key(item, field)
            
            if key_field is None:
                meets_conditions = False
                error_log.append(f"Record {idx}: Missing field '{field}' for condition '{field} {operator} {value}'. Skipping.")
                break
            item_value = item[key_field]

            # If item_value is None, we can't compare
            if item_value is None:
                meets_conditions = False
                error_log.append(f"Record {idx}: Missing value for field '{field}'. Skipping.")
                break

            # Parse item_value if condition value is a date
            if is_date:
                if isinstance(item_value, str):
                    item_value = custom_parse_date(item_value)
                elif isinstance(item_value, (datetime.date, datetime.datetime)):
                    item_value = item_value.date() if isinstance(item_value, datetime.datetime) else item_value
                elif isinstance(item_value, (int, float)):
                    try:
                        date_str = str(int(item_value))
                        item_value = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                    except (ValueError, TypeError):
                        meets_conditions = False
                        error_log.append(f"Record {idx}: Invalid numeric date for field '{field}'. Skipping.")
                        break
                else:
                    meets_conditions = False
                    error_log.append(f"Record {idx}: Unrecognized type for field '{field}': {type(item_value)}. Skipping.")
                    break

                if item_value is None:
                    meets_conditions = False
                    error_log.append(f"Record {idx}: Invalid date for field '{field}'. Skipping.")
                    break

                # Perform date comparison
                try:
                    if operator == '>' and not (item_value > value):
                        meets_conditions = False
                        break
                    elif operator == '<' and not (item_value < value):
                        meets_conditions = False
                        break
                    elif operator == '>=' and not (item_value >= value):
                        meets_conditions = False
                        break
                    elif operator == '<=' and not (item_value <= value):
                        meets_conditions = False
                        break
                    elif operator == '==' and not (item_value == value):
                        meets_conditions = False
                        break
                    elif operator == '!=' and not (item_value != value):
                        meets_conditions = False
                        break
                except Exception as e:
                    meets_conditions = False
                    error_log.append(f"Record {idx}: Error during date comparison for field '{field}': {e}. Skipping.")
                    break
            else:
                # Clean and convert values if numeric
                if isinstance(item_value, str):
                    item_value = item_value.replace(',', '')
                if isinstance(value, str):
                    value = value.replace(',', '')

                # Try numeric comparison first
                try:
                    item_value_float = float(item_value)
                    value_float = float(value)
                    is_numeric = True
                except (ValueError, TypeError):
                    is_numeric = False

                # Condition checks for non-date fields
                try:
                    if operator in ['=', '==']:
                        # For equality, use direct comparison without type conversion
                        if is_numeric:
                            meets_conditions = (item_value_float == value_float)
                        else:
                            meets_conditions = (str(item_value) == str(value))
                        if not meets_conditions:
                            break
                    elif operator == '!=':
                        if is_numeric:
                            meets_conditions = (item_value_float != value_float)
                        else:
                            meets_conditions = (str(item_value) != str(value))
                        if not meets_conditions:
                            break
                    elif operator in ['<', '<=', '>', '>=']:
                        if not is_numeric:
                            meets_conditions = False
                            error_log.append(f"Record {idx}: Cannot compare non-numeric values for field '{field}' with operator '{operator}'. Skipping.")
                            break
                        if operator == '<' and not (item_value_float < value_float):
                            meets_conditions = False
                            break
                        elif operator == '<=' and not (item_value_float <= value_float):
                            meets_conditions = False
                            break
                        elif operator == '>' and not (item_value_float > value_float):
                            meets_conditions = False
                            break
                        elif operator == '>=' and not (item_value_float >= value_float):
                            meets_conditions = False
                            break
                except Exception as e:
                    meets_conditions = False
                    error_log.append(f"Record {idx}: Error during comparison for field '{field}': {e}. Skipping.")
                    break

        if meets_conditions:
            filtered_data.append(item)

    # Add summary of date filtering
    if date_field and start_date and end_date:
        logging.info(f"Date filtering summary:")
        logging.info(f"  Total records before date filtering: {len(data)}")
        logging.info(f"  Records passing date filter: {len(filtered_data)}")
        logging.info(f"  Records filtered out by date: {len(data) - len(filtered_data)}")

    # At the end of the loop, log the error summary
    total_records = len(data)
    filtered_records = len(filtered_data)
    invalid_records = total_records - filtered_records
    logging.info(f"Total records processed: {total_records}")
    logging.info(f"Total records after filtering: {filtered_records}")
    logging.info(f"Total invalid records: {invalid_records}")
    if error_log:
        logging.info("Error details:")
        for error in error_log:
            logging.info(error)

    return filtered_data

def get_month_range(start_date, end_date):
    from dateutil.relativedelta import relativedelta
    months = []
    current_date = start_date.replace(day=1)
    while current_date <= end_date:
        # Only use YYYY-MM format consistently
        months.append(current_date.strftime("%Y-%m"))
        current_date += relativedelta(months=1)
    logging.info(f"Generated month range: {months}")
    return months


def anomaly_detection(
    context_variables,
    group_field=None,
    filter_conditions=[],
    min_diff=2,
    recent_period=None,
    comparison_period=None,
    date_field=None,
    numeric_field=None,
    y_axis_label=None,
    title=None,
    period_type='month'  # Add period_type parameter with default
):
    
    data = context_variables.get("dataset")
    if data is None:
        logging.error("Dataset is not available.")
        return {"error": "Dataset is not available."}

    data_records = data.to_dict('records')
    logging.info(f"Total records in the dataset: {len(data_records)}")
      # Adjust date periods if date_field is provided
    if date_field and (recent_period is None or comparison_period is None):
        date_ranges = get_date_ranges()
        if recent_period is None:
            recent_period = date_ranges['recentPeriod']
        if comparison_period is None:
            comparison_period = date_ranges['comparisonPeriod']

    # Convert period dates to datetime.date objects, handling both YYYY-MM and YYYY-MM-DD formats
    def parse_period_date(date_val):
        if isinstance(date_val, str):
            if len(date_val.split('-')) == 2:
                # For YYYY-MM format
                year, month = map(int, date_val.split('-'))
                if 'start' in date_val:
                    return datetime.date(year, month, 1)
                else:
                    # For end date, get last day of month
                    if month == 12:
                        next_month = datetime.date(year + 1, 1, 1)
                    else:
                        next_month = datetime.date(year, month + 1, 1)
                    return next_month - datetime.timedelta(days=1)
            else:
                # For YYYY-MM-DD format
                return datetime.datetime.strptime(date_val, "%Y-%m-%d").date()
        return date_val

    if recent_period:
        recent_period = {
            'start': parse_period_date(recent_period['start']),
            'end': parse_period_date(recent_period['end'])
        }
    if comparison_period:
        comparison_period = {
            'start': parse_period_date(comparison_period['start']),
            'end': parse_period_date(comparison_period['end'])
        }

    # Log date periods
    if date_field:
        logging.info(f"Recent period: {recent_period['start']} to {recent_period['end']}")
        logging.info(f"Comparison period: {comparison_period['start']} to {comparison_period['end']}")
    else:
        logging.info("Date field not provided. Skipping date-based filtering.")

    # Apply filtering
    recent_data = filter_data_by_date_and_conditions(
        data_records,
        filter_conditions,
        start_date=comparison_period['start'] if comparison_period else None,
        end_date=recent_period['end'] if recent_period else None,
        date_field=date_field,
        period_type=period_type
    )

    logging.info(f"Filtered data size: {len(recent_data)} records after applying filters.")
    # Group data with period_type
    grouped_data = group_data_by_field_and_date(
        recent_data,
        group_field,
        numeric_field,
        date_field=date_field,
        period_type=period_type
    )
    logging.info(f"Grouped data size: {len(grouped_data)} groups.")
    print(grouped_data)
    # Get the full list of periods between comparison_period['start'] and recent_period['end']
    if date_field:
        if date_field.lower() == 'year':
            # Get full years between start and end, but format as YYYY-01
            start_year = comparison_period['start'].year
            end_year = recent_period['end'].year
            full_months = [f"{year}-01" for year in range(start_year, end_year + 1)]
        else:
            # Get full months between start and end
            full_months = get_month_range(comparison_period['start'], recent_period['end'])
    else:
        full_months = ['All']  # When date_field is not provided
    logging.info (f"full months {full_months}")
    results = []
    for group_value, data_points in grouped_data.items():
        logging.info(f"Processing group: {group_value}")

        # Get the first actual data point date for this group
        group_dates = sorted(data_points.keys())
        if not group_dates:
            continue  # Skip groups with no data
            
        first_group_date = group_dates[0]
        
        # Only fill zeros for months that are:
        # 1. After the first actual data point
        # 2. Within the comparison/recent periods
        # 3. Before the last actual data point
        filtered_full_months = [
            month for month in full_months 
            if month >= first_group_date
        ]

        # Now expand data_points only for relevant months
        for month in filtered_full_months:
            if month not in data_points:
                data_points[month] = 0

        dates = sorted(data_points.keys())
        counts = [data_points[date] for date in dates]

        logging.info(f"First data date for group {group_value}: {first_group_date}")
        logging.info(f"Dates in group {group_value}: {dates}")
        logging.debug(f"Counts in group {group_value}: {counts}")

        comparison_counts = []
        recent_counts = []

        # Only consider dates after the first actual data point
        for date_key in dates:
            if date_key >= first_group_date:  # Only process dates after first actual data
                count = data_points[date_key]
                logging.debug(f"Processing date {date_key} with count: {count}")
                if date_field:
                    try:
                        # Use appropriate date format based on period_type
                        if period_type == 'year':
                            item_date = datetime.datetime.strptime(date_key, "%Y").date()
                        elif period_type == 'month':
                            item_date = datetime.datetime.strptime(date_key, "%Y-%m").date()
                        else:  # day
                            item_date = datetime.datetime.strptime(date_key, "%Y-%m-%d").date()
                            
                        if comparison_period['start'] <= item_date <= comparison_period['end']:
                            comparison_counts.append(count)
                        elif recent_period['start'] <= item_date <= recent_period['end']:
                            recent_counts.append(count)
                    except ValueError as e:
                        logging.error(f"Error parsing date {date_key}: {e}")
                else:
                    comparison_counts = counts
                    recent_counts = counts
                    break

        logging.info(f"Comparison counts for {group_value}: {comparison_counts}")
        logging.info(f"Recent counts for {group_value}: {recent_counts}")

        # Initialize stats with default empty values
        comparison_stats = {'mean': None, 'stdDev': None}
        recent_stats = {'mean': None, 'stdDev': None}

        if comparison_counts:
            comparison_stats = calculate_stats(comparison_counts)
        if recent_counts:
            recent_stats = calculate_stats(recent_counts)

        logging.info(f"Comparison stats for {group_value}: {comparison_stats}")
        logging.info(f"Recent stats for {group_value}: {recent_stats}")

        if (
            comparison_stats['mean'] is not None and
            recent_stats['mean'] is not None and
            comparison_stats['stdDev'] is not None
        ):
            try:
                comparison_mean = float(comparison_stats['mean'])
                recent_mean = float(recent_stats['mean'])
                comparison_std_dev = float(comparison_stats['stdDev'])
                min_diff = float(min_diff)
            except ValueError as e:
                logging.error(f"Error converting statistics to float: {e}")
                continue

            difference = recent_mean - comparison_mean

            if comparison_std_dev > 0:
                out_of_bounds = (
                    abs(difference) > comparison_std_dev * min_diff and
                    comparison_mean > 2 and
                    recent_mean > 2
                )

                logging.debug(f"Difference for {group_value}: {difference}")
                logging.debug(f"Out of bounds for {group_value}: {out_of_bounds}")

                if True:
                    results.append({
                        'group_value': group_value,
                        'comparison_mean': comparison_mean,
                        'recent_mean': recent_mean,
                        'difference': difference,
                        'stdDev': comparison_std_dev,
                        'dates': dates,
                        'counts': counts,
                        'out_of_bounds': out_of_bounds
                    })

    results.sort(key=lambda x: abs(x['difference']), reverse=True)
    
    metadata = {
        'recent_period': recent_period,
        'comparison_period': comparison_period,
        'group_field': group_field,
        'date_field': date_field,
        'y_axis_label': y_axis_label,
        'title': title,
        'filter_conditions': filter_conditions,
        'numeric_field': numeric_field
    }

    html_content, markdown_content = generate_anomalies_summary_with_charts(results, metadata)
    
    return  {"anomalies":html_content, "anomalies_markdown":markdown_content}