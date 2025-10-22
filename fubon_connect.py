"""
Entry point wrapper for backwards compatibility.
"""

from vnpy_fubon.fubon_connect import create_authenticated_client, main

__all__ = ["create_authenticated_client", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
