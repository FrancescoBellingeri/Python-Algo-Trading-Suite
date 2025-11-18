import pandas as pd
from datetime import datetime, time
from ib_insync import Stock, MarketOrder, StopOrder, LimitOrder
from src.logger import logger
from config import SYMBOL, EXCHANGE, CURRENCY, MAX_RISK_PER_TRADE

class ExecutionHandler:
    """Gestisce l'esecuzione degli ordini basata su Daily Range e predizione HMM."""
    
    def __init__(self, ib_connector, capital=25000):
        """
        Inizializza l'ExecutionHandler.
        
        Args:
            ib_connector: Connessione IB attiva
            capital: Capitale per il calcolo della size (default 25k)
        """
        self.ib = ib_connector.ib
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        self.capital = capital
        self.base_risk = MAX_RISK_PER_TRADE
        
        # Tracking
        self.current_position = None
        self.active_orders = []
        self.daily_range = None
        
        logger.info(f"ExecutionHandler inizializzato - Capitale: ${capital:,.0f}")
    
    def calculate_position_size(self, entry_price, stop_loss, risk_multiplier=1.0):
        """
        Calcola la size della posizione basata sul rischio.
        """
        # Rischio per share
        risk_per_share = abs(entry_price - stop_loss)
        
        if risk_per_share < 0.01:
            logger.warning("Rischio per share troppo basso")
            return 0
        
        # IMPORTANTE: Verifica che stop_loss sia ragionevole!
        logger.info(f"Entry: ${entry_price:.2f}, Stop: ${stop_loss:.2f}, Risk/share: ${risk_per_share:.2f}")
        
        # Calcolo size basato sul rischio (es. 1% di $25k = $250)
        risk_amount = self.capital * self.base_risk * risk_multiplier
        shares = int(risk_amount / risk_per_share)
        
        # AGGIUNGI LIMITI DI SICUREZZA!
        max_shares_by_capital = int(5000000 / entry_price)  # max 95% del capitale
        
        shares = min(shares, max_shares_by_capital)
        
        logger.info(f"Position sizing:")
        logger.info(f"  Capitale: ${self.capital:,.0f}")
        logger.info(f"  Risk amount: ${risk_amount:.0f}")
        logger.info(f"  Risk/share: ${risk_per_share:.2f}")
        logger.info(f"  Shares calcolate: {shares}")
        logger.info(f"  Valore posizione: ${shares * entry_price:,.0f}")
        
        # CONTROLLO DI SICUREZZA
        if shares * entry_price > 5000000:
            logger.error(f"ERRORE: Posizione ${shares * entry_price:,.0f} > Capitale ${self.capital:,.0f}")
            return 0
        
        return shares
    
    def analyze_first_candle(self, first_candle):
        """
        Analizza la prima candela 5 min per determinare Daily Range e direzione.
        
        Args:
            first_candle: Dict con dati OHLCV della prima candela
            
        Returns:
            dict con daily range e direzione
        """
        # Verifica candela Doji
        if first_candle['open'] == first_candle['close']:
            logger.warning("Prima candela è Doji - No trade")
            return None
        
        # Determina direzione
        candle_direction = 'bullish' if first_candle['close'] > first_candle['open'] else 'bearish'
        
        # Calcola Daily Range
        self.daily_range = {
            'high': first_candle['high'],
            'low': first_candle['low'],
            'size': first_candle['high'] - first_candle['low'],
            'direction': candle_direction
        }
        
        logger.info(f"Daily Range: High={self.daily_range['high']:.2f}, Low={self.daily_range['low']:.2f}")
        logger.info(f"Prima candela: {candle_direction}")
        
        return self.daily_range
    
    def execute_strategy(self, second_candle, prediction, atr_value):
        """
        Esegue la strategia basata su candela, predizione e ATR.
        
        Args:
            second_candle: Dict con dati della seconda candela 5 min
            prediction: Predizione HMM ('BULL' o 'BEAR')
            atr_value: Valore ATRr_14 per Mean Reversion
            
        Returns:
            bool: True se l'ordine è stato piazzato
        """
        if not self.daily_range:
            logger.error("Daily Range non calcolato")
            return False
        
        if self.has_position():
            logger.warning("Posizione già aperta")
            return False
        
        # Determina il tipo di trade
        trade_type = None
        entry_price = second_candle['open']
        stop_loss = None
        risk_multiplier = 1.0
        
        # LONG: candela bullish + stato BULL
        if self.daily_range['direction'] == 'bullish' and prediction == 'BULL':
            trade_type = 'LONG'
            stop_loss = self.daily_range['low']
            risk_multiplier = 1.0
            
        # SHORT: candela bearish + stato BEAR
        elif self.daily_range['direction'] == 'bearish' and prediction == 'BEAR':
            trade_type = 'SHORT'
            stop_loss = self.daily_range['high']
            risk_multiplier = 1.0
        
        else:
            logger.info("Nessun setup valido per oggi")
            return False
        
        # Calcola position size
        shares = self.calculate_position_size(entry_price, stop_loss, risk_multiplier)
        
        if shares <= 0:
            logger.warning("Position size = 0, nessun trade")
            return False
        
        # Piazza l'ordine
        return self._place_order(trade_type, entry_price, stop_loss, shares)
    
    def _place_order(self, trade_type, entry_price, stop_loss, shares):
        """
        Piazza l'ordine con stop loss e take profit.
        
        Args:
            trade_type: 'LONG', 'SHORT'
            entry_price: Prezzo di ingresso
            stop_loss: Prezzo di stop loss
            shares: Numero di azioni
            
        Returns:
            bool: True se successo
        """
        try:
            # Calcola take profit (10R)
            risk_per_share = abs(entry_price - stop_loss)
            
            if trade_type == 'LONG':
                action = 'BUY'
                take_profit = entry_price + (10 * risk_per_share)
                sl_action = 'SELL'
            else:  # SHORT
                action = 'SELL'
                take_profit = entry_price - (10 * risk_per_share)
                sl_action = 'BUY'
            
            logger.info(f"Piazzamento ordine {trade_type}:")
            logger.info(f"- Entry: ${entry_price:.2f}")
            logger.info(f"- Stop Loss: ${stop_loss:.2f}")
            logger.info(f"- Take Profit: ${take_profit:.2f} (10R)")
            logger.info(f"- Shares: {shares}")
            
            # Ordine di ingresso (market order alla seconda candela)
            entry_order = MarketOrder(action, shares)
            entry_order.orderRef = f'{trade_type}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            
            # Piazza l'ordine di ingresso
            entry_trade = self.ib.placeOrder(self.contract, entry_order)
            self.active_orders.append(entry_trade)
            
            # Prepara SL e TP
            stop_loss_order = StopOrder(sl_action, shares, stop_loss)
            stop_loss_order.orderRef = f'SL_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            
            take_profit_order = LimitOrder(sl_action, shares, take_profit)
            take_profit_order.orderRef = f'TP_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            
            # Callback per quando l'entry viene riempito
            entry_trade.fillEvent += lambda trade, fill: self._on_entry_filled(
                trade, fill, stop_loss_order, take_profit_order, trade_type
            )
            
            logger.info(f"Ordine {trade_type} piazzato - ID: {entry_trade.order.orderId}")
            return True
            
        except Exception as e:
            logger.error(f"Errore nel piazzamento ordine: {e}")
            return False
    
    def _on_entry_filled(self, entry_trade, fill, stop_loss_order, take_profit_order, trade_type):
        """
        Callback quando l'ordine di ingresso viene eseguito.
        """
        logger.info(f"Entry filled! Type: {trade_type}, Price: {fill.avgPrice}, Shares: {fill.shares}")
        
        # Aggiorna tracking
        self.current_position = {
            'type': trade_type,
            'shares': fill.execution.shares,
            'entry_price': fill.execution.avgPrice,
            'entry_time': datetime.now()
        }
        
        # Piazza SL e TP come bracket order
        sl_trade = self.ib.placeOrder(self.contract, stop_loss_order)
        tp_trade = self.ib.placeOrder(self.contract, take_profit_order)
        
        self.active_orders.extend([sl_trade, tp_trade])
        
        # OCA (One-Cancels-All) per SL e TP
        self.ib.oneCancelsAll([sl_trade.order, tp_trade.order], "OCA_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        
        logger.info("Stop Loss e Take Profit piazzati con OCA")
    
    def close_all_positions(self):
        """Chiude tutte le posizioni a fine giornata."""
        try:
            positions = self.ib.positions()
            
            for position in positions:
                if position.contract.symbol == SYMBOL and position.position != 0:
                    shares = abs(position.position)
                    action = 'SELL' if position.position > 0 else 'BUY'
                    
                    logger.info(f"EOD Close: {action} {shares} {SYMBOL}")
                    
                    order = MarketOrder(action, shares)
                    order.orderRef = f'EOD_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                    
                    trade = self.ib.placeOrder(position.contract, order)
                    logger.info(f"Ordine EOD inviato - ID: {trade.order.orderId}")
            
            # Cancella ordini pendenti
            self.cancel_all_orders()
            
            # Reset
            self.current_position = None
            self.daily_range = None
            
        except Exception as e:
            logger.error(f"Errore nella chiusura EOD: {e}")
    
    def cancel_all_orders(self):
        """Cancella tutti gli ordini pendenti."""
        try:
            open_orders = self.ib.openOrders()
            
            for order in open_orders:
                if order.orderRef and any(x in order.orderRef for x in ['LONG', 'SHORT', 'MEAN_REVERSION', 'SL', 'TP']):
                    self.ib.cancelOrder(order)
                    logger.info(f"Ordine cancellato: {order.orderRef}")
                    
        except Exception as e:
            logger.error(f"Errore nella cancellazione ordini: {e}")
    
    def has_position(self):
        """Verifica se abbiamo una posizione aperta."""
        positions = self.ib.positions()
        
        for position in positions:
            if position.contract.symbol == SYMBOL and position.position != 0:
                return True
        
        return False
    
    def get_current_pnl(self):
        """Ottiene il P&L corrente della posizione aperta."""
        portfolio = self.ib.portfolio()
        
        for item in portfolio:
            if item.contract.symbol == SYMBOL:
                return item.unrealizedPnL
        
        return 0.0
    
    def get_position_info(self):
        """
        Ottiene informazioni dettagliate sulla posizione corrente.
        
        Returns:
            dict con info sulla posizione o None
        """
        positions = self.ib.positions()
        
        for position in positions:
            if position.contract.symbol == SYMBOL and position.position != 0:
                portfolio = self.ib.portfolio()
                
                for item in portfolio:
                    if item.contract.symbol == SYMBOL:
                        return {
                            'shares': position.position,
                            'avg_cost': position.avgCost,
                            'market_value': item.marketValue,
                            'unrealized_pnl': item.unrealizedPnL,
                            'realized_pnl': item.realizedPNL
                        }
        
        return None

    def update_capital(self):
        """
        Aggiorna self.capital recuperando il valore NetLiquidation dal conto IB.
        """
        try:
            # Aspetta che i dati del conto siano disponibili
            self.ib.reqAccountSummary()
            account_values = self.ib.accountValues()
            
            # Cerca il valore 'NetLiquidation' per la valuta base del conto (es. USD)
            net_liquidation_value = None
            for value in account_values:
                if value.tag == 'NetLiquidation' and value.currency == 'EUR': # Assicurati che la valuta sia corretta
                    net_liquidation_value = float(value.value)
                    break
            
            if net_liquidation_value is not None:
                self.capital = net_liquidation_value
                logger.info(f"Capitale aggiornato con successo: ${self.capital:,.2f}")
                return True
            else:
                logger.error("Impossibile trovare il valore 'NetLiquidation' nei dati del conto.")
                # Fallback: non aggiornare e usare il valore precedente
                return False

        except Exception as e:
            logger.error(f"Errore durante l'aggiornamento del capitale: {e}")
            return False