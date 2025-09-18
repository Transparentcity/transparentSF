# Legal Code Data Directory

This directory contains ingested San Francisco legal code and ordinance data.

## Structure

### Google Cloud Storage (Primary)
When GCS is enabled, files are stored in the cloud bucket under `legal/`:
- `legal/municipal_code_YYYYMMDD_HHMMSS.json` - Municipal code sections from SF Open Law
- `legal/ordinance_YYYYMMDD_HHMMSS.json` - Recent ordinances from Board of Supervisors

### Local Storage (Fallback)
Local files are stored in this directory when GCS is unavailable:
- `municipal_code_YYYYMMDD_HHMMSS.json` - Municipal code sections from SF Open Law
- `ordinance_YYYYMMDD_HHMMSS.json` - Recent ordinances from Board of Supervisors
- `config/` - Configuration files for legal code ingestion

## Data Sources

1. **SF Open Law Initiative** (https://open.innovatesf.com/openlaw/)
   - Structured JSON data of municipal code
   - API access via OpenGov Foundation
   - Regular updates from city

2. **SF Board of Supervisors** (https://sfbos.org/ordinances-YYYY)
   - Recent ordinances by year
   - PDF documents with full text
   - Metadata including file numbers, enactment numbers

## Usage

### Ingesting Data
```bash
# Ingest municipal code only
python tools/legal_code_ingestion.py --municipal-code

# Ingest recent 50 ordinances only  
python tools/legal_code_ingestion.py --ordinances --limit 50

# Ingest both
python tools/legal_code_ingestion.py --municipal-code --ordinances --limit 50
```

### Processing for Vector Search
```bash
# Setup collection and process all documents
python tools/legal_vector_processor.py --setup --process

# Process limited number of documents
python tools/legal_vector_processor.py --process --limit 100

# Search legal code
python tools/legal_vector_processor.py --search "housing regulations"
```

### API Endpoints

- `GET /legal/stats` - Legal code database statistics
- `POST /legal/search` - Semantic search of legal code
- `GET /legal/document/{id}` - Get full legal document
- `POST /legal/ingest` - Trigger ingestion process
- `GET /legal/recent-ordinances` - Get recent ordinances
- `GET /legal/files` - List stored legal files (GCS + local)
- `GET /legal/files/{filename}` - Retrieve specific legal file
- `GET /legal/health` - System health check (includes GCS status)

## Configuration

Set environment variables for API access:
```bash
# Legal Code APIs
SF_OPENLAW_API_KEY=your_api_key_here  # Optional, will fallback to direct downloads

# Vector Database
QDRANT_URL=localhost
QDRANT_PORT=6333

# Google Cloud Storage (recommended)
GCP_PROJECT_ID=your-project-id
GCS_BUCKET_NAME=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
ENABLE_GCS=true
```

## Database Schema

The legal documents are stored in PostgreSQL with the following schema:

```sql
CREATE TABLE legal_documents (
    id VARCHAR(255) PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    document_type VARCHAR(50) NOT NULL,  -- 'municipal_code' or 'ordinance'
    source VARCHAR(100) NOT NULL,
    url TEXT,
    effective_date TIMESTAMP,
    enactment_number VARCHAR(100),
    file_number VARCHAR(100),
    section_number VARCHAR(100),
    chapter VARCHAR(100),
    metadata JSONB,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Vector Storage

Legal documents are processed into embeddings and stored in Qdrant for semantic search:

- Collection: `sf_legal_code`
- Vector size: 1536 (OpenAI text-embedding-ada-002)
- Distance metric: Cosine similarity

## Impact Analysis

The system supports analyzing the impact of new ordinances by:

1. **Semantic Similarity**: Finding related existing code sections
2. **Content Analysis**: Extracting key provisions and changes
3. **Timeline Tracking**: Monitoring ordinance effective dates
4. **Cross-referencing**: Linking ordinances to affected code sections

## Maintenance

- **Regular Updates**: Run ingestion weekly to capture new ordinances
- **Vector Refresh**: Re-process vectors when embedding models change
- **Data Cleanup**: Archive old ordinances based on retention policy
- **Health Monitoring**: Check `/legal/health` endpoint for system status
