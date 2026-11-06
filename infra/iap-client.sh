#!/usr/bin/env bash
# One-time setup: give IAP the OAuth client it signs people in with, from the
# JSON the console downloads for that client (infra/deploy.md). The secret
# reaches gcloud through a file only this user can read, deleted after.
#
#   bash infra/iap-client.sh ~/Downloads/client_secret_<...>.json
set -euo pipefail

PROJECT="${HH_GCP_PROJECT:-honest-healthcare}"
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: bash infra/iap-client.sh <the client's downloaded JSON>" >&2; exit 1; }

settings="$(mktemp)"
trap 'rm -f "$settings"' EXIT
python3 - "$1" >"$settings" <<'EOF'
import json, sys
web = json.load(open(sys.argv[1]))["web"]
print(json.dumps({"accessSettings": {"oauthSettings": {"clientId": web["client_id"], "clientSecret": web["client_secret"]}}}))
EOF
gcloud iap settings set "$settings" --project "$PROJECT" >/dev/null
echo "IAP signs people in with OAuth client $(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["web"]["client_id"])' "$1")"
