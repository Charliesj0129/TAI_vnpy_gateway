"""
Unit Tests for Enum Mappings between VnPy and Fubon Neo API

This test suite verifies the correctness and consistency of bidirectional mappings
defined in src.enum_map. Each test uses pytest.mark.parametrize to ensure that
forward and reverse mappings are consistent for all defined pairs.
"""

import pytest
from vnpy.trader.constant import Direction, OrderType, Offset, Exchange, Product, Status, OptionType
from fubon_neo.constant import (
    BSAction as FubonBSAction,
    PriceType as FubonPriceType,
    FutOptOrderType as FubonFutOptOrderType,
    FutOptPriceType as FubonFutOptPriceType,
    FutOptMarketType as FubonFutOptMarketType,
    CallPut as FubonCallPut,
    MarketType as FubonMarketType,
)
from src.enum_map import (
    DIRECTION_MAP, DIRECTION_MAP_REVERSE,
    PRICE_TYPE_MAP, PRICE_TYPE_MAP_REVERSE,
    FUTOPT_PRICE_TYPE_MAP, FUTOPT_PRICE_TYPE_MAP_REVERSE,
    FUTURES_OFFSET_MAP, FUTURES_OFFSET_MAP_REVERSE,
    OPTION_TYPE_MAP, OPTION_TYPE_MAP_REVERSE,
    MARKET_TYPE_EXCHANGE_MAP, MARKET_TYPE_PRODUCT_MAP,
    FUTOPT_MARKET_TYPE_MAP,
    STATUS_MAP
)

# --- Direction Mapping Tests ---
@pytest.mark.parametrize("vnpy_dir, fubon_action", DIRECTION_MAP.items())
def test_direction_mapping_forward(vnpy_dir, fubon_action):
    """Test VnPy Direction to Fubon BSAction mapping"""
    assert DIRECTION_MAP[vnpy_dir] == fubon_action

@pytest.mark.parametrize("fubon_action_str, vnpy_dir", [(str(k), v) for k, v in DIRECTION_MAP_REVERSE.items()])
def test_direction_mapping_reverse(fubon_action_str, vnpy_dir):
    """Test Fubon BSAction to VnPy Direction mapping using string keys"""
    assert DIRECTION_MAP_REVERSE[fubon_action_str] == vnpy_dir

# --- Price Type Mapping Tests ---
@pytest.mark.parametrize("vnpy_order_type, fubon_price_type", PRICE_TYPE_MAP.items())
def test_price_type_mapping_forward(vnpy_order_type, fubon_price_type):
    """Test VnPy OrderType to Fubon PriceType mapping"""
    assert PRICE_TYPE_MAP[vnpy_order_type] == fubon_price_type

@pytest.mark.parametrize("fubon_price_type_str, vnpy_order_type", [(str(k), v) for k, v in PRICE_TYPE_MAP_REVERSE.items()])
def test_price_type_mapping_reverse(fubon_price_type_str, vnpy_order_type):
    """Test Fubon PriceType to VnPy OrderType mapping using string keys"""
    assert PRICE_TYPE_MAP_REVERSE[fubon_price_type_str] == vnpy_order_type

# --- Futures/Options Price Type Mapping Tests ---
@pytest.mark.parametrize("vnpy_order_type, fubon_futopt_price_type", FUTOPT_PRICE_TYPE_MAP.items())
def test_futopt_price_type_mapping_forward(vnpy_order_type, fubon_futopt_price_type):
    """Test VnPy OrderType to Fubon FutOptPriceType mapping"""
    assert FUTOPT_PRICE_TYPE_MAP[vnpy_order_type] == fubon_futopt_price_type

@pytest.mark.parametrize("fubon_futopt_price_type_str, vnpy_order_type", [(str(k), v) for k, v in FUTOPT_PRICE_TYPE_MAP_REVERSE.items()])
def test_futopt_price_type_mapping_reverse(fubon_futopt_price_type_str, vnpy_order_type):
    """Test Fubon FutOptPriceType to VnPy OrderType mapping using string keys"""
    assert FUTOPT_PRICE_TYPE_MAP_REVERSE[fubon_futopt_price_type_str] == vnpy_order_type

