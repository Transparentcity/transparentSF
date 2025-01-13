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
from tools.anomaly_detection import custom_parse_date
import datetime
import shutil

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

def save_fixed_csv(dataframe, original_path):
    """Save the cleaned DataFrame to a new CSV file."""
    base_name = os.path.basename(original_path)
    clean_path = os.path.join(os.path.dirname(original_path), f"cleaned_{base_name}")
    dataframe.to_csv(clean_path, index=False)
    logging.info(f"Saved cleaned data to {clean_path}")

def load_and_clean_csv(file_path):
    """Load, clean, and save the corrected CSV."""
    
    df = pd.read_csv(file_path)
    cleaned_df = clean_data(df)
    save_fixed_csv(cleaned_df, file_path)

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
    """Download latest credit retirement data from Verra by automating search and CSV download"""
    # Initialize WebDriver with WebDriverManager
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()))
    
    # Go to the Verra registry page
    url = REGISTRIES['Verra']
    driver.get(url)
    
    # Allow time for the page to load
    time.sleep(5)
    
    try:
        # Find and click the search button
        search_button = driver.find_element(By.CSS_SELECTOR, "button.btn.btn-primary[type='submit']")
        search_button.click()
        logging.info("Clicked search button")
        
        # Wait for results to load
        time.sleep(10)
        
        # Find and click the CSV download button
        csv_button = driver.find_element(By.CSS_SELECTOR, "i.fas.fa-file-csv")
        csv_button.click()
        logging.info("Clicked CSV download button")
        
        # Wait for download to complete
        time.sleep(10)
        
        # Move downloaded file to correct location
        os.makedirs('data/climate', exist_ok=True)
        downloaded_file = max([f for f in os.listdir(os.path.expanduser("~/Downloads")) if f.endswith('.csv')], 
                            key=lambda x: os.path.getctime(os.path.join(os.path.expanduser("~/Downloads"), x)))
        shutil.move(os.path.join(os.path.expanduser("~/Downloads"), downloaded_file),
                   os.path.join('data/climate', 'Verra_credißt_retirement.csv'))
        logging.info("Verra data downloaded successfully")
        
    except Exception as e:
        logging.error(f"Error downloading Verra data: {e}")
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
    if not os.path.exists('data/climate'):
        os.makedirs('data/climate')
        logging.info("Created 'data/climate' directory")

    # Change working directory to store the data
    
    # Call the functions to download data for each registry
    # download_car_data_selenium()  # Updated to use Selenium
    # download_acm_data()
    # download_verra_data()
    # download_puro_data()
    # download_gsm_data()
    data_folder = 'data/climate'
    vera_file = os.path.join(data_folder, 'vcusNov19.csv')

    # Load and clean the CSV files
    load_and_clean_csv(vera_file)
if __name__ == '__main__':
    main()
