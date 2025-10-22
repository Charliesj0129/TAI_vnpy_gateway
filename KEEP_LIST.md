| Path                          | Reason |
| ----------------------------- | ------ |
| main.py                       | FastAPI entrypoint (`/healthz`, `/api/v1/session`) |
| adapters/fubon_to_vnpy.py     | Core market data normalisation logic |
| clients/fubon_api_client.py   | Streaming REST/WS client helper |
| config/fubon_credentials.toml | Minimal credential template |
| docs/API文檔.md                | Canonical external API contract |
| docs/API_Analysis.md          | Contract analysis reference |
| docs/PROJECT_OVERVIEW.md      | Architecture overview |
| fubon_connect.py              | Backwards compatibility wrapper |
| vnpy_fubon/                   | Core gateway package |
| requirements.txt              | Minimal runtime dependencies |
| Dockerfile                    | Cloud Run-ready container recipe |
| .dockerignore                 | Keeps docker context lean |
| deploy_instructions.md        | Cloud Run deployment guide |
| smoke_tests.md                | Standardised smoke checks |
| CHANGELOG_MINIFY.md           | Minification change log |
| NAMESPEC.md                   | Naming conventions and mapping |
| naming_report.md              | Consistency metrics |
| rename_map.yml                | Rename dictionary |
| codemod_rename.py             | LibCST-based rename tool |
| pyproject.toml                | Tooling configuration |
| .pre-commit-config.yaml       | Local lint/format guardrails |
| spectral.yaml                 | OpenAPI naming rules |
| .github/workflows/naming.yml  | CI enforcement of lint/naming |
