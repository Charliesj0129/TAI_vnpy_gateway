"""
Reusable connector that authenticates against the Fubon Securities SDK.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DOTENV_PATH,
    FubonCredentials,
    SdkConfig,
    load_configuration,
)
from .exceptions import (
    FubonConfigurationError,
    FubonLoginError,
    FubonSDKImportError,
    FubonSDKMethodNotFoundError,
)
from .logging_config import configure_logging

LOGIN_METHOD_CANDIDATES = ("login", "Login", "log_in", "sign_in")

LOGIN_PARAM_ALIASES: Mapping[str, Iterable[str]] = {
    "user_id": ("user_id", "userid", "user", "account", "account_id", "id"),
    "user_password": ("user_password", "password", "passwd", "userpwd", "pwd"),
    "ca_path": (
        "ca_path",
        "cert_path",
        "certificate_path",
        "pfx_path",
        "ca",
        "certificate",
    ),
    "ca_password": (
        "ca_password",
        "cert_password",
        "certificate_password",
        "pfx_password",
        "capwd",
    ),
}


def _resolve_client_class(client_class_path: str) -> type[Any]:
    module_path, _, class_name = client_class_path.rpartition(".")
    if not module_path:
        raise FubonSDKImportError(
            f"Invalid client class path '{client_class_path}'. Expected 'module.ClassName'."
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise FubonSDKImportError(
            f"Unable to import module '{module_path}' for SDK client."
        ) from exc

    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise FubonSDKImportError(
            f"Client class '{class_name}' not found in module '{module_path}'."
        ) from exc


def _normalise_param_name(name: str) -> str:
    return name.replace("_", "").lower()


def _build_keyword_payload(method: Any, credentials: FubonCredentials) -> Dict[str, Any]:
    """
    Attempt to adapt credentials for SDKs that prefer keyword arguments.
    """

    signature = inspect.signature(method)
    payload: Dict[str, Any] = {}

    normalized_alias_map: Dict[str, str] = {}
    for target, aliases in LOGIN_PARAM_ALIASES.items():
        for alias in aliases:
            normalized_alias_map[_normalise_param_name(alias)] = target

    for param_name, param in signature.parameters.items():
        key = _normalise_param_name(param_name)
        target_field = normalized_alias_map.get(key)
        if not target_field:
            continue
        value = getattr(credentials, target_field)
        payload[param_name] = str(value) if isinstance(value, Path) else value

    return payload


def _call_login_with_fallbacks(
    method: Any, credentials: FubonCredentials
) -> Tuple[Any, Dict[str, Any]]:
    """
    Attempt to call the login method using several strategies.
    """

    keyword_payload = {}
    try:
        keyword_payload = _build_keyword_payload(method, credentials)
    except (TypeError, ValueError):
        keyword_payload = {}

    if keyword_payload:
        try:
            return method(**keyword_payload), keyword_payload
        except TypeError:
            keyword_payload = {}

    positional_args = [
        credentials.user_id,
        credentials.user_password,
        str(credentials.ca_path),
        credentials.ca_password,
    ]

    try:
        return method(*positional_args), {
            "user_id": credentials.user_id,
            "ca_path": str(credentials.ca_path),
        }
    except TypeError as exc:
        raise FubonSDKMethodNotFoundError(
            f"SDK login method signature did not accept expected parameters: {exc}"
        ) from exc


def _interpret_login_response(response: Any) -> Tuple[bool, str]:
    """
    Normalise the SDK response into a success boolean and diagnostic message.
    """

    if response is None:
        return True, "SDK login returned None (treated as success)."

    if isinstance(response, bool):
        return response, f"SDK login returned boolean {response}."

    if isinstance(response, (int, float)):
        success = response == 0
        message = f"SDK login returned numeric code {response}."
        return success, message

    if isinstance(response, str):
        lower = response.strip().lower()
        success = lower in {"ok", "success", "0"}
        return success, f"SDK login returned string '{response}'."

    if isinstance(response, Mapping):
        message_parts = json.dumps(response, ensure_ascii=False)
        success_indicators = (
            response.get("success"),
            response.get("is_success"),
            response.get("status"),
            response.get("code"),
        )
        success = any(
            indicator in (True, 0, "0", "OK", "SUCCESS") for indicator in success_indicators
        )
        return success, message_parts

    if isinstance(response, (list, tuple)) and response:
        primary = response[0]
        success, _ = _interpret_login_response(primary)
        return success, f"SDK login returned sequence {response}."

    return True, f"SDK login returned object of type {type(response).__name__}."


@dataclass
class SdkSessionConnector:
    """
    Helper responsible for instantiating the SDK client and performing authentication.
    """

    credentials: FubonCredentials
    sdk_config: SdkConfig
    log_level: int = logging.INFO
    login_method_candidates: Tuple[str, ...] = LOGIN_METHOD_CANDIDATES

    def __post_init__(self) -> None:
        self.logger = configure_logging(
            log_level=self.log_level,
            log_directory=self.sdk_config.log_directory,
            logger_name="vnpy_fubon.connector",
            gateway_name="connector",
        )
        self.logger.debug("Initialised SdkSessionConnector with SDK client %s", self.sdk_config.client_class)

    def _instantiate_client(self) -> Any:
        client_class = _resolve_client_class(self.sdk_config.client_class)
        self.logger.debug(
            "Instantiating SDK client %s with kwargs %s",
            client_class,
            self.sdk_config.extra_init_kwargs,
        )
        return client_class(**self.sdk_config.extra_init_kwargs)

    def _locate_login_method(self, client: Any) -> Any:
        for candidate in self.login_method_candidates:
            method = getattr(client, candidate, None)
            if callable(method):
                self.logger.debug("Using login method '%s'.", candidate)
                return method
        available = [func for func in dir(client) if func.lower().startswith("log")]
        raise FubonSDKMethodNotFoundError(
            f"Unable to locate login method on SDK client. Checked: {self.login_method_candidates}. "
            f"Available candidates: {available}"
        )

    def connect(self) -> Tuple[Any, Any]:
        """
        Create an authenticated SDK client and return it alongside the raw login response.
        """

        self.logger.info(
            "Establishing SDK session for user %s with certificate %s",
            self.credentials.user_id,
            self.credentials.ca_path,
            extra={"gateway_state": "authenticating"},
        )

        client = self._instantiate_client()
        login_method = self._locate_login_method(client)

        response, payload_logged = _call_login_with_fallbacks(login_method, self.credentials)
        success, message = _interpret_login_response(response)

        self.logger.info(
            "SDK login response: %s | payload: %s",
            message,
            payload_logged,
            extra={"gateway_state": "auth_response"},
        )

        if not success:
            raise FubonLoginError(f"SDK login failed: {message}")

        self.logger.info(
            "Fubon SDK authentication successful.",
            extra={"gateway_state": "authenticated"},
        )
        return client, response


def create_authenticated_client(
    *,
    config_path: Optional[Path] = None,
    dotenv_path: Optional[Path] = None,
    log_level: int = logging.INFO,
    env_overrides: Optional[Mapping[str, Any]] = None,
) -> Tuple[Any, Any]:
    """
    Convenience wrapper that loads configuration and returns an authenticated client.
    """

    credentials, sdk_config = load_configuration(
        config_path=config_path,
        dotenv_path=dotenv_path,
        env_overrides=env_overrides,
    )
    connector = SdkSessionConnector(
        credentials=credentials,
        sdk_config=sdk_config,
        log_level=log_level,
    )
    return connector.connect()


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authenticate against the Fubon Securities SDK.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the TOML configuration file (default: config/fubon_credentials.toml).",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=DEFAULT_DOTENV_PATH,
        help="Path to the .env file with overrides (default: .env).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level.",
    )
    parser.add_argument(
        "--dump-response",
        action="store_true",
        help="Print the raw SDK response to stdout in JSON form if possible.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)

    try:
        client, response = create_authenticated_client(
            config_path=args.config,
            dotenv_path=args.dotenv,
            log_level=log_level,
        )
    except (FubonConfigurationError, FubonSDKImportError, FubonLoginError) as exc:
        logging.basicConfig(level=log_level)
        logging.getLogger("vnpy_fubon").exception("Connection failed: %s", exc)
        return 1

    if args.dump_response:
        try:
            print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        except (TypeError, ValueError):
            print(response)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

# Backwards-compatible alias for legacy imports
FubonAPIConnector = SdkSessionConnector

