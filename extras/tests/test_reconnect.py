import pytest

pytest.skip(
    "Legacy reconnection test relied on the deprecated custom websocket client. "
    "The refreshed implementation delegates connection handling to the official "
    "Fubon SDK, so this test no longer applies.",
    allow_module_level=True,
)
