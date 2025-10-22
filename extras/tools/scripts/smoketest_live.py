from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vnpy.event import Event, EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_TICK, EVENT_TRADE

try:
    from vnpy.trader.event import EVENT_FUBON_MARKET_RAW
except ImportError:  # Fallback when vn.py lacks the custom event
    try:
        from vnpy_fubon.vnpy_compat import EVENT_FUBON_MARKET_RAW
    except ImportError:
        EVENT_FUBON_MARKET_RAW = "eFubonMarketRaw"

from vnpy_fubon.config import load_configuration
from vnpy_fubon.fubon_connect import FubonAPIConnector
from vnpy_fubon.gateway import FubonGateway


class EventRecorder:
    def __init__(self, engine: EventEngine, record_dir: Optional[Path]) -> None:
        self.counts: Counter[str] = Counter()
        self._raw_file = None
        self._record_dir = record_dir
        if record_dir:
            record_dir.mkdir(parents=True, exist_ok=True)
            self._raw_file = (record_dir / "ws_raw.jsonl").open("a", encoding="utf-8")
        engine.register(EVENT_TICK, self._on_tick)
        engine.register(EVENT_TRADE, self._on_trade)
        engine.register(EVENT_FUBON_MARKET_RAW, self._on_raw)

    def _on_tick(self, event: Event) -> None:
        self.counts["ticks"] += 1

    def _on_trade(self, event: Event) -> None:
        self.counts["trades"] += 1

    def _on_raw(self, event: Event) -> None:
        self.counts["raw"] += 1
        if self._raw_file:
            json.dump({"timestamp": time.time(), "payload": event.data}, self._raw_file, ensure_ascii=False)
            self._raw_file.write("\n")
            self._raw_file.flush()

    def close(self) -> None:
        if self._raw_file:
            self._raw_file.close()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fubon live smoke test (REST + WS)")
    parser.add_argument("--config", type=Path, default=Path("config") / "fubon_credentials.toml")
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--duration", type=int, default=60, help="Smoke duration in seconds")
    parser.add_argument("--symbols", nargs="*", default=["TXF"])
    parser.add_argument("--after-hours", action="store_true", help="Subscribe after-hours book")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--record-dir", type=Path, default=None, help="Optional directory to persist websocket raw events")
    parser.add_argument("--output", choices=["json", "pretty"], default="json")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    credentials, sdk_config = load_configuration(config_path=args.config, dotenv_path=args.dotenv)
    connector = FubonAPIConnector(credentials=credentials, sdk_config=sdk_config, log_level=getattr(logging, args.log_level.upper(), logging.INFO))

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    recorder = EventRecorder(event_engine, args.record_dir)

    gateway = FubonGateway(event_engine, connector=connector, log_level=getattr(logging, args.log_level.upper(), logging.INFO))

    summary = {
        "status": "ok",
        "contracts": 0,
        "ticks": 0,
        "trades": 0,
        "raw": 0,
        "duration": args.duration,
        "symbols": args.symbols,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    def _signal_handler(_sig, _frame):
        summary["status"] = "cancelled"
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _signal_handler)

    try:
        gateway.connect({"config_path": args.config, "dotenv_path": args.dotenv})
        summary["contracts"] = len(gateway.contracts)
        if args.symbols:
            gateway.subscribe_quotes(args.symbols, after_hours=args.after_hours)
        time.sleep(max(1, args.duration))
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # pragma: no cover - live dependency
        summary["status"] = "error"
        summary["error"] = str(exc)
    finally:
        summary.update(recorder.counts)
        recorder.close()
        try:
            gateway.close()
        finally:
            event_engine.stop()

    summary["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.output == "json":
        print(json.dumps(summary, ensure_ascii=False))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
