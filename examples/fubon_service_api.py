from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from vnpy_fubon.config import ENV_VAR_MAP, load_configuration
from vnpy_fubon.fubon_connect import SdkSessionConnector
from vnpy_fubon.logging_config import configure_logging

LOGGER = configure_logging(
    log_level=logging.INFO,
    logger_name="vnpy_fubon.service",
    gateway_name="service",
)

app = FastAPI(
    title="vnpy-fubon-gateway",
    version="0.1.0",
    docs_url=None,
)

_SESSION_STATE: Dict[str, Any] = {
    "status": "cold",
    "timestamp": None,
    "message": None,
    "detail": None,
}


class SessionRequest(BaseModel):
    """
    Certificate-based credential payload accepted by the session endpoint.
    All four fields are required by the API contract.
    """

    user_id: Optional[str] = Field(default=None, alias="user_id")
    user_password: Optional[str] = Field(default=None, alias="user_password")
    ca_path: Optional[str] = Field(default=None, alias="ca_path")
    ca_password: Optional[str] = Field(default=None, alias="ca_password")


class SessionResponse(BaseModel):
    is_success: bool
    message: str
    data: Optional[Any] = None
    issued_at: datetime


def _build_env_overrides(payload: SessionRequest) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    mapping = {
        "user_id": "FUBON_USER_ID",
        "user_password": "FUBON_USER_PASSWORD",
        "ca_path": "FUBON_CA_PATH",
        "ca_password": "FUBON_CA_PASSWORD",
    }
    for attr, env_key in mapping.items():
        value = getattr(payload, attr)
        if value:
            overrides[env_key] = str(value)

    return overrides


def _log_health(status: str, message: str, detail: Optional[str] = None) -> None:
    _SESSION_STATE.update(
        {
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "detail": detail,
        }
    )
    LOGGER.info("session_status=%s message=%s detail=%s", status, message, detail)


def _serialize_response(response: Any) -> Any:
    try:
        encoded = jsonable_encoder(response)
        if isinstance(encoded, dict) and "password" in encoded:
            encoded = dict(encoded)
            encoded["password"] = "***"
        return encoded
    except Exception:  # pragma: no cover - fallback for opaque SDK objects
        return repr(response)


@app.get("/healthz", response_model=Dict[str, Any])
def healthcheck() -> Dict[str, Any]:
    """
    Lightweight health probe consumed by Cloud Run and smoketests.
    """

    return {
        "status": "ok",
        "last_login": _SESSION_STATE.get("timestamp"),
        "last_status": _SESSION_STATE.get("status"),
        "detail": _SESSION_STATE.get("detail"),
    }


@app.post("/api/v1/session", response_model=SessionResponse)
def create_session(request: SessionRequest) -> SessionResponse:
    """
    Authenticate against the SDK using configuration defaults unless overrides are provided.
    """

    required_fields = ["user_id", "user_password", "ca_path", "ca_password"]
    missing = [field for field in required_fields if not getattr(request, field)]
    if missing:
        detail = {
            "message": "Missing credential fields.",
            "missing": missing,
        }
        _log_health("config_error", f"Missing credential fields: {', '.join(missing)}")
        raise HTTPException(status_code=400, detail=detail)

    overrides = _build_env_overrides(request)

    try:
        credentials, sdk_config = load_configuration(env_overrides=overrides)
    except Exception as exc:  # pragma: no cover - configuration error path
        detail = f"Configuration error: {exc}"
        _log_health("config_error", detail)
        raise HTTPException(status_code=400, detail=detail) from exc

    connector = SdkSessionConnector(
        credentials=credentials,
        sdk_config=sdk_config,
        log_level=LOGGER.level,
    )

    try:
        client, response = connector.connect()
        message = "SDK login successful."
        data = _serialize_response(response)
        _log_health("healthy", message)
    except Exception as exc:  # pragma: no cover - depends on vendor SDK responses
        detail = f"SDK authentication failed: {exc}"
        _log_health("error", "SDK authentication failed", detail)
        raise HTTPException(status_code=502, detail=detail) from exc
    finally:
        logout = getattr(locals().get("client", None), "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:  # pragma: no cover - defensive cleanup
                LOGGER.debug("SDK client logout failed.", exc_info=True)

    return SessionResponse(
        is_success=True,
        message=message,
        data=data,
        issued_at=datetime.now(timezone.utc),
    )


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "vnpy-fubon-gateway",
        "status": _SESSION_STATE.get("status"),
        "last_login": _SESSION_STATE.get("timestamp"),
    }


# Backwards compatible exposure of ENV_VAR_MAP (FastAPI layer re-exports)
CONFIG_ENV_VARS = ENV_VAR_MAP
