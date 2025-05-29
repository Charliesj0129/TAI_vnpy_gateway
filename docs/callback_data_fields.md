# 回调数据字段清单 (Callback Data Field List)

This document outlines the data fields returned by the Fubon Neo SDK callbacks for stock orders and trades, specifically `set_on_stock_order`, `set_on_stock_filled`, and `set_on_stock_order_changed`. It details how these fields are mapped to `vnpy.trader.object.OrderData` and `vnpy.trader.object.TradeData` in the vn.py framework, as implemented in `fubon_gateway.py`. This serves as a reference for understanding callback data processing in the Fubon Gateway.

## Callback: set_on_stock_order

This callback is triggered when a new stock order is placed or updated. It is handled by the `_on_stock_order` method in `fubon_gateway.py`, which maps the received data to a new `OrderData` object.

| **Fubon SDK Field** | **vn.py Field (OrderData)** | **Mapping Details / Transformation**                                                                 | **Notes**                                                                                     |
|---------------------|-----------------------------|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| `symbol`            | `symbol`                   | Direct mapping. Used as the stock symbol identifier (e.g., "2330.TWSE").                            | Retrieved directly from callback data. If not found, order processing fails with a log error. |
| `seq_no`            | `orderid`                  | Direct mapping. Used as the unique identifier for the order within vn.py.                           | Stored in `self.orders` dictionary for tracking.                                              |
| `buy_sell`          | `direction`                | Mapped via `DIRECTION_MAP_REVERSE`:<br>- `"Buy"` → `Direction.LONG`<br>- `"Sell"` → `Direction.SHORT` | Converted from Fubon string to vn.py `Direction` enum.                                        |
| `price`             | `price`                    | Converted to float. Defaults to 0.0 if not provided.                                                | Represents the order price, especially for limit orders.                                      |
| `quantity`          | `volume`                   | Converted to float. Defaults to 0.0 if not provided.                                                | Represents the number of shares ordered.                                              |
| `status`            | `status`                   | Mapped via `STATUS_MAP`:<br>- `"Pending"` → `Status.SUBMITTING`<br>- Other statuses mapped as defined in `enum_map.py` (e.g., `"Filled"` → `Status.ALLTRADED`, `"Cancelled"` → `Status.CANCELLED`). | Indicates the current state of the order. Defaults to `SUBMITTING` if not mapped.             |
| Not applicable      | `exchange`                 | Derived from `contract.exchange` based on the symbol's contract data.                               | Looked up from `self.contracts` dictionary. Fails if contract not found.                      |
| Not applicable      | `datetime`                 | Set to current time using `datetime.now(TAIPEI_TZ)`.                                                | Reflects the time of order processing in Taipei timezone.                                     |
| Not applicable      | `gateway_name`             | Set to `self.gateway_name` (e.g., "FUBON").                                                         | Identifies the gateway processing the order.                                                  |

**Processing Notes**: 
- The `_on_stock_order` method creates a new `OrderData` object with the above mappings and stores it in `self.orders` using `seq_no` as the key.
- The `on_order` method is called to push the order update to the vn.py event engine.
- If the contract for the symbol is not found in `self.contracts`, processing fails with a log message.

## Callback: set_on_stock_filled

This callback is triggered when a stock order is fully or partially filled. It is handled by the `_on_stock_filled` method in `fubon_gateway.py`, which maps the data to a `TradeData` object and updates the corresponding `OrderData`.

