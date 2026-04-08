#!/bin/bash

# Configuration
PROJECT_ID=$(gcloud config get-value project)
REGION="europe-west1" # London/Brussels region for better latency
SERVICE_NAME_BACKEND="averroes-deal-backend"
SERVICE_NAME_FRONTEND="averroes-deal-frontend"

echo "🚀 Starting Google Cloud Deployment for Averroes Deal Origination Tool"
echo "Project: $PROJECT_ID | Region: $REGION"

# 1. Build and push Backend
echo "📦 Building Backend..."
cd backend
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND
cd ..

# 2. Deploy Backend to Cloud Run
echo "🌍 Deploying Backend to Cloud Run..."
gcloud run deploy $SERVICE_NAME_BACKEND \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME_BACKEND \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"

# Get Backend URL
BACKEND_URL=$(gcloud run services describe $SERVICE_NAME_BACKEND --region $REGION --format='value(status.url)')
echo "✅ Backend Live at: $BACKEND_URL"

# 3. Build and push Frontend
echo "📦 Building Frontend..."
cd frontend
gcloud builds submit --config=cloudbuild.yaml \
    --substitutions=_NEXT_PUBLIC_API_URL=$BACKEND_URL
cd ..

# 4. Deploy Frontend to Cloud Run
echo "🌍 Deploying Frontend to Cloud Run..."
gcloud run deploy $SERVICE_NAME_FRONTEND \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME_FRONTEND \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars="NEXT_PUBLIC_API_URL=$BACKEND_URL"

# Get Frontend URL
FRONTEND_URL=$(gcloud run services describe $SERVICE_NAME_FRONTEND --region $REGION --format='value(status.url)')

echo "--------------------------------------------------------"
echo "🎉 DEPLOYMENT COMPLETE!"
echo "FRONTEND URL: $FRONTEND_URL"
echo "BACKEND URL:  $BACKEND_URL"
echo "--------------------------------------------------------"
