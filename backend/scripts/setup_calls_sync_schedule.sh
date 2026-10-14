#!/usr/bin/env bash
#
# The 7 AM call-notes sync: every Granola meeting about a Responded-or-later
# company lands on its card before the day starts, and a first call moves
# the company from Responded to Meeting.
#
#   bash backend/scripts/setup_calls_sync_schedule.sh
#
# One Cloud Scheduler job, 07:00 Europe/London daily (after the 6 AM email
# sync, so a reply that arrived overnight is already Responded when the call
# notes look for candidates), hitting /admin/calls/sync?dry_run=0 (token-
# gated). Reads 7 days back: Granola only exposes a note once its AI summary
# has finished, so yesterday's late call may surface a day later, and the
# sync skips notes it already holds unchanged.
#
# attempt-deadline 10m; retries capped at 1 (the sync is idempotent: MERGE
# on granola_id + company, promotion only from Responded).
#
# Idempotent: re-running updates the job rather than creating duplicates.
set -uo pipefail

PROJECT="${PROJECT:-averroes-deal-origination}"
SERVICE="${SERVICE:-averroes-deal-backend}"
REGION="${REGION:-europe-west1}"
TZ_NAME="${TZ_NAME:-Europe/London}"

echo "Project : $PROJECT"
echo "Service : $SERVICE ($REGION)"

SCHED_LOC=$(gcloud scheduler jobs list --project="$PROJECT" --format="value(name)" 2>/dev/null \
            | head -1 | sed -E 's#.*/locations/([^/]+)/.*#\1#')
[ -z "$SCHED_LOC" ] && SCHED_LOC="$REGION"
echo "Scheduler location: $SCHED_LOC"

BASE=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
       --format="value(status.url)" 2>/dev/null)
if [ -z "$BASE" ]; then
  echo "ERROR: could not read the Cloud Run URL for $SERVICE in $REGION."
  exit 1
fi

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
  echo "ERROR: WATCH_TOKEN is not set on the Cloud Run service."
  exit 1
fi
echo "Token   : read from the running service (not printed)"

NAME="averroes-calls-sync-morning"
ACTION="create"
gcloud scheduler jobs describe "$NAME" --location="$SCHED_LOC" --project="$PROJECT" >/dev/null 2>&1 && ACTION="update"

gcloud scheduler jobs "$ACTION" http "$NAME" \
  --location="$SCHED_LOC" \
  --project="$PROJECT" \
  --schedule="0 7 * * *" \
  --time-zone="$TZ_NAME" \
  --uri="${BASE}/admin/calls/sync?token=${TOKEN}&days=7&dry_run=0" \
  --http-method=GET \
  --attempt-deadline=10m \
  --max-retry-attempts=1 \
  --min-backoff=120s \
  --description="Morning call-notes sync: Granola meetings onto company cards; Responded to Meeting on a first call" \
  && echo "${ACTION}d $NAME (0 7 * * * $TZ_NAME)" \
  || { echo "FAILED to $ACTION $NAME"; exit 1; }

echo
echo "Test one run now without waiting for 7 AM:"
echo "  gcloud scheduler jobs run $NAME --location=$SCHED_LOC --project=$PROJECT"
echo
echo "Pause if ever needed:"
echo "  gcloud scheduler jobs pause $NAME --location=$SCHED_LOC --project=$PROJECT"
