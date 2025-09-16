# Time Series Database Module

This module stores time series chart data in the PostgreSQL database, allowing for efficient storage, querying, and analysis of time series metrics.

## Database Schema

The time series data is stored in two tables:

### 1. Time Series Metadata Table (`time_series_metadata`)

Stores metadata about the chart and time series:

| Column Name       | Type      | Description                                  |
|-------------------|-----------|----------------------------------------------|
| chart_id          | SERIAL    | Primary key                                  |
| object_type       | TEXT      | Type of data (e.g., "incident", "call")      |
| object_id         | TEXT      | Identifier for the data source               |
| object_name       | TEXT      | Human-readable name for the data source      |
| field_name        | TEXT      | Name of the metric field                     |
| y_axis_label      | TEXT      | Label for the Y-axis                         |
| period_type       | TEXT      | Period type (day, month, year)               |
| chart_title       | TEXT      | Title of the chart                           |
| filter_conditions | JSONB     | Filter conditions used for the chart         |
| district          | INTEGER   | District identifier (extracted from filters) |
| created_at        | TIMESTAMP | Timestamp when the record was created        |

### 2. Time Series Data Table (`time_series_data`)

Stores the individual data points:

| Column Name   | Type      | Description                              |
|---------------|-----------|------------------------------------------|
| id            | SERIAL    | Primary key                              |
| chart_id      | INTEGER   | Foreign key to time_series_metadata      |
| time_period   | DATE      | Date of the data point                   |
| group_value   | TEXT      | Group value (if data is grouped)         |
| numeric_value | FLOAT     | Actual numeric value                     |
| created_at    | TIMESTAMP | Timestamp when the record was created    |

## Usage

### 1. Storing Chart Data

To store time series data when generating a chart, use the `store_in_db` parameter:

```python
result = generate_time_series_chart(
    context_variables=context_variables,
    time_series_field='incident_date',
    numeric_fields='count',
    aggregation_period='month',
    group_field='category',
    filter_conditions=[{'field': 'district', 'operator': '=', 'value': 5}],
    store_in_db=True,  # Enable database storage
    object_type='incident',  # Specify object type
    object_id='sf-police-incidents'  # Specify object ID
)
```

### 2. Querying for Biggest Deltas

To find metrics with the biggest deltas between time periods:

```python
from tools.store_time_series import get_biggest_deltas

# Get metrics with biggest changes between April and May 2023
result = get_biggest_deltas(
    current_period='2023-05',
    comparison_period='2023-04',
    limit=10,
    district=5,  # Optional: filter by district
    object_type='incident'  # Optional: filter by object type
)

# Process results
for item in result['results']:
    print(f"{item['object_name']} - {item['field_name']} ({item['group_value']}): "
          f"{item['percentage_delta_formatted']} change")
```

### 3. Using the API Endpoint

You can also use the `/get-biggest-deltas` API endpoint:

```javascript
// Example fetch request
fetch('/get-biggest-deltas', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    current_period: '2023-05',
    comparison_period: '2023-04',
    limit: 10,
    district: 5
  }),
})
.then(response => response.json())
.then(data => {
  console.log('Biggest deltas:', data.results);
});
```

## Running the Test Script

A test script is provided to demonstrate the functionality:

```bash
cd ai/tools
python test_time_series_db.py
```

This script:
1. Generates sample time series data
2. Stores it in the database
3. Queries for the biggest deltas between recent periods

## Storage Considerations

The time series data structure is designed for efficient storage:
- Each row in the `time_series_data` table is approximately 64-84 bytes (depending on group value length)
- For 500,000 rows, the expected storage requirement is approximately 32-42 MB
- Data compression is automatically handled by PostgreSQL's TOAST system for larger text values 