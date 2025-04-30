import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
import logging
import datetime
import shutil
import glob
from pathlib import Path
import tempfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Define URLs and Endpoints for each registry
REGISTRIES = {
    'CAR': 'https://thereserve2.apx.com/myModule/rpt/myrpt.asp?r=206',  # Updated with CAR's actual page for data
    'ACM': 'https://americancarbonregistry.org/carbon-accounting/retirement-data',
    'Verra': 'https://registry.verra.org/app/search/VCS',
    'Puro': 'https://puro.earth/retirement-records',
    'GSM': 'https://registry.goldstandard.org/credit-blocks?q=&page=1&sort_column=created_at&sort_direction=desc'
}

def clean_data(dataframe):
    """Clean the date and numeric fields in the DataFrame and save a fixed version."""
    
    # Define the date fields
    date_fields = ['Issuance Date', 'Retirement/Cancellation Date', 'Vintage Start', 'Vintage End']  # Update with actual date column names
    numeric_fields = ['Quantity Issued', 'Total Vintage Quantity']
    
    # Parse dates
    for field in date_fields:
        missing_count = 0
        invalid_numeric_count = 0
        unrecognized_type_count = 0
        
        for index, item in dataframe.iterrows():
            # Ensure the item contains the specified date_field
            date_value = item.get(field)
            if pd.isna(date_value):
                missing_count += 1
                continue
            
            # Convert date_field value to datetime.date if necessary
            if isinstance(date_value, str):
                item_date = custom_parse_date(date_value)
            elif isinstance(date_value, (datetime.date, datetime.datetime)):
                item_date = date_value.date() if isinstance(date_value, datetime.datetime) else date_value
            elif isinstance(date_value, (int, float)):
                try:
                    date_str = str(int(date_value))
                    item_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                except (ValueError, TypeError):
                    invalid_numeric_count += 1
                    continue
            else:
                unrecognized_type_count += 1
                continue
            
            # Overwrite the initial value with the new item_date
            dataframe.at[index, field] = item_date
            
        # Log summary of issues for this field
        if missing_count > 0:
            logging.info(f"{missing_count} records missing {field} information")
        if invalid_numeric_count > 0:
            logging.info(f"{invalid_numeric_count} records had invalid numeric dates for {field}")
        if unrecognized_type_count > 0:
            logging.info(f"{unrecognized_type_count} records had unrecognized {field} types")
            
    # Clean numeric fields
    for field in numeric_fields:
        if field in dataframe.columns:
            dataframe[field] = dataframe[field].astype(str).str.replace(',', '')  # Remove commas
            dataframe[field] = pd.to_numeric(dataframe[field], errors='coerce')  # Convert to numeric
    
    # Log rows where date parsing failed
    for field in date_fields:
        invalid_dates = dataframe[dataframe[field].isna()]
        if not invalid_dates.empty:
            logging.info(f"Invalid entries in {field} field:")
            logging.info(invalid_dates[[field]])

    return dataframe

def custom_parse_date(date_str):
    """Parse date strings in various formats."""
    # Implement custom date parsing logic if needed
    # This function is mentioned in clean_data but isn't defined in the original code
    try:
        return pd.to_datetime(date_str).date()
    except:
        return None

def save_fixed_csv(dataframe, original_path):
    """Save the cleaned DataFrame to a new CSV file."""
    base_name = os.path.basename(original_path)
    clean_path = os.path.join(os.path.dirname(original_path), f"cleaned_{base_name}")
    dataframe.to_csv(clean_path, index=False)
    logging.info(f"Saved cleaned data to {clean_path}")

def load_and_clean_csv(file_path):
    """Load, clean, and save the corrected CSV."""
    if not os.path.exists(file_path):
        logging.error(f"File not found: {file_path}")
        return
    
    try:
        df = pd.read_csv(file_path)
        cleaned_df = clean_data(df)
        
        # Save to a separate location in our data directory
        output_dir = os.path.join(os.getcwd(), 'data', 'climate')
        os.makedirs(output_dir, exist_ok=True)
        
        base_name = os.path.basename(file_path)
        clean_path = os.path.join(output_dir, f"cleaned_{base_name}")
        cleaned_df.to_csv(clean_path, index=False)
        logging.info(f"Saved cleaned data to {clean_path}")
        return clean_path
    except Exception as e:
        logging.error(f"Error processing file {file_path}: {e}")
        return None

