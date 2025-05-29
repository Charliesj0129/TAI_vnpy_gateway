# MarketProxy Design Document

This document outlines the design and architecture of the `MarketProxy` class, which is responsible for handling market data subscriptions (trades and books) via WebSocket for the Fubon Gateway in vn.py. The design adheres to the requirements specified in the Week 5 schedule for high-performance, low-latency market data processing, ensuring compatibility with the Fubon Neo SDK and vn.py's `TickData` structure.

## Overview

The `MarketProxy` class will manage WebSocket connections to the Fubon Neo SDK for real-time market data, specifically trades and books (order book depth). It will parse incoming JSON messages, map them to vn.py's `TickData` objects, and push them to the vn.py event engine. The design focuses on modularity, performance optimization, and robust error handling to meet the target metrics of 10,000 messages/second throughput with <30ms latency.

## Subscription Parameters

Based on the existing implementation in `fubon_gateway.py` and inferred from the Fubon SDK's structure, the subscription parameters for market data are as follows:

- **Mode**: "Speed" (low-latency mode for WebSocket connections).
- **Channels**: 
  - `trades`: Provides real-time trade data including price, size, and timestamp.
  - `books`: Provides order book updates with bid and ask prices and volumes (multiple depth levels).
- **Symbols**: A list of symbols (e.g., "2330.TWSE") to subscribe to, batched for efficiency.
- **WebSocket Client**: 
  - For stocks: `sdk.marketdata.websocket_client.stock`
  - For futures/options: `sdk.marketdata.websocket_client.futopt`
- **Rate Limits**: To be confirmed with Fubon SDK documentation, but assumed to support high-frequency subscriptions with potential limits on symbols per request or messages per second.

## Message Formats

The expected JSON message formats from the Fubon SDK WebSocket for trades and books are inferred from the existing `subscribe` method in `fubon_gateway.py`. These formats will be validated against official documentation when available.

### Trade Message
```json
{
  "event": "trade",
  "price": 600.5,
  "size": 100,
  "time": 1634567890.123,
  "symbol": "2330.TWSE"
}
```

### Book Message
```json
{
  "event": "book",
  "bids": [
    {"price": 600.0, "size": 200},
    {"price": 599.5, "size": 150},
    {"price": 599.0, "size": 300},
    {"price": 598.5, "size": 100},
    {"price": 598.0, "size": 250}
  ],
  "asks": [
    {"price": 601.0, "size": 180},
    {"price": 601.5, "size": 220},
    {"price": 602.0, "size": 90},
    {"price": 602.5, "size": 310},
    {"price": 603.0, "size": 140}
  ],
  "symbol": "2330.TWSE"
}
```

## Field Mapping: JSON to TickData

The following table maps Fubon SDK JSON fields to vn.py `TickData` fields for both trade and book messages. This ensures accurate data transformation for downstream processing in vn.py.

