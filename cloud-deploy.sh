#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════════
# Averroes Deal Origination Tool — Optimized GCP Deployment
# Project: averroes-deal-origination
# ═══════════════════════════════════════════════════════════

PROJECT_ID="averroes-deal-origination"
REGION="europe-west1"
SERVICE_NAME_BACKEND="averroes-deal-backend"
SERVICE_NAME_FRONTEND="averroes-deal-frontend"
BUCKET_NAME="averroes-deal-intelligence"

DEPLOY_TARGET=${1:-"all"} # options: all, backend, frontend

# Load Gemini key
if [ -f backend/.env ]; then
    export $(grep -v '^#' backend/.env | xargs)
fi

echo "🚀 Starting Deployment [$DEPLOY_TARGET] for Averroes Platform"

# --- Backend ---
if [ "$DEPLOY_TARGET" == "all" ] || [ "$DEPLOY_TARGET" == "backend" ]; then
    echo "📦 [1/2] Building \u0026 Deploying Backend..."
    cd backend
    gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND --project=$PROJECT_ID
    gcloud run deploy $SERVICE_NAME_BACKEND \
        --image gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND \
        --region $REGION \
        --allow-unauthenticated \
        --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GCS_BUCKET=$BUCKET_NAME,GEMINI_API_KEY=$GEMINI_API_KEY" \
        --project=$PROJECT_ID
    cd ..
fi

# --- Frontend ---
if [ "$DEPLOY_TARGET" == "all" ] || [ "$DEPLOY_TARGET" == "frontend" ]; then
    echo "📦 [2/2] Building \u0026 Deploying Frontend..."
    BACKEND_URL=$(gcloud run services describe $SERVICE_NAME_BACKEND --region $REGION --format='value(status.url)' --project=$PROJECT_ID)
    cd frontend
    gcloud builds submit --config=cloudbuild.yaml --substitutions=_NEXT_PUBLIC_API_URL=$BACKEND_URL --project=$PROJECT_ID
    gcloud run deploy $SERVICE_NAME_FRONTEND \
        --image gcr.io/$PROJECT_ID/$SERVICE_NAME_FRONTEND \
        --region $REGION \
        --allow-unauthenticated \
        --set-env-vars="NEXT_PUBLIC_API_URL=$BACKEND_URL" \
        --project=$PROJECT_ID
    cd ..
fi

echo "✅ Done! Operation Complete."
