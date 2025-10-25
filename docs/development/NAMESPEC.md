# Naming Specification

## 1. Python code
- Modules/files: `snake_case.py`
- Functions & methods: `snake_case` (leading underscore for private helpers)
- Classes & exceptions: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private attributes: `_single_leading_underscore`

## 2. REST / JSON
- Paths: kebab-case plural (e.g. `/api/v1/session`)
- JSON fields emitted by FastAPI: `snake_case`
- Vendor payloads with camelCase fields must be normalised before reaching vn.py objects.

## 3. Cross-layer mapping
| External field (`docs/API文檔.md`) | Internal (Python/env)         |
| -------------------------------- | ----------------------------- |
| `personal_id`                    | `user_id` / `FUBON_USER_ID`   |
| `password`                       | `user_password`               |
| `cert_path`                      | `ca_path`                     |
| `cert_pass`                      | `ca_password`                 |

## 4. Canonical renames
| Legacy identifier                   | Canonical identifier          | Rationale                                      |
| ----------------------------------- | ----------------------------- | ---------------------------------------------- |
| `FubonCredentials` (clients)        | `StreamingCredentials`        | disambiguate from gateway credential object    |
| `FubonAPIClient`                    | `StreamingDataClient`         | clarifies streaming/WebSocket responsibility   |
| `FubonAPIConnector`                 | `SdkSessionConnector`         | emphasises authentication/session scope        |
| `FubonToVnpyAdapter`                | `MarketEnvelopeNormalizer`    | describes WS payload normalisation             |
| `_normalise_exchange` helpers       | `_normalize_exchange`         | unify American spelling with `normalize_*` API |

Compatibility aliases remain for each rename to avoid breaking imports.

## 5. Enforcement
- Ruff `pep8-naming` enabled via `pyproject.toml`
- GitHub workflow `.github/workflows/naming.yml` rejects S < 0.98 or lint failures
- `codemod_rename.py` + `rename_map.yml` provide reproducible rename transformations