| **Fubon SDK Field** | **vn.py Field (TradeData)** | **Mapping Details / Transformation**                                                                 | **Notes**                                                                                     |
|---------------------|-----------------------------|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| `symbol`            | `symbol`                   | Direct mapping. Used as the stock symbol identifier (e.g., "2330.TWSE").                            | Retrieved directly from callback data. If not found, trade processing fails with a log error. |
| `seq_no`            | `orderid`                  | Direct mapping. Links the trade to the original order.                                              | Used to look up the corresponding `OrderData` and store in `self.fubon_trades`.               |
| `fill_id`           | `tradeid`                  | Direct mapping. Unique identifier for the trade fill.                                               | Defaults to empty string if not provided.                                                     |
| `buy_sell`          | `direction`                | Mapped via `DIRECTION_MAP_REVERSE`:<br>- `"Buy"` → `Direction.LONG`<br>- `"Sell"` → `Direction.SHORT` | Converted from Fubon string to vn.py `Direction` enum.                                        |
| `price`             | `price`                    | Converted to float. Defaults to 0.0 if not provided.                                                | Represents the price at which the trade was executed.                                         |
| `quantity`          | `volume`                   | Converted to float. Defaults to 0.0 if not provided.                                                | Represents the number of shares traded in this fill.                                          |
| Not applicable      | `exchange`                 | Derived from `contract.exchange` based on the symbol's contract data.                               | Looked up from `self.contracts` dictionary. Fails if contract not found.                      |
| Not applicable      | `datetime`                 | Set to current time using `datetime.now(TAIPEI_TZ)`.                                                | Reflects the time of trade processing in Taipei timezone.                                     |
| Not applicable      | `gateway_name`             | Set to `self.gateway_name` (e.g., "FUBON").                                                         | Identifies the gateway processing the trade.                                                  |

**Processing Notes**:
- The `_on_stock_filled` method creates a `TradeData` object with the above mappings and stores it in `self.fubon_trades` using `seq_no` as the key.
- The `on_trade` method is called to push the trade update to the vn.py event engine.
- If a corresponding `OrderData` exists in `self.orders`, its status is updated to `Status.ALLTRADED`, and `on_order` is called to push the updated order status.
- If the contract for the symbol is not found, processing fails with a log message.

## Callback: set_on_stock_order_changed

This callback is triggered when an existing stock order is modified (e.g., price or quantity changed, or status updated). It is handled by the `_on_stock_order_changed` method in `fubon_gateway.py`, which updates the existing `OrderData`.

| **Fubon SDK Field** | **vn.py Field (OrderData)** | **Mapping Details / Transformation**                                                                 | **Notes**                                                                                     |
|---------------------|-----------------------------|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| `seq_no`            | `orderid`                  | Direct mapping. Used to look up the existing order in `self.orders`.                                | If not found, processing fails with a log message.                                            |
| `price`             | `price`                    | Converted to float. Updates the existing order's price if provided.                                 | Defaults to existing price if not provided in callback data.                                  |
| `quantity`          | `volume`                   | Converted to float. Updates the existing order's volume if provided.                                | Defaults to existing volume if not provided in callback data.                                 |
| `status`            | `status`                   | Mapped via `STATUS_MAP`:<br>- `"Filled"` → `Status.ALLTRADED`<br>- `"PartFilled"` → `Status.PARTTRADED`<br>- `"Cancelled"` → `Status.CANCELLED`<br>- Other statuses as defined in `enum_map.py`. | Updates the order status. Defaults to existing status if not mapped.                          |

**Processing Notes**:
- The `_on_stock_order_changed` method updates the existing `OrderData` object in `self.orders` with the new price, volume, or status if provided in the callback data.
- The `on_order` method is called to push the updated order to the vn.py event engine.
- If the order is not found in `self.orders`, processing fails with a log message.

## General Implementation Notes

- **Error Handling**: All callback methods include try-except blocks to catch and log any exceptions during data processing, ensuring the gateway remains operational even if a single callback fails.
- **Contract Lookup**: For both order and trade processing, the `contract` object is looked up from `self.contracts` using the symbol to determine the `exchange`. If the contract is not found, processing is aborted with a log error.
- **Thread Safety**: Updates to `self.orders` and `self.fubon_trades` are protected by `self.order_map_lock` to ensure thread safety in a multi-threaded environment.
- **Event Pushing**: After processing, `on_order` and `on_trade` methods push the `OrderData` and `TradeData` objects to the vn.py event engine, ensuring downstream components (e.g., strategy engines) receive updates.

This document provides a clear reference for how callback data from the Fubon Neo SDK is transformed and integrated into the vn.py framework, ensuring accurate order and trade status tracking.
