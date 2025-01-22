import os
import pandas as pd
import plotly.express as px
import datetime
import uuid
import logging
from tools.genAggregate import aggregate_data
from tools.anomaly_detection import filter_data_by_date_and_conditions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def generate_time_series_chart(
    context_variables: dict,
    time_series_field: str,
    numeric_fields,
    aggregation_period: str = 'day',
    group_field: str = None,
    agg_functions: dict = None,
    max_legend_items: int = 10,
    filter_conditions: dict = None,
    null_group_label: str = 'NA',
    show_average_line: bool = False,
    y_axis_min: float = 0,  # Ensure default is 0
    y_axis_max: float = None,
    return_html: bool = False  # New parameter
) -> str:
    try:
        logging.info("Full context_variables: %s", context_variables)

        if isinstance(numeric_fields, str):
            numeric_fields = [numeric_fields]
        logging.debug("Numeric fields: %s", numeric_fields)

        logging.debug("Context Variables: %s", context_variables)

        # Retrieve title and y_axis_label from context_variables
        chart_title = context_variables.get("chart_title", "Time Series Chart")
        field_name = numeric_fields[0].lower().replace('_', ' ')
        logging.info(f"Checking field name '{field_name}' for y-axis label")
        y_axis_label = context_variables.get("y_axis_label", 
            "count" if field_name == "item count" 
            else numeric_fields[0].capitalize())
        logging.info(f"Selected y_axis_label: {y_axis_label}")
        noun = context_variables.get("noun", y_axis_label)
        # Create a copy of the dataset to avoid modifying the original data
        original_df = context_variables.get("dataset")
        if original_df is None or original_df.empty:
            logging.error("Dataset is not available or is empty.")
            return "**Error**: Dataset is missing or empty. Please provide a valid dataset in 'context_variables'."

        df = original_df.copy()
        logging.debug("DataFrame copied to avoid in-place modifications.")

        # Store original column names for later reference
        original_columns = df.columns.tolist()
        column_mapping = {col.lower(): col for col in original_columns}
        logging.debug("Original column mapping: %s", column_mapping)
        
        # Convert columns to lowercase for case-insensitive comparison
        df.columns = df.columns.str.lower()
        logging.debug("Standardized DataFrame columns: %s", df.columns.tolist())
        
        # Convert input fields to lowercase for comparison
        time_series_field = time_series_field.lower()
        numeric_fields = [field.lower() for field in numeric_fields]
        if group_field:
            group_field = group_field.lower()
            logging.debug(f"Lowercased group_field: {group_field}")

        # Check for fields using case-insensitive comparison
        required_fields = [time_series_field] + numeric_fields
        if group_field:
            required_fields.append(group_field)
        missing_fields = [field for field in required_fields if field not in df.columns]

        if missing_fields:
            logging.error("Missing fields in DataFrame: %s. Available columns: %s", missing_fields, df.columns.tolist())
            return f"**Error**: Missing required fields in DataFrame: {missing_fields}. Please check the column names and ensure they are present."

        # Apply filter conditions if provided
        if filter_conditions:
            data_records = df.to_dict('records')
            # Apply filter_data_by_date_and_conditions
            filtered_data = filter_data_by_date_and_conditions(
                data_records,
                filter_conditions,
                start_date=None,
                end_date=None,
                date_field=time_series_field
            )
            # Convert back to DataFrame
            df = pd.DataFrame(filtered_data)
            logging.info(f"Filtered data size: {len(df)} records after applying filters.")
            if df.empty:
                logging.error("No data available after applying filters.")
                return "**Error**: No data available after applying filters. Please adjust your filter conditions."

        for field in numeric_fields:
            pre_conversion_count = df[field].notna().sum()
            df[field] = df[field].astype(str).str.strip()
            df[field] = pd.to_numeric(df[field], errors='coerce')
            
            # Convert to integer if all values have no decimal places
            if df[field].notna().all() and (df[field] % 1 == 0).all():
                df[field] = df[field].astype(int)
                
            post_conversion_count = df[field].notna().sum()
            coerced_count = pre_conversion_count - post_conversion_count

            if coerced_count > 0:
                logging.info("Field '%s': %d values were coerced to NaN during conversion to numeric.", field, coerced_count)
                if 'id' in df.columns:
                    coerced_values = df[df[field].isna()][['id', field]].head(20)
                    logging.info("Sample of coerced values in field '%s' (ID and Value):\n%s", field, coerced_values)
                else:
                    coerced_values = df[df[field].isna()].head(20)
                    logging.info("Sample of coerced values in field '%s' (Full Row):\n%s", field, coerced_values)

        # Drop rows with NaN in numeric fields
        df.dropna(subset=numeric_fields, inplace=True)
        logging.info("Dropped NA values from DataFrame.")
        
        if group_field and null_group_label:
            df[group_field] = df[group_field].fillna(null_group_label)
            df[group_field] = df[group_field].replace('', null_group_label)
        
        if agg_functions is None:
            agg_functions = {
                field: 'mean' if any(field.endswith(suffix) for suffix in ['_avg', '_pct']) else 'sum'
                for field in numeric_fields
            }
        logging.info("Aggregation functions: %s", agg_functions)

        aggregated_df = aggregate_data(
            df=df,
            time_series_field=time_series_field,
            numeric_fields=numeric_fields,
            aggregation_period=aggregation_period,
            group_field=group_field,
            agg_functions=agg_functions
        )

        if 'time_period' not in aggregated_df.columns:
            logging.error("'time_period' column is missing after aggregation.")
            return "**Error**: The 'time_period' column is missing after aggregation. Check the 'aggregate_data' function for proper time grouping."

        # Ensure 'time_period' is datetime and sort the DataFrame
        aggregated_df['time_period'] = pd.to_datetime(aggregated_df['time_period'])
        aggregated_df = aggregated_df.sort_values('time_period')
        logging.debug("Aggregated DataFrame sorted by 'time_period'.")

        # Compute values for the caption
        try:
            last_time_period = aggregated_df['time_period'].max()
            earliest_time_period = aggregated_df['time_period'].min()

            # Get the appropriate time period name based on aggregation_period
            if aggregation_period == 'year':
                period_name = last_time_period.strftime('%Y')
            elif aggregation_period == 'month':
                period_name = last_time_period.strftime('%B')
            elif aggregation_period == 'quarter':
                quarter = (last_time_period.month - 1) // 3 + 1
                period_name = f"Q{quarter} {last_time_period.year}"
            elif aggregation_period == 'week':
                period_name = f"Week {last_time_period.strftime('%U')} of {last_time_period.year}"
            else:  # day or any other period
                period_name = last_time_period.strftime('%Y-%m-%d')

            logging.debug(f"Last time period: {last_time_period}, Period name: {period_name}")

            # Format numbers for caption
            def format_number(num):
                if num >= 1:
                    return f"{round(num):,}"
                return f"{num:.2f}"

            total_latest = format_number(aggregated_df[aggregated_df['time_period'] == last_time_period][numeric_fields[0]].sum())
            logging.debug(f"Total value for last period: {total_latest}")

            # Exclude the last period for calculating the average of the rest across all groups
            rest_periods = aggregated_df[aggregated_df['time_period'] < last_time_period]
            if rest_periods.empty:
                average_of_rest = 0
            else:
                # Sum over groups to get total per time period
                total_per_time_period = rest_periods.groupby('time_period')[numeric_fields[0]].sum()
                average_of_rest = total_per_time_period.mean()
                total_periods = len(rest_periods['time_period'].unique())

            formatted_average = format_number(average_of_rest)
            logging.debug(f"Average value of rest periods: {formatted_average}")

            percentage_diff_total = ((float(total_latest.replace(',', '')) - average_of_rest) / average_of_rest) * 100 if average_of_rest != 0 else 0
            above_below_total = 'above' if float(total_latest.replace(',', '')) > average_of_rest else 'below'
            percentage_diff_total = abs(round(percentage_diff_total))
            y_axis_label_lower = y_axis_label.lower()

            caption_total = f"In {period_name}, {y_axis_label_lower} was {total_latest}, which is {percentage_diff_total}% {above_below_total} the {total_periods} {aggregation_period} average of {formatted_average}."
            logging.info(f"Caption for total: {caption_total}")

            # Caption for charts with a group_field
            caption_group = ""
            if group_field:
                try:
                    last_period_df = aggregated_df[aggregated_df['time_period'] == last_time_period]
                    numeric_values = last_period_df.groupby(group_field)[numeric_fields[0]].sum().to_dict()
                    logging.debug(f"Numeric values for last period by group: {numeric_values}")

                    # Calculate the average of the prior periods for each group
                    prior_periods = aggregated_df[aggregated_df['time_period'] < last_time_period]
                    average_of_prior = prior_periods.groupby(group_field)[numeric_fields[0]].mean().to_dict()
                    logging.debug(f"Average values of prior periods by group: {average_of_prior}")

                    # Sort groups by their latest values to show most significant first
                    sorted_groups = sorted(numeric_values.items(), key=lambda x: x[1], reverse=True)
                    
                    captions_group = []
                    # Limit to top 5 groups to avoid overly long captions
                    for grp, value in sorted_groups[:5]:
                        if grp not in average_of_prior or average_of_prior[grp] == 0:
                            continue
                            
                        percentage_diff_group = ((value - average_of_prior[grp]) / average_of_prior[grp]) * 100
                        above_below_group = 'above' if value > average_of_prior[grp] else 'below'
                        percentage_diff_group = abs(round(percentage_diff_group))
                        
                        formatted_value = format_number(value)
                        formatted_avg = format_number(average_of_prior[grp])
                        
                        captions_group.append(
                            f"For {grp}, in {period_name}, there were {formatted_value} {y_axis_label_lower}, "
                            f"which is {percentage_diff_group}% {above_below_group} the {total_periods} {aggregation_period} average "
                            f"of {formatted_avg}."
                        )
                    
                    if len(sorted_groups) > 5:
                        captions_group.append(f"... and {len(sorted_groups) - 5} more groups.")
                        
                    caption_group = "<br>".join(captions_group)
                    logging.info(f"Generated group captions: {caption_group}")
                    
                except Exception as e:
                    logging.error(f"Error generating group captions: {e}")
                    caption_group = "Error generating group details."

            if group_field:
                caption = f"{caption_total}\n\n{caption_group}"
            else:
                caption = caption_total

            # ---------------------------------------------------------------------
            #  NEW LOGIC FOR MORE THAN 2 YEARS OF DATA
            # ---------------------------------------------------------------------
            time_span_years = (last_time_period - earliest_time_period).days / 365
            if time_span_years > 2:
                try:
                    last_year_num = last_time_period.year
                    last_month_num = last_time_period.month
                    prior_year_num = last_year_num - 1

                    # For yearly charts, we compare full years without YTD label
                    is_yearly = time_series_field == 'year'

                    # Filter for current year, through last month in data
                    mask_current_year = (
                        aggregated_df['time_period'].dt.year == last_year_num
                    )
                    if not is_yearly:
                        mask_current_year &= (
                            aggregated_df['time_period'].dt.month <= last_month_num
                        )
                    current_year_df = aggregated_df[mask_current_year]

                    # Filter for prior year, through the same last month
                    mask_prior_year = (
                        aggregated_df['time_period'].dt.year == prior_year_num
                    )
                    if not is_yearly:
                        mask_prior_year &= (
                            aggregated_df['time_period'].dt.month <= last_month_num
                        )
                    prior_year_df = aggregated_df[mask_prior_year]

                    ytd_captions = []
                    
                    # Overall comparison
                    current_year_sum = current_year_df[numeric_fields[0]].sum()
                    prior_year_sum = prior_year_df[numeric_fields[0]].sum()

                    if prior_year_sum != 0:
                        ytd_diff_pct = ((current_year_sum - prior_year_sum) / prior_year_sum) * 100
                        ytd_diff_pct = round(ytd_diff_pct)
                        above_below_ytd = 'above' if current_year_sum > prior_year_sum else 'below'

                        # Adjust caption based on whether it's a yearly chart
                        if is_yearly:
                            ytd_captions.append(
                                f"In {last_year_num}, total {y_axis_label_lower} was {format_number(current_year_sum)}, "
                                f"which is {abs(ytd_diff_pct)}% {above_below_ytd} the {prior_year_num} total of {format_number(prior_year_sum)}."
                            )
                        else:
                            ytd_captions.append(
                                f"As of the end of {period_name}, YTD {last_year_num}, total {y_axis_label_lower} is {format_number(current_year_sum)}, "
                                f"which is {abs(ytd_diff_pct)}% {above_below_ytd} the YTD {prior_year_num} total of {format_number(prior_year_sum)}."
                            )

                    # Group-specific comparisons if group_field exists
                    if group_field:
                        # Get top groups by current year total
                        current_year_totals = current_year_df.groupby(group_field)[numeric_fields[0]].sum()
                        top_groups = current_year_totals.sort_values(ascending=False).head(5).index

                        for group in top_groups:
                            curr_group_sum = current_year_df[current_year_df[group_field] == group][numeric_fields[0]].sum()
                            prior_group_sum = prior_year_df[prior_year_df[group_field] == group][numeric_fields[0]].sum()

                            if prior_group_sum != 0:
                                group_ytd_diff_pct = ((curr_group_sum - prior_group_sum) / prior_group_sum) * 100
                                group_ytd_diff_pct = round(group_ytd_diff_pct)
                                group_above_below = 'above' if curr_group_sum > prior_group_sum else 'below'

                                # Adjust group caption based on whether it's a yearly chart
                                if is_yearly:
                                    ytd_captions.append(
                                        f"<br>For {group}, {last_year_num} {y_axis_label_lower} was {format_number(curr_group_sum)}, "
                                        f"which is {abs(group_ytd_diff_pct)}% {group_above_below} the {prior_year_num} total "
                                        f"of {format_number(prior_group_sum)}."
                                    )
                                else:
                                    ytd_captions.append(
                                        f"<br>For {group}, YTD {last_year_num} {y_axis_label_lower} is {format_number(curr_group_sum)}, "
                                        f"which is {abs(group_ytd_diff_pct)}% {group_above_below} the YTD {prior_year_num} total "
                                        f"of {format_number(prior_group_sum)}."
                                    )

                    ytd_caption = "\n".join(ytd_captions)
                    caption = f"{caption}\n\n{ytd_caption}"

                except Exception as ytd_err:
                    logging.warning(f"Failed to compute YTD comparison: {ytd_err}")

        except Exception as e:
            logging.error("Failed to compute caption values: %s", e)
            caption = ""

        # Limit legend to top max_legend_items
        if group_field:
            group_totals = aggregated_df.groupby(group_field)[numeric_fields].sum().sum(axis=1)
            top_groups = group_totals.sort_values(ascending=False).head(max_legend_items).index.tolist()
            logging.info("Top groups based on total values: %s", top_groups)

            # Create mask for top groups
            mask_top = aggregated_df[group_field].isin(top_groups)
            
            # Get the filtered data for top groups
            filtered_agg = aggregated_df[mask_top].copy()
            
            # Sum all other groups per time period
            others_df = (aggregated_df[~mask_top]
                        .groupby('time_period')[numeric_fields[0]]
                        .sum()
                        .reset_index())
            
            # Add the group field with "Others" value
            if not others_df.empty:
                others_df[group_field] = 'Others'
                filtered_agg = pd.concat([filtered_agg, others_df], ignore_index=True)
            
            aggregated_df = filtered_agg

        script_dir = os.path.dirname(os.path.abspath(__file__))
        static_dir = os.path.join(script_dir, '..', 'static')
        os.makedirs(static_dir, exist_ok=True)

        # Use a short unique ID for the filename
        chart_id = uuid.uuid4().hex[:6]
        image_filename = f"chart_{chart_id}.png"
        image_path = os.path.join(static_dir, image_filename)
        logging.debug("Image will be saved to: %s", image_path)

        try:
            if group_field:
                group_field_original = column_mapping.get(group_field, group_field)
                logging.debug(f"Original group field name from mapping: {group_field_original}")
                fig = px.line(
                    aggregated_df,
                    x='time_period',
                    y=numeric_fields[0],
                    color=group_field,
                    labels={
                        'time_period': time_series_field.capitalize(),
                        numeric_fields[0]: y_axis_label,
                        group_field: group_field_original.capitalize()
                    }
                )
                # Add markers to all lines
                fig.update_traces(
                    mode='lines+markers',
                    marker=dict(
                        size=6,
                        opacity=0.6,
                        line=dict(width=1)
                    )
                )
            else:
                fig = px.line(
                    aggregated_df,
                    x='time_period',
                    y=numeric_fields[0],
                    labels={
                        'time_period': time_series_field.capitalize(),
                        numeric_fields[0]: y_axis_label
                    }
                )
                # Add markers to the single line
                fig.update_traces(
                    mode='lines+markers',
                    marker=dict(
                        size=6,
                        opacity=0.6,
                        line=dict(width=1)
                    )
                )

            # Calculate y-axis range
            y_min = y_axis_min  # This will be 0 by default from the function parameters
            if y_axis_max is None:
                # Find the maximum value in the numeric column and add 10% padding
                y_max = aggregated_df[numeric_fields[0]].max() * 1.1
            else:
                y_max = y_axis_max

            # Update the layout configuration for x-axis ticks and y-axis range
            fig.update_layout(
                yaxis=dict(
                    title=dict(
                        text=y_axis_label,
                        font=dict(size=14, family='Arial', color='black')
                    ),
                    tickfont=dict(size=10, family='Arial', color='black'),
                    range=[y_min, y_max],
                    zeroline=True,
                    zerolinewidth=2,
                    zerolinecolor='lightgrey',
                    fixedrange=True
                ),
                xaxis=dict(
                    title=dict(font=dict(size=14, family='Arial', color='black')),
                    tickfont=dict(size=10, family='Arial', color='black'),
                    tickformat='%Y' if time_series_field == 'year' else '%m-%y',
                    dtick="M12",      # One tick per year
                    tickangle=0,      # Horizontal tick labels
                    tickmode='array',
                    ticktext=[d.strftime('%Y' if time_series_field == 'year' else '%m-%y') 
                             for d in aggregated_df['time_period'].unique()],
                    tickvals=aggregated_df['time_period'].unique()
                )
            )

            # Add average line if show_average_line is True
            if show_average_line:
                # Calculate average excluding the last month to match caption
                last_period = aggregated_df['time_period'].max()
                prior_periods_df = aggregated_df[aggregated_df['time_period'] < last_period]
                
                # Calculate average the same way as in caption
                total_per_time_period = prior_periods_df.groupby('time_period')[numeric_fields[0]].sum()
                average_value = total_per_time_period.mean()
                
                # Create a series for the average line
                average_line = pd.Series(average_value, index=aggregated_df['time_period'])
                
                # Format the average value
                formatted_avg = (
                    f"{average_value:,.0f}" if average_value >= 1 
                    else f"{average_value:.1f}"
                )
                
                fig.add_scatter(
                    x=average_line.index, 
                    y=average_line.values, 
                    mode='lines+text', 
                    name=f'Prior periods Average ({formatted_avg})',
                    line=dict(width=2, color='blue', dash='dash'),
                    text=[f"AVG: {formatted_avg}" if i == len(average_line)-1 else "" 
                          for i in range(len(average_line))],
                    textposition="middle right",
                    textfont=dict(size=10, color='blue')
                )

            fig.update_layout(
                legend=dict(
                    orientation="h",    # Horizontal orientation
                    yanchor="bottom",
                    y=-0.15,           # Places legend 25% below the plot
                    xanchor="center",
                    x=0.5,             # Centers the legend horizontally
                    font=dict(size=8),
                    title=dict(
                        text=column_mapping.get(group_field, '').capitalize() if group_field else '',
                        side='left',  # Can be 'top', 'left', etc.
                        font=dict(size=8)  # Optional: control title font separately
                    )
                ),
                title={
                    'text': f"{chart_title} <BR>" if group_field else chart_title,
                    'y': 0.95,
                    'x': 0.5,
                    'font': dict(
                        family='Arial',
                        size=16,
                        color='black',
                        weight='bold'
                    )
                },
                xaxis=dict(
                    title=dict(font=dict(size=14, family='Arial', color='black')),
                    tickfont=dict(size=10, family='Arial', color='black')
                ),
                yaxis=dict(
                    title=dict(
                        text=y_axis_label,
                        font=dict(size=14, family='Arial', color='black')
                    ),
                    tickfont=dict(size=10, family='Arial', color='black')
                ),
                plot_bgcolor='white',
                paper_bgcolor='white',
                font=dict(family="Arial", size=10, color="black"),
                autosize=True,
                margin=dict(l=50, r=50, t=80, b=30)  # Reduce bottom margin to 30
            )

            # Highlight the last data point (only if no group_field)
            if not group_field and not aggregated_df.empty:
                last_point = aggregated_df.iloc[-1]
                last_x = last_point['time_period']
                last_y = last_point[numeric_fields[0]]
                
                # Use the same format as x-axis labels
                point_label = last_x.strftime('%Y' if time_series_field == 'year' else '%m-%y')

                fig.add_scatter(
                    x=[last_x],
                    y=[last_y],
                    mode='markers',
                    name=point_label,
                    marker=dict(
                        size=12,
                        color='gold',
                        symbol='circle-open',
                        line=dict(width=2, color='gold')
                    ),
                    showlegend=False,
                    hoverinfo='skip'
                )

                fig.add_annotation(
                    x=last_x,
                    y=last_y,
                    text=f"{point_label}<br>{last_y:,} {y_axis_label}",
                    showarrow=True,
                    arrowhead=2,
                    font=dict(size=12, family='Arial', color='#333'),
                    arrowcolor='gold',
                    arrowwidth=1,
                    bgcolor='rgba(255, 255, 0, 0.7)',
                    bordercolor='gold',
                    borderwidth=1,
                    ax=-60,
                    ay=-20,
                )

            fig.update_xaxes(showgrid=True)
            fig.update_yaxes(showgrid=True, gridcolor='lightgrey')

            # Add filter conditions annotation
            if filter_conditions:
                filter_conditions_str = ', '.join([f"{cond['field']} {cond['operator']} {cond['value']}" for cond in filter_conditions])
            else:
                filter_conditions_str = "No filter conditions provided"
            fig.add_annotation(
                text=f"Filter Conditions: {filter_conditions_str}",
                xref="paper", yref="paper",
                x=0.0, y=-0.15,
                showarrow=False,
                font=dict(size=8, family='Arial', color='black'),
                xanchor='left'
            )

            # Save the chart as an image
            fig.write_image(image_path, engine="kaleido")
            logging.info("Chart saved successfully at %s", image_path)
            relative_path = os.path.relpath(image_path, start=script_dir)
            
            # Prepare crosstabbed data
            if group_field:
                crosstab_df = aggregated_df.pivot_table(
                    index=group_field,
                    columns='time_period',
                    values=numeric_fields[0],
                    aggfunc='sum',
                    fill_value=0
                )
                # Format the date columns for better readability
                crosstab_df.columns = [col.strftime('%Y') for col in crosstab_df.columns]
            else:
                crosstab_df = aggregated_df.set_index('time_period')[[numeric_fields[0]]].T
                crosstab_df.columns = [col.strftime('%Y') for col in crosstab_df.columns]
                crosstab_df.index = [y_axis_label]

            # Convert crosstab to HTML table
            html_table = crosstab_df.to_html(classes='data-table', index=True)

            # Create markdown content
            markdown_content = f""" 
{context_variables.get("chart_title", "Time Series Chart")}
![Chart]({relative_path.replace(os.sep, '/')})
Caption: {caption}

### Data Table
{crosstab_df.to_markdown()}

"""

            # Include crosstab data table with toggle in HTML content
            html_content = f'''
<div style="width:100%" id="chart_{chart_id}">
    {fig.to_html(full_html=False)}
    <div> 
        {caption}
    </div>
    <p>
        <a href="javascript:void(0);" onclick="toggleDataTable('data_table_{chart_id}')">Show Data</a>
    </p>
    <div id="data_table_{chart_id}" style="display:none;">
        {html_table}
    </div>
    
</div>

<script>
function toggleDataTable(tableId) {{
    var table = document.getElementById(tableId);
    if (table.style.display === "none") {{
        table.style.display = "block";
    }} else {{
        table.style.display = "none";
    }}
}}
</script>

<style>
.data-table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
}}
.data-table th, .data-table td {{
    border: 1px solid #dddddd;
    text-align: left;
    padding: 8px;
}}
.data-table tr:nth-child(even) {{
    background-color: #f9f9f9;
}}
</style>
'''

            logging.info("Markdown content created with chart ID: %s", markdown_content)

            if return_html:
                return markdown_content, html_content
            else:
                return markdown_content

        except Exception as e:
            logging.error("Failed to generate or save chart: %s", e)
            return f"**Error**: An unexpected error occurred while generating the chart: {e}"

    except ValueError as ve:
        logging.error("ValueError in generate_time_series_chart: %s", ve)
        return f"**Error**: {ve}"

    except Exception as e:
        logging.error("Unexpected error in generate_time_series_chart: %s", e)
        return f"**Error**: An unexpected error occurred: {e}"
