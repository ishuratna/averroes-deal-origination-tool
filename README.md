# Averroes Deal Origination Tool

An AI-powered sourcing engine designed to identify "diamonds in the rough" that align with **Averroes Capital's** investment philosophy.

## Project Structure
- **/frontend**: Next.js 14 Executive Dashboard (Navy & Gold).
- **/backend**: FastAPI Python service for AI Sourcing & Enrichment.
- **/docs**: Implementation plans and architecture documentation.

## Features
- **AI-Powered Ingestion**: Multi-source scraping and analysis using Gemini 1.5 Pro.
- **Criteria-Driven Filtering**: Automated matching against investment KPIs (Rule of 40, EBITDA Floors).
- **Target Enrichment**: Deep-dive analysis of company activities and contact discovery.
- **Executive Pipeline**: A high-end visual management tool for the investment team.

## Getting Started

### Backend (Sourcing Engine)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Frontend (Executive Dashboard)
```bash
cd frontend
npm install
npm run dev
```

## Tech Stack
- **Languages**: TypeScript, Python.
- **AI**: Gemini 1.5 Pro via Vertex AI / Google AI SDK.
- **Frontend**: Next.js, Vanilla CSS.
- **Backend**: FastAPI, Playwright (Scraping).
- **Storage**: Google Cloud Storage (GCS) + BigQuery.
