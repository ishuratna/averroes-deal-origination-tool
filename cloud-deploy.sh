#!/bin/bash
set -e  # Exit immediately if a command fails

# ═══════════════════════════════════════════════════════════
# Averroes Deal Origination Tool — GCP Deployment Script
# Project: averroes-deal-origination
# ═══════════════════════════════════════════════════════════

# --- Configuration ---
PROJECT_ID="averroes-deal-origination"
REGION="europe-west1"
SERVICE_NAME_BACKEND="averroes-deal-backend"
SERVICE_NAME_FRONTEND="averroes-deal-frontend"
BUCKET_NAME="averroes-deal-intelligence"

# Load Gemini key from local .env if present
GEMINI_API_KEY=${GEMINI_API_KEY:-""}
if [ -f backend/.env ]; then
    export $(grep -v '^#' backend/.env | xargs)
fi

echo "🚀 Starting Deployment: Averroes Deal Origination Tool"
echo "   Project : $PROJECT_ID"
echo "   Region  : $REGION"
echo "   Bucket  : $BUCKET_NAME"
echo "─────────────────────────────────────────────────────"

# --- Step 0: Set active project and enable required APIs ---
echo "⚙️  Setting active project to: $PROJECT_ID..."
gcloud config set project $PROJECT_ID

echo "🔧 Enabling required GCP APIs (first-time setup may take ~2min)..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    bigquery.googleapis.com \
    artifactregistry.googleapis.com \
    --project=$PROJECT_ID

echo "✅ APIs enabled."

# --- Step 1: Build and push Backend image ---
echo ""
echo "📦 [1/4] Building Backend Docker Image..."
cd backend
gcloud builds submit \
    --tag gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND \
    --project=$PROJECT_ID
cd ..
echo "✅ Backend image pushed to gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND"

# --- Step 2: Deploy Backend to Cloud Run ---
echo ""
echo "🌍 [2/4] Deploying Backend to Cloud Run ($REGION)..."
gcloud run deploy $SERVICE_NAME_BACKEND \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GCS_BUCKET=$BUCKET_NAME,GEMINI_API_KEY=$GEMINI_API_KEY" \
    --project=$PROJECT_ID

BACKEND_URL=$(gcloud run services describe $SERVICE_NAME_BACKEND \
    --region $REGION \
    --format='value(status.url)' \
    --project=$PROJECT_ID)
echo "✅ Backend Live: $BACKEND_URL"

# --- Step 3: Build and push Frontend image (injecting backend URL at build time) ---
echo ""
echo "📦 [3/4] Building Frontend Docker Image (NEXT_PUBLIC_API_URL=$BACKEND_URL)..."
cd frontend
gcloud builds submit \
    --config=cloudbuild.yaml \
    --substitutions=_NEXT_PUBLIC_API_URL=$BACKEND_URL \
    --project=$PROJECT_ID
cd ..
echo "✅ Frontend image pushed."

# --- Step 4: Deploy Frontend to Cloud Run ---
echo ""
echo "🌍 [4/4] Deploying Frontend to Cloud Run ($REGION)..."
gcloud run deploy $SERVICE_NAME_FRONTEND \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME_FRONTEND \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars="NEXT_PUBLIC_API_URL=$BACKEND_URL" \
    --project=$PROJECT_ID

FRONTEND_URL=$(gcloud run services describe $SERVICE_NAME_FRONTEND \
    --region $REGION \
    --format='value(status.url)' \
    --project=$PROJECT_ID)

echo ""
echo "═════════════════════════════════════════════════════"
echo "🎉 DEPLOYMENT COMPLETE!"
echo "   FRONTEND  : $FRONTEND_URL"
echo "   BACKEND   : $BACKEND_URL"
echo "   PROJECT   : https://console.cloud.google.com/home/dashboard?project=$PROJECT_ID"
echo "═════════════════════════════════════════════════════"
