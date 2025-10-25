# vnpy-fubon

Utilities and scaffolding to integrate the Fubon Securities (ÂØåÈÇ¶Ë≠âÂà∏) Next Generation API with the vn.py trading framework.

## Project Goals

- Provide a reusable connection layer that handles credentials, certificate-based authentication, and logging.
- Offer an executable test harness to exercise individual API endpoints with both positive and negative test cases.
- Document behaviour, quirks, and data semantics of the SDK as they are discovered.

## Getting Started

### 1. Create & Activate Virtual Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
# Optional: developer tooling and linters
pip install -r requirements-dev.txt
```

> **Note**
>
> The proprietary `fubon_neo` SDK is not published on PyPI. Download the latest wheel from your brokerage channel and install it manually, for example:
>
> ```powershell
> pip install C:\Path\To\fubon_neo-2.2.4-cp37-abi3-win_amd64.whl
> ```
>
> The wheel is intentionally not version-controlled inside this repository.

### 3. Configure Credentials Securely

Copy the example template and fill in your real credentials **without** committing the resulting file.

```powershell
Copy-Item config\fubon_credentials.example.toml config\fubon_credentials.toml
```

Or set environment variables (preferred for production use):

| Key | Description |
| --- | --- |
| `FUBON_USER_ID` | Brokerage user ID |
| `FUBON_USER_PASSWORD` | Broker password |
| `FUBON_CA_PATH` | Absolute path to your `.pfx` certificate |
| `FUBON_CA_PASSWORD` | Certificate password |
| `FUBON_PRIMARY_ACCOUNT` | (Optional) default account number used by the gateway |


Or manage them centrally in `vt_setting.json` by adding:

```json
{
  "FUBON.user_id": "YOUR_FUBON_USER_ID",
  "FUBON.password": "YOUR_FUBON_PASSWORD",
  "FUBON.ca_path": "C:/Path/To/Certificate.pfx",
  "FUBON.ca_password": "YOUR_CERT_PASSWORD",
  "FUBON.account_id": "YOUR_PRIMARY_ACCOUNT"
}
```

Values saved in `vt_setting.json` are applied automatically when you call `MainEngine.connect("FUBON")` and can still be overridden by explicit `connect()` parameters or environment variables.

### 4. Smoke-Test Connectivity

```powershell
python -m vnpy_fubon.fubon_connect --log-level INFO
```

### 5. Execute API Test Suite

Tests are designed to run against the live brokerage environment and are disabled by default to avoid accidental order placement.

```powershell
setx FUBON_ENABLE_LIVE_TESTS 1
pytest
```

## Repository Layout

- `vnpy_fubon/` ??Source package with connector, gateway, and helper modules.
- `tests/` ??Pytest suites (live API tests gated by `FUBON_ENABLE_LIVE_TESTS`).
- `docs/` ??Architecture notes, API analysis, and project plans.
- `config/` ??Credential/test-case templates (copy locally before editing).
- `examples/` ??Demo scripts (e.g. `examples/run_fubon_gui.py`, `examples/fubon_service_api.py`).

## Gateway Usage

1. Ensure credentials are configured via environment variables or `config/fubon_credentials.toml`.
2. Run the example event engine to inspect streaming events:

   ```powershell
   $env:FUBON_USER_ID = "..."
   $env:FUBON_USER_PASSWORD = "..."
   $env:FUBON_CA_PATH = "C:\CAFubon\<ID>\<ID>.pfx"
   $env:FUBON_CA_PASSWORD = "..."
   python examples/run_fubon_gui.py
   ```

   The launcher boots the vn.py GUI with the Fubon gateway registered and installs the CTA apps used for backtesting and data inspection.

   To spin up the FastAPI utility service, run:

   ```powershell
   uvicorn examples.fubon_service_api:app --reload
   ```

3. To switch accounts, set `FUBON_PRIMARY_ACCOUNT` (?ñÊñº `connect()` settings ?ê‰? `account_id`) Ôºå‰??ØÂú®Á®ãÂ?‰∏≠Âëº??`gateway.switch_account("Â∏≥Ë??üÁ¢º")`??
   The gateway now applies available SDK setters and warns via `EVENT_LOG` if validation shows the session is still pointing at the previous account.
## Troubleshooting

- **WebSocket protocol error** ??the gateway auto-retries; persistent failures usually indicate VPN / firewall issues.
- **Login failure** ??verify `.env`/TOML credentials and certificate path (`FUBON_CA_PATH`).
- **Subscription rejected** °V indicates an invalid symbol/channel; adjust parameters because the gateway no longer retries automatically.
- **Token refresh interval** ??defaults to 15 minutes; override with `FUBON_TOKEN_REFRESH_INTERVAL` if broker Ë¶ÅÊ?‰∏çÂ???- Additional notes and roadmap: `docs/PROJECT_OVERVIEW.md`, `docs/GATEWAY_REDESIGN_PLAN.md`.

## Documentation Workflow

1. Read the vendor SDK/API documentation and experiment via the test harness.
2. Capture raw notes or copied tables in `docs/API?áÊ?.md`.
3. Summarise key learnings in `docs/API_Analysis.md` (behaviour, rate limits, payload quirks, etc.).

## Contributing

Pull requests are welcome. Please ensure:

- Type hints and docstrings explain the public API surface.
- Logging messages highlight request parameters (sanitised) and raw responses.
- Tests are updated or added for new API capabilities.
