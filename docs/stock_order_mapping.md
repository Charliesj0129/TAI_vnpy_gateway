# 股票下单映射表 (Stock Order Mapping Table)

This document provides a comprehensive mapping between the fields of `vnpy.trader.object.OrderRequest` in the vn.py framework and `fubon_neo.sdk.stock.Order` in the Fubon Neo SDK. It ensures accurate translation of order parameters for stock trading through the Fubon Gateway.

## Field Mappings

The following table maps the fields from `OrderRequest` (vn.py) to `Order` (Fubon SDK), including the relevant enumeration constants used for translation. These mappings are implemented in `src/enum_map.py` and utilized in `fubon_gateway.py` within the `send_order` method.

| **vn.py Field (OrderRequest)** | **Fubon SDK Field (Order)**      | **Mapping Details / Enum Constants**                                                                                     | **Notes**                                                                                           |
|--------------------------------|----------------------------------|-------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `symbol`                      | `symbol`                        | Direct mapping. The symbol identifier for the stock (e.g., "2330.TWSE").                                                | Retrieved from `req.symbol` and passed as-is to Fubon SDK.                                          |
| `direction`                   | `buy_sell`                      | Mapped via `DIRECTION_MAP`:<br>- `Direction.LONG` → `FubonBSAction.Buy`<br>- `Direction.SHORT` → `FubonBSAction.Sell` | Determines if the order is a buy or sell action.                                                    |
| `type`                        | `price_type` & `time_in_force`  | Mapped via `PRICE_TYPE_MAP`:<br>- `OrderType.LIMIT` → `(FubonPriceType.Limit, FubonTimeInForce.ROD)`<br>- `OrderType.MARKET` → `(FubonPriceType.Market, FubonTimeInForce.ROD)`<br>- `OrderType.FAK` → `(FubonPriceType.Market, FubonTimeInForce.IOC)`<br>- `OrderType.FOK` → `(FubonPriceType.Market, FubonTimeInForce.FOK)` | Defines the order pricing type (Limit/Market) and time in force (ROD/IOC/FOK).                     |
| `price`                       | `price`                         | Direct mapping for limit orders. Set to 0.0 for market orders.                                                          | Used only when `type` is `OrderType.LIMIT`; otherwise, Fubon SDK handles market price internally.   |
| `volume`                      | `quantity`                      | Direct mapping. Converted to integer as Fubon SDK requires whole shares.                                                | Represents the number of shares to trade.                                                           |
| `offset`                      | Not applicable                  | Not used for stock orders in Fubon SDK. Stock orders do not distinguish between open/close offsets.                     | Offset is relevant for futures/options but ignored for stocks in this implementation.               |

## Additional Notes on Implementation

- **Account Selection**: In `fubon_gateway.py`, the `send_order` method selects the first available stock account from `self.stock_accounts` to place the order. If no stock account is available, the order fails with a log message.
- **Order Creation**: The `Order` object for Fubon SDK is constructed using the mapped fields, and `sdk.stock.place_order(account, order)` is called to submit the order non-blocking.
- **Order ID Handling**: Upon successful order placement, the `seq_no` from the Fubon SDK response is used as the `orderid` in `OrderData` for tracking within vn.py, initialized with `Status.SUBMITTING`.
- **Error Handling**: Any failures during mapping or SDK calls are logged, and an empty string is returned as the order ID to indicate failure to vn.py.

This mapping ensures that all stock order requests from vn.py are correctly translated to Fubon Neo API calls, adhering to the specifications of both systems.
