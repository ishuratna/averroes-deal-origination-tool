#!/usr/bin/env bash
#
# Put the backup on a schedule so it stops depending on anyone remembering.
#
#   bash backend/scripts/setup_backup_schedule.sh
#
# Creates (or updates) two Cloud Scheduler jobs:
#
#   02:00 Europe/London  archive  -> appends every CHANGED company to
#                                    targets_archive (append-only history)
#   02:30 Europe/London  export   -> copies every table into the backup bucket
#
# ORDER MATTERS. The export runs AFTER the archive on purpose, so each night's
# export contains that night's archive rows too. Reverse them and the off-site
# copy is always one day behind the history it is supposed to preserve.
#
# Idempotent: re-running updates the jobs rather than creating duplicates.
#
set -uo pipefail

PROJECT="${PROJECT:-averroes-deal-origination}"
SERVICE="${SERVICE:-averroes-deal-backend}"
REGION="${REGION:-europe-west1}"
TZ_NAME="${TZ_NAME:-Europe/London}"

echo "Project : $PROJECT"
echo "Service : $SERVICE ($REGION)"
echo

# ── 1. Scheduler API ─────────────────────────────────────────────────────────
gcloud services enable cloudscheduler.googleapis.com --project="$PROJECT" 2>/dev/null \
  && echo "Cloud Scheduler API enabled." || echo "Cloud Scheduler API already enabled."

# ── 2. Where do existing jobs live? ──────────────────────────────────────────
# A project's scheduler jobs all share one location. Creating in a different one
# fails, so inherit whatever the existing ch-watch job already uses.
SCHED_LOC=$(gcloud scheduler jobs list --project="$PROJECT" --format="value(name)" 2>/dev/null \
            | head -1 | sed -E 's#.*/locations/([^/]+)/.*#\1#')
[ -z "$SCHED_LOC" ] && SCHED_LOC="$REGION"
echo "Scheduler location: $SCHED_LOC"

# ── 3. Service URL ───────────────────────────────────────────────────────────
BASE=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
       --format="value(status.url)" 2>/dev/null)
if [ -z "$BASE" ]; then
  echo "ERROR: could not read the Cloud Run URL for $SERVICE in $REGION."
  exit 1
fi
echo "Backend : $BASE"

# ── 4. The shared ops token ──────────────────────────────────────────────────
# Read straight off the deployed service so nobody has to paste a secret. These
# endpoints are exempt from Google sign-in (Scheduler cannot hold a session) and
# guarded by this token instead.
TOKEN=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
        --format=json 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    env=d['spec']['template']['spec']['containers'][0].get('env',[])
    print(next((e.get('value','') for e in env if e.get('name')=='WATCH_TOKEN'), ''))
except Exception:
    print('')
")
if [ -z "$TOKEN" ]; then
  echo
  echo "ERROR: WATCH_TOKEN is not set on the Cloud Run service, so the scheduler"
  echo "       would have nothing to authenticate with. Set it first:"
  echo "  gcloud run services update $SERVICE --region=$REGION --project=$PROJECT \\"
  echo "      --update-env-vars WATCH_TOKEN=<your-token>"
  exit 1
fi
echo "Token   : read from the running service (not printed)"
echo

# ── 5. Create or update each job ─────────────────────────────────────────────
make_job () {
  local NAME="$1" SCHEDULE="$2" URI="$3" DESC="$4"
  local ACTION="create"
  if gcloud scheduler jobs describe "$NAME" --location="$SCHED_LOC" --project="$PROJECT" >/dev/null 2>&1; then
    ACTION="update"
  fi
  # attempt-deadline 20m: the archive reads and hashes ~13k rows, so the default
  # 3 minutes would time out and Scheduler would retry a job that was in fact
  # still working. Retries are idempotent (append-only, hash-compared), so a
  # duplicate run appends nothing.
  gcloud scheduler jobs "$ACTION" http "$NAME" \
    --location="$SCHED_LOC" \
    --project="$PROJECT" \
    --schedule="$SCHEDULE" \
    --time-zone="$TZ_NAME" \
    --uri="$URI" \
    --http-method=POST \
    --attempt-deadline=20m \
    --max-retry-attempts=2 \
    --min-backoff=60s \
    --description="$DESC" \
    >/dev/null 2>&1 \
    && echo "  ${ACTION}d  $NAME  ($SCHEDULE $TZ_NAME)" \
    || { echo "  FAILED to $ACTION $NAME"; return 1; }
}

echo "Jobs:"
make_job "averroes-archive-nightly" "0 2 * * *" \
  "${BASE}/admin/archive/run?token=${TOKEN}&note=nightly" \
  "Append every changed company to targets_archive (append-only history)"

make_job "averroes-backup-export-nightly" "30 2 * * *" \
  "${BASE}/admin/backup/export?token=${TOKEN}" \
  "Export all BigQuery tables to the backup bucket (runs after the archive)"

echo
echo "Current schedule:"
gcloud scheduler jobs list --location="$SCHED_LOC" --project="$PROJECT" \
  --format="table(name.basename(), schedule, timeZone, state, lastAttemptTime)" 2>/dev/null

cat <<INFO

Test one now without waiting for 02:00:
  gcloud scheduler jobs run averroes-archive-nightly --location=$SCHED_LOC --project=$PROJECT

Then confirm it landed:
  bash backend/scripts/check_backup.sh

Pause if ever needed:
  gcloud scheduler jobs pause averroes-archive-nightly --location=$SCHED_LOC --project=$PROJECT
INFO
