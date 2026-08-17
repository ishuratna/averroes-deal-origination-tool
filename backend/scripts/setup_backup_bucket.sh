#!/usr/bin/env bash
#
# Create the append-only backup bucket for the Averroes deal tool.
#
# WHAT THIS PROTECTS AGAINST
#   * a bad job or migration overwriting good enrichment  -> targets_archive (BigQuery)
#   * losing the BigQuery dataset entirely                -> this bucket
# Two different failures. You need both; neither substitutes for the other.
#
# Run it once:
#   bash backend/scripts/setup_backup_bucket.sh
#
set -euo pipefail

PROJECT="${PROJECT:-averroes-deal-origination}"
BUCKET="${BUCKET:-averroes-deal-archive}"
REGION="${REGION:-europe-west2}"          # London, same jurisdiction as the data
BACKEND_SA="${BACKEND_SA:-}"              # optional: service account of the Cloud Run backend

echo "Project : $PROJECT"
echo "Bucket  : gs://$BUCKET"
echo "Region  : $REGION"
echo

# ── 1. Create the bucket ─────────────────────────────────────────────────────
# Uniform bucket-level access: permissions come from IAM only, so nobody can
# quietly loosen a single object's ACL.
if gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Bucket already exists — leaving it alone."
else
  gcloud storage buckets create "gs://$BUCKET" \
    --project="$PROJECT" \
    --location="$REGION" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access
  echo "Created gs://$BUCKET"
fi

# ── 2. Versioning: an overwrite keeps the previous copy ──────────────────────
# This is the core of "nothing existing gets changed". Writing to the same path
# does not replace the old object, it creates a new generation and keeps the old.
gcloud storage buckets update "gs://$BUCKET" --versioning
echo "Versioning ON — overwrites keep every previous generation."

# ── 3. Soft delete: a delete is recoverable ──────────────────────────────────
# Even an explicit delete is retained and restorable for this window.
gcloud storage buckets update "gs://$BUCKET" \
  --soft-delete-duration=30d 2>/dev/null \
  && echo "Soft delete ON — deletions recoverable for 30 days." \
  || echo "NOTE: soft delete not applied (needs a recent gcloud). Set it in the console: Bucket > Protection."

# ── 4. Lifecycle: age out OLD GENERATIONS only, never the live object ────────
# Without this, every daily export keeps every superseded generation forever.
# numNewerVersions means "only if this many newer copies exist", so the current
# object is never touched. isLive:false restricts it to superseded generations.
LIFECYCLE="$(mktemp)"
cat > "$LIFECYCLE" <<'JSON'
{
  "rule": [
    {
      "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
      "condition": {"age": 30, "matchesPrefix": ["bigquery/"]}
    },
    {
      "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
      "condition": {"age": 180, "matchesPrefix": ["bigquery/"]}
    },
    {
      "action": {"type": "Delete"},
      "condition": {"numNewerVersions": 10, "isLive": false, "age": 365}
    }
  ]
}
JSON
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LIFECYCLE"
rm -f "$LIFECYCLE"
echo "Lifecycle set — exports cool to cheaper classes; only superseded"
echo "  generations older than a year with 10+ newer copies are ever removed."

# ── 5. Let the backend write to it ───────────────────────────────────────────
# objectCreator, NOT objectAdmin: the service can CREATE objects but cannot
# delete or overwrite them. Append-only enforced by IAM, not by convention.
if [[ -n "$BACKEND_SA" ]]; then
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member="serviceAccount:$BACKEND_SA" \
    --role="roles/storage.objectCreator"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member="serviceAccount:$BACKEND_SA" \
    --role="roles/storage.legacyBucketReader"
  echo "Granted objectCreator to $BACKEND_SA (create only — cannot delete or overwrite)."
else
  echo
  echo "ACTION NEEDED: grant the backend write access. Find its service account:"
  echo "  gcloud run services describe averroes-deal-backend --region=europe-west1 \\"
  echo "      --project=$PROJECT --format='value(spec.template.spec.serviceAccountName)'"
  echo "then re-run with BACKEND_SA=<that-address> bash $0"
fi

# ── 6. Point the backend at the bucket ───────────────────────────────────────
echo
echo "Then set the env var (ALWAYS --update-env-vars, never --set-env-vars):"
echo "  gcloud run services update averroes-deal-backend --region=europe-west1 \\"
echo "      --project=$PROJECT --update-env-vars BACKUP_BUCKET=$BUCKET"

# ── 7. OPTIONAL, IRREVERSIBLE: Bucket Lock ───────────────────────────────────
cat <<'WARN'

────────────────────────────────────────────────────────────────────────────
OPTIONAL AND PERMANENT: Bucket Lock (WORM)
────────────────────────────────────────────────────────────────────────────
A retention policy stops objects being deleted before they reach a given age.
LOCKING that policy makes it irreversible: after locking, NOBODY can delete
those objects or the bucket until the retention period expires. Not you, not a
project owner, not Google Support. You also cannot shorten the period, and you
carry the storage cost for the full term.

Deliberately NOT run by this script. The settings above already protect against
every realistic accident. Only lock if you have a compliance reason to.

  # Reversible first — try living with it before committing:
  gcloud storage buckets update gs://BUCKET --retention-period=1y

  # ONE-WAY DOOR. There is no undo.
  # gcloud storage buckets update gs://BUCKET --lock-retention-period
────────────────────────────────────────────────────────────────────────────
WARN

echo
echo "Done. Verify with:"
echo "  gcloud storage buckets describe gs://$BUCKET --project=$PROJECT"
