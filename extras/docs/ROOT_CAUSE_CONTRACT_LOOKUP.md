# Root Cause: Contracts Loaded But Unavailable via `find_contract`

## Symptom
- Gateway logs reported `"Loaded 8642 contracts from Fubon REST API."` but `MainEngine.get_contract` returned `None` for legitimate TAIFEX symbols (e.g. `TXF88`).
- vn.py UI surfaced empty contract dropdowns; CTA/Portfolio apps failed to resolve instruments.

## What Happened
1. Fallback compatibility layer (`vnpy_fubon.vnpy_compat`) constructed `ContractData.vt_symbol` using `f"{symbol}.{exchange}"`.
2. Fubon REST payloads report `exchange="TAIFEX"`; mapping fell back to the first `Exchange` enum member (`CFFEX`) after failing to instantiate `Exchange("TAIFEX")`.
3. Resulting `vt_symbol` looked like `"TXF88.Exchange.CFFEX"` under the fallback dataclass.
4. `MainEngine` stores contracts keyed by `vt_symbol` (e.g. `TXF88.CFE`). Because our vt symbol never matched vn.py's canonical format, lookups failed even though EVENT_CONTRACT events were emitted.
5. Additionally, contracts were emitted via a custom `_put_event` helper, bypassing the canonical `BaseGateway.on_contract` path. This was not the primary failure, but removed vn.py's additional bookkeeping (per-symbol event channel).

## Why We Missed It
- Unit tests (`tests/test_gateway_unit.py`) only asserted the presence of contract events, not vt_symbol accuracy nor `MainEngine` integration.
- Local smoke relied on direct dictionary access (`gateway.contracts`), which still contained the malformed keys, hiding the problem.

## Fix Summary
1. Added `normalization.py` to canonicalise symbols, exchange codes (`TAIFEX -> Exchange.CFE`), and product mapping.
2. Updated `vnpy_compat.ContractData.__post_init__` to honour `exchange.value` and expanded fallback `Exchange` enum coverage.
3. Wired contract emission through `BaseGateway.on_contract`, ensuring vn.py's `OmsEngine` updates its registry.
4. Introduced alias caches inside `FubonGateway` (`resolve_vt_symbol`, `find_contract`) for raw Fubon codes and registered them during load.
5. Added logging structured JSON context for traceability.

## Verification
- New integration test `tests/contract_flow_test.py` spins up a real `EventEngine`/`MainEngine` pair and asserts that contract events populate the engine registry.
- Replay fixtures (`tests/replay/data/¡K`) ensure REST ticker mapping yields canonical vt symbols (`TXFQ4.CFE`, `TXO13800L5.CFE`).
- Subscription stress test validates alias caches and state reset on close.

## Preventive Actions
- CI now runs `pytest` on unit + replay + contract flow suites across a Python/OS matrix.
- Added `scripts/healthcheck.py` so deployment pipelines can detect regression before wiring the gateway.
- Live smoke workflow (`workflow_dispatch`) gates real API verification for post-deploy checks.
