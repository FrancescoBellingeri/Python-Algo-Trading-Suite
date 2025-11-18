from datetime import datetime, time
from zoneinfo import ZoneInfo
import schedule
import pandas as pd
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
    
    def initialize_components(self):
        """Inizializza tutti i componenti del sistema."""
        try:
            # Connetti a IB
            self.connector = IBConnector()
            if not self.connector.connect():
                raise Exception("Impossibile connettersi a IB")
            
            # Inizializza i moduli
            self.data_handler = DataHandler(self.connector)
            self.indicator_calculator = IndicatorCalculator()
            self.execution = ExecutionHandler(self.connector, capital=25000)

            if not self.execution.update_capital():
                logger.error("Fallito l'aggiornamento del capitale. Il bot si ferma per sicurezza.")
                return False
            
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
            positions = self.ib.positions()
            
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
            # Assicurati che il monitor sia fermato
            self.stop_candle_monitor()
            
            # 1. Chiudi tutte le posizioni
            logger.info("1. Chiusura posizioni aperte...")
            self.execution.close_all_positions()
            
            # 2. Reset stato
            self.today_prediction = None
            self.today_traded = False
            self.candle_processed = False
            
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
            df = self.data_handler.update_data()
            if df is None or df.empty:
                logger.error("Errore nell'aggiornamento dati")
                return
            
            # 2. Calcola indicatori (incrementale)
            df = self.indicator_calculator.calculate_incremental(df)
            
            # 4. Check segnali (se non siamo già in posizione)
            # if not self.in_position:
            #     self.check_entry_signals(df)
            # else:
            #     self.check_exit_signals(df)
            #     self.update_trailing_stop(df)
            
        except Exception as e:
            logger.error(f"Errore in on_new_candle: {e}")

    def setup_schedules(self):
        """Configura tutti gli schedule."""
        ny_tz = ZoneInfo("America/New_York")
        
        # Orari target in orario di New York
        target_times_ny = [
            time(9, 28),    # 09:28:00 NY leggermente prima dell'apertura
            time(15, 58)     # 15:58:00 NY leggermente prima della chiusura
        ]

        # Convertili nell'orario locale del server
        target_times_local = []
        for t in target_times_ny:
            ny_dt = datetime.combine(datetime.now(ny_tz).date(), t, ny_tz)
            local_dt = ny_dt.astimezone()  # converte in timezone locale del server
            target_times_local.append(local_dt.time())

        # Ora puoi schedulare i job con gli orari *locali equivalenti*
        schedule.every().day.at(target_times_local[0].strftime("%H:%M:%S")).do(self.pre_market_routine)
        schedule.every().day.at(target_times_local[1].strftime("%H:%M:%S")).do(self.end_of_day_routine)
        
        # Schedule per candele ogni 5 minuti durante il trading
        # Parte da 9:35 e continua ogni 5 minuti fino a 15:55
        for hour in range(9, 16):  # 9-15
            for minute in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
                # Skip prima delle 9:35 e dopo le 15:55
                if hour == 9 and minute < 35:
                    continue
                if hour == 15 and minute > 55:
                    continue
                    
                # Crea time in NY timezone e converti in locale
                ny_time = time(hour, minute)
                ny_dt = datetime.combine(datetime.now(ny_tz).date(), ny_time, ny_tz)
                local_dt = ny_dt.astimezone()
                local_time_str = local_dt.strftime("%H:%M:%S")
                schedule.every().day.at(local_time_str).do(self.on_new_candle)
        
        logger.info("Schedule configurati:")
        logger.info("- 09:30: Pre-market routine")
        logger.info("- 09:35-15:55: Check candele ogni 5 minuti")
        logger.info("- 16:00: End of day routine")

    def run(self):
        """Loop principale del bot."""
        logger.info("Trading Bot avviato")
        
        # Inizializza componenti
        if not self.initialize_components():
            logger.error("Inizializzazione fallita")
            return
        
        # Impostiamo gli schedule
        self.setup_schedules()
        
        # Loop principale
        try:
            while self.is_running:
                schedule.run_pending()
                self.connector.ib.sleep(1)  # usa ib.sleep per mantenere attive le callback
                
        except KeyboardInterrupt:
            logger.info("Bot interrotto dall'utente")
        except Exception as e:
            logger.error(f"Errore nel loop principale: {e}")
        finally:
            self.shutdown()
    
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
    bot = TradingBot()
    bot.run()