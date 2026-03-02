import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from ib_insync import IB, Contract, Order, Trade, Position
from src.redis_publisher import redis_publisher
import config

logger = logging.getLogger(__name__)

class IBDashboardHandler:
    """Handler to send IB data to dashboard via Redis/WebSocket"""
    
    def __init__(self, ib: IB):
        self.ib = ib
        self.publisher = redis_publisher
        self.last_account_update = datetime.now()
        self.update_interval = config.WEBSOCKET_UPDATE_INTERVAL
        
        # Setup event handlers
        self._setup_event_handlers()
        
        # Send initial state
        self._send_initial_state()
        
        logger.info("IBDashboardHandler initialized")
    
    def _setup_event_handlers(self):
        """Configures IB event handlers"""
        # Account updates
        self.ib.accountValueEvent += self.on_account_value
        self.ib.accountSummaryEvent += self.on_account_summary
        
        # Position updates
        self.ib.positionEvent += self.on_position
        
        # Order updates
        self.ib.orderStatusEvent += self.on_order_status
        self.ib.execDetailsEvent += self.on_exec_details
        
        # PnL updates
        self.ib.pnlEvent += self.on_pnl
        self.ib.pnlSingleEvent += self.on_pnl_single
        
        # Error handling
        self.ib.errorEvent += self.on_error
        
        # Connection events
        self.ib.connectedEvent += self.on_connected
        self.ib.disconnectedEvent += self.on_disconnected
        
    def _send_initial_state(self):
        """Sends initial state to dashboard"""
        try:
            # Account info
            account_values = self.ib.accountValues()
            self._process_account_values(account_values)
            
            # Positions
            positions = self.ib.positions()
            self._process_positions(positions)
            
            # Open orders
            trades = self.ib.trades()
            self._process_trades(trades)
            
            self.publisher.log("info", "Initial state sent to dashboard")
            
        except Exception as e:
            logger.error(f"Error sending initial state: {e}")
            self.publisher.send_error(f"Failed to send initial state: {str(e)}")
    
    def on_account_value(self, value):
        """Handler for account value updates"""
        # Aggregates updates to avoid too many messages
        now = datetime.now()
        if (now - self.last_account_update).total_seconds() > self.update_interval:
            account_values = self.ib.accountValues()
            self._process_account_values(account_values)
            self.last_account_update = now
    
    def on_account_summary(self, value):
        """Handler for account summary"""
        # Similar to account value but less frequent
        pass
    
    def _process_account_values(self, account_values):
        """Processes and sends account values"""
        account_dict = {}
        for av in account_values:
            account_dict[av.tag] = av.value
        
        self.publisher.send_account_update(account_dict)
    
    def on_position(self, position: Position):
        """Handler for position updates"""
        positions = self.ib.positions()
        self._process_positions(positions)
    
    def _process_positions(self, positions: List[Position]):
        """Processes and sends positions"""
        pos_list = []
        for pos in positions:
            pos_dict = {
                "symbol": pos.contract.symbol,
                "conId": pos.contract.conId,
                "position": pos.position,
                "avgCost": pos.avgCost,
                "marketPrice": 0,  # Will be updated with market data
                "marketValue": 0,
                "unrealizedPNL": 0,
                "realizedPNL": 0,
            }
            
            # Request market data for this position
            if pos.position != 0:
                self._update_position_market_data(pos.contract, pos_dict)
            
            pos_list.append(pos_dict)
        
        self.publisher.send_position_update(pos_list)
    
    def _update_position_market_data(self, contract: Contract, pos_dict: dict):
        """Updates market data for a position"""
        try:
            # Request market data snapshot
            ticker = self.ib.reqMktData(contract, '', False, False)
            self.ib.sleep(0.5)  # Wait for data
            
            if ticker.marketPrice():
                market_price = ticker.marketPrice()
                pos_dict["marketPrice"] = market_price
                pos_dict["marketValue"] = market_price * pos_dict["position"]
                pos_dict["unrealizedPNL"] = (market_price - pos_dict["avgCost"]) * pos_dict["position"]
            
            # Cancel market data subscription
            self.ib.cancelMktData(contract)
            
        except Exception as e:
            logger.error(f"Error getting market data for {contract.symbol}: {e}")
    
    def on_order_status(self, trade: Trade):
        """Handler for order status"""
        order_dict = {
            "orderId": trade.order.orderId,
            "symbol": trade.contract.symbol,
            "action": trade.order.action,
            "totalQuantity": trade.order.totalQuantity,
            "orderType": trade.order.orderType,
            "lmtPrice": trade.order.lmtPrice if hasattr(trade.order, 'lmtPrice') else None,
            "status": trade.orderStatus.status,
            "filled": trade.orderStatus.filled,
            "remaining": trade.orderStatus.remaining,
            "avgFillPrice": trade.orderStatus.avgFillPrice,
            "lastFillTime": datetime.now().isoformat(),
        }
        
        self.publisher.send_order_update(order_dict)
        
        # Important log for orders
        if trade.orderStatus.status in ['Filled', 'Cancelled']:
            self.publisher.log(
                "info", 
                f"Order {trade.orderStatus.status}: {trade.contract.symbol} {trade.order.action} {trade.order.totalQuantity}",
                {"orderId": trade.order.orderId, "avgFillPrice": trade.orderStatus.avgFillPrice}
            )
    
    def _process_trades(self, trades: List[Trade]):
        """Processes active trades"""
        for trade in trades:
            if trade.orderStatus.status not in ['Filled', 'Cancelled', 'Inactive']:
                self.on_order_status(trade)
    
    def on_exec_details(self, trade: Trade, fill):
        """Handler for execution details"""
        exec_details = {
            "symbol": trade.contract.symbol,
            "action": fill.execution.side,
            "quantity": fill.execution.shares,
            "price": fill.execution.price,
            "commission": fill.commissionReport.commission if fill.commissionReport else 0,
            "time": fill.execution.time,
        }
        
        self.publisher.log(
            "success",
            f"Execution: {fill.execution.side} {fill.execution.shares} {trade.contract.symbol} @ {fill.execution.price}",
            exec_details
        )
    
    def on_pnl(self, pnl):
        """Handler for total account PnL"""
        if pnl:
            self.publisher.send_pnl_update(
                daily_pnl=float(pnl.dailyPnL) if pnl.dailyPnL else 0,
                unrealized_pnl=float(pnl.unrealizedPnL) if pnl.unrealizedPnL else 0,
                realized_pnl=float(pnl.realizedPnL) if pnl.realizedPnL else 0
            )
    
    def on_pnl_single(self, pnl):
        """Handler for single position PnL"""
        if pnl:
            pnl_data = {
                "conId": pnl.conId,
                "daily_pnl": float(pnl.dailyPnL) if pnl.dailyPnL else 0,
                "unrealized_pnl": float(pnl.unrealizedPnL) if pnl.unrealizedPnL else 0,
                "realized_pnl": float(pnl.realizedPnL) if pnl.realizedPnL else 0,
                "position": float(pnl.position) if pnl.position else 0,
                "value": float(pnl.value) if pnl.value else 0,
            }
            # You might want to handle PnL for single position
            logger.debug(f"PnL Single: {pnl_data}")
    
    def on_error(self, reqId, errorCode, errorString, contract):
        """Handler for IB errors"""
        # Ignore some common non-critical errors
        if errorCode in [2104, 2106, 2158]:  # Market data farm messages
            return
            
        error_msg = f"IB Error {errorCode}: {errorString}"
        
        if errorCode < 2000:  # Critical errors
            logger.error(error_msg)
            self.publisher.send_error(error_msg, errorCode, {"reqId": reqId, "contract": str(contract)})
            self.publisher.log("error", error_msg)
        else:  # Warning
            logger.warning(error_msg)
            if errorCode not in [2104, 2106, 2107, 2108]:  # Filter market data farm messages
                self.publisher.log("warning", error_msg)
    
    def on_connected(self):
        """Handler for connection established"""
        self.publisher.log("success", "Connected to Interactive Brokers")
        self._send_initial_state()
    
    def on_disconnected(self):
        """Handler for disconnection"""
        self.publisher.log("error", "Disconnected from Interactive Brokers")
        self.publisher.send_error("IB Connection lost")
    
    def send_trade_signal(self, signal_type: str, details: Dict[str, Any]):
        """Wrapper to send trading signals"""
        self.publisher.send_trade_signal(signal_type, details)
    
    def handle_dashboard_command(self, command: Dict[str, Any]):
        """Handles commands received from dashboard"""
        cmd_type = command.get("type")
        
        logger.info(f"Processing dashboard command: {cmd_type}")
        
        if cmd_type == "get_status":
            self._send_status_update()
        else:
            logger.warning(f"Unknown command: {cmd_type}")
    
    def _send_status_update(self):
        """Sends full status update"""
        self._send_initial_state()