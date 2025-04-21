# TransparentSF

A data analysis and visualization platform for San Francisco city data, focusing on public data and governmental transparency.

## Overview

TransparentSF is a web-based application that provides interactive visualizations and analysis of San Francisco city data. It includes features for:

- Automated analysis of public datasets
- AI-powered insights generation
- Interactive chat interface for data exploration
- Integration with Ghost CMS for publishing insights
- Anomaly detection with PostgreSQL storage

## Technology Stack

- **Backend**: Python
- **Vector Database**: Qdrant
- **Database**: PostgreSQL
- **Content Management**: Ghost CMS
- **APIs**:  
  - OpenAI API for analysis  
  - Ghost CMS API for content publishing  

## Installation

**Prerequisites:**  
- Python3 with `pip`  
- Docker (for Qdrant) 
- PostgreSQL (for anomaly storage)
- OpenAI API key
- Ghost admin API key (optional)
  - Publishing ghost blogs

**Steps:**

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/transparentSF.git
   cd transparentSF
   ```

2. **Install Python dependencies:**
   ```bash
   cd ai
   pip install -r requirements.txt
   cd ..
   ```

3. **Set up environment variables:**  
   Create a `.env` file in the project root with the following variables:
   ```env
   OPENAI_API_KEY=your_openai_api_key
   GHOST_URL=your_ghost_cms_url
   GHOST_ADMIN_API_KEY=your_ghost_admin_api_key
   ```

4. **Set up PostgreSQL:**
   ```bash
   # For macOS with Homebrew
   brew install postgresql
   brew services start postgresql
   
   # For Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install postgresql postgresql-contrib
   
   # For Windows
   # Download and install from https://www.postgresql.org/download/windows/
   ```

5. **Initialize the PostgreSQL database:**
   ```bash
   python ai/tools/init_postgres_db.py
   ```
   
   This script will create:
   - A database named "transparentsf" (if it doesn't exist)
   - An "anomalies" table to store detected anomalies

   You can customize the connection parameters as needed:
   ```bash
   python ai/tools/init_postgres_db.py --host localhost --port 5432 --user postgres --password <pass> --dbname transparentsf
   ```

## Usage

1. **Start all services:**

   The easiest way to start all required services is to use the included script:
   ```bash
   ./start_services.sh
   ```
   
   This script will:
   - Start the main backend service
   - Start Qdrant for vector search
   - Start the Ghost Bridge for CMS integration
   - Ensure all services are ready before proceeding

2. **Access the application:**

   After starting the services, you can access:
   - **Chat Interface**: http://localhost:8000/
     - Use this for interactive data exploration and queries
   - **Backend Configuration**: http://localhost:8000/backend
     - Use this for system configuration, data analysis setup, and administration tasks
     - From here you can run analysis tasks, configure metrics, and manage the system

3. **Manual service startup (alternative):**

   If you prefer to start services individually:
   ```bash
   # Start Qdrant
   docker run -p 6333:6333 qdrant/qdrant
   
   # Start the backend service
   cd ai
   python main.py
   ```

4. **Run Initial Analysis:**
   
   To perform initial data analysis, visit the backend configuration at:
   ```
   http://localhost:8000/backend
   ```
   
   From the backend interface, you can:
   - Configure data sources
   - Run analysis on specific metrics
   - Schedule automatic analysis
   - View analysis results



## Project Structure

- `/ai`: Core analysis and processing scripts
  - `backend.py`: Initial data analysis pipeline
  - `webChat.py`: Interactive chat interface
  - `load_analysis_2_vec.py`: Vector database loader
  - `/tools`: Utility scripts and tools
    - `anomaly_detection.py`: Anomaly detection with PostgreSQL storage
    - `init_postgres_db.py`: Database initialization tool
    - `view_anomalies.py`: Tool for viewing anomalies in the database
- `/output`: Generated analysis results
- (deprecated) `ghostbridge.js`: Ghost CMS integration


## Contributing

1. Fork the repository  
2. Create your feature branch:  
   ```bash
   git checkout -b feature/AmazingFeature
   ```
3. Commit your changes:  
   ```bash
   git commit -m 'Add some AmazingFeature'
   ```
4. Push to the branch:  
   ```bash
   git push origin feature/AmazingFeature
   ```
5. Open a Pull Request

## License

This project is licensed under the ISC License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

- San Francisco's DataSF efforts and all the departments that publish data
- OpenAI for AI capabilities
- Ghost CMS team for the content management platform

---

**Need help?** Feel free to open an issue for support.