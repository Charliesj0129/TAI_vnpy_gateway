from __future__ import annotations

import os
import time
from typing import Iterable, Tuple

import pytest

vnpy_engine = pytest.importorskip("vnpy.trader.engine", reason="vn.py runtime not available")
vnpy_event = pytest.importorskip("vnpy.event", reason="vn.py event engine unavailable")

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.normalization import normalize_symbol
from vnpy_fubon.vnpy_compat import ContractData, Exchange, Product  # type: ignore

EventEngine = vnpy_event.EventEngine
MainEngine = vnpy_engine.MainEngine


def _wait_for(predicate: callable, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _sample_contracts() -> Iterable[Tuple[ContractData, str, str]]:
    fut = ContractData(
        gateway_name="Fubon",
        symbol="TXF88",
        exchange=Exchange.CFE,
        name="TXF Regular",
        product=Product.FUTURES,
        size=200,
        pricetick=0.5,
    )
    fut.min_volume = 1

    opt = ContractData(
        gateway_name="Fubon",
        symbol="TXO13800L5",
        exchange=Exchange.CFE,
        name="TXO 13800 Call",
        product=Product.OPTION,
        size=50,
        pricetick=0.1,
    )
    opt.min_volume = 1

    return [
        (fut, "txf88", "TAIFEX"),
        (opt, "txo13800l5", "TAIFEX"),
    ]


def test_contract_events_populate_main_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    original_cwd = os.getcwd()
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    gateway = FubonGateway(event_engine)

    records = list(_sample_contracts())
    monkeypatch.setattr(gateway, "_fetch_contracts_from_rest", lambda: records)

    try:
        gateway._load_and_publish_contracts()

        all_received = _wait_for(
            lambda: all(main_engine.get_contract(contract.vt_symbol) for contract, _, _ in records),
        )
        assert all_received, "MainEngine failed to register emitted contracts."

        vt_symbols = {contract.vt_symbol for contract, _, _ in records}
        assert set(gateway.contracts.keys()) == vt_symbols

        for contract, raw_symbol, raw_exchange in records:
            resolved = gateway.resolve_vt_symbol(raw_symbol)
            assert resolved == contract.vt_symbol
            resolved_with_exchange = gateway.resolve_vt_symbol(raw_symbol, raw_exchange)
            assert resolved_with_exchange == contract.vt_symbol

            normalized_symbol = normalize_symbol(raw_symbol)
            alias_contract = gateway.find_contract(normalized_symbol)
            assert alias_contract is contract

        assert len(main_engine.get_all_contracts()) == len(records)

    finally:
        event_engine.stop()
        os.chdir(original_cwd)
