from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
import asyncio
import ib_insync
from src.ib_connector import IBConnector
from src.data_handler import DataHandler
from src.indicator_calculator import IndicatorCalculator
from src.execution_handler import ExecutionHandler
from src.logger import logger

class TradingBot:
    """Bot di trading automatico che coordina tutti i moduli."""
    
    def __init__(self):
        """Inizializza il trading bot."""
        self.connector = None
        self.data_handler = None
        self.indicator_calculator = None
        self.execution = None
        
        # Stato del bot
        self.is_running = True
        self.in_position = False
        self.last_signal_time = None
        
        logger.info("Trading Bot inizializzato")
    
    async def initialize_components(self):
        """Inizializza tutti i componenti del sistema."""
        try:
            # Connetti a IB
            self.connector = IBConnector()
            if not await self.connector.connect():
                raise Exception("Impossibile connettersi a IB")
            
            # Inizializza i moduli
            self.data_handler = DataHandler(self.connector)
            self.indicator_calculator = IndicatorCalculator()
            self.execution = ExecutionHandler(self.connector, capital=25000)

            if not self.execution.update_capital():
                logger.error("Fallito l'aggiornamento del capitale. Il bot si ferma per sicurezza.")
                return False
            
            position_info = self.execution.sync_position_state()
            if position_info:
                self.in_position = True
                logger.warning(f"⚠️ Bot avviato con posizione aperta di {position_info['shares']} shares")
            else:
                self.in_position = False
                logger.info("✅ Bot avviato senza posizioni aperte")
            
            logger.info("Tutti i componenti inizializzati con successo")
            return True
            
        except Exception as e:
            logger.error(f"Errore nell'inizializzazione: {e}")
            return False
    
    def pre_market_routine(self):
        """
        Routine pre-market: aggiorna dati.
        Da eseguire alle 9:30 ET.
        """
        logger.info("=" * 50)
        logger.info("INIZIO ROUTINE PRE-MARKET")
        logger.info("=" * 50)
        
        try:
            # Controlliamo se c'è una posizione aperta da ieri
            self.get_open_positions()
            
            # 1. Aggiorna i dati storici
            logger.info("1. Aggiornamento dati storici...")
            df = self.data_handler.download_historical_data()
            if df.empty:
                logger.error("Errore nell'aggiornamento dati")
                return
            
            self.indicator_calculator.calculate_all(df)
            
        except Exception as e:
            logger.error(f"Errore nella routine pre-market: {e}")
    
    def get_open_positions(self):
        """
        Recupera tutte le posizioni aperte
        """
        try:
            # Metodo 1: Posizioni attuali
            positions = self.connector.ib.positions()
            
            logger.info(f"Trovate {len(positions)} posizioni aperte")

            if len(positions) > 0:
                self.in_position = True
            
        except Exception as e:
            logger.error(f"Errore nel recupero posizioni: {e}")
    
    def end_of_day_routine(self):
        """
        Routine di fine giornata.
        Da eseguire alle 16:00 ET.
        """
        logger.info("=" * 50)
        logger.info("ROUTINE FINE GIORNATA")
        logger.info("=" * 50)
        
        try:
            
            logger.info("Fine giornata completata")
            
        except Exception as e:
            logger.error(f"Errore nella routine EOD: {e}")
    
    def on_new_candle(self):
        """
        Callback eseguita ogni 5 minuti durante il trading.
        """
        try:
            current_time = datetime.now(ZoneInfo("America/New_York"))
            
            # Verifica che siamo in orario di trading (9:35 - 15:55 NY time)
            if not (time(9, 35) <= current_time.time() <= time(15, 55)):
                return
            
            logger.info(f"[{current_time.strftime('%H:%M:%S')}] Processando nuova candela 5 minuti...")
            
            # 1. Aggiorna dati
            df = self.data_handler.update_data(max_retries=10, retry_delay=0.2)
            if df is None or df.empty:
                logger.error("Errore nell'aggiornamento dati")
                return
            
            # 2. Calcola indicatori (incrementale)
            df = self.indicator_calculator.calculate_incremental(df)
            
            # 4. Check segnali (se non siamo già in posizione)
            # if not self.in_position:
            #     if self.execution.check_entry_signals(df):
            #         self.in_position = True
            # else:
            #     if self.execution.check_exit_signals(df):
            #         logger.info("Trade chiuso perchè non rispettava più le condizioni")
            #         self.in_position = False

            #     self.execution.update_trailing_stop(df)
            
        except Exception as e:
            logger.error(f"Errore in on_new_candle: {e}")

    async def run(self):
        """Loop principale asincrono."""
        logger.info("Trading Bot avviato")
        
        if not await self.initialize_components():
            return
        
        # Definizione orari target (New York Time)
        ny_tz = ZoneInfo("America/New_York")
        
        logger.info("⏳ In attesa di trigger orari...")

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
            except Exception as e:
                logger.error(f"Errore nel loop: {e}")
                await asyncio.sleep(5)
    
    def shutdown(self):
        """Chiude il bot in modo pulito."""
        logger.info("Shutdown del bot...")
        
        # Chiudi posizioni se necessario
        if self.execution and self.execution.has_position():
            logger.warning("Chiusura posizioni aperte...")
            # self.execution.close_all_positions()
        
        # Disconnetti da IB
        if self.connector:
            self.connector.disconnect()
        
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