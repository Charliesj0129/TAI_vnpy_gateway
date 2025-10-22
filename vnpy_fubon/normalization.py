"""
Utilities for normalising Fubon contract metadata to vn.py canonical values.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableSequence, Sequence

from .vnpy_compat import Exchange, Product

# Canonical exchange aliases. Keys are upper-cased input variants.
EXCHANGE_ALIASES: Mapping[str, str] = {
    "": "",
    "TAIFEX": "CFE",
    "TAIFEXREGULAR": "CFE",
    "TAIFEXAFTERHOURS": "CFE",
    "TAIFEX-AFTERHOURS": "CFE",
    "TAIFEX_AH": "CFE",
    "TAIFEX-AH": "CFE",
    "TAIFEXAH": "CFE",
    "TAIFEXR": "CFE",
    "CFE": "CFE",
    "HKFE": "HKFE",
    "SGX": "SGX",
    "TWSE": "TSE",
    "TW": "TSE",
    "TSE": "TSE",
    "TPEX": "OTC",
    "OTC": "OTC",
    "OTCX": "OTC",
    "GLOBEX": "GLOBEX",
    "CME": "CME",
}

# Product aliases between vendor payload and vn.py enumerations.
PRODUCT_ALIASES: Mapping[str, Product] = {
    "FUT": Product.FUTURES,
    "FUTURE": Product.FUTURES,
    "FUTURES": Product.FUTURES,
    "OPT": Product.OPTION,
    "OPTION": Product.OPTION,
    "OPTIONS": Product.OPTION,
}


def _iter_candidates(value: Any) -> Iterable[str]:
    """
    Produce a sequence of canonicalised strings that might represent an exchange code.
    """

    if value is None:
        return ()

    if isinstance(value, Exchange):
        return (getattr(value, "value", str(value)).upper(),)

    text = str(value).strip()
    if not text:
        return ()

    upper = text.upper()
    if upper:
        yield upper

    collapsed = "".join(ch for ch in upper if ch.isalnum())
    if collapsed and collapsed != upper:
        yield collapsed


def _resolve_exchange_code(codes: Sequence[str]) -> Exchange | None:
    """
    Attempt to resolve the first matching exchange enum from provided codes.
    """

    for code in codes:
        alias = EXCHANGE_ALIASES.get(code, code)
        candidates: MutableSequence[str] = [alias]
        if alias:
            collapsed = "".join(ch for ch in alias if ch.isalnum())
            if collapsed and collapsed != alias:
                candidates.append(collapsed)

        for candidate in candidates:
            if not candidate:
                continue
            try:
                return Exchange(candidate)  # type: ignore[arg-type]
            except Exception:
                continue
    return None


def normalize_exchange(raw: Any, *, default: Any | None = None) -> Exchange:
    """
    Map vendor-specific exchange identifiers to vn.py's Exchange enum.
    """

    codes: list[str] = []
    codes.extend(_iter_candidates(raw))
    if default is not None:
        codes.extend(_iter_candidates(default))

    exchange = _resolve_exchange_code(codes)
    if exchange is not None:
        return exchange

    fallback = getattr(Exchange, "LOCAL", None)
    if fallback is not None:
        return fallback

    all_exchanges = list(Exchange)
    if not all_exchanges:
        raise RuntimeError("Exchange enumeration has no members.")
    return all_exchanges[0]


def normalize_symbol(raw: Any) -> str:
    """
    Canonicalise contract symbols by stripping whitespace and upper-casing.
    """

    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    return text.replace(" ", "").upper()


def normalize_product(raw: Any, *, default: Product | None = None) -> Product:
    """
    Translate vendor product descriptors into vn.py Product enumeration.
    """

    if isinstance(raw, Product):
        return raw

    text = str(raw or "").strip().upper()
    if not text:
        return default or Product.FUTURES

    if text in PRODUCT_ALIASES:
        return PRODUCT_ALIASES[text]

    for key, product in PRODUCT_ALIASES.items():
        if key in text:
            return product

    return default or Product.FUTURES


def vt_symbol_from_parts(symbol: str, exchange: Exchange) -> str:
    """
    Helper that mirrors vn.py's vt_symbol composition for tests and helpers.
    """

    exchange_code = getattr(exchange, "value", str(exchange))
    return f"{symbol}.{exchange_code}"