def download_car_data_selenium():
    """Use Selenium to click the JavaScript download button on the CAR page."""
    # Initialize WebDriver with WebDriverManager
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()))
    
    # Go to the CAR report page
    url = REGISTRIES['CAR']
    driver.get(url)
    
    # Allow time for the page to load and JavaScript to execute
    time.sleep(5)
    
    # Locate and click the download button by ID
    try:
        download_button = driver.find_element(By.ID, "downloadIcon")  # Locate by ID
        download_button.click()
        logging.info("CAR data download triggered.")
    except Exception as e:
        logging.error(f"Error: Unable to find the download button. Details: {e}")

    # Wait for the file to download
    time.sleep(10)

    # Close the browser
    driver.quit()

def download_acm_data():
    """Download latest credit retirement data from American Carbon Registry (ACM)"""
    url = REGISTRIES['ACM']
    response = requests.get(url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')
        csv_link = soup.find('a', href=True, text='Download CSV')
        if csv_link:
            csv_response = requests.get(csv_link['href'])
            os.makedirs('data/climate', exist_ok=True)
            file_path = os.path.join('data', 'climate', 'ACM_credit_retirement.csv')
            with open(file_path, 'wb') as f:
                f.write(csv_response.content)
            logging.info("ACM data downloaded successfully")
        else:
            logging.error("Failed to find ACM CSV link")
    else:
        logging.error(f"Failed to download ACM data. Status Code: {response.status_code}")

def download_verra_data():
    """Download latest credit retirement data from Verra directly to the project directory"""
    # Create output directory
    output_dir = os.path.join(os.getcwd(), 'data', 'climate')
    os.makedirs(output_dir, exist_ok=True)
    logging.info(f"Ensuring output directory exists: {output_dir}")
    
    # Set up Chrome options to download directly to our project directory
    chrome_options = webdriver.ChromeOptions()
    
    # Disable the Chrome download prompt and specify download directory
    prefs = {
        "download.default_directory": output_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    # Initialize WebDriver with WebDriverManager and our custom options
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        # Go to the Verra registry page
        url = REGISTRIES['Verra']
        driver.get(url)
        
        # Allow time for the page to load
        time.sleep(5)
        
        # Click on the "VCUs" tab in the tabnav first
        try:
            # Print all available tabs for debugging
            tabs = driver.find_elements(By.CSS_SELECTOR, "a.nav-link")
            logging.info("Available tabs:")
            for tab in tabs:
                logging.info(f"Tab text: '{tab.text.strip()}', href: '{tab.get_attribute('href')}'")
            
            # Use a more specific XPath to find the VCUs tab
            vcus_tab = driver.find_element(By.XPATH, "//a[contains(@class, 'nav-link') and contains(normalize-space(.), 'VCUs')]")
            
            logging.info(f"Found VCUs tab with text: '{vcus_tab.text}'")
            
            # Scroll to the element to ensure it's visible
            driver.execute_script("arguments[0].scrollIntoView(true);", vcus_tab)
            time.sleep(1)
            
            # Try to click using JavaScript if regular click might be intercepted
            driver.execute_script("arguments[0].click();", vcus_tab)
            logging.info("Clicked on the VCUs tab using JavaScript")
            
            # Wait for the tab contents to load
            time.sleep(5)
        except Exception as e:
            logging.warning(f"Could not find or click the VCUs tab: {e}")
            logging.info("Continuing without clicking the VCUs tab...")
            
            # Take a screenshot for debugging
            try:
                screenshot_file = os.path.join(output_dir, "verra_page_screenshot.png")
                driver.save_screenshot(screenshot_file)
                logging.info(f"Saved screenshot to {screenshot_file}")
            except Exception as ss_error:
                logging.warning(f"Failed to take screenshot: {ss_error}")
        
        # Find and click the search button
        try:
            search_button = driver.find_element(By.CSS_SELECTOR, "button.btn.btn-primary[type='submit']")
            driver.execute_script("arguments[0].click();", search_button)
            logging.info("Clicked search button")
        except Exception as e:
            logging.error(f"Could not find or click the search button: {e}")
            return None
        
        # Wait longer for results to load
        logging.info("Waiting for search results to load...")
        time.sleep(20)  # Increased from 10 to 20 seconds
        
        # Make sure we're still on the VCUs tab before downloading
        try:
            # Check if we're on the VCUs tab
            active_tab = driver.find_element(By.CSS_SELECTOR, "a.nav-link.active")
            logging.info(f"Currently active tab: '{active_tab.text.strip()}'")
            
            # If not on VCUs tab, try to click it again
            if "VCUs" not in active_tab.text:
                logging.info("Not on VCUs tab, clicking it again")
                vcus_tab = driver.find_element(By.XPATH, "//a[contains(@class, 'nav-link') and contains(normalize-space(.), 'VCUs')]")
                driver.execute_script("arguments[0].click();", vcus_tab)
                logging.info("Clicked on the VCUs tab again")
                time.sleep(10)  # Increased from 5 to 10 seconds
        except Exception as e:
            logging.warning(f"Error checking or resetting to VCUs tab: {e}")
        
        # Wait for page to be completely stable before clicking download
        logging.info("Ensuring page is stable before downloading...")
        time.sleep(10)  # Additional wait to ensure page stability
        
        # Find and click the CSV download button
        try:
            # Take a screenshot before attempting to click download
            screenshot_before_download = os.path.join(output_dir, "before_download.png")
            driver.save_screenshot(screenshot_before_download)
            logging.info(f"Saved screenshot before download to {screenshot_before_download}")
            
            csv_button = driver.find_element(By.CSS_SELECTOR, "i.fas.fa-file-csv")
            
            # Scroll to make the button visible
            driver.execute_script("arguments[0].scrollIntoView(true);", csv_button)
            time.sleep(2)  # Wait after scrolling
            
            # Click the download button
            driver.execute_script("arguments[0].click();", csv_button)
            logging.info("Clicked CSV download button")
        except Exception as e:
            logging.error(f"Could not find or click the CSV download button: {e}")
            return None
        
        # Wait longer for download to complete
        logging.info("Waiting for download to complete...")
        time.sleep(30)  # Increased from 15 to 30 seconds
        
        # Check for downloaded file in our project directory
        possible_files = [
            os.path.join(output_dir, "allvcus.csv"),
            os.path.join(output_dir, "vcus.csv"),
            os.path.join(output_dir, "allprojects.csv"),
            # Check for any CSV file if none of the above are found
            *glob.glob(os.path.join(output_dir, "*.csv"))
        ]
        
        # Check if any of the expected files exist
        for file_path in possible_files:
            if os.path.exists(file_path):
                logging.info(f"Found downloaded file: {file_path}")
                return file_path
        
        logging.error("No CSV files found in the output directory")
        
        # Use a direct download method if the file wasn't downloaded to our directory
        try:
            # Get the download link from the page if possible
            download_link = driver.find_element(By.CSS_SELECTOR, "a.csv-download-link").get_attribute("href")
            if download_link:
                output_file = os.path.join(output_dir, "verra_vcus.csv")
                response = requests.get(download_link)
                with open(output_file, 'wb') as f:
                    f.write(response.content)
                logging.info(f"Downloaded file directly to {output_file}")
                return output_file
        except Exception as e:
            logging.error(f"Error getting direct download link: {e}")
            return None
        
        return None
        
    except Exception as e:
        logging.error(f"Error during Verra data download: {e}")
        return None
    finally:
        driver.quit()

def download_puro_data():
    """Download latest credit retirement data from Puro.earth"""
    url = REGISTRIES['Puro']
    response = requests.get(url)
    if response.status_code == 200:
        with open('Puro_credit_retirement.csv', 'wb') as f:
            f.write(response.content)
        logging.info("Puro data downloaded successfully")
    else:
        logging.error(f"Failed to download Puro data. Status Code: {response.status_code}")

def download_gsm_data():
    """Download latest credit retirement data from Gold Standard (GSM)"""
    url = REGISTRIES['GSM']
    response = requests.get(url)
    if response.status_code == 200:
        with open('GSM_credit_retirement.csv', 'wb') as f:
            f.write(response.content)
        logging.info("GSM data downloaded successfully")
    else:
        logging.error(f"Failed to download GSM data. Status Code: {response.status_code}")

def main():
    # Create output directory if it doesn't exist
    os.makedirs('data/climate', exist_ok=True)
    logging.info("Ensured 'data/climate' directory exists")

    # Download and get path to Verra data
    verra_file = download_verra_data()
    
    # Process the downloaded file
    if verra_file and os.path.exists(verra_file):
        logging.info(f"Processing Verra data from: {verra_file}")
        cleaned_file = load_and_clean_csv(verra_file)
        if cleaned_file:
            logging.info(f"Successfully processed Verra data. Cleaned file saved to: {cleaned_file}")
        else:
            logging.error("Failed to process Verra data")
    else:
        logging.error("Verra data file not available for cleaning")

if __name__ == '__main__':
    main()
