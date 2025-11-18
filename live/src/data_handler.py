import pandas as pd
import os
from ib_insync import Stock, util
from src.logger import logger
from config import SYMBOL, EXCHANGE, CURRENCY

class DataHandler:
    """Gestisce il download e l'aggiornamento dei dati di mercato."""
    
    def __init__(self, ib_connector):
        """
        Inizializza il DataHandler.
        
        Args:
            ib_connector: Istanza di IBConnector già connessa
        """
        self.ib = ib_connector.ib
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
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                
                # Salva su file
                df.to_csv(self.data_file, index=False)
                
                logger.info(f"Scaricati e salvati {len(df)} candele. Dal {df['date'].min()} al {df['date'].max()}")
                return df
            
            logger.info(f"Nessun dato scaricato")

            return pd.DataFrame
        except Exception as e:
            logger.error(f"Errore nel download dei dati storici: {e}")
            return pd.DataFrame()
    
    def update_data(self):
        """
        Aggiorna i dati con l'ultima candela da 5 minuti.
        Da eseguire ogni giorno ogni 5 minuti.
        """
        try:
            # Carica i dati esistenti
            if not os.path.exists(self.data_file):
                logger.error(f"File dati non trovato: {self.data_file}")
                logger.info("Esegui prima download_historical_data()")
                return False
            
            df = pd.read_csv(self.data_file)
            df['date'] = pd.to_datetime(df['date'])
            
            # Trova l'ultimo timestamp nei dati
            last_timestamp = df['date'].max()
            logger.info(f"Ultimo timestamp nel dataset: {last_timestamp}")
            
            # Scarica le ultime candele (ultimi 30 minuti per sicurezza)
            # Questo garantisce di catturare anche eventuali candele mancate
            logger.info(f"Scaricando ultime candele da 5 minuti...")
            
            bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime='',
                durationStr='1800 S',
                barSizeSetting='5 mins',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )

            if not bars:
                logger.warning("Nessun dato ricevuto da IB")
                return df
            
            # Converti e filtra solo i nuovi giorni
            new_df = util.df(bars)
            new_df['date'] = pd.to_datetime(new_df['date'])
            new_candele = new_df[new_df['date'] > last_timestamp].copy()
        
            if new_candele.empty:
                logger.info("Nessuna nuova candela da aggiungere")
                return df
            
            # Aggiungi le nuove candele
            df = pd.concat([df, new_candele], ignore_index=True)
            df = df.drop_duplicates(subset=['date'], keep='last')
            df = df.sort_values('date').reset_index(drop=True)
            
            if len(df) > 300:
                df = df.tail(300).reset_index(drop=True)
                logger.info(f"Dataset limitato agli ultimi 300 record (60 minuti)")

            # Salva
            df.to_csv(self.data_file, index=False)
            
            logger.info(f"Dataset aggiornato. Totale candele: {len(df)}")
            return df
            
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dei dati: {e}")
            return pd.DataFrame()