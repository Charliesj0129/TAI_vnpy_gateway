## Container smoke
1. `docker build -t app-min .`
2. `docker run --rm -p 8080:8080 -e PORT=8080 app-min`
3. `curl -fsS http://127.0.0.1:8080/healthz`
4. `curl -fsS -X POST http://127.0.0.1:8080/api/v1/session -H "Content-Type: application/json" -d '{"user_id":"demo","user_password":"demo","ca_path":"/tmp/demo.pfx","ca_password":"secret"}'`
5. `curl -fsS -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8080/api/v1/session -H "Content-Type: application/json" -d '{"user_id":"demo"}'` (expect `400`)

## Cloud Run smoke
1. `SERVICE_URL=$(gcloud run services describe vnpy-fubon-gateway --region asia-east1 --format='value(status.url)')`
2. `curl -fsS "${SERVICE_URL}/healthz"`
3. `curl -fsS -X POST "${SERVICE_URL}/api/v1/session" -H "Content-Type: application/json" -d '{"user_id":"demo","user_password":"demo","ca_path":"/tmp/demo.pfx","ca_password":"secret"}'`
4. `curl -fsS -o /dev/null -w "%{http_code}" -X POST "${SERVICE_URL}/api/v1/session" -H "Content-Type: application/json" -d '{"user_id":"demo"}'` (expect `400`)

## Expected responses
- `GET /healthz` -> status `200` with payload `{"status":"ok","last_login":...}`
- `POST /api/v1/session` -> status `200` with `{"is_success": true, ...}` when四項認證正確；若缺任一欄位則回 `400` 並附上 `{"message": "...", "missing": [...]}`；SDK 連線失敗則回 `502`。
