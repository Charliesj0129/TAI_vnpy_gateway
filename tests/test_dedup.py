from adapters import FubonToVnpyAdapter


def test_dedup_token_identical_payload():
    adapter = FubonToVnpyAdapter()
    payload = {
        "channel": "trades",
        "contractId": "TXF202510",
        "exchangeTime": "2025-10-16T01:00:00+00:00",
        "matchNo": 101,
        "matchPrice": "16500",
        "matchQty": "2",
    }
    raw1 = adapter.build_raw_envelope(payload, default_channel="trades")
    raw2 = adapter.build_raw_envelope(payload, default_channel="trades")

    assert raw1.dedup_token() == raw2.dedup_token()


def test_dedup_token_changes_with_seq():
    adapter = FubonToVnpyAdapter()
    payload = {
        "channel": "trades",
        "contractId": "TXF202510",
        "exchangeTime": "2025-10-16T01:00:01+00:00",
        "matchNo": 102,
        "matchPrice": "16510",
        "matchQty": "3",
    }
    raw1 = adapter.build_raw_envelope(payload, default_channel="trades")

    payload["matchNo"] = 103
    raw2 = adapter.build_raw_envelope(payload, default_channel="trades")

    assert raw1.dedup_token() != raw2.dedup_token()