| **Fubon SDK Field**       | **vn.py TickData Field**      | **Mapping Details / Transformation**                                      | **Notes**                                                                 |
|---------------------------|-------------------------------|-----------------------------------------------------------------------|---------------------------------------------------------------------------|
| `symbol`                 | `symbol`                     | Direct mapping. Used as the identifier (e.g., "2330.TWSE").           | Retrieved directly from message.                                         |
| Trade: `price`           | `last_price`                 | Converted to float. Represents the latest transaction price.          | Updated only on trade messages.                                          |
| Trade: `size`            | `last_volume`                | Converted to float. Represents the volume of the latest transaction.  | Updated only on trade messages.                                          |
| Trade: `time`            | `datetime`                   | Converted from UNIX timestamp to `datetime` object with Taipei TZ.    | Ensures accurate timestamp for time-sensitive strategies.                |
| Book: `bids[0].price`    | `bid_price_1`                | Converted to float. First level bid price.                            | Updated only on book messages; defaults to 0.0 if not available.         |
| Book: `bids[0].size`     | `bid_volume_1`               | Converted to float. First level bid volume.                           | Updated only on book messages; defaults to 0.0 if not available.         |
| Book: `bids[1].price`    | `bid_price_2`                | Converted to float. Second level bid price.                           | As above, for second level.                                              |
| Book: `bids[1].size`     | `bid_volume_2`               | Converted to float. Second level bid volume.                          | As above, for second level.                                              |
| Book: `bids[2].price`    | `bid_price_3`                | Converted to float. Third level bid price.                            | As above, for third level.                                               |
| Book: `bids[2].size`     | `bid_volume_3`               | Converted to float. Third level bid volume.                           | As above, for third level.                                               |
| Book: `bids[3].price`    | `bid_price_4`                | Converted to float. Fourth level bid price.                           | As above, for fourth level.                                              |
| Book: `bids[3].size`     | `bid_volume_4`               | Converted to float. Fourth level bid volume.                          | As above, for fourth level.                                              |
| Book: `bids[4].price`    | `bid_price_5`                | Converted to float. Fifth level bid price.                            | As above, for fifth level.                                               |
| Book: `bids[4].size`     | `bid_volume_5`               | Converted to float. Fifth level bid volume.                           | As above, for fifth level.                                               |
| Book: `asks[0].price`    | `ask_price_1`                | Converted to float. First level ask price.                            | Updated only on book messages; defaults to 0.0 if not available.         |
| Book: `asks[0].size`     | `ask_volume_1`               | Converted to float. First level ask volume.                           | Updated only on book messages; defaults to 0.0 if not available.         |
| Book: `asks[1].price`    | `ask_price_2`                | Converted to float. Second level ask price.                           | As above, for second level.                                              |
| Book: `asks[1].size`     | `ask_volume_2`               | Converted to float. Second level ask volume.                          | As above, for second level.                                              |
| Book: `asks[2].price`    | `ask_price_3`                | Converted to float. Third level ask price.                            | As above, for third level.                                               |
| Book: `asks[2].size`     | `ask_volume_3`               | Converted to float. Third level ask volume.                           | As above, for third level.                                               |
| Book: `asks[3].price`    | `ask_price_4`                | Converted to float. Fourth level ask price.                           | As above, for fourth level.                                              |
| Book: `asks[3].size`     | `ask_volume_4`               | Converted to float. Fourth level ask volume.                          | As above, for fourth level.                                              |
| Book: `asks[4].price`    | `ask_price_5`                | Converted to float. Fifth level ask price.                            | As above, for fifth level.                                               |
| Book: `asks[4].size`     | `ask_volume_5`               | Converted to float. Fifth level ask volume.                           | As above, for fifth level.                                               |
| Not applicable           | `exchange`                   | Derived from `contract.exchange` based on the symbol's contract data. | Looked up from gateway's contract dictionary.                            |
| Not applicable           | `gateway_name`               | Set to the gateway name (e.g., "FUBON").                              | Identifies the source gateway for the data.                              |

**Processing Notes**:
- Trade and book messages for the same symbol are merged into a single `TickData` object when possible to reduce event engine load.
- A caching mechanism will store the latest `TickData` per symbol to facilitate merging of trade and book updates.
- Timestamps are converted to Taipei timezone to align with market operation hours.

## MarketProxy Class Interface Signatures

The `MarketProxy` class will be designed as a modular component, potentially integrated within or alongside `FubonGateway`, to handle market data subscriptions with high performance and reliability.

### Class Definition
```python
class MarketProxy:
    """
    A class to manage WebSocket-based market data subscriptions for Fubon Neo SDK,
    converting incoming trades and books data to vn.py TickData objects.
    """
    def __init__(self, sdk: FubonSDK, gateway: BaseGateway):
        """
        Initialize the MarketProxy with Fubon SDK and gateway reference.
        
        Args:
            sdk (FubonSDK): The initialized Fubon Neo SDK instance.
            gateway (BaseGateway): The vn.py gateway instance for event pushing.
        """
        self.sdk = sdk
        self.gateway = gateway
        self.ws_initialized = False
        self.subscribed_symbols = set()
        self.tick_cache = {}
        self.queue = janus.Queue(maxsize=3000)  # For buffering high-frequency data
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3
        self.reconnect_interval = 5  # seconds
```

