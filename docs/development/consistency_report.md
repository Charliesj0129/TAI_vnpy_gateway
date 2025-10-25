# Consistency Report

## Credential Contract
- `clients/fubon_api_client.py`: `StreamingCredentials` now only exposes `user_id`, `user_password`, `ca_path`, `ca_password`. `FubonRESTClient` posts the same four fields and refresh requests reuse the certificate identity.
- `extras/tools/fubon_subscribe.py`: environment bootstrap validates the four `FUBON_*` variables and aborts with a concrete missing-field list when any are absent.
- `main.py`: the `POST /api/v1/session` endpoint requires the four snake_case fields in its JSON body and returns HTTP 400 with a `missing` array when one or more values are absent.

## Documentation Alignment
- `docs/API文檔.md` login parameter table now mirrors the four canonical names (`user_id`, `user_password`, `ca_path`, `ca_password`).
- `.env.template` and `README.md` examples only mention the four environment variables.
- Operations runbook (`extras/docs/runbook.md`) and smoke guide (`smoke_tests.md`) reference the new contract and negative cases.

## OpenAPI / FastAPI Surface
- No auto-generated OpenAPI bundle is present; FastAPI models enforce the new schema and reject unknown credential keys.

## Decisions & Follow-ups
- Removed all legacy key/secret credential paths instead of silently mapping them to the certificate-based flow.
- Streaming client bootstrap continues to rely on the REST endpoint but now submits the four certificate fields as payload, aligning behaviour with the enforced contract.
- No additional discrepancies detected; forbidden token scan returned zero matches for `api[_-]?key`/`api[_-]?secret` variants.
