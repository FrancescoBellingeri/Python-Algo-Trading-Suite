import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from ib_insync import IB, Contract, Order, Trade, Position
from src.redis_publisher import redis_publisher
import config

logger = logging.getLogger(__name__)

class IBDashboardHandler:
    """Handler per inviare dati IB alla dashboard via Redis/WebSocket"""
    
    def __init__(self, ib: IB):
        self.ib = ib
        self.publisher = redis_publisher
        self.last_account_update = datetime.now()
        self.update_interval = config.WEBSOCKET_UPDATE_INTERVAL
        
        # Setup event handlers
        self._setup_event_handlers()
        
        # Invia stato iniziale
        self._send_initial_state()
        
        logger.info("IBDashboardHandler initialized")
    
    def _setup_event_handlers(self):
        """Configura gli event handler di IB"""
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
        """Invia stato iniziale alla dashboard"""
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
        """Handler per aggiornamenti account value"""
        # Aggrega gli updates per evitare troppi messaggi
        now = datetime.now()
        if (now - self.last_account_update).total_seconds() > self.update_interval:
            account_values = self.ib.accountValues()
            self._process_account_values(account_values)
            self.last_account_update = now
    
    def on_account_summary(self, value):
        """Handler per account summary"""
        # Simile a account value ma meno frequente
        pass
    
    def _process_account_values(self, account_values):
        """Processa e invia account values"""
        account_dict = {}
        for av in account_values:
            account_dict[av.tag] = av.value
        
        self.publisher.send_account_update(account_dict)
    
    def on_position(self, position: Position):
        """Handler per aggiornamenti posizione"""
        positions = self.ib.positions()
        self._process_positions(positions)
    
    def _process_positions(self, positions: List[Position]):
        """Processa e invia posizioni"""
        pos_list = []
        for pos in positions:
            pos_dict = {
                "symbol": pos.contract.symbol,
                "conId": pos.contract.conId,
                "position": pos.position,
                "avgCost": pos.avgCost,
                "marketPrice": 0,  # Verrà aggiornato con market data
                "marketValue": 0,
                "unrealizedPNL": 0,
                "realizedPNL": 0,
            }
            
            # Richiedi market data per questa posizione
            if pos.position != 0:
                self._update_position_market_data(pos.contract, pos_dict)
            
            pos_list.append(pos_dict)
        
        self.publisher.send_position_update(pos_list)
    
    def _update_position_market_data(self, contract: Contract, pos_dict: dict):
        """Aggiorna market data per una posizione"""
        try:
            # Richiedi snapshot di market data
            ticker = self.ib.reqMktData(contract, '', False, False)
            self.ib.sleep(0.5)  # Attendi dati
            
            if ticker.marketPrice():
                market_price = ticker.marketPrice()
                pos_dict["marketPrice"] = market_price
                pos_dict["marketValue"] = market_price * pos_dict["position"]
                pos_dict["unrealizedPNL"] = (market_price - pos_dict["avgCost"]) * pos_dict["position"]
            
            # Cancella market data subscription
            self.ib.cancelMktData(contract)
            
        except Exception as e:
            logger.error(f"Error getting market data for {contract.symbol}: {e}")
    
    def on_order_status(self, trade: Trade):
        """Handler per status ordini"""
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
        
        # Log importante per ordini
        if trade.orderStatus.status in ['Filled', 'Cancelled']:
            self.publisher.log(
                "info", 
                f"Order {trade.orderStatus.status}: {trade.contract.symbol} {trade.order.action} {trade.order.totalQuantity}",
                {"orderId": trade.order.orderId, "avgFillPrice": trade.orderStatus.avgFillPrice}
            )
    
    def _process_trades(self, trades: List[Trade]):
        """Processa trades attivi"""
        for trade in trades:
            if trade.orderStatus.status not in ['Filled', 'Cancelled', 'Inactive']:
                self.on_order_status(trade)
    
    def on_exec_details(self, trade: Trade, fill):
        """Handler per dettagli esecuzione"""
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
        """Handler per PnL account totale"""
        if pnl:
            self.publisher.send_pnl_update(
                daily_pnl=float(pnl.dailyPnL) if pnl.dailyPnL else 0,
                unrealized_pnl=float(pnl.unrealizedPnL) if pnl.unrealizedPnL else 0,
                realized_pnl=float(pnl.realizedPnL) if pnl.realizedPnL else 0
            )
    
    def on_pnl_single(self, pnl):
        """Handler per PnL di singola posizione"""
        if pnl:
            pnl_data = {
                "conId": pnl.conId,
                "daily_pnl": float(pnl.dailyPnL) if pnl.dailyPnL else 0,
                "unrealized_pnl": float(pnl.unrealizedPnL) if pnl.unrealizedPnL else 0,
                "realized_pnl": float(pnl.realizedPnL) if pnl.realizedPnL else 0,
                "position": float(pnl.position) if pnl.position else 0,
                "value": float(pnl.value) if pnl.value else 0,
            }
            # Potresti voler gestire PnL per singola posizione
            logger.debug(f"PnL Single: {pnl_data}")
    
    def on_error(self, reqId, errorCode, errorString, contract):
        """Handler per errori IB"""
        # Ignora alcuni errori comuni non critici
        if errorCode in [2104, 2106, 2158]:  # Market data farm messages
            return
            
        error_msg = f"IB Error {errorCode}: {errorString}"
        
        if errorCode < 2000:  # Errori critici
            logger.error(error_msg)
            self.publisher.send_error(error_msg, errorCode, {"reqId": reqId, "contract": str(contract)})
            self.publisher.log("error", error_msg)
        else:  # Warning
            logger.warning(error_msg)
            if errorCode not in [2104, 2106, 2107, 2108]:  # Filtra messaggi di market data farm
                self.publisher.log("warning", error_msg)
    
    def on_connected(self):
        """Handler per connessione stabilita"""
        self.publisher.log("success", "Connected to Interactive Brokers")
        self._send_initial_state()
    
    def on_disconnected(self):
        """Handler per disconnessione"""
        self.publisher.log("error", "Disconnected from Interactive Brokers")
        self.publisher.send_error("IB Connection lost")
    
    def send_trade_signal(self, signal_type: str, details: Dict[str, Any]):
        """Wrapper per inviare segnali di trading"""
        self.publisher.send_trade_signal(signal_type, details)
    
    def handle_dashboard_command(self, command: Dict[str, Any]):
        """Gestisce comandi ricevuti dalla dashboard"""
        cmd_type = command.get("type")
        payload = command.get("payload", {})
        
        logger.info(f"Processing dashboard command: {cmd_type}")
        
        if cmd_type == "close_all_positions":
            self._close_all_positions()
        elif cmd_type == "cancel_all_orders":
            self._cancel_all_orders()
        elif cmd_type == "get_status":
            self._send_status_update()
        elif cmd_type == "set_risk_limit":
            self._update_risk_limit(payload)
        else:
            logger.warning(f"Unknown command: {cmd_type}")
    
    def _close_all_positions(self):
        """Chiude tutte le posizioni aperte"""
        positions = self.ib.positions()
        for pos in positions:
            if pos.position != 0:
                order = Order()
                order.action = 'SELL' if pos.position > 0 else 'BUY'
                order.totalQuantity = abs(pos.position)
                order.orderType = 'MKT'
                
                trade = self.ib.placeOrder(pos.contract, order)
                self.publisher.log("warning", f"Closing position: {pos.contract.symbol} {pos.position} shares")
    
    def _cancel_all_orders(self):
        """Cancella tutti gli ordini aperti"""
        self.ib.reqGlobalCancel()
        self.publisher.log("warning", "All open orders cancelled by dashboard command")
    
    def _send_status_update(self):
        """Invia update completo dello status"""
        self._send_initial_state()
    
    def _update_risk_limit(self, payload: Dict[str, Any]):
        """Aggiorna limiti di rischio"""
        # Implementa la logica per aggiornare i limiti di rischio
        new_limit = payload.get("max_risk")
        if new_limit:
            config.MAX_RISK_PER_TRADE = new_limit
            self.publisher.log("info", f"Risk limit updated to {new_limit}")