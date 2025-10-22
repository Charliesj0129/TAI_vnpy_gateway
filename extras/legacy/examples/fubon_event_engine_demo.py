"""Simple example showing FubonGateway working with a minimal EventEngine.

This demo prints tick/order/trade events to stdout. It requires real
credentials and network connectivity to the Fubon Next Generation API.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from queue import Empty, Queue
from threading import Thread

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.vnpy_compat import Event, EVENT_ORDER, EVENT_TICK, EVENT_TRADE, Exchange, SubscribeRequest


class SimpleEventEngine:
    """Very small event engine that prints incoming events."""

    def __init__(self) -> None:
        self._queue: "Queue[Event]" = Queue()
        self._active = True
        self._thread = Thread(target=self._run, name="simple-event-engine", daemon=True)
        self._thread.start()

    def put(self, event: Event) -> None:
        self._queue.put(event)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self.put(Event("STOP"))
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while self._active:
            try:
                event = self._queue.get(timeout=1.0)
            except Empty:
                continue
            if event.type == "STOP":
                break
            print(f"[{event.type}] {event.data}")
        print("Event engine stopped.")


def main() -> int:
    event_engine = SimpleEventEngine()

    gateway = FubonGateway(event_engine)
    try:
        gateway.connect()

        account_id = os.getenv("FUBON_PRIMARY_ACCOUNT")
        if account_id:
            gateway.switch_account(account_id)

        gateway.subscribe(SubscribeRequest(symbol="TXFA4", exchange=Exchange.UNKNOWN))
        print("Subscribed to TXFA4 order book. Gathering events for 30 seconds...")
        time.sleep(30)
    except KeyboardInterrupt:
        print("Interrupted by user.")
    except Exception as exc:
        print(f"Gateway error: {exc}", file=sys.stderr)
        return 1
    finally:
        gateway.close()
        event_engine.stop()

    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    raise SystemExit(main())
