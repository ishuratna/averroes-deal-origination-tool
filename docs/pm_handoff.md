# Averroes Deal Origination Tool: Product Manager Handoff

Welcome to the Averroes Deal Origination Platform. This document outlines the product vision, current operational capabilities, user journeys, and the strategic roadmap. This is your guide to owning and scaling the product successfully.

## 1. Product Vision & Value Proposition
**The Problem**: Identifying high-potential B2B SaaS investment targets historically requires hundreds of hours of manual web scraping, conference list parsing, and spreadsheet filtering by junior analysts.
**The Solution**: The Averroes Deal Origination Tool is an automated, multi-agentic system that continuously aggregates startup data from global conferences and databases, evaluates them using our proprietary AI guidelines, and presents only the high-conviction targets on a premium executive dashboard.

**Core Value Add**: 
*   **Time-to-Insight**: Reduces sourcing time from weeks to near-instantaneous pipeline generation.
*   **Conviction Quality**: Removes human error/bias by strictly adhering to the mandated investment criteria using Google Gemini 1.5 Pro.

## 2. The Averroes Investment Thesis (The "Brain")
The AI matching engine strictly adheres to the following criteria encoded in the product. As a PM, adjusting these filters controls the entire funnel.
*   **Sector**: Strictly B2B SaaS (FinTech, HealthTech, Infrastructure, etc.).
*   **Geography**: UK & Europe focus.
*   **Financial Floors**: ~$2M+ EBITDA (estimated or verified).
*   **Performance Metrics**: Seeking Rule of 40 characteristics.

*Note: Targets are scored dynamically from 0% to 100%. Anything over 85% is considered "High Conviction" (Under Review).*

## 3. Current Product Capabilities (v1.0 Live)
The platform consists of a backend multi-agent ingestion pipeline and a frontend executive visualization layer.

### **Features Live Today**
*   **Automated Conference Scraper**: Can pull raw exhibitor lists (e.g., SaaStr London, London Tech Week) and convert them into actionable company targets.
*   **AI Match Scoring**: Each target is evaluated by the AI and assigned a "Match Score".
*   **Executive Dashboard**: A premium, Navy & Gold interface where partners can view targets progressing through three columns: `Qualified` -> `Under Review` -> `Engaged`.
*   **Production Deployment**: The application is fully live and accessible to the team via Google Cloud.

## 4. Product Access URLs
Share these links with your stakeholders:
*   **Live Executive Dashboard**: [https://averroes-deal-frontend-934700272055.europe-west1.run.app](https://averroes-deal-frontend-934700272055.europe-west1.run.app)
*   **Sourcing API Hub**: [https://averroes-deal-backend-e44q256enq-ew.a.run.app](https://averroes-deal-backend-e44q256enq-ew.a.run.app)

## 5. Strategic Roadmap & Next Epic Priorities
To move from v1.0 to a complete end-to-end deal flow engine, prioritize these features in your backlog:

### **Epic 1: The "Outreach" Agent (Founder Identification)**
*   **Goal**: We have the company names and match scores. Now we need the precise contact details of the CEO or Founder.
*   **Feature**: When a company enters the "Under Review" state, trigger a secondary AI search to scrape LinkedIn or web data to populate `contact_name` and `contact_email` directly on the dashboard card.

### **Epic 2: Data Source Expansion**
*   **Goal**: Increase top-of-funnel volume.
*   **Feature**: Partner with engineering to build ingestion pipelines for:
    *   LinkedIn Sales Navigator lists.
    *   Crunchbase API Integration.
    *   Specific G2 software categories.

### **Epic 3: Deal Lifecycle & CRM Sync**
*   **Goal**: Make the dashboard a bidirectional working environment.
*   **Feature**: Add "Reject" and "Move to Engaged" buttons on the cards. When a deal is "Engaged", push the entire payload automatically into Averroes' primary CRM (e.g., Salesforce or Affinity).

### **Epic 4: Enterprise Security & Access Control**
*   **Goal**: Protect proprietary deal flow data.
*   **Feature**: Implement SSO (Single Sign-On) so only internal `@averroescapital.com` emails can view the dashboard.

## 6. Key Metrics to Monitor (KPIs)
As you iterate on the product, set up Analytics to track:
1.  **Top-of-Funnel Volume**: Number of raw targets ingested per week.
2.  **AI Precision**: The percentage of targets scored >85% that actually result in an introductory meeting (Measures the accuracy of the Gemini prompts).
3.  **Time Saved**: Estimated hours saved by analysts not manually scrubbing conference lists.

The runway is clear. You possess a distinct technological edge in the PE landscape. Godspeed!
