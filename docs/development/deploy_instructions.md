# Cloud Run Deployment Guide (FastAPI Fubon Gateway)

## Prerequisites
- gcloud CLI >= 426.0
- Google Cloud project with Cloud Run + Artifact Registry APIs enabled
- Docker or Cloud Build permissions
- Secrets supplied via Secret Manager or deploy-time env vars:
  - `FUBON_USER_ID`
  - `FUBON_USER_PASSWORD`
  - `FUBON_CA_PATH`
  - `FUBON_CA_PASSWORD`

## Local sanity check
```bash
docker build -t app-min .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e FUBON_USER_ID=demo-id \
  -e FUBON_USER_PASSWORD=demo-pass \
  -e FUBON_CA_PATH=/app/certs/demo.pfx \
  -e FUBON_CA_PASSWORD=demo-ca-pass \
  app-min
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS -X POST http://127.0.0.1:8080/api/v1/session -H "Content-Type: application/json" -d '{}'
```

## Build & push image
```bash
PROJECT_ID=$(gcloud config get-value project)
REGION=asia-east1
IMAGE="asia-east1-docker.pkg.dev/${PROJECT_ID}/vnpy-fubon/app-min:$(date +%Y%m%d-%H%M%S)"

gcloud builds submit --tag "${IMAGE}"
```

## Deploy to Cloud Run
```bash
SERVICE=vnpy-fubon-gateway
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --port 8080 \
  --allow-unauthenticated \
  --set-env-vars PORT=8080 \
  --set-secrets FUBON_USER_ID=projects/${PROJECT_ID}/secrets/fubon-user-id:latest \
                 FUBON_USER_PASSWORD=projects/${PROJECT_ID}/secrets/fubon-user-password:latest \
                 FUBON_CA_PATH=projects/${PROJECT_ID}/secrets/fubon-ca-path:latest \
                 FUBON_CA_PASSWORD=projects/${PROJECT_ID}/secrets/fubon-ca-password:latest
```

## Health verification
```bash
SERVICE_URL=$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')
curl -fsS "${SERVICE_URL}/healthz"
```

## Optional: trigger session login
```bash
curl -fsS -X POST "${SERVICE_URL}/api/v1/session" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"override-id","user_password":"override-pass"}'
```

## Rollback / cleanup
```bash
# Shift traffic to previous revision
gcloud run services update-traffic "${SERVICE}" --region "${REGION}" --to-latest=false --splits <REVISION>=100

# Delete service if decommissioning
gcloud run services delete "${SERVICE}" --region "${REGION}"
```

## Configuration map
| Env var                       | Purpose                            | Default source                         |
| ----------------------------- | ---------------------------------- | -------------------------------------- |
| `FUBON_USER_ID`               | SDK login personal ID              | Secret / request payload override      |
| `FUBON_USER_PASSWORD`         | SDK login password                 | Secret / request payload override      |
| `FUBON_CA_PATH`               | Certificate path inside container  | Secret / request payload override      |
| `FUBON_CA_PASSWORD`           | Certificate password               | Secret / request payload override      |
| `FUBON_SDK_CLIENT_CLASS`      | Override SDK client class          | Optional env/config                    |
| `FUBON_SDK_EXTRA_INIT_KWARGS` | JSON extras for client constructor | Optional env/config                    |
| `PORT`                        | HTTP port (Cloud Run)              | Defaults to `8080`                     |

> **Assumption:** Certificate files are mounted via Cloud Run secrets/volumes when authenticating against live endpoints.
