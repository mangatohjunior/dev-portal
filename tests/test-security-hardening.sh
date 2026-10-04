#!/usr/bin/env bash
# Bash/curl port of test-security-hardening.ps1 (for macOS/Linux).
# Requires the docker compose stack to already be running (portal + keycloak).
set -uo pipefail

BASE="http://localhost:8000"
COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT

echo "== 1. Security headers present even on an unauthenticated redirect =="
headers=$(curl -s -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/")
status_code=$(echo "$headers" | head -1 | awk '{print $2}')
location=$(echo "$headers" | grep -i '^location:' | sed 's/^[Ll]ocation: //' | tr -d '\r')
echo "GET / status: $status_code -> $location"
echo "X-Frame-Options present: $(echo "$headers" | grep -qi '^x-frame-options:' && echo true || echo false)"
echo "CSP present: $(echo "$headers" | grep -qi '^content-security-policy:' && echo true || echo false)"

echo
echo "== 2. Full login flow for the in-group user =="
login_headers=$(curl -s -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/login/keycloak")
authorize_url=$(echo "$login_headers" | grep -i '^location:' | sed 's/^[Ll]ocation: //' | tr -d '\r')

auth_page=$(curl -s -b "$COOKIES" -c "$COOKIES" "$authorize_url")
action_url=$(echo "$auth_page" | grep -oE '<form[^>]*action="[^"]*"' | head -1 | sed -E 's/.*action="([^"]*)".*/\1/' | sed 's/&amp;/\&/g')

final_status_url=$(curl -s -o /dev/null -w '%{http_code} %{url_effective}' -L --max-redirs 20 \
  -b "$COOKIES" -c "$COOKIES" \
  --data-urlencode "username=devportal-user" \
  --data-urlencode "password=devportal-pass" \
  "$action_url")
echo "Post-login status/url: $final_status_url"

me=$(curl -s -b "$COOKIES" -c "$COOKIES" "$BASE/api/me")
echo "GET /api/me: $me"

echo
echo "== 3. Invalid enum value should be rejected with 422 =="
bad_status=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIES" -c "$COOKIES" \
  -H 'Content-Type: application/json' \
  -d '{"targetEnvironment":"staging-hacked","allowProd":false,"tenantList":"tenant-a","pipelineTrigger":"tenant_baseline","taint":false}' \
  "$BASE/api/trigger-deployment")
echo "Invalid enum request status: $bad_status"

echo
echo "== 4. RP-initiated logout should end the Keycloak SSO session too =="
logout_headers=$(curl -s -D - -o /dev/null -b "$COOKIES" -c "$COOKIES" "$BASE/logout")
logout_location=$(echo "$logout_headers" | grep -i '^location:' | sed 's/^[Ll]ocation: //' | tr -d '\r')
echo "GET /logout -> $logout_location"

end_session_body=$(curl -s -L --max-redirs 20 -b "$COOKIES" -c "$COOKIES" "$logout_location")
if echo "$end_session_body" | grep -qi 'logged out'; then
  echo "Landing page shows logged out: true"
else
  echo "Landing page shows logged out: false"
fi

me_after_status=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIES" -c "$COOKIES" "$BASE/api/me")
echo "GET /api/me after logout status: $me_after_status"
