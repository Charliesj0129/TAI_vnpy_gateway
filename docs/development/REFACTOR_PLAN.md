## Refactor Plan

1. **Archive non-runtime assets**
   - Move tests, tools, scripts, storage, logs, and ancillary docs into `extras/`.
   - Remove caches (`__pycache__`, `.pytest_cache`) and local virtualenvs to shrink footprint.

2. **Introduce HTTP entrypoint & configuration**
   - Add `examples/fubon_service_api.py` FastAPI service exposing `/healthz` and `/api/v1/session`.
   - Reuse `SdkSessionConnector` and `load_configuration` for credential management.

3. **Resolve naming inconsistencies**
   - Rename streaming credentials/client, connector, and adapter using `codemod_rename.py`.
   - Align `_normalize_exchange` helper spelling across modules.
   - Keep aliases (`FubonCredentials`, `FubonAPIClient`, `FubonAPIConnector`, `FubonToVnpyAdapter`) for compatibility.

4. **Packaging & deployment**
   - Replace dependency metadata with minimal `requirements.txt` + updated `pyproject.toml`.
   - Add Dockerfile, `.dockerignore`, and deployment/smoke documentation.
   - Introduce `.pre-commit-config.yaml` and GitHub workflow `naming.yml`.

5. **Validation**
   - Run lint/format tooling (ruff, black, isort) via pre-commit or CI.
   - Execute container smokes (`docker build` / `docker run` / `curl /healthz`).
   - Confirm naming score >= 0.98 using workflow script.

## Rollback
- Restore directories from `extras/` to root if legacy footprint required.
- Revert rename aliases if downstream tooling depends on old class names.
- Re-deploy prior Cloud Run revision (`gcloud run services update-traffic ...`).
