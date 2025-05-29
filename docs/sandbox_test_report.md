# Sandbox Test Report for Fubon Gateway Stock Orders

This document serves as a template and report for testing the Fubon Gateway's stock order functionalities in the Fubon Neo API sandbox environment. It outlines the test procedure for validating the end-to-end flow of order placement, status updates, modification, and cancellation. The results, logs, and any issues encountered during testing should be recorded here.

## Test Overview

- **Date of Test**: [Insert Date]
- **Tester**: [Insert Name]
- **Environment**: Fubon Neo API Sandbox
- **Gateway Configuration**: `fubon_connect.json` with `simulation=True` (if applicable)
- **Objective**: Validate the complete stock order flow including placement, callback updates, modification, and cancellation through the Fubon Gateway integrated with vn.py.

## Test Prerequisites

- Ensure the Fubon Gateway is configured to connect to the sandbox environment. Check `fubon_connect.json` for correct settings (`simulation=True` if required by SDK).
- Verify that valid sandbox credentials (UserID, Password, CA path, CA password) are provided in the configuration.
- Confirm that the vn.py event engine and Fubon Gateway are running properly in a test setup.
- Have a test stock symbol (e.g., "2330.TWSE") available for trading in the sandbox environment.

## Test Procedure and Results

### Step 1: Connection to Sandbox Environment
- **Action**: Start the Fubon Gateway and connect to the sandbox environment using the provided credentials.
- **Expected Result**: Successful connection and login, with log message indicating "登入成功，找到 X 個股票帳戶，Y 個期貨帳戶".
- **Actual Result**: [Insert Result]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing connection success or failure]
- **Issues Encountered**: [Describe any connection issues, timeouts, or errors]

### Step 2: Place a Stock Order
- **Action**: Use the `send_order` method to place a limit order for a stock (e.g., symbol="2330.TWSE", direction=Direction.LONG, type=OrderType.LIMIT, price=[reasonable price], volume=100.0).
- **Expected Result**: Order is submitted successfully, returning a valid `orderid` (seq_no), and an `OrderData` object with `Status.SUBMITTING` is pushed via `on_order`.
- **Actual Result**: [Insert Result, including orderid if successful]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing order placement and initial status]
- **Issues Encountered**: [Describe any failures in order submission or unexpected status]

### Step 3: Receive Order Confirmation via Callback
- **Action**: Wait for the `set_on_stock_order` callback to update the order status.
- **Expected Result**: Callback updates the `OrderData` status (e.g., to `Status.NOTTRADED` or other relevant status), and the update is pushed via `on_order`.
- **Actual Result**: [Insert Result, including updated status]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing callback receipt and status update]
- **Issues Encountered**: [Describe any delays or missing callbacks, or incorrect status mapping]

### Step 4: Modify Order Price
- **Action**: Use the `modify_order_price` method to change the price of the placed order (e.g., new_price=[original price + small increment]).
- **Expected Result**: Price modification is successful, returning `True`, and the updated `OrderData` with the new price is pushed via `on_order`.
- **Actual Result**: [Insert Result, including new price if successful]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing price modification]
- **Issues Encountered**: [Describe any failures in price modification or lack of update]

### Step 5: Modify Order Quantity
- **Action**: Use the `modify_order_quantity` method to change the quantity of the placed order (e.g., new_quantity=[original quantity + small increment]).
- **Expected Result**: Quantity modification is successful, returning `True`, and the updated `OrderData` with the new quantity is pushed via `on_order`.
- **Actual Result**: [Insert Result, including new quantity if successful]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing quantity modification]
- **Issues Encountered**: [Describe any failures in quantity modification or lack of update]

### Step 6: Cancel the Order
- **Action**: Use the `cancel_order` method to cancel the placed order.
- **Expected Result**: Order cancellation is successful, and the `OrderData` status is updated to `Status.CANCELLED`, pushed via `on_order`.
- **Actual Result**: [Insert Result, including final status]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing cancellation request and status update]
- **Issues Encountered**: [Describe any failures in cancellation or unexpected status]

### Step 7: Receive Trade Fill (if applicable)
- **Action**: If the order was partially or fully filled before cancellation, wait for the `set_on_stock_filled` callback.
- **Expected Result**: Callback creates a `TradeData` object with trade details (price, volume), pushed via `on_trade`, and updates `OrderData` status to `Status.PARTTRADED` or `Status.ALLTRADED`.
- **Actual Result**: [Insert Result, including trade details if received]
- **Logs/Screenshots**: [Insert or reference logs/screenshots showing trade fill if applicable]
- **Issues Encountered**: [Describe any missing or incorrect trade data]

## Summary of Test Results

- **Overall Outcome**: [Pass/Fail - Insert overall result of the test flow]
- **Key Findings**: [Summarize key successes or failures in the test flow]
- **Boundary Issues**: [List any boundary issues encountered, such as timeouts, unexpected status codes, or SDK errors]
- **Recommendations**: [Suggest fixes or improvements based on issues encountered, e.g., retry logic for timeouts, better error handling]

## Appendices

- **Full Logs**: [Attach or reference complete logs from the test run]
- **Screenshots**: [Attach or reference screenshots capturing key steps or errors]
- **Configuration Used**: [Detail the `fubon_connect.json` settings used for the test, omitting sensitive data like passwords]

This report template should be completed with actual test results and committed to the repository as part of the Week 3 deliverables for the Fubon Gateway integration with vn.py.
