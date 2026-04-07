# Averroes Deal Origination Tool: Developer Handoff Document

Welcome! This document provides comprehensive context, architectural details, and the current state of the Averroes Deal Origination Tool. It is designed to act as your blueprint to pick up development seamlessly.

## 1. Project Overview & Objective
The Averroes Deal Origination Tool is an AI-powered pipeline designed to automate the sourcing, enrichment, and filtering of B2B SaaS investment targets for Averroes Capital. 
*   **Goal**: Replace manual sourcing with a multi-agentic system that ingests conference data, scores companies against the Averroes investment philosophy (Rule of 40, EBITDA thresholds, UK/Europe focus), and visualizes the results on an executive dashboard.

## 2. Technology Stack & Architecture
This repository is split into decoupled frontend and backend services, housed in a monorepo structure.

### **Frontend (The Executive Dashboard)**
*   **Framework**: Next.js 14+ (App Router).
*   **Language**: TypeScript / React.
*   **Styling**: Pure CSS (`src/styles/variables.css` and `globals.css`). We explicitly avoid generic utilities like Tailwind to maintain a highly custom, premium **Navy & Gold** executive aesthetic.
*   **State / API**: Standard React hooks combined with native `fetch` wrappers (`src/services/api.ts`).

### **Backend (The Sourcing Engine)**
*   **Framework**: FastAPI.
*   **Language**: Python 3.13.
*   **AI Integration**: Google Gemini 1.5 Pro to power the matching engine (`ai/criteria.py`).
*   **Scraping capability**: Playwright is utilized for robust content extraction (`scrapers/conference_scraper.py`).
*   **Persistence**: 
    1.  Local JSON (`backend/data/candidates.json`) acts as an immediate mock database for UI testing.
    2.  `storage/gcs_handler.py` handles raw data lake dumps to Google Cloud Storage.
    3.  BigQuery schemas (`scripts/setup_bigquery.py`) are prepped for the production "Gold" layer.

### **Infrastructure (Google Cloud)**
*   **GCP Project**: `averroes-portfolio-intel`
*   **Hosting**: Google Cloud Run (Fully managed, serverless containers).
*   **Region**: `europe-west1` (Belgium).

## 3. Directory Structure & Key Files

```text
averroes-deal-origination-tool/
├── backend/
│   ├── ai/
│   │   └── criteria.py             # Averroes investment philosophy (B2B SaaS, filters) and Gemini evaluation logic.
│   ├── scrapers/
│   │   └── conference_scraper.py   # Modular base class and specific logic for extracting attendee lists.
│   ├── scripts/
│   │   └── setup_bigquery.py       # DDL and schema definitions for the target company database.
│   ├── storage/
│   │   └── gcs_handler.py          # Google Cloud Storage integration.
│   ├── data/
│   │   └── candidates.json         # Current "live" database bridging the backend to the frontend.
│   ├── main.py                     # FastAPI entry point & API routes (`/pipeline`, `/ingest/conference`).
│   ├── requirements.txt            # Python dependencies.
│   └── Dockerfile                  # Python 3.13-slim + Playwright setup for Cloud Run.
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx            # Main dashboard UI (Pipeline View).
│   │   │   └── layout.tsx          # Global layout & SEO metadata.
│   │   ├── services/
│   │   │   └── api.ts              # API client. Handles fallback mock data if backend errors.
│   │   ├── styles/
│   │   │   └── variables.css       # Core design tokens (--navy, --gold, typography).
│   │   └── types/
│   │       └── index.ts            # TypeScript interfaces (Crucial: `CompanyTarget`).
│   ├── cloudbuild.yaml             # Multi-stage Docker build config for Cloud Build.
│   ├── Dockerfile                  # Next.js standalone multi-stage build.
│   └── package.json
│
├── .gcloudignore                   # CRITICAL: Ignores node_modules/.venv to keep deployments fast.
└── cloud-deploy.sh                 # Master deployment automation script.
```

## 4. Current State & Workflows

### **The Current Data Pipeline**
1.  **Ingestion Request**: A call to backend `/ingest/conference` triggers the scraper.
2.  **AI Evaluation**: Scraped results are passed to `ai/criteria.py`, which grades the targets producing a `match_score` (0.0 to 1.0).
3.  **Storage**: The result is appended to `backend/data/candidates.json`.
4.  **Display**: The Next.js frontend fetches `/pipeline` on load. The `CompanyTarget` types are strictly enforced (requires `source`, `description`, etc.).

### **Live Deployments**
The app is currently containerized and deployed on Cloud Run.
*   **Live Dashboard**: `https://averroes-deal-frontend-934700272055.europe-west1.run.app`
*   **Live API**: `https://averroes-deal-backend-e44q256enq-ew.a.run.app`

*Note regarding Deployment:* The frontend Next.js app requires the backend URL at **build time**. This is handled in `cloud-deploy.sh` which uses `cloudbuild.yaml` to pass `NEXT_PUBLIC_API_URL` as a `--build-arg`.

## 5. Next Steps & Feature Roadmap for You

To transition this from a robust prototype to an enterprise-grade platform, prioritize the following tasks:

### 1. Database Migration (Local JSON -> BigQuery)
*   **Task**: Deprecate `backend/data/candidates.json`. Connect the backend directly to Google BigQuery.
*   **References**: Use the schema defined in `backend/scripts/setup_bigquery.py`. Connect the `/pipeline` endpoint to run a `SELECT *` against the BQ table.

### 2. Deep Enrichment Agent (Founder Search)
*   **Task**: Implement a secondary AI agent workflow. Once a company reaches the "Under Review" stage (score > 0.85), trigger a web agent to find the Founder/CEO's name and email via LinkedIn or web search.
*   **References**: Update the `CompanyTarget` UI properly populate `contact_name` and `contact_email`.

### 3. CI/CD Pipeline Configuration
*   **Task**: Currently, deployments rely on executing `cloud-deploy.sh` locally. Abstract this into GitHub Actions.
*   **Acceptance Criteria**: Pushes to the `main` branch automatically trigger Google Cloud Build using the provided `frontend/cloudbuild.yaml` and equivalent bash logic.

### 4. Expansion of Scraping Sources
*   **Task**: Add new `Scraper` modules to target live data sources (e.g., Crunchbase API, specific G2 categories, or LinkedIn Sales Navigator). The architecture in `scrapers/` is highly modular to support this.

### 5. Authentication (Security)
*   **Task**: The dashboard currently lacks an authentication layer.
*   **Acceptance Criteria**: Implement a secure login mechanism (e.g., NextAuth.js or Google Identity-Aware Proxy in GCP) to protect deal pipeline data.

## 6. Technical Gotchas & Important Notes
*   **TypeScript Strictness**: If you add new data fields in the backend, you *must* update `frontend/src/types/index.ts`. Next.js build will fail if the mock data in `api.ts` does not satisfy the `CompanyTarget` interface (e.g., missing `source` or `description`).
*   **Cloud Build Size Limits**: ALWAYS ensure `.gcloudignore` exists in the root, `frontend/`, and `backend/` directories. Uploading `node_modules` or `.venv` will crash or stall GCP builds.
*   **CSS Paradigm**: Stick to vanilla CSS. You will find all necessary spacing, padding, glassmorphism, and color variables in `variables.css`. Do not bring in Tailwind without firm justification and team agreement.

Good luck! The foundational multi-agentic architecture is solid, and you are stepping into a codebase primed for rapid scaling.
