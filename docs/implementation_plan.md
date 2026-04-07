# Implementation Plan: Averroes Deal Origination Tool

## 1. Project Overview
The **Averroes Deal Origination Tool** is an AI-powered pipeline designed to automate company sourcing, filtering, and enrichment for private equity deal flow. 

### Key Features:
- **AI-Driven Sourcing**: Ingest data from multiple financial and business databases.
- **Investment Philosophy Engine**: Use Gemini AI to screen targets against specific criteria (Rule of 40, EBITDA, Growth targets).
- **Target Enrichment**: Automatically fetch contact details, news, and executive profiles.
- **Executive Deal Command Center**: A premiumNext.js dashboard to manage the sourcing pipeline.

## 2. Technical Stack
- **Frontend**: Next.js 14 (App Router), TypeScript, Vanilla CSS (Premium Executive UI).
- **Backend**: FastAPI (Python), Playwright (Web Scraping/Parsing), Gemini 1.5 Pro (AI).
- **Storage**: 
    - **Google Cloud Storage (GCS)**: Raw data ingestion and document processing.
    - **BigQuery**: Analysis-ready target database.
- **Integration**: GitHub repository for core logic.

## 3. Implementation Roadmap

### Phase 1: Foundation (Next.js & Design System)
- [ ] Initialize Next.js project with a custom **Navy & Gold** aesthetic.
- [ ] Build key layout and components (Deal Pipeline, Target Detail View).
- [ ] Create core theme variables (CSS).

### Phase 2: Ingestion & Enrichment (FastAPI Backend)
- [ ] Setup FastAPI server for data processing.
- [ ] Implement modular scraping framework for initial target identification.
- [ ] Connect with Gemini for automated evaluation.

### Phase 3: Data Storage & Deployment
- [ ] Configure GCS storage for raw data exports.
- [ ] Map evaluation results to BigQuery.
- [ ] Connect Frontend to Backend.

## 4. Design Guidelines
- **Primary Color**: Deep Navy (#0A192F)
- **Secondary Color**: Champagne Gold (#D4AF37)
- **Accent**: Pure White / Subtle Grey
- **Style**: Minimalist, fast, and high-performance.
