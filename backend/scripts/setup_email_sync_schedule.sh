#!/usr/bin/env bash
#
# The 6 AM email sync: the board is already up to date when the day starts.
#
#   bash backend/scripts/setup_email_sync_schedule.sh
#
# One Cloud Scheduler job, 06:00 Europe/London daily, hitting
# /email/sync/run (token-gated). One run does the whole morning routine in
# order: read the mailbox, log new messages, classify replies, verify delivery
# (bounces before the reply rule), apply the reply rule both ways, detect
# out-of-office deferrals.
#
# attempt-deadline 20m: a sync with many new messages classifies each with AI
# and can take several minutes; the default 3-minute deadline would kill a run
# that was working. Retries are safe - the sync dedups by Message-ID and the
# reply rule is idempotent - but capped at 1 so a genuinely failing morning
# does not hammer the mailbox.
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

NAME="averroes-email-sync-morning"
ACTION="create"
gcloud scheduler jobs describe "$NAME" --location="$SCHED_LOC" --project="$PROJECT" >/dev/null 2>&1 && ACTION="update"

gcloud scheduler jobs "$ACTION" http "$NAME" \
  --location="$SCHED_LOC" \
  --project="$PROJECT" \
  --schedule="0 6 * * *" \
  --time-zone="$TZ_NAME" \
  --uri="${BASE}/email/sync/run?token=${TOKEN}" \
  --http-method=POST \
  --attempt-deadline=20m \
  --max-retry-attempts=1 \
  --min-backoff=120s \
  --description="Morning email sync: log replies, verify delivery, apply the reply rule before the day starts" \
  && echo "${ACTION}d $NAME (0 6 * * * $TZ_NAME)" \
  || { echo "FAILED to $ACTION $NAME"; exit 1; }

echo
echo "Test one run now without waiting for 6 AM:"
echo "  gcloud scheduler jobs run $NAME --location=$SCHED_LOC --project=$PROJECT"
echo
echo "Pause if ever needed:"
echo "  gcloud scheduler jobs pause $NAME --location=$SCHED_LOC --project=$PROJECT"
