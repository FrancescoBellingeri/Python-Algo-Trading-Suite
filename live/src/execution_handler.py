import pandas as pd
from ib_insync import Stock, MarketOrder, StopOrder
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
        self.current_stop_order = None
        self.entry_price = None
        self.stop_price = None
        self.position_size = 0

        self.atr_multiplier = 10
        
        logger.info(f"ExecutionHandler inizializzato - Capitale: ${capital:,.0f}")
    
    def calculate_position_size(self, entry_price, stop_loss, account_size, risk_per_trade_pct, leverage=4):
        """
        Calcola il numero di contratti (o azioni) da acquistare tenendo conto di:
        - rischio per trade in percentuale,
        - leva finanziaria,
        - perdita massima assoluta consentita in dollari.
        """

        # Rischio per contratto
        R = abs(entry_price - stop_loss)
        if R == 0 or R < 0.01:  # rischio minimo simbolico per evitare divisione per zero
            return 0

        risk_dollars = account_size * risk_per_trade_pct
        risk_based_size = risk_dollars / R
        leverage_based_size = (account_size * leverage) / entry_price
        position_size = int(min(risk_based_size, leverage_based_size))

        return position_size
    
    def check_entry_signals(self, df):
        """
        Esegue la strategia basata sull'ultima candela recuperata.
        
        Args:
            df: DataFrame con le informazioni per eseguire strategia
            
        Returns:
            bool: True se l'ordine è stato piazzato
        """
        
        if self.has_position():
            logger.warning("Posizione già aperta")
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] < -80 and last_candle['close'] > last_candle['SMA_200']:
            entry_price = last_candle['close']
            
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossibile eseguire il trade")
                return False
            
            risk_per_share = atr_value * self.atr_multiplier
            # Imposta lo stop loss iniziale
            trailing_stop_price = round(entry_price - risk_per_share, 2)
        
            shares = self.calculate_position_size(
                    entry_price=entry_price,
                    stop_loss=trailing_stop_price,
                    account_size=self.capital,
                    risk_per_trade_pct=self.base_risk,
                    leverage=4
                )
        
            if shares <= 0:
                logger.warning("Position size = 0, nessun trade")
                return False

            # Piazza l'ordine
            return self.open_long_position(shares, trailing_stop_price)

        return False
    
    def check_exit_signals(self, df):
        """
        Controlla se ci sono le condizioni per chiudere il trade.
        
        Args:
            df: DataFrame con le informazioni per eseguire strategia
            
        Returns:
            bool: True se il trade è stato chiuso 
        """
        
        if not self.has_position():
            logger.warning("Non ci sono posizioni aperte")
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] > -20 and last_candle['close'] < last_candle['SMA_200']:
            return self.close_position()

        return False
    
    def open_long_position(self, shares, stop_price):
        """
        Apre una posizione long usando un BRACKET ORDER (Parent + Child).
        """
        try:
            logger.info(f"📈 Invio Bracket Order: Buy {shares} @ MKT, Stop @ {stop_price}")

            # 1. Ordine Genitore (Entry)
            parent = MarketOrder('BUY', shares)
            parent.transmit = False # <--- NON INVIARE ANCORA!
            
            # 2. Ordine Figlio (Stop Loss)
            stop_loss = StopOrder('SELL', shares, stop_price)
            stop_loss.transmit = True # <--- Questo invierà tutto il pacchetto
            
            parent_trade = self.ib.placeOrder(parent)
            stop_loss.parentId = parent.orderId
            stop_trade = self.ib.placeOrder(stop_loss)
            
            logger.info(f"Ordini inviati. Parent ID: {parent.orderId}, Stop ParentId: {stop_loss.parentId}")

            # 5. Attendiamo conferma del FILL del genitore
            self.ib.waitOnUpdate(parent_trade, timeout=10)
            
            if parent_trade.orderStatus.status == 'Filled':
                fill_price = parent_trade.orderStatus.avgFillPrice
                self.entry_price = fill_price
                self.position_size = shares
                
                # Salviamo il riferimento all'ordine stop (che è già attivo sul server!)
                self.current_stop_order = stop_loss
                self.stop_price = stop_price
                self.current_position = parent_trade
                
                logger.info(f"✅ Bracket Eseguito. Entry: {fill_price}, Stop Attivo: {stop_price}")
                return True
            else:
                logger.warning(f"Ordine Entry non immediato: {parent_trade.orderStatus.status}")
                # In un bracket, se l'entry non è fillata, lo stop non si attiva. 
                # Possiamo lasciare correre o cancellare.
                return False

        except Exception as e:
            logger.error(f"Errore Bracket Order: {e}")
            return False

    def update_trailing_stop(self, df):
        """
        Aggiorna lo stop loss (trailing stop manuale).
        
        Args:
            new_stop_price: Nuovo prezzo di stop
            
        Returns:
            bool: True se aggiornato con successo
        """
        try:
            if not self.has_position():
                logger.warning("Nessuna posizione aperta")
                return False
            
            if not self.current_stop_order:
                logger.warning("Posizione aperta ma nessun ordine Stop Loss tracciato in memoria.")
                self.sync_position_state()
                return False
            
            last_candle = df.iloc[-1]
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossibile aggiornare lo stop loss")
                return False
            
            risk_per_share = atr_value * self.atr_multiplier
            # Imposta lo stop loss iniziale
            new_stop_price = round(last_candle['close'] - risk_per_share, 2)
            
            if new_stop_price <= self.stop_price:
                logger.debug(f"Nuovo stop ${new_stop_price:.2f} non migliore dell'attuale ${self.stop_price:.2f}")
                return False
            
            self.current_stop_order.auxPrice = new_stop_price
        
            # Riapplicare l'ordine aggiorna quello esistente
            trade = self.ib.placeOrder(self.contract, self.current_stop_order)
            
            # Aggiorna i riferimenti
            old_stop = self.stop_price
            self.stop_price = new_stop_price
            
            logger.info(f"📈 Stop Loss aggiornato: ${old_stop:.2f} → ${new_stop_price:.2f}")
            
            return True
            
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dello stop loss: {e}")
            return False
        
    def close_position(self):
        """Chiude la posizione corrente al mercato."""
        try:
            if not self.has_position():
                logger.warning("Nessuna posizione da chiudere")
                return False
            
            # Cancella lo stop loss
            if self.current_stop_order:
                self.ib.cancelOrder(self.current_stop_order)
            
            # Piazza ordine market di chiusura
            close_order = MarketOrder('SELL', self.position_size)
            trade = self.ib.placeOrder(self.contract, close_order)
            
            # Aspetta esecuzione
            self.ib.waitOnUpdate(trade, timeout=10)
            
            if trade.orderStatus.status == 'Filled':
                exit_price = trade.orderStatus.avgFillPrice
                pnl = (exit_price - self.entry_price) * self.position_size
                
                logger.info(f"✅ Posizione chiusa @ ${exit_price:.2f}")
                logger.info(f"💰 P&L: ${pnl:.2f} ({pnl/self.capital*100:.2f}%)")
                
                # Reset tracking
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.stop_price = None
                self.position_size = 0
                
                return True
            
            logger.error(f"Chiusura fallita: {trade.orderStatus.status}")

            return False
            
        except Exception as e:
            logger.error(f"Errore nella chiusura posizione: {e}")
            return False
        
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
        
    def sync_position_state(self):
        """
        Sincronizza lo stato locale con quello di IB all'avvio.
        """
        try:
            logger.info("🔄 Sincronizzazione stato posizioni...")
            
            # 1. Trova posizione
            positions = self.ib.positions()
            target_pos = None
            for p in positions:
                if p.contract.symbol == SYMBOL and p.position > 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("Nessuna posizione aperta su IB.")
                self.current_position = None
                self.position_size = 0
                return None
            
            # 2. Aggiorna stato
            self.position_size = target_pos.position
            self.entry_price = target_pos.avgCost
            logger.info(f"Trovata posizione: {self.position_size} shares @ avg ${self.entry_price:.2f}")
            
            # 3. Trova stop order attivo
            # ib.openOrders() ritorna tutti gli ordini aperti
            orders = self.ib.openOrders()
            found_stop = False
            for o in orders:
                if (o.contract.symbol == SYMBOL and 
                    o.orderType in ['STP', 'TRAIL'] and 
                    o.action == 'SELL'):
                    
                    self.current_stop_order = o
                    self.stop_price = o.auxPrice
                    logger.info(f"Trovato Stop Loss attivo: ID {o.orderId} @ ${o.auxPrice}")
                    found_stop = True
                    break
            
            if not found_stop:
                logger.warning("⚠️ ATTENZIONE: Posizione aperta SENZA Stop Loss rilevato!")
            
            return {'shares': self.position_size}
            
        except Exception as e:
            logger.error(f"Errore sync: {e}")
            return None