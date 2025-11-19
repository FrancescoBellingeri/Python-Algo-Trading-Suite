import pandas as pd
import os
from ib_insync import Stock, util
from src.logger import logger
from config import SYMBOL, EXCHANGE, CURRENCY
from src.database import DatabaseHandler
import time
from datetime import datetime, timedelta
import pytz

class DataHandler:
    """Gestisce il download e l'aggiornamento dei dati di mercato."""
    
    def __init__(self, ib_connector):
        """
        Inizializza il DataHandler.
        
        Args:
            ib_connector: Istanza di IBConnector già connessa
        """
        self.ib = ib_connector.ib
        self.db = DatabaseHandler()
        self.symbol = SYMBOL
        self.contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        
        # Path per salvare i dati
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.data_file = os.path.join(self.data_dir, f'{SYMBOL}_5min.csv')
        
    def download_historical_data(self):
        """
        Scarica gli ultimi 1000 minuti per calcolare tutti gli indicatori.
        """
        try:            
            logger.info(f"Scaricando 1000 minuti di dati storici per {self.symbol}...")
            
            bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime='',
                durationStr='5 D',
                barSizeSetting='5 mins',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )
            
            # Converti in DataFrame
            if bars:
                df = util.df(bars)
                df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
                df = df.sort_values('date').reset_index(drop=True)
                
                # Salva su file
                df.to_csv(self.data_file, index=False)

                # Salva su DB
                success = self.db.save_candles(df, self.symbol)
                if success:
                    logger.info(f"✅ Scaricate e salvate {len(df)} candele nel Database.")

                logger.info(f"Scaricati e salvati {len(df)} candele. Dal {df['date'].min()} al {df['date'].max()}")
                return df
            
            logger.info(f"Nessun dato scaricato")

            return pd.DataFrame
        except Exception as e:
            logger.error(f"Errore nel download dei dati storici: {e}")
            return pd.DataFrame()
    
    def update_data(self, max_retries=10, retry_delay=0.2):
        """
        Aggiorna i dati con l'ultima candela da 5 minuti.
        Da eseguire ogni giorno ogni 5 minuti.
        """
        try:
            # Carica i dati esistenti
            # if not os.path.exists(self.data_file):
            #     logger.error(f"File dati non trovato: {self.data_file}")
            #     logger.info("Esegui prima download_historical_data()")
            #     return False
            
            # df = pd.read_csv(self.data_file)
            # df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
            
            # --- STEP 1: Calcoliamo quale DOVREBBE essere l'ultima candela ---
            ny_tz = pytz.timezone('America/New_York')
            now = datetime.now(ny_tz)

            # Arrotondiamo "adesso" ai 5 minuti precedenti
            # Es. 10:03:45 -> 10:00:00
            current_interval = now.replace(second=0, microsecond=0) 
            current_interval = current_interval - timedelta(minutes=now.minute % 5)

            # L'ultima candela CHIUSA è quella finita 5 minuti fa
            # Es. Se siamo nell'intervallo delle 10:00, l'ultima candela completa è quella delle 09:55
            expected_candle_time = current_interval - timedelta(minutes=5)
            
            # 1. Use limit=1 to fetch only the last candle
            df_last = self.db.get_latest_data(self.symbol, limit=1)
            
            if df_last.empty:
                logger.warning("DB vuoto. Eseguo download completo...")
                return self.download_historical_data()
            
            # Get last date (NY time)
            last_db_time = df_last['date'].iloc[-1]

            logger.info(f"Ultimo timestamp nel dataset: {last_db_time}")

            # --- STEP 3: Confronto ---
            # Se l'ultima candela nel DB è uguale (o successiva) a quella attesa, siamo a posto.
            if last_db_time >= expected_candle_time:
                logger.info(f"Dati aggiornati. (Ultima: {last_db_time})")
                return self.db.get_latest_data(self.symbol, limit=300)
            
            # Se siamo qui, MANCANO dei dati.
            # Calcoliamo il "buco" per decidere quanto scaricare
            gap = expected_candle_time - last_db_time
            
            logger.info(f"Manca la candela {expected_candle_time}. Gap temporale: {gap}")
            
             # --- STEP 4: Strategia di Download Intelligente ---
        
            if gap < timedelta(minutes=10):
                # Manca solo l'ultima candela (o poco più). Scarico veloce.
                duration_str = '1800 S' # 30 min
            elif gap < timedelta(days=2):
                # Cambio giorno (es. ieri sera -> oggi mattina)
                duration_str = '2 D'
            else:
                # Weekend o bot spento da giorni
                duration_str = '1 W'
            
            # --- STEP 5: Scarico da IB ---
            logger.info(f"Richiedo dati a IB (Duration: {duration_str})...")

            for attempt in range(max_retries):
                bars = self.ib.reqHistoricalData(
                    self.contract,
                    endDateTime='',
                    durationStr=duration_str,
                    barSizeSetting='5 mins',
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1
                )

                if bars:
                    # Converti e filtra solo i nuovi giorni
                    new_df = util.df(bars)
                    new_df['date'] = pd.to_datetime(new_df['date'], utc=True).dt.tz_convert('America/New_York')
                    
                    # Filtro: Salvo solo ciò che è NUOVO rispetto al DB
                    new_candle = new_df[new_df['date'] > last_db_time]
        
                    if not new_candle.empty:
                        self.db.save_candles(new_candle, self.symbol)

                        logger.info(f"✅ Aggiunte {len(new_candle)} nuove candele.")
                        
                        # 5. Restituiamo al bot le ultime 300 candele dal DB (per il calcolo indicatori)
                        return self.db.get_latest_data(self.symbol, limit=300)

                # Retry
                if attempt < max_retries - 1:
                    logger.warning(f"Candela non ancora disponibile, retry {attempt+1}/{max_retries} tra {retry_delay}s...")
                    time.sleep(retry_delay)

            # Fallback: se dopo tutti i retry non abbiamo la candela attesa
            logger.warning(f"⚠️ Candela {expected_candle_time} non trovata dopo {max_retries} tentativi")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dei dati: {e}")
            return pd.DataFrame()