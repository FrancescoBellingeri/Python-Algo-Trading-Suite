import pandas as pd
from ib_insync import Stock, MarketOrder, StopOrder
from src.logger import logger
from src.redis_publisher import redis_publisher
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
        # Invia info iniziali alla dashboard
        redis_publisher.log("info", f"💰 ExecutionHandler inizializzato - Capitale: ${capital:,.0f}")
        redis_publisher.publish("execution-config", {
            "symbol": SYMBOL,
            "capital": capital,
            "risk_per_trade": self.base_risk,
            "atr_multiplier": self.atr_multiplier,
            "leverage": 4
        })
    
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
            redis_publisher.log("warning", "⚠️ Segnale entry ignorato - posizione già aperta")
            return False
        
        last_candle = df.iloc[-1]
        if last_candle['WILLR_10'] < -80 and last_candle['close'] > last_candle['SMA_200']:
            entry_price = last_candle['close']
            
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossibile eseguire il trade")
                redis_publisher.send_error("ATR invalido, trade annullato")
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
                redis_publisher.log("warning", "⚠️ Position size = 0, trade annullato")
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
            redis_publisher.log("info", f"📈 Invio ordine: BUY {shares} shares @ MARKET, Stop Loss @ ${stop_price:.2f}")

            # 1. Ordine Genitore (Entry)
            parent = MarketOrder('BUY', shares)
            parent.transmit = False # <--- NON INVIARE ANCORA!
            
            # 2. Ordine Figlio (Stop Loss)
            stop_loss = StopOrder('SELL', shares, stop_price)
            stop_loss.transmit = True # <--- Questo invierà tutto il pacchetto
            
            parent_trade = self.ib.placeOrder(self.contract, parent)
            stop_loss.parentId = parent.orderId
            stop_trade = self.ib.placeOrder(self.contract, stop_loss)
            
            logger.info(f"Ordini inviati. Parent ID: {parent.orderId}, Stop ParentId: {stop_loss.parentId}")
            redis_publisher.log("info", f"📤 Ordini inviati - Parent ID: {parent.orderId}")

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
                # Invia conferma esecuzione alla dashboard
                redis_publisher.log("success", f"✅ POSIZIONE APERTA: {shares} shares @ ${fill_price:.2f}")
                redis_publisher.publish("order-filled", {
                    "type": "entry",
                    "side": "BUY",
                    "shares": shares,
                    "fill_price": fill_price,
                    "stop_price": stop_price,
                    "order_id": parent.orderId
                })
                
                redis_publisher.publish("position-opened", {
                    "symbol": SYMBOL,
                    "shares": shares,
                    "entry_price": fill_price,
                    "stop_price": stop_price,
                    "risk": (fill_price - stop_price) * shares,
                    "timestamp": pd.Timestamp.now().isoformat()
                })

                return True
            else:
                logger.warning(f"Ordine Entry non immediato: {parent_trade.orderStatus.status}")
                redis_publisher.log("warning", f"⚠️ Ordine non fillato: {parent_trade.orderStatus.status}")
                redis_publisher.publish("order-placement", {
                    "status": "failed",
                    "reason": parent_trade.orderStatus.status
                })
                return False

        except Exception as e:
            logger.error(f"Errore Bracket Order: {e}")
            redis_publisher.send_error(f"Errore apertura posizione: {str(e)}")
            redis_publisher.publish("order-placement", {
                "status": "error",
                "error": str(e)
            })
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
                redis_publisher.log("warning", "⚠️ Stop loss non trovato in memoria, sync in corso...")
                self.sync_position_state()
                return False
            
            last_candle = df.iloc[-1]
            atr_value = last_candle['ATR_14']

            if atr_value <= 0:
                logger.error("ATR < 0, impossibile aggiornare lo stop loss")
                redis_publisher.send_error("ATR < 0, impossibile aggiornare lo stop loss")
                return False
            
            risk_per_share = atr_value * self.atr_multiplier
            # Imposta lo stop loss iniziale
            new_stop_price = round(last_candle['close'] - risk_per_share, 2)
            
            if new_stop_price <= self.stop_price:
                logger.debug(f"Nuovo stop ${new_stop_price:.2f} non migliore dell'attuale ${self.stop_price:.2f}")
                redis_publisher.log("success", f"Nuovo stop ${new_stop_price:.2f} non migliore dell'attuale ${self.stop_price:.2f}")
                return False
            
            self.current_stop_order.auxPrice = new_stop_price
        
            # Riapplicare l'ordine aggiorna quello esistente
            trade = self.ib.placeOrder(self.contract, self.current_stop_order)
            
            # Aggiorna i riferimenti
            old_stop = self.stop_price
            self.stop_price = new_stop_price
            
            logger.info(f"📈 Stop Loss aggiornato: ${old_stop:.2f} → ${new_stop_price:.2f}")
            redis_publisher.log("success", f"📈 TRAILING STOP: ${old_stop:.2f} → ${new_stop_price:.2f} (+${new_stop_price - old_stop:.2f})")
            
            return True
            
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dello stop loss: {e}")
            redis_publisher.send_error(f"Errore aggiornamento stop: {str(e)}")
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
                redis_publisher.log("info", "❌ Stop loss cancellato")
            
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

                # Invia risultato trade alla dashboard
                redis_publisher.log("success", f"✅ POSIZIONE CHIUSA @ ${exit_price:.2f} - P&L: ${pnl:.2f})")
                
                redis_publisher.publish("position-closed", {
                    "symbol": SYMBOL,
                    "shares": self.position_size,
                    "entry_price": self.entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "result": "WIN" if pnl > 0 else "LOSS"
                })
                
                redis_publisher.publish("trade-result", {
                    "pnl": pnl,
                    "entry": self.entry_price,
                    "exit": exit_price,
                    "shares": self.position_size,
                    "duration_minutes": 0  # Potresti calcolare la durata reale
                })
                
                # Reset tracking
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.stop_price = None
                self.position_size = 0
                
                return True
            
            logger.error(f"Chiusura fallita: {trade.orderStatus.status}")
            redis_publisher.send_error(f"Chiusura posizione fallita: {trade.orderStatus.status}")

            return False
            
        except Exception as e:
            logger.error(f"Errore nella chiusura posizione: {e}")
            redis_publisher.send_error(f"Errore chiusura: {str(e)}")
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
                pnl = item.unrealizedPnL
                
                # Invia P&L corrente
                redis_publisher.publish("current-pnl", {
                    "symbol": SYMBOL,
                    "unrealized_pnl": pnl,
                    "position_size": self.position_size,
                    "entry_price": self.entry_price
                })
                
                return pnl
        
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
                        info = {
                            'shares': position.position,
                            'avg_cost': position.avgCost,
                            'market_value': item.marketValue,
                            'unrealized_pnl': item.unrealizedPnL,
                            'realized_pnl': item.realizedPNL
                        }
                        
                        # Invia info posizione
                        redis_publisher.publish("position-info", info)
                        
                        return info
        
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
                old_capital = self.capital
                self.capital = net_liquidation_value
                logger.info(f"Capitale aggiornato con successo: ${self.capital:,.2f}")
                redis_publisher.log("success", f"✅ Capitale aggiornato: ${self.capital:,.2f} (variazione: ${self.capital - old_capital:+,.2f})")

                redis_publisher.publish("capital-update", {
                    "old_capital": old_capital,
                    "new_capital": self.capital,
                    "change": self.capital - old_capital,
                    "currency": "EUR"
                })

                return True
            else:
                logger.error("Impossibile trovare il valore 'NetLiquidation' nei dati del conto.")
                redis_publisher.log("error", "❌ NetLiquidation non trovato nei dati del conto")
                redis_publisher.send_error("Impossibile aggiornare capitale: NetLiquidation non trovato")
                return False

        except Exception as e:
            logger.error(f"Errore durante l'aggiornamento del capitale: {e}")
            redis_publisher.send_error(f"Errore aggiornamento capitale: {str(e)}")
            return False
        
    def sync_position_state(self):
        """
        Sincronizza lo stato locale con quello di IB all'avvio.
        """
        try:
            logger.info("🔄 Sincronizzazione stato posizioni...")
            redis_publisher.log("info", "🔄 Sincronizzazione posizioni con IB...")
            
            redis_publisher.publish("sync-status", {
                "status": "syncing",
                "timestamp": pd.Timestamp.now().isoformat()
            })
            
            # 1. Trova posizione
            positions = self.ib.positions()
            target_pos = None
            for p in positions:
                if p.contract.symbol == SYMBOL and p.position > 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("Nessuna posizione aperta su IB.")
                redis_publisher.log("info", "✅ Nessuna posizione aperta rilevata")
                
                redis_publisher.publish("sync-status", {
                    "status": "completed",
                    "has_position": False
                })

                self.current_position = None
                self.position_size = 0
                return None
            
            # 2. Aggiorna stato
            self.position_size = target_pos.position
            self.entry_price = target_pos.avgCost
            logger.info(f"Trovata posizione: {self.position_size} shares @ avg ${self.entry_price:.2f}")
            redis_publisher.log("warning", f"⚠️ POSIZIONE ESISTENTE: {self.position_size} shares @ ${self.entry_price:.2f}")
            
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
                    redis_publisher.log("success", f"✅ Stop Loss attivo trovato @ ${o.auxPrice:.2f}")

                    found_stop = True
                    break
            
            if not found_stop:
                logger.warning("⚠️ ATTENZIONE: Posizione aperta SENZA Stop Loss rilevato!")
                redis_publisher.log("error", "⚠️ ATTENZIONE: Posizione SENZA Stop Loss!")
                redis_publisher.send_error("Posizione aperta senza protezione Stop Loss")
                
                redis_publisher.publish("risk-alert", {
                    "type": "no_stop_loss",
                    "position_size": self.position_size,
                    "entry_price": self.entry_price,
                    "risk": "unlimited"
                })
            
            return {'shares': self.position_size}
            
        except Exception as e:
            logger.error(f"Errore sync: {e}")
            redis_publisher.send_error(f"Errore sincronizzazione: {str(e)}")
            
            redis_publisher.publish("sync-status", {
                "status": "error",
                "error": str(e)
            })
            
            return None
        
    def place_stop_loss(self, stop_price):
        """
        Piazza un ordine di Stop Loss standalone per una posizione esistente.
        Utile per ripristinare lo stop dopo la notte.
        """
        try:
            # Verifica che ci sia una posizione reale su IB
            positions = self.ib.positions()
            current_share_count = 0
            for p in positions:
                if p.contract.symbol == SYMBOL:
                    current_share_count = p.position
                    break
            
            if current_share_count == 0:
                logger.warning("Impossibile piazzare Stop Loss: Nessuna posizione aperta su IB.")
                return False

            # Aggiorna la size interna se necessario
            self.position_size = current_share_count

            logger.info(f"🛡️ Ripristino Stop Loss a ${stop_price:.2f} per {self.position_size} shares")
            redis_publisher.log("info", f"🛡️ Ripristino Stop Loss a ${stop_price:.2f}")

            # Crea l'ordine Stop
            stop_order = StopOrder('SELL', self.position_size, stop_price)
            
            # Invia ordine
            trade = self.ib.placeOrder(self.contract, stop_order)
            
            # Aggiorna stato interno
            self.current_stop_order = stop_order
            self.stop_price = stop_price
            
            # Log
            logger.info(f"Stop Loss ripristinato con successo. ID: {trade.order.orderId}")
            redis_publisher.log("success", f"✅ Stop Loss riattivato a ${stop_price:.2f}")
            
            redis_publisher.publish("order-placed", {
                "type": "stop_loss_restore",
                "price": stop_price,
                "shares": self.position_size
            })

            return True

        except Exception as e:
            logger.error(f"Errore nel piazzare Stop Loss: {e}")
            redis_publisher.send_error(f"Errore ripristino Stop Loss: {str(e)}")
            return False
        
    def close_all_positions(self):
        """
        CHIUSURA DI EMERGENZA.
        Cancella tutti gli ordini pendenti e chiude le posizioni a mercato.
        """
        logger.warning("🚨 ESECUZIONE CHIUSURA TOTALE (PANIC BUTTON) 🚨")
        redis_publisher.log("warning", "🚨 CHIUSURA TOTALE AVVIATA")
        
        try:
            # 1. Cancella tutti gli ordini aperti per questo simbolo
            open_orders = self.ib.openOrders()
            for order in open_orders:
                if order.contract.symbol == SYMBOL:
                    self.ib.cancelOrder(order)
            
            # Aspetta un attimo che le cancellazioni vengano processate
            self.ib.sleep(0.5)

            # 2. Ottieni la posizione reale attuale da IB
            positions = self.ib.positions()
            target_pos = None
            
            for p in positions:
                if p.contract.symbol == SYMBOL and p.position != 0:
                    target_pos = p
                    break
            
            if not target_pos:
                logger.info("Nessuna posizione trovata da chiudere.")
                redis_publisher.log("info", "Nessuna posizione da chiudere.")
                
                # Pulizia variabili interne per sicurezza
                self.current_position = None
                self.current_stop_order = None
                self.position_size = 0
                return True

            shares_to_close = abs(target_pos.position)
            action = 'SELL' if target_pos.position > 0 else 'BUY' # Gestisce anche short se servisse
            
            logger.info(f"Closing {shares_to_close} shares via Market Order...")
            
            # 3. Invia ordine Market
            close_order = MarketOrder(action, shares_to_close)
            trade = self.ib.placeOrder(self.contract, close_order)
            
            self.ib.waitOnUpdate(trade, timeout=10)
            
            if trade.orderStatus.status == 'Filled':
                fill_price = trade.orderStatus.avgFillPrice
                logger.info(f"✅ Posizione liquidata totalmente a ${fill_price:.2f}")
                redis_publisher.log("success", f"✅ LIQUIDAZIONE COMPLETATA @ ${fill_price:.2f}")
                
                # Reset totale stato interno
                self.current_position = None
                self.current_stop_order = None
                self.entry_price = None
                self.stop_price = None
                self.position_size = 0
                
                redis_publisher.publish("position-closed", {
                    "reason": "force_close",
                    "exit_price": fill_price,
                    "shares": shares_to_close
                })
                return True
            else:
                logger.error(f"Liquidazione non completata: {trade.orderStatus.status}")
                return False

        except Exception as e:
            logger.error(f"Errore critical close_all_positions: {e}")
            redis_publisher.send_error(f"Errore chiusura totale: {str(e)}")
            return False