### Core Methods
```python
def connect(self) -> bool:
    """
    Initialize the WebSocket connection for real-time market data in low-latency mode.
    Sets up callbacks for incoming messages and starts heartbeat mechanism.
    
    Returns:
        bool: True if connection is successful, False otherwise.
    """
    pass

def disconnect(self) -> None:
    """
    Cleanly close the WebSocket connection and clear any subscriptions or cached data.
    """
    pass

def subscribe(self, symbols: List[str]) -> bool:
    """
    Subscribe to trades and books data for the given list of symbols.
    Implements batch subscription with retry mechanism on failure.
    
    Args:
        symbols (List[str]): List of symbol identifiers (e.g., ["2330.TWSE", "0050.TWSE"]).
    
    Returns:
        bool: True if subscription is successful for at least one symbol, False otherwise.
    """
    pass

def on_message(self, ws, raw: str) -> None:
    """
    Callback for processing incoming WebSocket messages.
    Parses JSON data, converts to TickData, and pushes to queue for event engine.
    
    Args:
        ws: WebSocket instance (provided by SDK).
        raw (str): Raw JSON message string from WebSocket.
    """
    pass

def on_tick(self, tick: TickData) -> None:
    """
    Push the processed TickData to the vn.py event engine.
    Called after parsing and queue processing to ensure non-blocking operation.
    
    Args:
        tick (TickData): The processed market data object.
    """
    pass
```

### Helper Methods
- **Heartbeat Mechanism**: To maintain connection stability with periodic ping/pong messages.
- **Reconnection Logic**: Detect disconnection (e.g., no messages for ≥1s) and attempt auto-reconnect with re-subscription.
- **Queue Management**: Process `janus.Queue` items in batches to minimize context switching and ensure WebSocket thread is not blocked.

## Performance Considerations
- **Threading/Async**: WebSocket callbacks will run in a separate thread or asyncio loop from the vn.py event engine to prevent blocking.
- **Queue Size**: `janus.Queue` will be tuned (initial maxsize=3000) to balance memory usage and processing speed, with batch processing to reduce overhead.
- **JSON Parsing**: Optimized to extract only necessary fields, reusing `TickData` objects to minimize memory allocation.
- **Latency Target**: End-to-end processing latency <30ms for 10,000 messages/second, achieved through efficient parsing and queuing.

## Integration with FubonGateway
The `MarketProxy` may be instantiated within `FubonGateway` or as a standalone component. It will use the gateway's contract dictionary for exchange lookup and push `TickData` via `gateway.on_tick()`. This ensures seamless integration with vn.py's event-driven architecture without synthesizing Bar data at the gateway level (as per vn.py norms).

## Testing Strategy
- **Unit Tests**: Validate JSON to `TickData` mapping for various trade and book message scenarios.
- **Integration Tests**: Local subscription to active contracts, verifying continuous `TickData` output.
- **Stress Tests**: Simulate 10,000 msg/s to confirm latency <30ms and throughput targets, plus sandbox testing with 50+ contracts for 30+ minutes.
- **Reconnection Tests**: Simulate network interruptions to ensure auto-reconnect and re-subscription success rate ≥98%.

## Next Steps
This design will guide the implementation over the Week 5 schedule, starting with WebSocket client setup and progressing to performance optimization and stress testing. Any deviations or updates to message formats or SDK behaviors will be documented as implementation proceeds.

**Note**: Specific Fubon SDK documentation for "行情订阅" is assumed based on existing code. Official documentation should be consulted to confirm rate limits, exact field names, and additional channel parameters if available.
