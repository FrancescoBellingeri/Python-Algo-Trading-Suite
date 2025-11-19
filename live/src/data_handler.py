import pandas as pd
import os
from ib_insync import Stock, util
from src.logger import logger
from config import SYMBOL, EXCHANGE, CURRENCY
from src.database import DatabaseHandler
import time

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
                useRTH=False,
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
            
            # 1. Chiediamo al DB qual è l'ultima candela che ha
            df = self.db.get_latest_data(self.symbol, limit=1)
            
            if df.empty:
                logger.warning("DB vuoto. Eseguo download completo...")
                return self.download_historical_data()
            
            # Trova l'ultimo timestamp nei dati
            last_timestamp = df['date'].max()
            expected_timestamp = last_timestamp + pd.Timedelta(minutes=5)

            logger.info(f"Ultimo timestamp nel dataset: {last_timestamp}")
            
            # Scarica le ultime candele (ultimi 30 minuti per sicurezza)
            # Questo garantisce di catturare anche eventuali candele mancate
            logger.info(f"Scaricando ultime candele da 5 minuti...")
            
            for attempt in range(max_retries):
                bars = self.ib.reqHistoricalData(
                    self.contract,
                    endDateTime='',
                    durationStr='1800 S',
                    barSizeSetting='5 mins',
                    whatToShow='TRADES',
                    useRTH=False,
                    formatDate=1
                )

                if bars:
                    # Converti e filtra solo i nuovi giorni
                    new_df = util.df(bars)
                    new_df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
                    new_candele = new_df[new_df['date'] >= expected_timestamp]
        
                    if not new_candele.empty:
                        # Trovata! Aggiungi solo candele nuove
                        candele_da_aggiungere = new_candele[new_candele['date'] > last_timestamp]

                        if not candele_da_aggiungere.empty:
                            # df = pd.concat([df, candele_da_aggiungere], ignore_index=True)
                            # df = df.drop_duplicates(subset=['date'], keep='last')
                            # df = df.sort_values('date').reset_index(drop=True)
                            
                            # # Mantieni solo ultime 300 candele
                            # if len(df) > 300:
                            #     df = df.tail(300).reset_index(drop=True)
                            
                            # # Salva
                            # df.to_csv(self.data_file, index=False)
                            
                            # logger.info(f"✅ Aggiunte {len(candele_da_aggiungere)} candele (attempt {attempt+1}/{max_retries})")
                            
                            # return df
                            # 4. Salviamo le nuove nel DB
                            self.db.save_candles(candele_da_aggiungere, self.symbol)
                            
                            logger.info(f"✅ Sync completato: aggiunte {len(candele_da_aggiungere)} nuove candele.")
                            
                            # 5. Restituiamo al bot le ultime 300 candele dal DB (per il calcolo indicatori)
                            return self.db.get_latest_data(self.symbol, limit=300)

                # Retry
                if attempt < max_retries - 1:
                    logger.warning(f"Candela non ancora disponibile, retry {attempt+1}/{max_retries} tra {retry_delay}s...")
                    time.sleep(retry_delay)

            # Fallback: se dopo tutti i retry non abbiamo la candela attesa
            logger.warning(f"⚠️ Candela {expected_timestamp} non trovata dopo {max_retries} tentativi")
            return df
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dei dati: {e}")
            return pd.DataFrame()