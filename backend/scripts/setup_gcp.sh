#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Averroes Deal Origination – GCP One-Time Project Setup Script
# Run this in Google Cloud Shell from the averroes-deal-origination project
# ═══════════════════════════════════════════════════════════════
set -e

PROJECT_ID="averroes-deal-origination"
REGION="europe-west1"
SA_NAME="averroes-deal-backend"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BQ_DATASET="averroes_deal_flow"
BQ_LOCATION="EU"
BUCKET="averroes-deal-intelligence"
KEY_FILE="service-account-deal.json"

echo "═══════════════════════════════════════════════════"
echo " Averroes Deal Origination — GCP Setup"
echo " Project: $PROJECT_ID"
echo "═══════════════════════════════════════════════════"

# 0. Set active project
gcloud config set project $PROJECT_ID

# 1. Enable required APIs
echo ""
echo "🔧 [1/5] Enabling GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    bigquery.googleapis.com \
    bigquerystorage.googleapis.com \
    iam.googleapis.com \
    iamcredentials.googleapis.com \
    artifactregistry.googleapis.com \
    generativelanguage.googleapis.com \
    --project=$PROJECT_ID
echo "✅ APIs enabled."

# 2. Create Service Account
echo ""
echo "🔑 [2/5] Creating Service Account: $SA_NAME..."
gcloud iam service-accounts create $SA_NAME \
    --display-name="Averroes Deal Backend" \
    --description="Service account for the Deal Origination backend (Cloud Run, GCS, BigQuery)" \
    --project=$PROJECT_ID || echo "Service account already exists, continuing..."

# 3. Grant IAM Roles
echo ""
echo "🛡️  [3/5] Granting IAM roles to $SA_EMAIL..."

ROLES=(
    "roles/storage.objectAdmin"
    "roles/bigquery.dataEditor"
    "roles/bigquery.jobUser"
    "roles/run.invoker"
    "roles/logging.logWriter"
)

for ROLE in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding $PROJECT_ID \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$ROLE" \
        --quiet
    echo "  ✓ Granted: $ROLE"
done
echo "✅ IAM roles granted."

# 4. Create and download service account key
echo ""
echo "📥 [4/5] Generating service account key → $KEY_FILE..."
gcloud iam service-accounts keys create $KEY_FILE \
    --iam-account=$SA_EMAIL \
    --project=$PROJECT_ID
echo "✅ Key saved to: $KEY_FILE"
echo "⚠️  IMPORTANT: Move this file to the project root as 'service-account.json' and do NOT commit it to git."

# 5. Create BigQuery Dataset and Table
echo ""
echo "🗃️  [5/5] Setting up BigQuery Dataset: $BQ_DATASET..."

bq --location=$BQ_LOCATION mk \
    --dataset \
    --description="Averroes Deal Origination target company database" \
    ${PROJECT_ID}:${BQ_DATASET} 2>/dev/null || echo "Dataset already exists, continuing..."

bq mk \
    --table \
    --description="Master table of all evaluated deal targets" \
    ${PROJECT_ID}:${BQ_DATASET}.targets \
    company_id:STRING,name:STRING,website:STRING,sector:STRING,region:STRING,ownership:STRING,description:STRING,match_score:FLOAT,status:STRING,source:STRING,contact_name:STRING,contact_email:STRING,growth_signals:BOOL,ingested_at:TIMESTAMP,estimated_ebitda:FLOAT 2>/dev/null || echo "Table already exists, continuing..."

echo "✅ BigQuery dataset and table ready."

# Final summary
echo ""
echo "═══════════════════════════════════════════════════"
echo "🎉 GCP Setup Complete for: $PROJECT_ID"
echo ""
echo "   Service Account : $SA_EMAIL"
echo "   Key File        : $KEY_FILE  ← move to project root"
echo "   BigQuery        : ${PROJECT_ID}:${BQ_DATASET}.targets"
echo "   GCS Bucket      : gs://$BUCKET"
echo ""
echo "📋 Next Steps:"
echo "   1. Download $KEY_FILE from Cloud Shell → Files → Download"
echo "   2. Place it at: averroes-deal-origination-tool/service-account.json"
echo "   3. Fill in GEMINI_API_KEY in backend/.env"
echo "   4. Run: bash cloud-deploy.sh"
echo "═══════════════════════════════════════════════════"
