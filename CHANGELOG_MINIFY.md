# Minify Changelog

## Summary
- Removed deprecated legacy key/secret flows. All services, tools, and docs now require only `FUBON_USER_ID`, `FUBON_USER_PASSWORD`, `FUBON_CA_PATH`, `FUBON_CA_PASSWORD`. Added negative-path validation (HTTP 400) and updated ingest tooling, templates, and runbooks.
- Archived non-runtime assets (tests, tools, scripts, logs, docs) into `extras/`.
- Added FastAPI entrypoint (`main.py`) exposing `/healthz` and `/api/v1/session`.
- Normalised naming conflicts across adapters/clients/connector modules with aliases retained.
- Introduced container assets (`Dockerfile`, `.dockerignore`) and Cloud Run deployment guide.
- Added governance artefacts (`requirements.txt`, `pyproject.toml`, `.pre-commit-config.yaml`) reflecting minimal runtime dependencies.

## Size impact (approximate)
| Artifact                            | Before            | After (core)      | Delta        |
| ----------------------------------- | ----------------- | ----------------- | ------------ |
| Working tree (incl. logs/artifacts) | ~1.12 GB          | ~40 MB + extras/  | down ~96%    |
| Runtime source bundle               | >300 MB           | ~40 MB            | down ~86%    |
| Docker image (est.)                 | ~350 MB (legacy)  | ~215 MB (slim)    | down ~135 MB |

*Estimates assume vendor SDK installed at runtime; extras/ retained for rollback.*

## Risks & mitigations
- **Vendor SDK availability:** Container expects SDK wheels supplied at deploy time (e.g. via Artifact Registry or volume). Mitigation: document requirement; session endpoint returns 502 with diagnostics when missing.
- **Credential handling:** Secrets must be mounted or set as env vars; fallback instructions provided.
- **Naming regressions:** CI workflow `naming.yml` enforces S >= 0.98 and lint compliance.

## Rollback
- Restore directories from `extras/` to their original locations.
- Revert merge commit or redeploy previous Cloud Run revision.
- Compatibility aliases (`FubonCredentials`, `FubonAPIClient`, `FubonAPIConnector`, `FubonToVnpyAdapter`) preserve existing imports during transition.

## Acceptance criteria
- API contract preserved (`docs/API??.md` untouched); new HTTP layer is additive.
- Minimal dependency set codified in `requirements.txt`.
- `docker build -t app-min .` followed by `docker run ...` exposes healthy `/healthz`.
- Cloud Run deployment commands succeed using documented secrets.
- Naming score S >= 0.98 enforced via workflow.
