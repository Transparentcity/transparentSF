# TransparentSF

A data analysis and visualization platform for San Francisco city data, focusing on public data and governmental transparency.

## Overview

TransparentSF is a web-based application that provides interactive visualizations and analysis of San Francisco city data. It includes features for:

- Automated analysis of public datasets
- AI-powered insights generation
- Interactive chat interface for data exploration
- Integration with Ghost CMS for publishing insights

## Technology Stack

- **Backend**: Python
- **Vector Database**: Qdrant
- **Content Management**: Ghost CMS
- **APIs**:  
  - OpenAI API for analysis  
  - Ghost CMS API for content publishing  

## Installation

**Prerequisites:**  
- Python3 with `pip`  
- Docker (for Qdrant) 
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

## Usage

1. **Start Qdrant:**
   ```bash
   docker run -p 6333:6333 qdrant/qdrant
   ```

2. **Run Initial Analysis:**
   ```bash
   cd ai
   python backend.py
   ```
   Visit the provided URL with the path `/backend` (http://0.0.0.0:8000/backend) to start the analysis process. Note that this can take anywhere from a few minutes to several hours depending on your configuration in `backend.py`.

3. **Load Analysis Results to Vector Database:**
   Once the analysis is complete and the output folder is generated:
   ```bash
   python vector_loader.py
   ```   

4. **Start the Chat Interface:**
   ```bash
   python webChat.py
   ```
   Visit the provided URL (http://0.0.0.0:8001/) to access the interactive chat interface.

## Project Structure

- `/ai`: Core analysis and processing scripts
  - `backend.py`: Initial data analysis pipeline
  - `webChat.py`: Interactive chat interface
  - `load_analysis_2_vec.py`: Vector database loader
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