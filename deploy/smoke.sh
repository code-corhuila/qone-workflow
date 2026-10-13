#!/usr/bin/env sh
# Smoke checks of the workflow image by HTTP (Annex E entry, without a token): health with its
# store, the envelope on 401 and on an unknown route, the correlation header. Usage:
#   deploy/smoke.sh http://localhost:8012
set -eu
BASE="${1:-http://localhost:8012}"
fail() { echo "FAIL: $1" >&2; exit 1; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

[ "$(status "$BASE/api/v1/sagas/health")" = "200" ] || fail "/health is not 200 (is the saga store up?)"
curl -s "$BASE/api/v1/sagas/health" | grep -q '"service":"qone-workflow"' || fail "health does not name the service"
[ "$(status -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/sagas/enroll-student")" = "401" ] || fail "a start without a token is not 401"
curl -s -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/sagas/enroll-student" | grep -q '"error":"UNAUTHORIZED"' || fail "401 has no envelope"
[ "$(status "$BASE/api/v1/nothing")" = "404" ] || fail "an unknown route is not 404"
corr=$(curl -s -D - -o /dev/null -H 'X-Correlation-Id: smoke-1' "$BASE/api/v1/sagas/health" | tr -d '\r' | grep -i '^x-correlation-id:' | cut -d' ' -f2)
[ "$corr" = "smoke-1" ] || fail "X-Correlation-Id is not returned"
echo "OK: workflow smoke checks passed against $BASE"
