from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple, TYPE_CHECKING

import pytest

from vnpy_fubon import create_authenticated_client
from vnpy_fubon.config import load_configuration

TEST_LOGGER = logging.getLogger("tests.runtime")

if TYPE_CHECKING:
    from .test_api_functions import ApiTestCase, StreamingExpectation


@dataclass
class StreamingSession:
    expectation: "StreamingExpectation"
    artifact_path: Path
    queue: "queue.Queue[Any]"
    stop_event: threading.Event
    writer_thread: threading.Thread
    registered_via: Optional[str]
    detach_callback: Optional[Callable[[], None]]
    counter: List[int]


class StreamingRecorder:
    """
    Helper that captures callback-based streaming data and serialises it to artifacts.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.logger = logging.getLogger("tests.streaming")

    @contextlib.contextmanager
    def capture(self, client: Any, case: "ApiTestCase") -> Iterator[List[StreamingSession]]:
        expectations = getattr(case, "streaming_expectations", ())
        if not expectations:
            yield []
            return

        sessions: List[StreamingSession] = []
        for expectation in expectations:
            session = self._start_session(client, expectation, case)
            if session:
                sessions.append(session)

        try:
            yield sessions
            for session in sessions:
                self._await_events(session, case)
        finally:
            for session in sessions:
                self._stop_session(session)

    def _start_session(
        self,
        client: Any,
        expectation: "StreamingExpectation",
        case: "ApiTestCase",
    ) -> Optional[StreamingSession]:
        artifact_path = self._resolve_artifact_path(expectation.artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        event_queue: "queue.Queue[Any]" = queue.Queue()
        stop_event = threading.Event()
        counter: List[int] = [0]

        def handler(*args: Any, **kwargs: Any) -> None:
            record = {
                "received_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "args": args,
                "kwargs": kwargs,
            }
            event_queue.put(record)
            counter[0] += 1
            if counter[0] >= expectation.max_events:
                stop_event.set()

        registered_via, detach_callback = self._register_handler(
            client,
            expectation,
            handler,
        )

        if not registered_via:
            self.logger.warning(
                "Skipping streaming capture for %s (%s); no registration method matched.",
                case.name,
                case.category,
            )
            return None

        writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(artifact_path, event_queue, stop_event),
            name=f"stream-writer-{case.name}",
            daemon=True,
        )
        writer_thread.start()

        return StreamingSession(
            expectation=expectation,
            artifact_path=artifact_path,
            queue=event_queue,
            stop_event=stop_event,
            writer_thread=writer_thread,
            registered_via=registered_via,
            detach_callback=detach_callback,
            counter=counter,
        )

    def _register_handler(
        self,
        client: Any,
        expectation: "StreamingExpectation",
        handler: Callable[..., None],
    ) -> Tuple[Optional[str], Optional[Callable[[], None]]]:
        kwargs = dict(expectation.register_kwargs)

        for method_name in expectation.register_method_candidates:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                if expectation.handler_keyword:
                    payload = dict(kwargs)
                    payload[expectation.handler_keyword] = handler
                    method(**payload)
                else:
                    method(handler, **kwargs)
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - SDK dependent
                self.logger.warning(
                    "Registering streaming handler via method '%s' failed: %s",
                    method_name,
                    exc,
                )
                continue
            return f"method:{method_name}", None

        for attr_name in expectation.attribute_candidates:
            if not hasattr(client, attr_name):
                continue
            previous = getattr(client, attr_name)
            try:
                setattr(client, attr_name, handler)
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "Assigning streaming handler to attribute '%s' failed: %s",
                    attr_name,
                    exc,
                )
                continue

            def restore() -> None:
                with contextlib.suppress(Exception):
                    setattr(client, attr_name, previous)

            return f"attribute:{attr_name}", restore

        return None, None

    def _writer_loop(
        self,
        artifact_path: Path,
        event_queue: "queue.Queue[Any]",
        stop_event: threading.Event,
    ) -> None:
        with artifact_path.open("w", encoding="utf-8") as handle:
            first_line = True
            while not stop_event.is_set() or not event_queue.empty():
                try:
                    event = event_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                line = json.dumps(event, ensure_ascii=False, default=str)
                if not first_line:
                    handle.write("\n")
                handle.write(line)
                handle.flush()
                first_line = False

    def _await_events(self, session: StreamingSession, case: "ApiTestCase") -> None:
        deadline = time.monotonic() + session.expectation.timeout_seconds
        while time.monotonic() < deadline:
            if session.counter[0] >= session.expectation.max_events:
                break
            time.sleep(0.1)
        else:
            self.logger.warning(
                "Streaming expectation for %s (%s) timed out after %.1fs; "
                "received %d/%d events.",
                case.name,
                case.category,
                session.expectation.timeout_seconds,
                session.counter[0],
                session.expectation.max_events,
            )
        session.stop_event.set()

    def _stop_session(self, session: StreamingSession) -> None:
        session.stop_event.set()
        session.writer_thread.join(timeout=1.0)
        if session.detach_callback:
            session.detach_callback()

    def _resolve_artifact_path(self, artifact: str) -> Path:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = self.base_dir / artifact_path
        return artifact_path


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--enable-live-tests",
        action="store_true",
        default=False,
        help="Run live API tests regardless of the FUBON_ENABLE_LIVE_TESTS env variable.",
    )


@pytest.fixture(scope="session")
def live_test_enabled(pytestconfig: pytest.Config) -> bool:
    if pytestconfig.getoption("--enable-live-tests"):
        return True
    return os.getenv("FUBON_ENABLE_LIVE_TESTS", "0") == "1"


@pytest.fixture(scope="session")
def config_path() -> Path:
    return Path("config") / "fubon_credentials.toml"


@pytest.fixture(scope="session")
def dotenv_path() -> Path:
    return Path(".env")


def _initialise_market_streaming_if_supported(client: Any) -> None:
    """
    Ensure market data connectivity is established before running streaming tests.
    """

    exchange_token = getattr(client, "exchange_realtime_token", None)
    if callable(exchange_token):
        try:
            exchange_token()
            TEST_LOGGER.info("Invoked client.exchange_realtime_token() successfully.")
        except Exception as exc:  # pragma: no cover - vendor behaviour
            TEST_LOGGER.warning("client.exchange_realtime_token() raised %s", exc)

    init_realtime = getattr(client, "init_realtime", None)
    if callable(init_realtime):
        try:
            init_realtime()
            TEST_LOGGER.info("Invoked client.init_realtime() successfully.")
        except Exception as exc:  # pragma: no cover - depends on vendor behaviour
            TEST_LOGGER.warning("client.init_realtime() raised %s", exc)


@pytest.fixture(scope="session")
def fubon_connection(
    live_test_enabled: bool,
    config_path: Path,
    dotenv_path: Path,
) -> Iterator[Tuple[object, object]]:
    if not live_test_enabled:
        pytest.skip(
            "Live API tests disabled. Set FUBON_ENABLE_LIVE_TESTS=1 or pass --enable-live-tests."
        )

    if not config_path.exists() and not dotenv_path.exists():
        pytest.skip(
            "No credential configuration found. Create config/fubon_credentials.toml "
            "or set environment variables."
        )

    try:
        client, response = create_authenticated_client(
            config_path=config_path if config_path.exists() else None,
            dotenv_path=dotenv_path if dotenv_path.exists() else None,
        )
    except ValueError as exc:
        pytest.skip(f"Unable to instantiate Fubon SDK client: {exc}")

    _initialise_market_streaming_if_supported(client)

    yield client, response


@pytest.fixture(scope="session")
def ensure_market_ready(fubon_client: Any) -> Callable[[], None]:
    """
    Provide tests with a callable to re-establish market connectivity after relogin.
    """

    def _ensure() -> None:
        _initialise_market_streaming_if_supported(fubon_client)

    return _ensure


@pytest.fixture(scope="session")
def fubon_client(fubon_connection: Tuple[object, object]) -> object:
    client, _ = fubon_connection
    return client


@pytest.fixture(scope="session")
def login_response(fubon_connection: Tuple[object, object]) -> object:
    _, response = fubon_connection
    return response


@pytest.fixture(scope="session")
def streaming_recorder(artifact_dir: Path) -> StreamingRecorder:
    return StreamingRecorder(artifact_dir)


@pytest.fixture(scope="session")
def fubon_credentials(config_path: Path, dotenv_path: Path):
    credentials, _ = load_configuration(
        config_path=config_path if config_path.exists() else None,
        dotenv_path=dotenv_path if dotenv_path.exists() else None,
    )
    return credentials


@pytest.fixture(scope="session")
def api_runtime_context(
    fubon_client: Any,
    login_response: object,
    fubon_credentials,
) -> Mapping[str, Any]:
    context: Dict[str, Any] = {
        "user_id": fubon_credentials.user_id,
        "user_password": fubon_credentials.user_password,
        "ca_path": str(fubon_credentials.ca_path),
        "ca_password": fubon_credentials.ca_password,
    }

    accounts: List[Any] = []
    response_data = getattr(login_response, "data", None)
    if isinstance(response_data, (list, tuple)):
        accounts = list(response_data)

    context["accounts"] = accounts

    if accounts:
        context["primary_account"] = accounts[0]
        context["primary_account_number"] = getattr(accounts[0], "account", None)
    else:
        context["primary_account"] = None
        context["primary_account_number"] = None

    context["secondary_account"] = accounts[1] if len(accounts) > 1 else None
    context["futopt"] = getattr(fubon_client, "futopt", None)
    context["futopt_accounting"] = getattr(fubon_client, "futopt_accounting", None)
    context["stock"] = getattr(fubon_client, "stock", None)

    today = datetime.now(UTC)
    context["order_history_end"] = today.strftime("%Y%m%d")
    context["order_history_start"] = (today - timedelta(days=7)).strftime("%Y%m%d")

    return context


@pytest.fixture(scope="session")
def artifact_dir() -> Path:
    path = Path("artifacts") / "api_tests"
    path.mkdir(parents=True, exist_ok=True)
    return path
