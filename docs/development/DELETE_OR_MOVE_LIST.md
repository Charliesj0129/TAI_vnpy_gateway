| Path / Pattern                     | Action              | Notes |
| ---------------------------------- | ------------------- | ----- |
| tests/                             | Moved to `extras/tests` | Regression coverage retained outside runtime bundle |
| tools/                             | Moved to `extras/tools` | Operational utilities (PySide6, psycopg) |
| scripts/                           | Moved to `extras/tools/scripts` | Legacy CLI smoke scripts |
| storage/                           | Moved to `extras/storage` | PostgreSQL writers not required for deployment |
| examples/                          | Moved to `extras/legacy/examples` | Demo assets |
| artifacts/                         | Moved to `extras/legacy/artifacts` | Historical outputs |
| run.py                             | Moved to `extras/legacy/run.py` | GUI bootstrap kept for rollback |
| log/, logs/                        | Moved to `extras/logs` | Large telemetry archives |
| docs/* (non-contract)              | Moved to `extras/docs` | Backlog, runbooks, schema, ops notes |
| config/api_test_cases.toml         | Moved to `extras/config` | Test-only configuration |
| config/pipeline.toml               | Moved to `extras/config` | Non-runtime metadata |
| config/symbols.toml                | Moved to `extras/config` | Reference dataset |
| HEALTHCHECKS.md, OBSERVABILITY.md, OPERATIONS.md | Moved to `extras/docs` | Ops collateral superseded by deploy docs |
| requirements-dev.txt               | Moved to `extras/legacy` | Dev dependencies now in `pyproject.toml` |
| temp_script.py                     | Deleted             | Ad-hoc script |
| .venv/, __pycache__/, .pytest_cache/ | Deleted           | Local/build artefacts |
