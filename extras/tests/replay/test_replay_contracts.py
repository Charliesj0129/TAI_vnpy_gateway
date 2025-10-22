
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.market import MarketAPI
from vnpy_fubon.normalization import normalize_symbol
from vnpy_fubon.vnpy_compat import Product


DATA_DIR = Path(__file__).resolve().parent / "data"


class DummyEventEngine:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


class DummyClient:
    pass


def _load_json(path: str) -> Dict:
    return json.loads((DATA_DIR / path).read_text(encoding="utf-8"))


def test_contract_replay_normalisation() -> None:
    gateway = FubonGateway(DummyEventEngine())
    products_raw = _load_json("intraday_products_regular.json")
    tickers_raw = _load_json("intraday_tickers_regular.json")

    product_metadata: Dict[str, Dict] = {}
    for entry in products_raw["data"]:
        product_metadata[normalize_symbol(entry.get("symbol"))] = entry

    mapped: Dict[str, Tuple] = {}

    for ticker in tickers_raw["data"]:
        result = gateway._map_ticker_to_contract(ticker, product_metadata)
        assert result is not None
        contract, raw_symbol, raw_exchange = result
        mapped[contract.vt_symbol] = result

        assert contract.symbol == normalize_symbol(raw_symbol)
        assert contract.history_data is True
        assert contract.min_volume >= 1
        assert contract.size > 0
        assert contract.pricetick > 0
        assert getattr(contract.exchange, "value", str(contract.exchange)) == "CFE"

        if contract.product is Product.OPTION:
            assert contract.option_underlying == "TXF.CFE"
            assert contract.option_type is not None

        gateway.contracts[contract.vt_symbol] = contract
        gateway._register_contract_aliases(contract, raw_symbol, raw_exchange)

    assert "TXFQ4.CFE" in mapped
    assert "TXO13800L5.CFE" in mapped

    assert gateway.resolve_vt_symbol("txfq4") == "TXFQ4.CFE"
    assert gateway.resolve_vt_symbol("txo13800l5", "taifex") == "TXO13800L5.CFE"
    assert gateway.find_contract("TXO13800L5") is gateway.contracts["TXO13800L5.CFE"]


def test_market_replay_parsing() -> None:
    api = MarketAPI(DummyClient())
    lines = (DATA_DIR / "ws_books_sample.jsonl").read_text(encoding="utf-8").splitlines()
    ticks = []

    for line in lines:
        events = api.parse_market_events(line)
        for event in events:
            if event.tick is not None:
                ticks.append(event.tick)

    assert ticks, "Expected websocket replay to yield TickData entries."
    symbols = {tick.symbol for tick in ticks}
    assert symbols == {"TXFQ4", "TXO13800L5"}
    for tick in ticks:
        assert getattr(tick.exchange, "value", str(tick.exchange)) == "CFE"
