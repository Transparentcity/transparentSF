from webChat import get_dashboard_metric
import json
from datetime import datetime

# Initialize context variables
context_variables = {
    "dataset": None,
    "notes": None
}

# Test the function
district_number = 0  # Citywide
metric_id = "7"     # Example metric ID

# Call the function
result = get_dashboard_metric(context_variables, district_number, metric_id)

# Count tokens (rough estimate - words + punctuation)
result_str = json.dumps(result)
token_count = len(result_str.split())

# Create markdown content
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
md_content = f"""# Dashboard Metric Test Results
Generated at: {timestamp}

## Parameters
- District: {district_number}
- Metric ID: {metric_id}

## Token Count
{token_count}

## Full Result
```json
{json.dumps(result, indent=2)}
```
"""

# Save to markdown file
output_file = f"dashboard_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
with open(output_file, 'w') as f:
    f.write(md_content)

print(f"Results saved to: {output_file}")
