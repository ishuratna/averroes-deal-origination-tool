#!/usr/bin/env bash
#
# Nightly bulk SmartFill: enrich the backlog unattended, best prospects first.
#
#   bash backend/scripts/setup_auto_smartfill.sh
#
# One Cloud Scheduler job ticking every 12 minutes from 20:00 to 01:48
# Europe/London. Each tick processes a batch of ~15 with 5 concurrent workers
# and stops for the night the moment today's total (manual runs INCLUDED)
# reaches the 250 target, so the job and a heavy manual day can never stack.
#
# WHY TICKS AND NOT ONE 8 PM CALL: a scheduled HTTP call cannot run for hours.
# WHY CONCURRENCY AND A LONGER WINDOW: measured on the first night, a real
# SmartFill takes 5-6 minutes, so a sequential tick finished ~1 company and the
# night produced 21 instead of ~240. Five workers x ~2 rounds per 11-minute
# tick = ~8-10 companies; 30 ticks = 240-300 capacity against the 250 target.
#
# NOTE the window crosses midnight UTC, when the daily counter resets: ticks
# from 00:00 count against the NEXT day's target, which only means the next
# evening starts with a little less headroom. The hard cap still binds per day.
#
# COST: 250/day = ~1,000 grounded requests, inside the 1,500/day free
# search-grounding allowance. Tokens only, ~£11/day while the backlog lasts
# (~6-7 weeks), then pennies: each night finds only newly ingested companies.
#
# Idempotent: re-running updates the job rather than duplicating it.
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

NAME="averroes-auto-smartfill"
ACTION="create"
gcloud scheduler jobs describe "$NAME" --location="$SCHED_LOC" --project="$PROJECT" >/dev/null 2>&1 && ACTION="update"

# attempt-deadline 11m: under the 12-minute spacing, so a slow tick is cut off
# before the next one starts and two ticks can never run at once.
gcloud scheduler jobs "$ACTION" http "$NAME" \
  --location="$SCHED_LOC" \
  --project="$PROJECT" \
  --schedule="*/12 0-1,20-23 * * *" \
  --time-zone="$TZ_NAME" \
  --uri="${BASE}/smartfill/auto/run?token=${TOKEN}" \
  --http-method=POST \
  --attempt-deadline=11m \
  --max-retry-attempts=0 \
  --description="Nightly bulk SmartFill: batches of 15, best prospects first, stops at the shared 250/day target" \
  && echo "${ACTION}d $NAME (*/12 20-23 * * * $TZ_NAME)" \
  || { echo "FAILED to $ACTION $NAME"; exit 1; }

echo
echo "Test one tick now without waiting for 8 PM:"
echo "  gcloud scheduler jobs run $NAME --location=$SCHED_LOC --project=$PROJECT"
echo
echo "Watch what it did:"
echo "  curl -s -X POST \"\$B/smartfill/auto/run?token=\$T\" | python3 -m json.tool"
echo
echo "Pause during quiet periods (saves the AI spend entirely):"
echo "  gcloud scheduler jobs pause $NAME --location=$SCHED_LOC --project=$PROJECT"
