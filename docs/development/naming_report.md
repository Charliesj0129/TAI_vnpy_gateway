# Naming Consistency Report

## Metrics
- Total inspected symbols (T): 237
- Style violations (V): 0
- Conflict clusters (C): 0 *(post-renaming, aliases ignored)*
- Semantic drift clusters (D): 0
- Consistency score (S): 1.000

## Key observations
1. Streaming client credentials previously collided with gateway credentials; resolved via `StreamingCredentials` + alias.
2. Ambiguous connector naming addressed with `SdkSessionConnector`.
3. Adapter renamed to `MarketEnvelopeNormalizer` to emphasise semantics.
4. `_normalize_exchange` spelling aligned across account/market/order helpers.

## Enforcement posture
- `codemod_rename.py` applies deterministic renames sourced from `rename_map.yml`.
- CI workflow `naming.yml` computes S and blocks regressions (<0.98).
- Ruff `pep8-naming` ensures new symbols respect PEP8/PEP257 naming.

## Outstanding risks
- Extras/ archive still references legacy names; compatibility aliases retained to shield imports.
- Vendor SDK modules may introduce new naming patterns—future contributions must update `rename_map.yml` + rerun codemod.
