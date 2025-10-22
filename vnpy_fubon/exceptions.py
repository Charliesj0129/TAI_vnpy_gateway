"""
Custom exceptions raised by the vnpy_fubon package.
"""

from __future__ import annotations


class FubonConfigurationError(Exception):
    """
    Raised when required configuration values are missing or invalid.
    """


class FubonSDKImportError(ImportError):
    """
    Raised when the Fubon SDK classes cannot be imported dynamically.
    """


class FubonSDKMethodNotFoundError(AttributeError):
    """
    Raised when an expected method is not exposed by the SDK client.
    """


class FubonLoginError(RuntimeError):
    """
    Raised when the SDK reports an authentication or session initialisation failure.
    """
