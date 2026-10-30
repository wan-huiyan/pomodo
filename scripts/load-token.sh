#!/usr/bin/env bash
# Store the board's GitHub token in Google Secret Manager as a new version, asking for it without showing it.
#
#   scripts/load-token.sh SECRET_NAME [PROJECT]
#
# SECRET_NAME can also come from POMODO_SECRET, and PROJECT from GOOGLE_CLOUD_PROJECT (or gcloud's default project).
# The secret is created the first time. Run the deploy again (or wait for new instances) to use the new version.
set -euo pipefail

secret="${1:-${POMODO_SECRET:-}}"
project="${2:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
if [ -z "$secret" ] || [ -z "$project" ]; then
  echo "Usage: $0 SECRET_NAME [PROJECT]   (or set POMODO_SECRET and GOOGLE_CLOUD_PROJECT)" >&2
  exit 2
fi

read -rs -p "Paste the GitHub token (it stays hidden), then press Enter: " token
echo
if [ -z "$token" ]; then echo "Nothing entered, so nothing was stored." >&2; exit 1; fi

if ! gcloud secrets describe "$secret" --project="$project" >/dev/null 2>&1; then
  gcloud secrets create "$secret" --project="$project" --replication-policy=automatic
fi
printf %s "$token" | gcloud secrets versions add "$secret" --project="$project" --data-file=-
unset token
echo "Stored as a new version of $secret in $project."
