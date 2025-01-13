from webChat import (
    journalist_agent,
    combined_df,
    combined_notes,
)
from swarm import Swarm
from pathlib import Path
import logging
import sys

# ------------------------------
# Configure Logging
# ------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
# Initialize the Swarm client
swarm_client = Swarm()

CATEGORIES = [
    "public safety",
    "economy and community",
    "health and social services",
    "housing"
]

def generate_post(location_type, district_number=None, category="All"):
    """
    Generate a summary of either citywide data or a specific district
    
    Args:
        location_type (str): Either 'citywide' or 'district'
        district_number (int, optional): District number (1-11) if location_type is 'district'
        category (str): Category to focus on
    """
    
    location_context = "Citywide" if location_type == 'citywide' else f"District {district_number}"

    # Create base output directory
    output_dir = Path("generated_posts")
    output_dir.mkdir(exist_ok=True)
    
    # Create subdirectory based on location type
    if location_type == 'citywide':
        sub_dir = output_dir / 'citywide'
    else:
        sub_dir = output_dir / f'district_{district_number}'
    sub_dir.mkdir(exist_ok=True)
    
    # Prepare the output file name (now just category-based since it's in a location-specific folder)
    filename = f"{category.replace(' ', '_')}_post.md"
    output_file = sub_dir / filename

    prompt = f"""Analyze {location_context} data trends for {category} in 2024, focusing on telling a clear, data-driven story about San Francisco. 

    First, identify and clearly state the most significant long-term trends (5-10 years) in {category}. 
    When searching your docs, if the cagtegory is:
    "public safety", then be sure to include an analysis of police incident reports including property and violent crime trends.  
    "economy and community", then be sure to include an analysis of 311 calls.
    "health and social services", then be sure to include an analysis of overdose deaths,  changes in demographics of jail bookings.
    "housing", then be sure to include an analysis of building permits and vacancy data.

     Then, find specific, concrete examples that bring these trends to life like recent anomalies, which compare 2024 to perior year averages. 

    For each trend:
    1. State the clear, overarching pattern supported by data, include an illustrative chart.
    2. Provide specific, detailed and memorable examples that illustrate the trend, include an illustrative chart.
    3. Include the query URL to the original data. The FULL URL including the ENTIRE QUERY is required.
    4. Reference the most compelling chart that visualizes the story, anomaly charts can be especially compelling.

    Your style is dry and factual - vulcan like.  No need to be overly verbose.  Don't speculate.  Just state the facts.         
    Process one document query at a time."""

    # Initialize messages list
    messages = [{"role": "user", "content": prompt}]
    
    # Run the agent and save the response
    with output_file.open('w', encoding='utf-8') as f:
        response = swarm_client.run(
            agent=journalist_agent,
            messages=messages,
            context_variables={"dataset": combined_df["dataset"], "notes": combined_notes},
            stream=True,
            debug=False
        )
        
        for chunk in response:
            if "content" in chunk and chunk["content"] is not None:
                print(chunk["content"], end="", flush=True)
                f.write(chunk["content"])
            if "tool_calls" in chunk and chunk["tool_calls"] is not None:
                for tool_call in chunk["tool_calls"]:
                    func = tool_call["function"]
                    name = func["name"]
                    if name:
                        print(f"\nCalling function: {name}")

def generate_all_posts():
    """Generate posts for citywide data and all districts"""
    # Generate citywide posts for each category
    print("Generating citywide posts...")
    for category in CATEGORIES:
        print(f"\nGenerating citywide post for {category}...")
        generate_post('citywide', category=category)
    
    # Generate district posts for each category
    for district in range(1, 12):  # Districts 1-11
        print(f"\nGenerating posts for District {district}...")
        for category in CATEGORIES:
            print(f"Generating District {district} post for {category}...")
            generate_post('district', district, category=category)

if __name__ == "__main__":
    generate_all_posts()