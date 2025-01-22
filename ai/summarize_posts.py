from pathlib import Path
import logging
import sys
from swarm import Swarm, Agent

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)

def create_document_summary(content: str, max_tokens: int = 1000) -> str:
    """
    Creates a structured summary of a data analysis markdown file containing:
    - Key metrics and their trends 
    - Chart descriptions
    - Anomaly findings
    - Time periods covered
    """
    # Create summarization agent with system message
    summarization_agent = Agent(
        model="gpt-4o",
        name="Summarizer",
        instructions="""You are an expert data analyst who creates concise, structured summaries of data analysis documents.  
        The idea is to give an AI an index of what data is where and a bit about the trends and anomalies.

        For each document, extract and organize the key information into this format:
        FILENAME: <extract from first line>
        TYPE: <main data type being analyzed>
        METRICS: <key metrics tracked>
        PERIOD: <time range of data>
        TRENDS: <2-5 key trends, focusing on YoY changes>
        ANOMALIES: <significant anomalies found, if any>

        Keep summaries under 1000 tokens and focus on the most significant findings.
        Be precise and quantitative in describing trends and changes.
        Highlight any unusual patterns or anomalies in the data.
        """,
        functions=[],
        context_variables={},
        debug=False
    )

    summarization_prompt = "Please analyze and summarize the following data analysis document:"
    # Initialize Swarm for summary generation
    summary_client = Swarm()
    messages = [
        {"role": "user", "content": f"{summarization_prompt}\n\nDocument:\n{content}"}
    ]
    
    response = summary_client.run(
        messages=messages,
        stream=False,
        debug=False,
        agent=summarization_agent
    )
    
    # Handle the response correctly
    if hasattr(response, 'messages') and isinstance(response.messages, list):
        return response.messages[0]['content']
    elif isinstance(response, list) and len(response) > 0:
        return response[0]['content']
    elif hasattr(response, 'choices') and len(response.choices) > 0:
        return response.choices[0].message.content
    else:
        logging.error(f"Unexpected response format: {response}")
        return "Error generating summary"

def process_output_summaries():
    """
    Process all markdown files in the generated_posts directory,
    create summaries, and save them as text files.
    Only processes files that don't already have a summary file.
    """
    output_dir = Path("output")
    
    # Ensure the directory exists
    if not output_dir.exists():
        logging.warning("Output directory does not exist. No files to process.")
        return
    
    md_files = list(output_dir.glob("*.md"))
    logging.info(f"Found {len(md_files)} markdown files to check")
    
    # Process each markdown file
    for md_file in md_files:
        logging.info(f"Checking file: {md_file.name}")
        # Check if summary already exists
        summary_file = output_dir / f"{md_file.stem}_summary.txt"
        if summary_file.exists():
            logging.info(f"Summary already exists for {md_file.name}, skipping...")
            continue
            
        logging.info(f"Generating summary for: {md_file.name}")
        
        try:
            # Read the markdown content
            content = md_file.read_text(encoding='utf-8')
            
            # Generate summary
            summary = create_document_summary(content)
            
            # Save summary
            summary_file.write_text(summary, encoding='utf-8')
            logging.info(f"✓ Successfully saved summary to {summary_file.name}")
            
        except Exception as e:
            logging.error(f"❌ Error processing {md_file.name}: {str(e)}")

if __name__ == "__main__":
    process_output_summaries() 