#!/bin/bash
set -e
PROJECT_ID="averroes-deal-origination"
GITHUB_OWNER=${1:?"Usage: ./setup-cicd.sh <github-owner> <github-repo>"}
GITHUB_REPO=${2:?"Usage: ./setup-cicd.sh <github-owner> <github-repo>"}

echo "Setting up CI/CD for $GITHUB_OWNER/$GITHUB_REPO"

# Grant Cloud Build deploy permissions
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CB_SA}" --role="roles/run.admin" --quiet

gcloud iam service-accounts add-iam-policy-binding $COMPUTE_SA \
  --member="serviceAccount:${CB_SA}" --role="roles/iam.serviceAccountUser" --quiet

# Create backend trigger
gcloud builds triggers create github \
  --project=$PROJECT_ID --repo-name=$GITHUB_REPO --repo-owner=$GITHUB_OWNER \
  --branch-pattern="^main$" --build-config="backend/cloudbuild.yaml" \
  --name="deploy-backend" --description="Auto-deploy backend on push to main" \
  --included-files="backend/**" \
  --substitutions="_GEMINI_API_KEY=AIzaSyCfqd4hM41uXmBAjs4A1Ig1OP76-VyYTV8" \
  --quiet || echo "Backend trigger may already exist"

# Create frontend trigger
gcloud builds triggers create github \
  --project=$PROJECT_ID --repo-name=$GITHUB_REPO --repo-owner=$GITHUB_OWNER \
  --branch-pattern="^main$" --build-config="frontend/cloudbuild.yaml" \
  --name="deploy-frontend" --description="Auto-deploy frontend on push to main" \
  --included-files="frontend/**" --quiet || echo "Frontend trigger may already exist"

echo ""
echo "✅ Done! Push to main and it auto-deploys."
echo "Triggers: https://console.cloud.google.com/cloud-build/triggers?project=$PROJECT_ID"
