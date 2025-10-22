"""
Public package interface for vnpy_fubon.
"""

from .account import AccountAPI, AccountSnapshot
from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DOTENV_PATH,
    DEFAULT_SDK_CLIENT_CLASS,
    FubonCredentials,
    SdkConfig,
    load_configuration,
)
from .gateway import FubonGateway
from .fubon_connect import FubonAPIConnector, SdkSessionConnector, create_authenticated_client
from .exceptions import (
    FubonConfigurationError,
    FubonLoginError,
    FubonSDKImportError,
    FubonSDKMethodNotFoundError,
)
from .market import MarketAPI
from .order import OrderAPI

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DOTENV_PATH",
    "DEFAULT_SDK_CLIENT_CLASS",
    "FubonCredentials",
    "SdkConfig",
    "load_configuration",
    "SdkSessionConnector",
    "FubonAPIConnector",
    "create_authenticated_client",
    "FubonConfigurationError",
    "FubonLoginError",
    "FubonSDKImportError",
    "FubonSDKMethodNotFoundError",
    "AccountAPI",
    "AccountSnapshot",
    "OrderAPI",
    "MarketAPI",
    "FubonGateway",
]
