"""
Configuration helpers for loading credentials and SDK settings.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Tuple

from .exceptions import FubonConfigurationError

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config") / "fubon_credentials.toml"
DEFAULT_DOTENV_PATH = Path(".env")
DEFAULT_SDK_CLIENT_CLASS = "fubon_neo.sdk.FubonSDK"

ENV_VAR_MAP = {
    "user_id": "FUBON_USER_ID",
    "user_password": "FUBON_USER_PASSWORD",
    "ca_path": "FUBON_CA_PATH",
    "ca_password": "FUBON_CA_PASSWORD",
    "client_class": "FUBON_SDK_CLIENT_CLASS",
    "log_directory": "FUBON_LOG_DIRECTORY",
    "extra_init_kwargs": "FUBON_SDK_EXTRA_INIT_KWARGS",
}

REQUIRED_CREDENTIAL_FIELDS = ("user_id", "user_password", "ca_path", "ca_password")


@dataclass(frozen=True)
class FubonCredentials:
    user_id: str
    user_password: str
    ca_path: Path
    ca_password: str


@dataclass(frozen=True)
class SdkConfig:
    client_class: str = DEFAULT_SDK_CLIENT_CLASS
    log_directory: Optional[Path] = None
    extra_init_kwargs: Dict[str, Any] = field(default_factory=dict)


def load_dotenv_if_present(path: Path = DEFAULT_DOTENV_PATH) -> None:
    """
    Lightweight .env loader to avoid extra dependencies.
    Existing environment variables take precedence over the file.
    """

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _read_toml_config(path: Path) -> Dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _parse_extra_kwargs(raw: Any) -> Dict[str, Any]:
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise FubonConfigurationError(
                f"Unable to parse JSON from FUBON_SDK_EXTRA_INIT_KWARGS: {raw}"
            ) from exc
    raise FubonConfigurationError(
        "extra_init_kwargs must be a mapping or JSON object string."
    )


def load_configuration(
    config_path: Optional[Path] = None,
    dotenv_path: Optional[Path] = None,
    env_overrides: Optional[Mapping[str, str]] = None,
) -> Tuple[FubonCredentials, SdkConfig]:
    """
    Load credentials and SDK configuration from environment variables and optional TOML file.
    """

    config_path = config_path or DEFAULT_CONFIG_PATH
    dotenv_path = dotenv_path or DEFAULT_DOTENV_PATH

    load_dotenv_if_present(dotenv_path)

    env: MutableMapping[str, str] = os.environ
    if env_overrides:
        for key, value in env_overrides.items():
            env[key] = value

    file_data: Dict[str, Any] = {}
    if config_path.exists():
        try:
            file_data = _read_toml_config(config_path)
            LOGGER.debug("Loaded configuration from %s", config_path)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise FubonConfigurationError(
                f"Failed to read TOML config at {config_path}: {exc}"
            ) from exc
    else:
        LOGGER.debug("Configuration file %s not found; relying on environment.", config_path)

    credentials_data: Dict[str, Any] = dict(file_data.get("credentials", {}))
    sdk_data: Dict[str, Any] = dict(file_data.get("sdk", {}))

    for attr, env_key in ENV_VAR_MAP.items():
        if env_key not in env:
            continue
        value = env[env_key]
        if attr in {"log_directory"} and value:
            sdk_data[attr] = value
        elif attr == "client_class" and value:
            sdk_data[attr] = value
        elif attr == "extra_init_kwargs" and value:
            sdk_data[attr] = value
        else:
            credentials_data[attr] = value

    missing = [
        field_name for field_name in REQUIRED_CREDENTIAL_FIELDS if not credentials_data.get(field_name)
    ]
    if missing:
        raise FubonConfigurationError(
            f"Missing credential fields: {', '.join(missing)}. "
            "Set environment variables or update the TOML configuration."
        )

    ca_path = Path(credentials_data["ca_path"]).expanduser()
    credentials = FubonCredentials(
        user_id=str(credentials_data["user_id"]),
        user_password=str(credentials_data["user_password"]),
        ca_path=ca_path,
        ca_password=str(credentials_data["ca_password"]),
    )

    if not credentials.ca_path.exists():
        LOGGER.warning("CA certificate path %s does not exist.", credentials.ca_path)

    log_directory = sdk_data.get("log_directory")
    if log_directory:
        log_directory_path = Path(str(log_directory)).expanduser()
    else:
        log_directory_path = None

    sdk_config = SdkConfig(
        client_class=str(sdk_data.get("client_class", DEFAULT_SDK_CLIENT_CLASS)),
        log_directory=log_directory_path,
        extra_init_kwargs=_parse_extra_kwargs(sdk_data.get("extra_init_kwargs", {})),
    )

    return credentials, sdk_config