# --- Futures Offset Mapping Tests ---
@pytest.mark.parametrize("vnpy_offset, fubon_futopt_order_type", FUTURES_OFFSET_MAP.items())
def test_futures_offset_mapping_forward(vnpy_offset, fubon_futopt_order_type):
    """Test VnPy Offset to Fubon FutOptOrderType mapping"""
    assert FUTURES_OFFSET_MAP[vnpy_offset] == fubon_futopt_order_type

@pytest.mark.parametrize("fubon_futopt_order_type_str, vnpy_offset", [(str(k), v) for k, v in FUTURES_OFFSET_MAP_REVERSE.items()])
def test_futures_offset_mapping_reverse(fubon_futopt_order_type_str, vnpy_offset):
    """Test Fubon FutOptOrderType to VnPy Offset mapping using string keys"""
    assert FUTURES_OFFSET_MAP_REVERSE[fubon_futopt_order_type_str] == vnpy_offset

# --- Option Type Mapping Tests ---
@pytest.mark.parametrize("vnpy_option_type, fubon_call_put", OPTION_TYPE_MAP.items())
def test_option_type_mapping_forward(vnpy_option_type, fubon_call_put):
    """Test VnPy OptionType to Fubon CallPut mapping"""
    assert OPTION_TYPE_MAP[vnpy_option_type] == fubon_call_put

@pytest.mark.parametrize("fubon_call_put_str, vnpy_option_type", [(str(k), v) for k, v in OPTION_TYPE_MAP_REVERSE.items()])
def test_option_type_mapping_reverse(fubon_call_put_str, vnpy_option_type):
    """Test Fubon CallPut to VnPy OptionType mapping using string keys"""
    assert OPTION_TYPE_MAP_REVERSE[fubon_call_put_str] == vnpy_option_type

# --- Market Type to Exchange Mapping Tests ---
@pytest.mark.parametrize("fubon_market_type, vnpy_exchange", MARKET_TYPE_EXCHANGE_MAP.items())
def test_market_type_exchange_mapping(fubon_market_type, vnpy_exchange):
    """Test Fubon MarketType to VnPy Exchange mapping"""
    assert MARKET_TYPE_EXCHANGE_MAP[fubon_market_type] == vnpy_exchange

# --- Market Type to Product Mapping Tests ---
@pytest.mark.parametrize("fubon_market_type, vnpy_product", MARKET_TYPE_PRODUCT_MAP.items())
def test_market_type_product_mapping(fubon_market_type, vnpy_product):
    """Test Fubon MarketType to VnPy Product mapping"""
    assert MARKET_TYPE_PRODUCT_MAP[fubon_market_type] == vnpy_product

# --- Futures/Options Market Type Mapping Tests ---
@pytest.mark.parametrize("fubon_futopt_market_type, vnpy_exchange", FUTOPT_MARKET_TYPE_MAP.items())
def test_futopt_market_type_mapping(fubon_futopt_market_type, vnpy_exchange):
    """Test Fubon FutOptMarketType to VnPy Exchange mapping"""
    assert FUTOPT_MARKET_TYPE_MAP[fubon_futopt_market_type] == vnpy_exchange

# --- Order Status Mapping Tests ---
@pytest.mark.parametrize("fubon_status_str, vnpy_status", STATUS_MAP.items())
def test_status_mapping(fubon_status_str, vnpy_status):
    """Test Fubon status string to VnPy Status mapping"""
    assert STATUS_MAP[fubon_status_str] == vnpy_status

# --- Edge Case Tests for Unmapped Values ---
def test_direction_mapping_unmapped():
    """Test handling of unmapped Fubon BSAction values"""
    unmapped_value = "UnknownAction"
    assert unmapped_value not in DIRECTION_MAP_REVERSE, "Unmapped value should not exist in reverse mapping"

def test_price_type_mapping_unmapped():
    """Test handling of unmapped Fubon PriceType values"""
    unmapped_value = str(("UnknownPriceType", "UnknownTimeInForce"))
    assert unmapped_value not in PRICE_TYPE_MAP_REVERSE, "Unmapped value should not exist in reverse mapping"

def test_status_mapping_unmapped():
    """Test handling of unmapped Fubon status strings"""
    unmapped_value = "UnknownStatus"
    assert unmapped_value not in STATUS_MAP, "Unmapped status should not exist in mapping"
