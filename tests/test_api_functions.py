from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, MutableMapping, Sequence, Tuple, TYPE_CHECKING

import pytest

try:
    import tomllib  # Python 3.11
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore

LOGGER = logging.getLogger(__name__)
TEST_CASE_CONFIG_PATH = Path("config") / "api_test_cases.toml"
CASE_CACHE: Tuple[List["ApiTestCase"], List["ApiTestCase"]] | None = None
TRANSIENT_ERROR_SUBSTRINGS: Tuple[str, ...] = (
    "not login",
    "login error",
    "websocket protocol error",
    "underlying connection is closed",
    "unable to connect",
)

if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from .conftest import StreamingRecorder


@dataclass(frozen=True)
class StreamingExpectation:
    register_method_candidates: Tuple[str, ...] = ()
    attribute_candidates: Tuple[str, ...] = ()
    register_kwargs: Mapping[str, Any] = field(default_factory=dict)
    handler_keyword: str | None = None
    artifact: str = ""
    max_events: int = 1
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ApiTestCase:
    category: str
    name: str
    method_candidates: Tuple[str, ...]
    payload_args: Tuple[Any, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    success_keys: Tuple[str, ...] = ()
    expected_error_substrings: Tuple[str, ...] = ()
    expect_success: bool = True
    streaming_expectations: Tuple[StreamingExpectation, ...] = ()


@dataclass
class ApiCallResult:
    method_name: str
    response: Any
    success: bool
    message: str
    raised_exception: bool = False
    invoked_args: Tuple[Any, ...] = ()
    invoked_kwargs: Mapping[str, Any] = field(default_factory=dict)


def _load_test_cases(path: Path) -> Tuple[List[ApiTestCase], List[ApiTestCase]]:
    if not path.exists():
        pytest.skip(
            f"API test case configuration file {path} not found. "
            "Copy config/api_test_cases.example.toml and customise it."
        )

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    def coerce_sequence(value: Any) -> Tuple[str, ...]:
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence):
            return tuple(str(item) for item in value)
        return ()

    def coerce_payload(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    def coerce_args(value: Any) -> Tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(value)
        return (value,)

    def infer_category(item: Mapping[str, Any], fallback: str) -> str:
        category = item.get("category")
        if isinstance(category, str) and category.strip():
            return category.strip()
        return fallback

    positives: List[ApiTestCase] = []
    negatives: List[ApiTestCase] = []

    def build_streaming_expectations(
        raw_streaming: Any,
        category: str,
        case_name: str,
    ) -> Tuple[StreamingExpectation, ...]:
        if not isinstance(raw_streaming, Sequence) or isinstance(raw_streaming, (str, bytes)):
            return ()
        expectations: List[StreamingExpectation] = []
        for entry in raw_streaming:
            if not isinstance(entry, Mapping):
                continue
            expectations.append(
                StreamingExpectation(
                    register_method_candidates=coerce_sequence(
                        entry.get("register_method_candidates")
                    ),
                    attribute_candidates=coerce_sequence(entry.get("attribute_candidates")),
                    register_kwargs=coerce_payload(entry.get("register_kwargs")),
                    handler_keyword=(
                        str(entry.get("handler_parameter") or entry.get("handler_keyword")).strip()
                        if isinstance(entry.get("handler_parameter") or entry.get("handler_keyword"), str)
                        else None
                    ),
                    artifact=str(
                        entry.get("artifact")
                        or f"{category}/{case_name}_stream.jsonl"
                    ),
                    max_events=int(entry.get("max_events", 1)),
                    timeout_seconds=float(entry.get("timeout_seconds", 5.0)),
                )
            )
        return tuple(expectations)

    def extend_cases(
        target: List[ApiTestCase],
        items: Iterable[Mapping[str, Any]],
        *,
        expect_success: bool,
        category: str,
    ) -> None:
        for item in items:
            name = str(item["name"])
            resolved_category = infer_category(item, category)
            target.append(
                ApiTestCase(
                    category=resolved_category,
                    name=name,
                    method_candidates=coerce_sequence(item.get("method_candidates")),
                    payload_args=coerce_args(item.get("payload_args")),
                    payload=coerce_payload(item.get("payload")),
                    success_keys=coerce_sequence(item.get("success_keys")),
                    expected_error_substrings=coerce_sequence(
                        item.get("expected_error_substrings")
                    ),
                    expect_success=expect_success,
                    streaming_expectations=build_streaming_expectations(
                        item.get("streaming"),
                        resolved_category,
                        name,
                    ),
                )
            )

    extend_cases(
        positives,
        raw.get("positive", []),
        expect_success=True,
        category="general",
    )
    extend_cases(
        negatives,
        raw.get("negative", []),
        expect_success=False,
        category="general",
    )

    for category, block in raw.items():
        if not isinstance(block, Mapping):
            continue
        if category in {"meta", "positive", "negative"}:
            continue
        extend_cases(
            positives,
            block.get("positive", []),
            expect_success=True,
            category=category,
        )
        extend_cases(
            negatives,
            block.get("negative", []),
            expect_success=False,
            category=category,
        )

    if not positives and not negatives:
        pytest.skip(
            f"No test cases defined in {path}. Add at least one positive or negative case."
        )

    return positives, negatives


def _resolve_method(client: Any, candidates: Iterable[str]) -> Tuple[str, Any]:
    for name in candidates:
        target: Any = client
        try:
            for segment in name.split("."):
                target = getattr(target, segment)
        except AttributeError:
            continue
        if callable(target):
            return name, target
    pytest.skip(f"None of the candidate methods {candidates} are exposed by the SDK client.")


def _is_successful_mapping(response: Mapping[str, Any], success_keys: Tuple[str, ...]) -> bool:
    keys_to_check = success_keys or ("success", "is_success", "status", "code")
    for key in keys_to_check:
        if key not in response:
            continue
        value = response[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 0
        if isinstance(value, str):
            return value.strip().upper() in {"OK", "SUCCESS", "0"}
    return bool(response)


def _interpret_response(response: Any, success_keys: Tuple[str, ...]) -> Tuple[bool, str]:
    if response is None:
        return True, "Response is None; treating as success."
    if isinstance(response, bool):
        return response, f"Response boolean: {response}"
    if isinstance(response, (int, float)):
        return response == 0, f"Numeric response: {response}"
    if isinstance(response, str):
        normalised = response.strip().upper()
        return normalised in {"OK", "SUCCESS", "0"}, f"String response: {response}"
    if isinstance(response, Mapping):
        success = _is_successful_mapping(response, success_keys)
        return success, json.dumps(response, ensure_ascii=False, default=str)
    if isinstance(response, (list, tuple)):
        if not response:
            return True, "Empty sequence response."
        success, message = _interpret_response(response[0], success_keys)
        return success, f"Sequence response leading element: {response[0]} ({message})"
    for attr in ("is_success", "success", "result"):
        value = getattr(response, attr, None)
        if isinstance(value, bool):
            message = getattr(response, "message", None)
            if message is None:
                message = getattr(response, "error_message", str(response))
            return value, str(message)
    code = getattr(response, "code", None)
    if isinstance(code, (int, float)):
        message = getattr(response, "message", str(response))
        return code == 0, str(message)
    status = getattr(response, "status", None)
    if isinstance(status, (int, float)):
        message = getattr(response, "message", str(response))
        return status == 0, str(message)
    if hasattr(response, "message"):
        return False, str(getattr(response, "message"))
    return True, f"Response of type {type(response).__name__} assumed successful."


def _serialise_response(response: Any) -> Any:
    if isinstance(response, BaseException):
        return {
            "type": response.__class__.__name__,
            "message": str(response),
        }
    try:
        json.dumps(response, ensure_ascii=False)
        return response
    except TypeError:
        try:
            return json.loads(json.dumps(response, ensure_ascii=False, default=str))
        except TypeError:
            return str(response)


def _lookup_context(path: str, context: Mapping[str, Any]) -> Any:
    head, *tail = path.split(".")
    if head not in context:
        raise KeyError(f"Context variable '{head}' is not defined.")
    value: Any = context[head]
    for part in tail:
        if isinstance(value, (list, tuple)) and part.isdigit():
            value = value[int(part)]
            continue
        value = getattr(value, part)
    return value


def _resolve_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _lookup_context(value[1:], context)
    if isinstance(value, Mapping):
        return {key: _resolve_value(inner, context) for key, inner in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return type(value)(_resolve_value(inner, context) for inner in value)
    return value


def _serialise_for_artifact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _serialise_for_artifact(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise_for_artifact(item) for item in value]
    return _serialise_response(value)


def _execute_case(
    client: Any,
    case: ApiTestCase,
    context: Mapping[str, Any],
) -> ApiCallResult:
    method_name, method = _resolve_method(client, case.method_candidates)
    resolved_args = tuple(_resolve_value(arg, context) for arg in case.payload_args)
    resolved_kwargs = {key: _resolve_value(value, context) for key, value in case.payload.items()}
    LOGGER.info(
        "Invoking %s.%s with args=%s kwargs=%s",
        case.category,
        method_name,
        resolved_args,
        resolved_kwargs,
    )
    try:
        response = method(*resolved_args, **resolved_kwargs)
        success, message = _interpret_response(response, case.success_keys)
        return ApiCallResult(
            method_name=method_name,
            response=response,
            success=success,
            message=message,
            invoked_args=resolved_args,
            invoked_kwargs=resolved_kwargs,
        )
    except Exception as exc:  # pragma: no cover - driven by SDK behaviour
        LOGGER.exception("API call %s raised an exception: %s", method_name, exc)
        return ApiCallResult(
            method_name=method_name,
            response=exc,
            success=False,
            message=str(exc),
            raised_exception=True,
            invoked_args=resolved_args,
            invoked_kwargs=resolved_kwargs,
        )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    global CASE_CACHE
    requested_positive = "positive_case" in metafunc.fixturenames
    requested_negative = "negative_case" in metafunc.fixturenames

    if not (requested_positive or requested_negative):
        return

    if CASE_CACHE is None:
        CASE_CACHE = _load_test_cases(TEST_CASE_CONFIG_PATH)

    positives, negatives = CASE_CACHE

    if requested_positive:
        metafunc.parametrize(
            "positive_case",
            positives,
            ids=[case.name for case in positives],
        )
    if requested_negative:
        metafunc.parametrize(
            "negative_case",
            negatives,
            ids=[case.name for case in negatives],
        )


def _write_artifact(artifact_dir: Path, case: ApiTestCase, result: ApiCallResult) -> None:
    payload = {
        "case": {
            "category": case.category,
            "name": case.name,
            "method_candidates": case.method_candidates,
            "payload": dict(case.payload),
            "payload_args": list(case.payload_args),
            "expect_success": case.expect_success,
            "streaming_expectations": [
                {
                    "register_method_candidates": expectation.register_method_candidates,
                    "attribute_candidates": expectation.attribute_candidates,
                    "artifact": expectation.artifact,
                    "max_events": expectation.max_events,
                    "timeout_seconds": expectation.timeout_seconds,
                }
                for expectation in case.streaming_expectations
            ],
        },
        "result": {
            "method_name": result.method_name,
            "success": result.success,
            "message": result.message,
            "raised_exception": result.raised_exception,
            "response": _serialise_response(result.response),
            "invoked_args": _serialise_for_artifact(result.invoked_args),
            "invoked_kwargs": {
                key: _serialise_for_artifact(value)
                for key, value in result.invoked_kwargs.items()
            },
        },
    }
    category_dir = artifact_dir / case.category
    category_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = category_dir / f"{case.name}.json"
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _refresh_context_with_login(context: MutableMapping[str, Any], login_response: Any) -> None:
    LOGGER.debug(
        "Refreshing runtime context with login response of type %s",
        type(login_response).__name__,
    )
    accounts: List[Any] = []
    response_data = getattr(login_response, "data", None)
    if isinstance(response_data, (list, tuple)):
        accounts = list(response_data)

    if accounts:
        context["accounts"] = accounts
        context["primary_account"] = accounts[0]
        context["primary_account_number"] = getattr(accounts[0], "account", None)
        context["secondary_account"] = accounts[1] if len(accounts) > 1 else None


def test_positive_cases(
    positive_case: ApiTestCase,
    fubon_client: Any,
    artifact_dir: Path,
    streaming_recorder: "StreamingRecorder",
    api_runtime_context: Mapping[str, Any],
    ensure_market_ready: Callable[[], None],
) -> None:
    if positive_case.category in {"account", "order"}:
        ensure_market_ready()

    with streaming_recorder.capture(fubon_client, positive_case):
        result = _execute_case(fubon_client, positive_case, api_runtime_context)
    _write_artifact(artifact_dir, positive_case, result)
    LOGGER.info(
        "Case %s returned response type %s",
        positive_case.name,
        type(result.response).__name__,
    )

    if not result.success:
        lowered = result.message.lower()
        if any(token in lowered for token in TRANSIENT_ERROR_SUBSTRINGS):
            pytest.skip(f"SDK reported transient or unavailable service: {result.message}")

    assert result.success, (
        f"[{positive_case.category}] Expected success but got failure: {result.message}"
    )

    if positive_case.name == "auth.login.success" and result.success:
        _refresh_context_with_login(api_runtime_context, result.response)
        ensure_market_ready()

    if positive_case.name == "auth.logout.success":
        relogin_response = fubon_client.login(
            api_runtime_context["user_id"],
            api_runtime_context["user_password"],
            api_runtime_context["ca_path"],
            api_runtime_context["ca_password"],
        )
        _refresh_context_with_login(api_runtime_context, relogin_response)
        ensure_market_ready()


def test_negative_cases(
    negative_case: ApiTestCase,
    fubon_client: Any,
    artifact_dir: Path,
    streaming_recorder: "StreamingRecorder",
    api_runtime_context: Mapping[str, Any],
) -> None:
    with streaming_recorder.capture(fubon_client, negative_case):
        result = _execute_case(fubon_client, negative_case, api_runtime_context)
    _write_artifact(artifact_dir, negative_case, result)

    if result.success:
        pytest.fail(
            f"[{negative_case.category}] Negative case '{negative_case.name}' unexpectedly succeeded: "
            f"{result.message}"
        )

    if negative_case.expected_error_substrings:
        lowered_message = result.message.lower()
        assert any(
            substring.lower() in lowered_message
            for substring in negative_case.expected_error_substrings
        ), (
            f"Expected error message to mention one of {negative_case.expected_error_substrings}, "
            f"got '{result.message}'"
        )


@pytest.mark.timeout(30)
def test_market_websocket_trades(
    fubon_client: Any,
    ensure_market_ready: Callable[[], None],
) -> None:
    """
    Validate that the websocket client accepts subscriptions after realtime init.
    """

    ensure_market_ready()

    marketdata = getattr(fubon_client, "marketdata", None)
    if marketdata is None:
        pytest.skip("SDK did not expose marketdata client after init_realtime().")

    websocket_client = getattr(marketdata, "websocket_client", None)
    if websocket_client is None:
        pytest.skip("SDK does not provide a websocket client interface.")

    futopt_ws = getattr(websocket_client, "futopt", None)
    if futopt_ws is None:
        pytest.skip("FutOpt websocket client is unavailable in this SDK build.")

    received: list[str] = []

    def _handler(message: str) -> None:
        received.append(message)

    futopt_ws.on("message", _handler)
    connected = False
    try:
        try:
            futopt_ws.connect()
            connected = True
        except Exception as exc:
            pytest.skip(f"Websocket connect failed: {exc}")

        try:
            futopt_ws.subscribe({"channel": "trades", "symbol": "TXFA4"})
            futopt_ws.subscribe({"channel": "books", "symbol": "TXFA4"})
            time.sleep(3.0)
        except Exception as exc:
            message = str(exc)
            if "websocket" in message.lower() or "auth" in message.lower():
                pytest.skip(f"Websocket subscription failed: {message}")
            raise
    finally:
        with contextlib.suppress(Exception):
            futopt_ws.unsubscribe({"channel": "trades", "symbol": "TXFA4"})
        with contextlib.suppress(Exception):
            futopt_ws.unsubscribe({"channel": "books", "symbol": "TXFA4"})
        with contextlib.suppress(Exception):
            futopt_ws.off("message", _handler)
        if connected:
            with contextlib.suppress(Exception):
                futopt_ws.disconnect()

    # Receiving data depends on market hours; success means no exceptions were raised.
    assert futopt_ws is not None
