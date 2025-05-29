"""
Unit Tests for Fubon Gateway and MarketProxy

This test suite verifies the functionality of the FubonGateway and MarketProxy classes
in fubon_gateway.py. It uses pytest and unittest.mock to simulate interactions with
the Fubon Neo SDK and vn.py event engine, ensuring correct behavior for asynchronous
data processing, historical data queries, heartbeat and reconnection logic, and
subscription/unsubscription operations.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime
import json
import time
from threading import Thread

from vnpy.trader.object import (
    TickData, BarData, HistoryRequest, SubscribeRequest
)
from vnpy.trader.constant import Exchange, Interval
from vnpy.event import EventEngine
from fubon_neo.sdk import FubonSDK
from fubon_gateway import FubonGateway, MarketProxy, TAIPEI_TZ

# Mock setup for FubonSDK and EventEngine
@pytest.fixture
def mock_sdk():
    sdk = Mock(spec=FubonSDK)
    sdk.marketdata.websocket_client.stock = Mock()
    sdk.marketdata.websocket_client.futopt = Mock()
    sdk.marketdata.rest_client.stock.aggregate = Mock(return_value={'data': [
        {'time': 1634567890, 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 103.0, 'volume': 1000}
    ]})
    sdk.marketdata.rest_client.stock.candles = Mock(return_value={'data': [
        {'time': 1634567890, 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 103.0, 'volume': 1000}
    ]})
    sdk.init_realtime = Mock()
    return sdk

@pytest.fixture
def mock_event_engine():
    return Mock(spec=EventEngine)

@pytest.fixture
def fubon_gateway(mock_event_engine):
    gateway = FubonGateway(mock_event_engine, gateway_name="FUBON_TEST")
    gateway.sdk = Mock(spec=FubonSDK)
    gateway.connected = True
    gateway.logged_in = True
    gateway.contracts = {
        "2330.TWSE": Mock(exchange=Exchange.TWSE),
        "TXF.TAIFEX": Mock(exchange=Exchange.TAIFEX)
    }
    return gateway

@pytest.fixture
def market_proxy(mock_sdk, fubon_gateway):
    proxy = MarketProxy(mock_sdk, fubon_gateway)
    proxy.ws_initialized = True
    proxy.stock_channel = mock_sdk.marketdata.websocket_client.stock
    proxy.futopt_channel = mock_sdk.marketdata.websocket_client.futopt
    return proxy

# --- Tests for Asynchronous Data Processing ---
@pytest.mark.asyncio
async def test_queue_consumer(fubon_gateway):
    """Test that _queue_consumer processes TickData from the queue and calls on_tick"""
    tick = TickData(
        symbol="2330.TWSE",
        exchange=Exchange.TWSE,
        last_price=600.5,
        last_volume=100,
        datetime=datetime.now(TAIPEI_TZ),
        gateway_name="FUBON_TEST"
    )
    fubon_gateway.janus_queue.async_q.put_nowait(tick)
    fubon_gateway.on_tick = Mock()

    # Run the consumer briefly to process the queue
    consumer_task = asyncio.create_task(fubon_gateway._queue_consumer())
    await asyncio.sleep(0.1)  # Give time for the consumer to process
    consumer_task.cancel()

    fubon_gateway.on_tick.assert_called_once_with(tick)

@pytest.mark.asyncio
async def test_process_batch(fubon_gateway):
    """Test that _process_batch processes a list of TickData and calls on_tick for each"""
    ticks = [
        TickData(
            symbol="2330.TWSE",
            exchange=Exchange.TWSE,
            last_price=600.5,
            last_volume=100,
            datetime=datetime.now(TAIPEI_TZ),
            gateway_name="FUBON_TEST"
        ),
        TickData(
            symbol="0050.TWSE",
            exchange=Exchange.TWSE,
            last_price=120.0,
            last_volume=200,
            datetime=datetime.now(TAIPEI_TZ),
            gateway_name="FUBON_TEST"
        )
    ]
    fubon_gateway.on_tick = Mock()

    await fubon_gateway._process_batch(ticks)
    assert fubon_gateway.on_tick.call_count == 2
    fubon_gateway.on_tick.assert_any_call(ticks[0])
    fubon_gateway.on_tick.assert_any_call(ticks[1])

# --- Tests for Historical Data Query ---
def test_query_history_minute_interval(fubon_gateway, mock_sdk):
    """Test querying historical data with minute interval"""
    req = HistoryRequest(
        symbol="2330.TWSE",
        exchange=Exchange.TWSE,
        interval=Interval.MINUTE,
        start=datetime(2021, 10, 1),
        end=datetime(2021, 10, 2)
    )
    bars = fubon_gateway.query_history(req)
    assert len(bars) == 1
    assert isinstance(bars[0], BarData)
    assert bars[0].symbol == "2330.TWSE"
    assert bars[0].interval == Interval.MINUTE
    assert bars[0].open_price == 100.0
    mock_sdk.marketdata.rest_client.stock.aggregate.assert_called_once()

def test_query_history_daily_interval(fubon_gateway, mock_sdk):
    """Test querying historical data with daily interval"""
    req = HistoryRequest(
        symbol="2330.TWSE",
        exchange=Exchange.TWSE,
        interval=Interval.DAILY,
        start=datetime(2021, 10, 1),
        end=datetime(2021, 10, 2)
    )
    bars = fubon_gateway.query_history(req)
    assert len(bars) == 1
    assert isinstance(bars[0], BarData)
    assert bars[0].symbol == "2330.TWSE"
    assert bars[0].interval == Interval.DAILY
    assert bars[0].open_price == 100.0
    mock_sdk.marketdata.rest_client.stock.candles.assert_called_once()

def test_query_history_unsupported_interval(fubon_gateway):
    """Test querying historical data with unsupported interval"""
    req = HistoryRequest(
        symbol="2330.TWSE",
        exchange=Exchange.TWSE,
        interval=Interval.HOUR,
        start=datetime(2021, 10, 1),
        end=datetime(2021, 10, 2)
    )
    bars = fubon_gateway.query_history(req)
    assert len(bars) == 0

# --- Tests for Subscription and Unsubscription ---
def test_subscribe_stock_symbol(fubon_gateway, market_proxy):
    """Test subscribing to a stock symbol"""
    fubon_gateway.market_proxy = market_proxy
    req = SubscribeRequest(symbol="2330.TWSE", exchange=Exchange.TWSE)
    fubon_gateway.subscribe(req)
    assert "2330.TWSE" in market_proxy.subscribed_symbols
    market_proxy.stock_channel.subscribe.assert_called_once()

def test_subscribe_futopt_symbol(fubon_gateway, market_proxy):
    """Test subscribing to a futures/options symbol"""
    fubon_gateway.market_proxy = market_proxy
    req = SubscribeRequest(symbol="TXF.TAIFEX", exchange=Exchange.TAIFEX)
    fubon_gateway.subscribe(req)
    assert "TXF.TAIFEX" in market_proxy.subscribed_symbols
    market_proxy.futopt_channel.subscribe.assert_called_once()

def test_unsubscribe_stock_symbol(fubon_gateway, market_proxy):
    """Test unsubscribing from a stock symbol"""
    fubon_gateway.market_proxy = market_proxy
    market_proxy.subscribed_symbols.add("2330.TWSE")
    with fubon_gateway.subscribed_lock:
        fubon_gateway.subscribed.add("2330.TWSE")
    req = SubscribeRequest(symbol="2330.TWSE", exchange=Exchange.TWSE)
    fubon_gateway.unsubscribe(req)
    assert "2330.TWSE" not in market_proxy.subscribed_symbols
    assert "2330.TWSE" not in fubon_gateway.subscribed
    market_proxy.stock_channel.unsubscribe.assert_called_once()

def test_unsubscribe_futopt_symbol(fubon_gateway, market_proxy):
    """Test unsubscribing from a futures/options symbol"""
    fubon_gateway.market_proxy = market_proxy
    market_proxy.subscribed_symbols.add("TXF.TAIFEX")
    with fubon_gateway.subscribed_lock:
        fubon_gateway.subscribed.add("TXF.TAIFEX")
    req = SubscribeRequest(symbol="TXF.TAIFEX", exchange=Exchange.TAIFEX)
    fubon_gateway.unsubscribe(req)
    assert "TXF.TAIFEX" not in market_proxy.subscribed_symbols
    assert "TXF.TAIFEX" not in fubon_gateway.subscribed
    market_proxy.futopt_channel.unsubscribe.assert_called_once()

# --- Tests for Heartbeat and Reconnection Logic ---
def test_heartbeat_starts_on_connect(market_proxy):
    """Test that heartbeat thread starts on connect"""
    with patch.object(market_proxy, '_start_heartbeat', Mock()) as mock_heartbeat:
        market_proxy.ws_initialized = False
        market_proxy.connect()
        mock_heartbeat.assert_called_once()

def test_reconnect_attempts_on_heartbeat_failure(market_proxy):
    """Test that reconnection is attempted on heartbeat failure"""
    with patch.object(market_proxy, '_attempt_reconnect', Mock()) as mock_reconnect:
        market_proxy.ws_initialized = True
        market_proxy.sdk.marketdata.websocket_client.ping = Mock(side_effect=Exception("Ping failed"))
        # Simulate heartbeat in a controlled way
        market_proxy._start_heartbeat()
        # Give a brief moment for the thread to run
        time.sleep(0.1)
        mock_reconnect.assert_called()

def test_reconnect_logic(market_proxy):
    """Test reconnection logic with max attempts"""
    with patch.object(market_proxy, 'connect', Mock(return_value=True)) as mock_connect:
        with patch.object(market_proxy, 'subscribe', Mock()) as mock_subscribe:
            market_proxy.reconnect_attempts = 0
            market_proxy._attempt_reconnect()
            mock_connect.assert_called_once()
            mock_subscribe.assert_called_once()
            assert market_proxy.reconnect_attempts == 0  # Reset on success

            # Test max attempts reached
            market_proxy.reconnect_attempts = market_proxy.max_reconnect_attempts
            market_proxy._attempt_reconnect()
            assert mock_connect.call_count == 1  # No additional call after max attempts
