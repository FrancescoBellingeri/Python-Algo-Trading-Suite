from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import asyncio
import ib_insync
import os
import json
from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.indicator_calculator import IndicatorCalculator
from src.execution_handler import ExecutionHandler
from src.ib_dashboard_handler import IBDashboardHandler
from src.redis_publisher import redis_publisher
from src.logger import logger
import config

class TradingBot:
    """Bot di trading automatico che coordina tutti i moduli."""
    
    def __init__(self):
        """Inizializza il trading bot."""
        self.connector = None
        self.data_handler = None
        self.indicator_calculator = None
        self.execution = None

        self.account_id = None
        self.pnl_stream = None
        
        # Stato del bot
        self.is_running = True
        self.in_position = False
        self.last_signal_time = None
        self.bot_start_time = datetime.now()
        
        logger.info("Trading Bot inizializzato")

        # Invia stato iniziale alla dashboard
        if redis_publisher.enabled:
            redis_publisher.log("info", "🚀 Trading Bot inizializzato")
    
    async def initialize_components(self):
        """Inizializza tutti i componenti del sistema."""
        try:
            # Connetti a IB
            self.connector = IBConnector()
            if not await self.connector.connect():
                redis_publisher.send_error("Impossibile connettersi a IB")
                raise Exception("Impossibile connettersi a IB")
            
            if config.WEBSOCKET_ENABLED and redis_publisher.enabled:
                self.dashboard_handler = IBDashboardHandler(self.connector.ib)
                
                # Setup callback per comandi dalla dashboard
                redis_publisher.set_command_callback(self.handle_dashboard_command)
                
                logger.info("✅ Dashboard integration attivata")
                redis_publisher.log("success", "Dashboard integration attiva")
            
            # Inizializza i moduli
            self.data_handler = DataHandler(self.connector)
            self.indicator_calculator = IndicatorCalculator()
            self.execution = ExecutionHandler(self.connector, capital=25000)

            self.account_id = self.connector.ib.managedAccounts()[0]
            self.pnl_stream = self.connector.ib.reqPnL(self.account_id)

            if not self.execution.update_capital():
                logger.error("Fallito l'aggiornamento del capitale. Il bot si ferma per sicurezza.")
                redis_publisher.send_error("Fallito aggiornamento capitale")
                return False
            
            # Invia capitale alla dashboard
            capital_info = {
                "available_capital": self.execution.capital,
                "max_risk_per_trade": self.execution.base_risk,
                "position_size_limit": self.execution.capital * self.execution.base_risk
            }
            redis_publisher.publish("capital-update", capital_info)
            
            position_info = self.execution.sync_position_state()
            if position_info:
                self.in_position = True
                logger.warning(f"⚠️ Bot avviato con posizione aperta di {position_info['shares']} shares")
                redis_publisher.log("warning", f"Bot avviato con posizione aperta: {position_info['shares']} shares")

                # Invia info posizione alla dashboard
                redis_publisher.publish("position-status", {
                    "has_position": True,
                    "shares": position_info['shares'],
                    "entry_price": position_info.get('avg_price', 0)
                })
            else:
                self.in_position = False
                logger.info("✅ Bot avviato senza posizioni aperte")
                redis_publisher.log("success", "Bot avviato senza posizioni aperte")
                redis_publisher.publish("position-status", {"has_position": False})
            
            logger.info("Tutti i componenti inizializzati con successo")
            redis_publisher.log("success", "✅ Tutti i componenti inizializzati")

            # Invia stato sistema alla dashboard
            self.send_system_status()

            return True
            
        except Exception as e:
            logger.error(f"Errore nell'inizializzazione: {e}")
            redis_publisher.send_error(f"Errore inizializzazione: {str(e)}")
            return False
    
    def send_system_status(self):
        """Invia stato completo del sistema alla dashboard."""
        status = {
            "bot_status": "running" if self.is_running else "stopped",
            "connection_status": "connected" if self.connector else "disconnected",
            "in_position": self.in_position,
            "market_hours": self.is_market_open(),
            "uptime_seconds": (datetime.now() - self.bot_start_time).total_seconds(),
            "config": {
                "symbol": config.SYMBOL,
                "exchange": config.EXCHANGE,
                "max_risk": config.MAX_RISK_PER_TRADE,
                "paper_trading": config.IB_PORT == 7497
            }
        }
        redis_publisher.publish("system-status", status)
    
    def is_market_open(self):
        """Verifica se il mercato è aperto."""
        now = datetime.now(ZoneInfo("America/New_York"))
        return time(9, 30) <= now.time() <= time(16, 0) and now.weekday() < 5
    
    def pre_market_routine(self):
        """
        Routine pre-market: aggiorna dati.
        Da eseguire alle 9:30 ET.
        """
        logger.info("=" * 50)
        logger.info("INIZIO ROUTINE PRE-MARKET")
        logger.info("=" * 50)

        redis_publisher.log("info", "🔔 Inizio routine pre-market")
        redis_publisher.publish("market-event", {"type": "pre-market", "time": datetime.now().isoformat()})
        
        try:
            # Controlliamo se c'è una posizione aperta da ieri
            self.get_open_positions()
            
            # 1. Aggiorna i dati storici
            logger.info("1. Aggiornamento dati storici...")
            redis_publisher.log("info", "📊 Aggiornamento dati storici...")

            df = self.data_handler.download_historical_data()
            if df.empty:
                logger.error("Errore nell'aggiornamento dati")
                redis_publisher.send_error("Errore aggiornamento dati storici")
                return
            
            self.indicator_calculator.calculate_all(df)

            # Invia ultimi indicatori alla dashboard
            if not df.empty:
                last_row = df.iloc[-1]
                indicators = {
                    "rsi": float(last_row.get('RSI', 0)),
                    "macd": float(last_row.get('MACD', 0)),
                    "macd_signal": float(last_row.get('MACD_signal', 0)),
                    "bb_upper": float(last_row.get('BB_Upper', 0)),
                    "bb_lower": float(last_row.get('BB_Lower', 0)),
                    "sma_20": float(last_row.get('SMA_20', 0)),
                    "sma_50": float(last_row.get('SMA_50', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "close": float(last_row.get('Close', 0))
                }
                redis_publisher.publish("indicators-update", indicators)
                redis_publisher.log("success", "✅ Indicatori calcolati e aggiornati")

            # --- LOGICA GAP CHECK ---
            if self.in_position:
                last_sl_price = self.load_overnight_state()
                
                if last_sl_price:
                    logger.info(f"🔍 Verifica Gap: SL salvato ieri = {last_sl_price}")
                    
                    # Otteniamo il prezzo attuale. 
                    # Opzione A: Ultima chiusura (se pre-market) o Apertura odierna se i dati sono live
                    # Per sicurezza, chiediamo un prezzo live istantaneo
                    tickers = self.connector.ib.reqTickers(self.connector.contract)
                    if tickers:
                        current_price = tickers[0].marketPrice() # Prezzo attuale (Last/Mark)
                        # Se il marketPrice non è disponibile (es. dati ritardati), usiamo l'ultima close del df
                        if pd.isna(current_price) or current_price == 0:
                             current_price = df.iloc[-1]['Close']
                    else:
                        current_price = df.iloc[-1]['Close']

                    logger.info(f"Prezzo Apertura stimato: {current_price}")

                    # CONDIZIONE: Se il prezzo attuale è INFERIORE al vecchio stop loss
                    if current_price < last_sl_price:
                        logger.warning(f"🚨 GAP DOWN RILEVATO! Open ({current_price}) < Old SL ({last_sl_price})")
                        redis_publisher.log("error", f"🚨 GAP DOWN: {current_price} < {last_sl_price}. Chiusura immediata!")
                        
                        # Chiudi posizione immediatamente (Market Order)
                        self.execution.close_all_positions() 
                        self.in_position = False
                        self.clear_overnight_state()
                        return # Esci, trade finito
                    
                    else:
                        logger.info("✅ Prezzo sopra il vecchio SL. La posizione rimane aperta.")
                        redis_publisher.log("success", "✅ Nessun Gap critico. Posizione mantenuta.")
                        
                        self.execution.place_stop_loss(last_sl_price) 
                else:
                    logger.info("Nessuno stato SL salvato trovato.")
            
        except Exception as e:
            logger.error(f"Errore nella routine pre-market: {e}")
            redis_publisher.send_error(f"Errore routine pre-market: {str(e)}")
    
    def get_open_positions(self):
        """
        Recupera tutte le posizioni aperte
        """
        try:
            # Metodo 1: Posizioni attuali
            positions = self.connector.ib.positions()
            
            logger.info(f"Trovate {len(positions)} posizioni aperte")
            redis_publisher.log("info", f"📈 Trovate {len(positions)} posizioni aperte")

            if len(positions) > 0:
                self.in_position = True

                # Invia dettagli posizioni alla dashboard
                for pos in positions:
                    if pos.contract.symbol == config.SYMBOL:
                        pos_info = {
                            "symbol": pos.contract.symbol,
                            "shares": pos.position,
                            "avg_cost": pos.avgCost
                        }
                        redis_publisher.publish("position-info", pos_info)
            
        except Exception as e:
            logger.error(f"Errore nel recupero posizioni: {e}")
            redis_publisher.send_error(f"Errore recupero posizioni: {str(e)}")
    
    def end_of_day_routine(self):
        """
        Routine di fine giornata.
        Alle 16:00: Cancella SL, mantieni posizione, salva livello SL.
        """
        logger.info("=" * 50)
        logger.info("ROUTINE FINE GIORNATA - PREPARAZIONE OVERNIGHT")
        logger.info("=" * 50)
        
        redis_publisher.log("info", "🔔 Inizio routine fine giornata (Overnight Mode)")
        
        try:
            # Aggiorna info posizioni
            self.get_open_positions()

            if self.in_position:
                logger.info("Posizione aperta rilevata. Ricerca Stop Loss attivo...")
                
                # 1. Trova l'ordine di Stop Loss attivo su IB
                # Nota: ib.openOrders() restituisce tutti gli ordini aperti
                open_orders = self.connector.ib.openOrders()
                stop_order = None
                
                for order in open_orders:
                    # Cerca ordini di tipo STP (Stop) o TRAIL (Trailing Stop)
                    if order.orderType in ['STP', 'TRAIL', 'STP LMT']:
                        stop_order = order
                        break
                
                if stop_order:
                    # 2. Ottieni il prezzo di stop (auxPrice)
                    # Per i Trailing a volte serve calcolarlo, ma auxPrice è il trigger base per STP
                    current_sl_price = stop_order.auxPrice
                    
                    if current_sl_price and current_sl_price > 0:
                        logger.info(f"Trovato Stop Loss attivo a: {current_sl_price}")
                        
                        # 3. Salva lo stato su file
                        self.save_overnight_state(current_sl_price)
                        
                        # 4. Cancella l'ordine Stop Loss su IB
                        self.connector.ib.cancelOrder(stop_order)
                        logger.info("❌ Ordine Stop Loss cancellato per la notte.")
                        redis_publisher.log("warning", f"🌙 SL cancellato a {current_sl_price} (Salvataggio Overnight)")
                    else:
                        logger.warning("Stop order trovato ma prezzo non valido.")
                else:
                    logger.info("Nessun ordine Stop Loss trovato da cancellare.")
            else:
                logger.info("Nessuna posizione aperta. Nessuna azione necessaria.")
                self.clear_overnight_state()

            redis_publisher.publish("market-event", {"type": "market-close", "time": datetime.now().isoformat()})
            
        except Exception as e:
            logger.error(f"Errore nella routine EOD: {e}")
            redis_publisher.send_error(f"Errore routine EOD: {str(e)}")
    
    def on_new_candle(self):
        """
        Callback eseguita ogni 5 minuti durante il trading.
        """
        try:
            current_time = datetime.now(ZoneInfo("America/New_York"))
            
            # Verifica che siamo in orario di trading (9:35 - 15:55 NY time)
            if not self.is_market_open():
                return
            
            logger.info(f"[{current_time.strftime('%H:%M:%S')}] Processando nuova candela 5 minuti...")
            redis_publisher.log("debug", f"📊 Nuova candela 5min: {current_time.strftime('%H:%M:%S')}")
            
            # 1. Aggiorna dati
            df = self.data_handler.update_data(max_retries=10, retry_delay=0.2)
            if df is None or df.empty:
                logger.error("Errore nell'aggiornamento dati")
                redis_publisher.send_error("Errore aggiornamento dati candela")
                return
            
            # 2. Calcola indicatori (incrementale)
            df = self.indicator_calculator.calculate_incremental(df)

            # Invia ultimi valori alla dashboard
            if not df.empty:
                last_row = df.iloc[-1]

                candle_data = {
                    "time": current_time.isoformat(),
                    "open": float(last_row.get('Open', 0)),
                    "high": float(last_row.get('High', 0)),
                    "low": float(last_row.get('Low', 0)),
                    "close": float(last_row.get('Close', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "rsi": float(last_row.get('RSI', 0)),
                    "macd": float(last_row.get('MACD', 0)),
                    "macd_signal": float(last_row.get('MACD_signal', 0))
                }
                redis_publisher.publish("candle-update", candle_data)
            
            # 3. Check segnali (commentato nel tuo codice originale)
            if not self.in_position:
                signal = self.execution.check_entry_signals(df)
                if signal:
                    redis_publisher.send_trade_signal("BUY", {
                        "reason": "Entry signal detected",
                        "indicators": candle_data
                    })
                    self.in_position = True
            else:
                if self.execution.check_exit_signals(df):
                    redis_publisher.send_trade_signal("SELL", {
                        "reason": "Exit signal detected",
                        "indicators": candle_data
                    })
                    logger.info("Trade chiuso perchè non rispettava più le condizioni")
                    self.in_position = False
            
                self.execution.update_trailing_stop(df)
            
            # 4. Aggiorna stato sistema
            self.send_system_status()
            
        except Exception as e:
            logger.error(f"Errore in on_new_candle: {e}")
            redis_publisher.send_error(f"Errore processamento candela: {str(e)}")

    def handle_dashboard_command(self, command: dict):
        """
        Gestisce comandi ricevuti dalla dashboard via Redis.
        """
        cmd_type = command.get("type")
        payload = command.get("payload", {})
        
        logger.info(f"📥 Comando ricevuto dalla dashboard: {cmd_type}")
        redis_publisher.log("info", f"Comando ricevuto: {cmd_type}")
        
        try:
            if cmd_type == "stop":
                self.handle_stop_command()
                
            elif cmd_type == "pause":
                self.handle_pause_command()
                
            elif cmd_type == "resume":
                self.handle_resume_command()
                
            elif cmd_type == "status":
                self.send_system_status()
                if self.dashboard_handler:
                    self.dashboard_handler._send_initial_state()
                    
            elif cmd_type == "close_positions":
                self.handle_close_positions()
                
            elif cmd_type == "cancel_orders":
                self.handle_cancel_orders()
                
            elif cmd_type == "update_risk":
                new_risk = payload.get("max_risk")
                if new_risk:
                    config.MAX_RISK_PER_TRADE = new_risk
                    redis_publisher.log("info", f"Risk limit aggiornato a {new_risk}")
                    
            elif cmd_type == "force_update":
                # Forza aggiornamento dati
                self.force_data_update()
                
            else:
                logger.warning(f"Comando non riconosciuto: {cmd_type}")
                redis_publisher.log("warning", f"Comando non riconosciuto: {cmd_type}")
                
        except Exception as e:
            logger.error(f"Errore gestione comando {cmd_type}: {e}")
            redis_publisher.send_error(f"Errore esecuzione comando: {str(e)}")
    
    def handle_stop_command(self):
        """Gestisce comando di stop."""
        logger.warning("⛔ STOP command received - Shutting down bot")
        redis_publisher.log("warning", "⛔ Bot arrestato da comando dashboard")
        self.is_running = False
        
        # Chiudi posizioni se richiesto
        if self.in_position:
            redis_publisher.log("warning", "Chiusura posizioni prima dello shutdown...")
            # self.execution.close_all_positions()
    
    def handle_pause_command(self):
        """Gestisce comando di pausa."""
        logger.info("⏸️ PAUSE command received")
        redis_publisher.log("info", "⏸️ Bot in pausa")
        self.is_running = False
        redis_publisher.publish("bot-status", {"status": "paused"})
    
    def handle_resume_command(self):
        """Gestisce comando di resume."""
        logger.info("▶️ RESUME command received")
        redis_publisher.log("info", "▶️ Bot ripreso")
        self.is_running = True
        redis_publisher.publish("bot-status", {"status": "running"})
    
    def handle_close_positions(self):
        """Chiude tutte le posizioni aperte."""
        logger.warning("Chiusura posizioni richiesta dalla dashboard")
        redis_publisher.log("warning", "📉 Chiusura posizioni da dashboard")
        
        if self.execution and self.execution.has_position():
            # self.execution.close_all_positions()
            self.in_position = False
            redis_publisher.publish("position-status", {"has_position": False})
        else:
            redis_publisher.log("info", "Nessuna posizione da chiudere")
    
    def handle_cancel_orders(self):
        """Cancella tutti gli ordini aperti."""
        logger.warning("Cancellazione ordini richiesta dalla dashboard")
        redis_publisher.log("warning", "❌ Cancellazione ordini da dashboard")
        
        if self.connector:
            self.connector.ib.reqGlobalCancel()
            redis_publisher.log("success", "Tutti gli ordini cancellati")
    
    def force_data_update(self):
        """Forza aggiornamento immediato dei dati."""
        logger.info("Aggiornamento dati forzato dalla dashboard")
        redis_publisher.log("info", "🔄 Aggiornamento dati forzato")
        
        try:
            df = self.data_handler.update_data()
            if df is not None and not df.empty:
                df = self.indicator_calculator.calculate_incremental(df)
                redis_publisher.log("success", "✅ Dati aggiornati con successo")
                
                # Invia ultimi dati
                last_row = df.iloc[-1]
                candle_data = {
                    "time": datetime.now().isoformat(),
                    "close": float(last_row.get('Close', 0)),
                    "volume": float(last_row.get('Volume', 0)),
                    "rsi": float(last_row.get('RSI', 0))
                }
                redis_publisher.publish("data-update", candle_data)
        except Exception as e:
            redis_publisher.send_error(f"Errore aggiornamento forzato: {str(e)}")

    async def monitor_pnl_task(self):
        """
        Task in background che invia il PnL a Redis ogni secondo.
        Non blocca il trading perché usa asyncio.sleep.
        """
        logger.info("Avvio monitoraggio PnL in background...")
        
        while self.is_running:
            try:
                # Leggiamo i valori dall'oggetto pnl_stream che IB aggiorna in real-time
                if self.pnl_stream:
                    pnl_data = {
                        "account": self.account_id,
                        "dailyPnL": self.pnl_stream.dailyPnL,      # PnL Giornaliero
                        "unrealizedPnL": self.pnl_stream.unrealizedPnL, # PnL Non realizzato (posizioni aperte)
                        "realizedPnL": self.pnl_stream.realizedPnL,     # PnL Realizzato
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # Pubblica su un canale dedicato per il WebSocket server
                    redis_publisher.publish("pnl-update", pnl_data)
                
                # Aspetta 1 secondo prima del prossimo invio (per non intasare Redis)
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Errore nel monitoraggio PnL: {e}")
                await asyncio.sleep(5) # Attesa più lunga in caso di errore

    def save_overnight_state(self, stop_loss_price):
        """Salva lo stop loss su file per la mattina dopo."""
        state = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "last_stop_loss": float(stop_loss_price),
            "symbol": config.SYMBOL
        }
        with open("bot_state.json", "w") as f:
            json.dump(state, f)
        logger.info(f"💾 Stato overnight salvato: SL a {stop_loss_price}")

    def load_overnight_state(self):
        """Carica lo stop loss salvato."""
        if not os.path.exists("bot_state.json"):
            return None
        
        try:
            with open("bot_state.json", "r") as f:
                state = json.load(f)
            
            # Verifica che il dato sia recente (di ieri o oggi)
            # Qui semplifichiamo restituendo solo il valore
            return state.get("last_stop_loss")
        except Exception as e:
            logger.error(f"Errore caricamento stato: {e}")
            return None
        
    def clear_overnight_state(self):
        """Cancella il file di stato."""
        if os.path.exists("bot_state.json"):
            os.remove("bot_state.json")

    async def run(self):
        """Loop principale asincrono."""
        logger.info("Trading Bot avviato")
        redis_publisher.log("success", "🚀 Trading Bot avviato")
        
        if not await self.initialize_components():
            redis_publisher.send_error("Inizializzazione fallita - bot arrestato")
            return
        
        asyncio.create_task(self.monitor_pnl_task())
        
        # Definizione orari target (New York Time)
        ny_tz = ZoneInfo("America/New_York")
        
        logger.info("⏳ In attesa di trigger orari...")
        redis_publisher.log("info", "⏳ Bot in attesa trigger orari...")

        while self.is_running:
            try:
                # 1. Ottieni ora attuale NY
                now = datetime.now(ny_tz)
                
                # 2. Controllo SECONDO 00 (Scatta all'inizio del minuto)
                if now.second == 0:
                    
                    # A) Routine Pre-Market (09:30)
                    if now.hour == 9 and now.minute == 30:
                        self.pre_market_routine()
                        await asyncio.sleep(2)

                    # B) Routine EOD (16:00)
                    elif now.hour == 16 and now.minute == 0:
                        self.end_of_day_routine()
                        await asyncio.sleep(2)

                    # C) Candele 5 Minuti (9:35 -> 15:55, ogni 5 min)
                    elif (time(9, 35) <= now.time() <= time(15, 55)):
                        # Verifica modulo 5 minuti (0, 5, 10, ...)
                        if now.minute % 5 == 0:
                            self.on_new_candle()
                            await asyncio.sleep(2)

                # 3. Permette a IBKR di fare tutto quello che deve fare per 1 secondo
                await asyncio.sleep(1) 
                
            except KeyboardInterrupt:
                self.is_running = False
                redis_publisher.log("warning", "Bot interrotto da tastiera")
            except Exception as e:
                logger.error(f"Errore nel loop: {e}")
                redis_publisher.send_error(f"Errore nel loop principale: {str(e)}")
                await asyncio.sleep(5)
    
    def shutdown(self):
        """Chiude il bot in modo pulito."""
        logger.info("Shutdown del bot...")
        redis_publisher.log("warning", "🛑 Shutdown bot in corso...")
        
        try:
            # Invia stato finale
            redis_publisher.publish("bot-status", {
                "status": "stopped",
                "timestamp": datetime.now().isoformat(),
                "reason": "shutdown"
            })
            
            # Chiudi posizioni se necessario
            if self.execution and self.execution.has_position():
                logger.warning("Chiusura posizioni aperte...")
                redis_publisher.log("warning", "Chiusura posizioni prima dello shutdown")
                # self.execution.close_all_positions()
            
            # Disconnetti da IB
            if self.connector:
                self.connector.disconnect()
                redis_publisher.log("info", "Disconnesso da IB")
            
            # Disconnetti Redis
            redis_publisher.disconnect()
            
        except Exception as e:
            logger.error(f"Errore durante shutdown: {e}")
        
        logger.info("Bot terminato")

if __name__ == "__main__":
    # Serve a ib_insync per convivere con il loop di asyncio.run()
    ib_insync.util.patchAsyncio()
    bot = TradingBot()
    try:
        # Avvia il loop asincrono
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.shutdown()