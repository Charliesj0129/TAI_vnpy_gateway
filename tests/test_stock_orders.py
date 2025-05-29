import unittest
from unittest.mock import patch, MagicMock
from vnpy.trader.object import OrderRequest, CancelRequest, OrderData, TradeData
from vnpy.trader.constant import Exchange, Direction, OrderType, Status
from fubon_neo.sdk import FubonSDK
from fubon_gateway import FubonGateway
from vnpy.event import EventEngine

class TestStockOrders(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.event_engine = EventEngine()
        self.gateway = FubonGateway(self.event_engine)
        self.gateway.connected = True
        self.gateway.logged_in = True
        self.gateway.sdk = MagicMock(spec=FubonSDK)
        
        # Mock stock accounts
        stock_account = MagicMock()
        stock_account.account = "STOCK_123"
        self.gateway.stock_accounts = [stock_account]
        
        # Mock contracts
        self.gateway.contracts = {
            "2330.TWSE": MagicMock(exchange=Exchange.TWSE)
        }
        
        # Mock methods to capture calls to on_order and on_trade
        self.gateway.on_order = MagicMock()
        self.gateway.on_trade = MagicMock()
        self.gateway.write_log = MagicMock()

    def test_send_order_success(self):
        """Test successful stock order placement."""
        req = OrderRequest(
            symbol="2330.TWSE",
            exchange=Exchange.TWSE,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            price=600.0,
            volume=100.0
        )
        
        # Mock SDK response
        mock_result = MagicMock()
        mock_result.seq_no = "ORD123"
        self.gateway.sdk.stock.place_order.return_value = mock_result
        
        # Call send_order
        order_id = self.gateway.send_order(req)
        
        # Assertions
        self.assertEqual(order_id, "ORD123")
        self.gateway.sdk.stock.place_order.assert_called_once()
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertIsInstance(call_args, OrderData)
        self.assertEqual(call_args.orderid, "ORD123")
        self.assertEqual(call_args.status, Status.SUBMITTING)
        self.assertEqual(call_args.price, 600.0)
        self.assertEqual(call_args.volume, 100.0)

    def test_send_order_failure_no_sdk(self):
        """Test stock order placement failure when SDK is not connected."""
        self.gateway.connected = False
        self.gateway.logged_in = False
        
        req = OrderRequest(
            symbol="2330.TWSE",
            exchange=Exchange.TWSE,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            price=600.0,
            volume=100.0
        )
        
        order_id = self.gateway.send_order(req)
        
        # Assertions
        self.assertEqual(order_id, "")
        self.gateway.write_log.assert_called_with("下單失敗：未連接或未登入")

    def test_send_order_failure_no_contract(self):
        """Test stock order placement failure when contract is not found."""
        req = OrderRequest(
            symbol="9999.TWSE",
            exchange=Exchange.TWSE,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            price=600.0,
            volume=100.0
        )
        
        order_id = self.gateway.send_order(req)
        
        # Assertions
        self.assertEqual(order_id, "")
        self.gateway.write_log.assert_called_with("下單失敗：未找到合約 9999.TWSE")

    def test_cancel_order_success(self):
        """Test successful cancellation of a stock order."""
        # First, simulate an order
        with self.gateway.order_map_lock:
            self.gateway.orders["ORD123"] = OrderData(
                symbol="2330.TWSE",
                exchange=Exchange.TWSE,
                orderid="ORD123",
                direction=Direction.LONG,
                price=600.0,
                volume=100.0,
                status=Status.SUBMITTING,
                datetime=None,
                gateway_name=self.gateway.gateway_name
            )
        
        req = CancelRequest(
            symbol="2330.TWSE",
            orderid="ORD123"
        )
        
        # Mock SDK response
        mock_result = MagicMock()
        mock_result.success = True
        self.gateway.sdk.stock.cancel_order.return_value = mock_result
        
        # Call cancel_order
        self.gateway.cancel_order(req)
        
        # Assertions
        self.gateway.sdk.stock.cancel_order.assert_called_once()
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertIsInstance(call_args, OrderData)
        self.assertEqual(call_args.orderid, "ORD123")
        self.assertEqual(call_args.status, Status.CANCELLED)
        self.gateway.write_log.assert_called_with("撤單成功：訂單 ORD123")

    def test_cancel_order_failure_not_found(self):
        """Test cancellation failure when order is not found."""
        req = CancelRequest(
            symbol="2330.TWSE",
            orderid="ORD999"
        )
        
        self.gateway.cancel_order(req)
        
        # Assertions
        self.gateway.write_log.assert_called_with("撤單失敗：未找到訂單 ORD999")
        self.gateway.sdk.stock.cancel_order.assert_not_called()

    def test_modify_order_price_success(self):
        """Test successful modification of order price."""
        # First, simulate an order
        with self.gateway.order_map_lock:
            self.gateway.orders["ORD123"] = OrderData(
                symbol="2330.TWSE",
                exchange=Exchange.TWSE,
                orderid="ORD123",
                direction=Direction.LONG,
                price=600.0,
                volume=100.0,
                status=Status.SUBMITTING,
                datetime=None,
                gateway_name=self.gateway.gateway_name
            )
        
        # Mock SDK response
        mock_result = MagicMock()
        mock_result.success = True
        self.gateway.sdk.stock.modify_order_price.return_value = mock_result
        
        # Call modify_order_price
        result = self.gateway.modify_order_price("2330.TWSE", "ORD123", 610.0)
        
        # Assertions
        self.assertTrue(result)
        self.gateway.sdk.stock.modify_order_price.assert_called_once_with(self.gateway.stock_accounts[0], "ORD123", 610.0)
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertIsInstance(call_args, OrderData)
        self.assertEqual(call_args.orderid, "ORD123")
        self.assertEqual(call_args.price, 610.0)
        self.gateway.write_log.assert_called_with("改價成功：訂單 ORD123，新價格 610.0")

    def test_modify_order_price_failure_not_found(self):
        """Test price modification failure when order is not found."""
        result = self.gateway.modify_order_price("2330.TWSE", "ORD999", 610.0)
        
        # Assertions
        self.assertFalse(result)
        self.gateway.write_log.assert_called_with("改價失敗：未找到訂單 ORD999")
        self.gateway.sdk.stock.modify_order_price.assert_not_called()

    def test_modify_order_quantity_success(self):
        """Test successful modification of order quantity."""
        # First, simulate an order
        with self.gateway.order_map_lock:
            self.gateway.orders["ORD123"] = OrderData(
                symbol="2330.TWSE",
                exchange=Exchange.TWSE,
                orderid="ORD123",
                direction=Direction.LONG,
                price=600.0,
                volume=100.0,
                status=Status.SUBMITTING,
                datetime=None,
                gateway_name=self.gateway.gateway_name
            )
        
        # Mock SDK response
        mock_result = MagicMock()
        mock_result.success = True
        self.gateway.sdk.stock.modify_order_quantity.return_value = mock_result
        
        # Call modify_order_quantity
        result = self.gateway.modify_order_quantity("2330.TWSE", "ORD123", 150.0)
        
        # Assertions
        self.assertTrue(result)
        self.gateway.sdk.stock.modify_order_quantity.assert_called_once_with(self.gateway.stock_accounts[0], "ORD123", 150)
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertIsInstance(call_args, OrderData)
        self.assertEqual(call_args.orderid, "ORD123")
        self.assertEqual(call_args.volume, 150.0)
        self.gateway.write_log.assert_called_with("改量成功：訂單 ORD123，新數量 150.0")

    def test_modify_order_quantity_failure_not_connected(self):
        """Test quantity modification failure when not connected."""
        self.gateway.connected = False
        self.gateway.logged_in = False
        
        result = self.gateway.modify_order_quantity("2330.TWSE", "ORD123", 150.0)
        
        # Assertions
        self.assertFalse(result)
        self.gateway.write_log.assert_called_with("改量失敗：未連接或未登入")
        self.gateway.sdk.stock.modify_order_quantity.assert_not_called()

    def test_on_stock_order_callback(self):
        """Test processing of stock order callback."""
        order_data = {
            "symbol": "2330.TWSE",
            "seq_no": "ORD123",
            "buy_sell": "Buy",
            "price": "600.0",
            "quantity": "100.0",
            "status": "Pending"
        }
        
        self.gateway._on_stock_order(order_data)
        
        # Assertions
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertIsInstance(call_args, OrderData)
        self.assertEqual(call_args.symbol, "2330.TWSE")
        self.assertEqual(call_args.orderid, "ORD123")
        self.assertEqual(call_args.direction, Direction.LONG)
        self.assertEqual(call_args.price, 600.0)
        self.assertEqual(call_args.volume, 100.0)
        self.assertEqual(call_args.status, Status.SUBMITTING)

    def test_on_stock_filled_callback(self):
        """Test processing of stock filled callback."""
        # First, simulate an order
        with self.gateway.order_map_lock:
            self.gateway.orders["ORD123"] = OrderData(
                symbol="2330.TWSE",
                exchange=Exchange.TWSE,
                orderid="ORD123",
                direction=Direction.LONG,
                price=600.0,
                volume=100.0,
                status=Status.SUBMITTING,
                datetime=None,
                gateway_name=self.gateway.gateway_name
            )
        
        fill_data = {
            "symbol": "2330.TWSE",
            "seq_no": "ORD123",
            "fill_id": "FILL456",
            "buy_sell": "Buy",
            "price": "600.5",
            "quantity": "100.0"
        }
        
        self.gateway._on_stock_filled(fill_data)
        
        # Assertions
        self.gateway.on_trade.assert_called_once()
        trade_args = self.gateway.on_trade.call_args[0][0]
        self.assertIsInstance(trade_args, TradeData)
        self.assertEqual(trade_args.symbol, "2330.TWSE")
        self.assertEqual(trade_args.orderid, "ORD123")
        self.assertEqual(trade_args.tradeid, "FILL456")
        self.assertEqual(trade_args.price, 600.5)
        self.assertEqual(trade_args.volume, 100.0)
        
        self.gateway.on_order.assert_called_once()
        order_args = self.gateway.on_order.call_args[0][0]
        self.assertEqual(order_args.status, Status.ALLTRADED)

    def test_on_stock_order_changed_callback(self):
        """Test processing of stock order changed callback."""
        # First, simulate an order
        with self.gateway.order_map_lock:
            self.gateway.orders["ORD123"] = OrderData(
                symbol="2330.TWSE",
                exchange=Exchange.TWSE,
                orderid="ORD123",
                direction=Direction.LONG,
                price=600.0,
                volume=100.0,
                status=Status.SUBMITTING,
                datetime=None,
                gateway_name=self.gateway.gateway_name
            )
        
        change_data = {
            "seq_no": "ORD123",
            "price": "610.0",
            "quantity": "150.0",
            "status": "PartFilled"
        }
        
        self.gateway._on_stock_order_changed(change_data)
        
        # Assertions
        self.gateway.on_order.assert_called_once()
        call_args = self.gateway.on_order.call_args[0][0]
        self.assertEqual(call_args.price, 610.0)
        self.assertEqual(call_args.volume, 150.0)
        self.assertEqual(call_args.status, Status.PARTTRADED)

if __name__ == '__main__':
    unittest.main()
