#!/usr/bin/env bash
#
# Is the backup actually working? Read-only. Changes nothing.
#
#   bash backend/scripts/check_backup.sh
#
# Deliberately does NOT use set -e: every check runs even if an earlier one
# fails, because a partial picture is what you need when diagnosing.

PROJECT="${PROJECT:-averroes-deal-origination}"
BUCKET="${BUCKET:-averroes-deal-archive}"
DATASET="${DATASET:-averroes_deal_flow}"
SERVICE="${SERVICE:-averroes-deal-backend}"
REGION="${REGION:-europe-west1}"

pass() { printf "  OK    %s\n" "$1"; }
fail() { printf "  FAIL  %s\n" "$1"; }
warn() { printf "  ----  %s\n" "$1"; }

echo "==================================================================="
echo " Backup health check"
echo " project=$PROJECT  bucket=$BUCKET  dataset=$DATASET"
echo "==================================================================="
echo

echo "[1] gcloud account"
ACCT=$(gcloud config get-value account 2>/dev/null)
if [ -n "$ACCT" ] && [ "$ACCT" != "(unset)" ]; then pass "signed in as $ACCT"; else fail "not signed in. Run: gcloud auth login"; fi
echo

echo "[2] Does the bucket exist, and is it protected?"
if gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
  pass "gs://$BUCKET exists"
  VER=$(gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" --format=json 2>/dev/null | grep -iE "\"versioning" -A2 | grep -io "true" | head -1)
  [ -z "$VER" ] && VER=$(gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" --format="value(versioning_enabled)" 2>/dev/null)
  SD=$(gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" --format="value(softDeletePolicy.retentionDurationSeconds)" 2>/dev/null)
  LOC=$(gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" --format="value(location)" 2>/dev/null)
  [ "$VER" = "True" ] || [ "$VER" = "true" ] && pass "versioning ON (an overwrite keeps the old copy)" || fail "versioning OFF. Run: gcloud storage buckets update gs://$BUCKET --versioning"
  if [ -n "$SD" ] && [ "$SD" != "0" ]; then pass "soft delete ON ($((SD/86400)) days)"; else warn "soft delete not set (optional)"; fi
  warn "location: $LOC"
else
  fail "gs://$BUCKET does NOT exist. Run: bash backend/scripts/setup_backup_bucket.sh"
fi
echo

echo "[3] Can the backend write to it?"
BSA=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" --format="value(spec.template.spec.serviceAccountName)" 2>/dev/null)
PNUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)" 2>/dev/null)
[ -z "$BSA" ] && BSA="${PNUM}-compute@developer.gserviceaccount.com"
warn "backend service account: $BSA"
POL=$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --project="$PROJECT" --format=json 2>/dev/null)
if echo "$POL" | grep -q "$BSA"; then
  pass "service account has a binding on the bucket"
  echo "$POL" | grep -q "objectCreator" && pass "objectCreator (create only, cannot delete or overwrite)" || warn "has access but not objectCreator"
else
  fail "no binding for the backend. Run: BACKEND_SA=$BSA bash backend/scripts/setup_backup_bucket.sh"
fi
echo

echo "[4] Does the backend know which bucket to use?"
ENVB=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" --format="value(spec.template.spec.containers[0].env)" 2>/dev/null | tr ';' '\n' | grep -i BACKUP_BUCKET)
if [ -n "$ENVB" ]; then pass "BACKUP_BUCKET is set: $ENVB"
else fail "BACKUP_BUCKET not set. Run: gcloud run services update $SERVICE --region=$REGION --project=$PROJECT --update-env-vars BACKUP_BUCKET=$BUCKET"; fi
echo

echo "[5] Has anything actually been exported to the bucket?"
OBJ=$(gcloud storage ls -r "gs://$BUCKET/**" 2>/dev/null | grep -c "json.gz")
if [ "${OBJ:-0}" -gt 0 ]; then
  pass "$OBJ export file(s) in the bucket"
  echo "      most recent:"
  gcloud storage ls -r "gs://$BUCKET/**" 2>/dev/null | grep "json.gz" | tail -4 | sed "s/^/        /"
  gcloud storage du -s "gs://$BUCKET" 2>/dev/null | sed "s/^/      total: /"
else
  fail "bucket is EMPTY. The export has not run yet."
  echo "        curl -X POST \"https://averroes-deal-backend-890361705054.$REGION.run.app/admin/backup/export?token=\$T\""
fi
echo

echo "[6] Has the append-only archive run?"
Q="SELECT COUNT(*) AS versions, COUNT(DISTINCT name) AS companies, CAST(MAX(archived_at) AS STRING) AS last_run FROM \`$PROJECT.$DATASET.targets_archive\`"
OUT=$(bq query --project_id="$PROJECT" --use_legacy_sql=false --format=csv "$Q" 2>/dev/null | grep -E "^[0-9]+," | tail -1)
if [ -n "$OUT" ] && [ "$OUT" != "versions,companies,last_run" ]; then
  V=$(echo "$OUT" | cut -d, -f1); C=$(echo "$OUT" | cut -d, -f2); L=$(echo "$OUT" | cut -d, -f3-)
  if [ "${V:-0}" -gt 0 ]; then
    pass "$V archived version(s) covering $C companies"
    warn "last archived: $L"
  else
    fail "archive table exists but is EMPTY. The archive has not run yet."
  fi
  LIVE=$(bq query --project_id="$PROJECT" --use_legacy_sql=false --format=csv \
        "SELECT COUNT(*) FROM \`$PROJECT.$DATASET.targets\`" 2>/dev/null | grep -E "^[0-9]+$" | tail -1)
  warn "live targets table holds $LIVE companies"
  if [ -n "$LIVE" ] && [ -n "$C" ] && [ "$C" -eq "$C" ] 2>/dev/null && [ "$LIVE" -eq "$LIVE" ] 2>/dev/null && [ "$C" -ge "$LIVE" ]; then
    pass "every live company has at least one archived version"
  elif [ -n "$LIVE" ] && [ -n "$C" ] && [ "$LIVE" -eq "$LIVE" ] 2>/dev/null && [ "$C" -eq "$C" ] 2>/dev/null; then
    fail "$((LIVE - C)) live companies have NO archived version yet"
    echo "        curl -X POST \"https://averroes-deal-backend-890361705054.$REGION.run.app/admin/archive/run?token=\$T&force=1&note=baseline\""
  fi
else
  fail "targets_archive does not exist yet. The archive has not run even once."
  echo "        curl -X POST \"https://averroes-deal-backend-890361705054.$REGION.run.app/admin/archive/run?token=\$T&force=1&note=baseline\""
fi
echo

echo "[7] Is it automatic, or does it depend on someone remembering?"
SLOC=$(gcloud scheduler jobs list --project="$PROJECT" --format="value(name)" 2>/dev/null | head -1 | sed -E "s#.*/locations/([^/]+)/.*#\\1#")
[ -z "$SLOC" ] && SLOC="$REGION"
JOBS=$(gcloud scheduler jobs list --location="$SLOC" --project="$PROJECT" --format="value(name.basename(),state,schedule)" 2>/dev/null)
for J in averroes-archive-nightly averroes-backup-export-nightly; do
  LINE=$(echo "$JOBS" | grep "^$J")
  if [ -z "$LINE" ]; then
    fail "$J does not exist. Run: bash backend/scripts/setup_backup_schedule.sh"
  elif echo "$LINE" | grep -qi "PAUSED"; then
    fail "$J exists but is PAUSED. Run: gcloud scheduler jobs resume $J --location=$SLOC --project=$PROJECT"
  else
    pass "$J is enabled ($(echo "$LINE" | awk "{print \$3, \$4, \$5, \$6, \$7}"))"
  fi
done
if [ -n "$JOBS" ]; then
  echo "      last attempts:"
  gcloud scheduler jobs list --location="$SLOC" --project="$PROJECT" \
    --format="table[no-heading](name.basename():label=JOB, lastAttemptTime:label=LAST)" 2>/dev/null | sed "s/^/        /"
fi
echo
echo "==================================================================="
echo " Any FAIL above tells you the next command to run."
echo "==================================================================="
