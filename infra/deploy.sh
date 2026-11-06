#!/usr/bin/env bash
# make deploy: build the application image on this machine with the find-care
# tables in it, push it to Artifact Registry, roll it out to the Cloud Run
# service behind Google sign-in (IAP), and smoke-check it through IAP. It ships
# only committed code. The guide, one-time console steps included, is
# infra/deploy.md.
#
# Settings, each overridable from the environment: HH_GCP_PROJECT,
# HH_GCP_REGION, HH_GCP_SERVICE, and HH_SERVING_DIR for the tables.
set -euo pipefail

PROJECT="${HH_GCP_PROJECT:-honest-healthcare}"
REGION="${HH_GCP_REGION:-us-east1}"
SERVICE="${HH_GCP_SERVICE:-application}"
REPO=containers
TABLES="${HH_SERVING_DIR:-data/serving}"
SMOKE_SA="deploy-smoke@${PROJECT}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
step() { printf '\n==> %s\n' "$*"; }
where=(--project "$PROJECT" --region "$REGION")

[ -z "$(git status --porcelain)" ] || { echo "the checkout has uncommitted changes, and make deploy ships only what's committed" >&2; exit 1; }
[ -f "$TABLES/manifest.json" ] || { echo "no find-care tables in $TABLES: run make build" >&2; exit 1; }
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = main ] || echo "note: deploying the branch $branch, not main"
# The image's tag says which code and which tables it holds.
built="$(python3 -c 'import json, sys; b = json.load(open(sys.argv[1]))["built_at"][:19]
print(b.replace("-", "").replace(":", "").replace("T", "-"))' "$TABLES/manifest.json")"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/application:$(git rev-parse --short=12 HEAD)-$built"

step "One-time setup, where it isn't done: the APIs, the image repository and the smoke check's account"
gcloud services enable artifactregistry.googleapis.com run.googleapis.com iap.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com --project "$PROJECT"
gcloud artifacts repositories describe "$REPO" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --project "$PROJECT" --location "$REGION" \
    --repository-format docker --description "The application image, from make deploy"
# Keep the last three images, which leaves room to roll back and stays in the free tier.
gcloud artifacts repositories set-cleanup-policies "$REPO" --project "$PROJECT" --location "$REGION" \
  --policy infra/cleanup-policy.json --no-dry-run >/dev/null
gcloud iam service-accounts describe "$SMOKE_SA" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create deploy-smoke --project "$PROJECT" --display-name "make deploy's smoke check"
gcloud iam service-accounts add-iam-policy-binding "$SMOKE_SA" --project "$PROJECT" \
  --member "user:$(gcloud config get account 2>/dev/null)" --role roles/iam.serviceAccountTokenCreator >/dev/null

step "Build $IMAGE for Cloud Run (linux/amd64), with the tables in $TABLES"
docker build --platform linux/amd64 --target prod --build-context tables="$TABLES" -t "$IMAGE" application

step "Push it"
gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin "https://$REGION-docker.pkg.dev" >/dev/null
docker push --quiet "$IMAGE"

# One instance at most bounds the bill; the Cloud Run spend cap (infra/deploy.md) is the backstop.
step "Roll it out to $SERVICE, behind IAP"
gcloud run deploy "$SERVICE" "${where[@]}" --image "$IMAGE" --no-allow-unauthenticated --iap \
  --port 8000 --cpu 1 --memory 512Mi --cpu-boost --concurrency 80 --timeout 30 \
  --min-instances 0 --max-instances 1 --set-env-vars DUCKDB_MEMORY_LIMIT=256MB,DUCKDB_THREADS=1 \
  --labels "commit=$(git rev-parse --short=12 HEAD)" --quiet
# IAP reaches the service as its own service agent, which the console grants and gcloud doesn't.
number="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
gcloud run services add-iam-policy-binding "$SERVICE" "${where[@]}" \
  --member "serviceAccount:service-$number@gcp-sa-iap.iam.gserviceaccount.com" --role roles/run.invoker >/dev/null
gcloud iap web add-iam-policy-binding "${where[@]}" --resource-type cloud-run --service "$SERVICE" \
  --member "serviceAccount:$SMOKE_SA" --role roles/iap.httpsResourceAccessor >/dev/null

step "Smoke-check it through IAP"
url="$(gcloud run services describe "$SERVICE" "${where[@]}" --format 'value(status.url)')"
client="$(gcloud iap settings get --project "$PROJECT" --format 'value(accessSettings.oauthSettings.clientId)')"
if [ -z "$client" ]; then
  echo "deployed, but not checked: IAP has no OAuth client yet, so nobody can sign in (infra/deploy.md)" >&2
  exit 1
fi
token="$(gcloud auth print-identity-token --impersonate-service-account "$SMOKE_SA" --audiences "$client" \
  --include-email 2>/dev/null)"
python3 infra/smoke.py "$url" --token "$token"
echo "Deployed $IMAGE to $url"
