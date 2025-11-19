import pandas as pd
import pandas_ta as ta
import os
from src.logger import logger
from src.database import DatabaseHandler
from config import SYMBOL

class IndicatorCalculator:
    """Calcola gli indicatori tecnici per la strategia di trading."""
    
    def __init__(self):
        """Inizializza il calcolatore di indicatori."""
        self.db = DatabaseHandler()
        self.symbol = SYMBOL

        # Parametri degli indicatori
        self.params = {
            'ATR_LENGTH': 14,
            'SMA_LENGTH': 200,
            'WILLR_LENGTH': 10
        }
        
        # Minimo numero di candele richieste per calcolare tutti gli indicatori
        self.min_candles_required = max(self.params.values()) + 50  # Buffer extra

        # Path per salvare i dati
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.data_file = os.path.join(self.data_dir, f'{self.symbol}_5min.csv')
            
    def calculate_all(self, df, timezone='America/New_York'):
        """
        Calcola tutti gli indicatori necessari per la strategia.
        
        Args:
            df: DataFrame con colonne OHLCV
            timezone: Timezone per la conversione delle date
            
        Returns:
            DataFrame con indicatori aggiunti
        """
        try:
            # Crea una copia per non modificare l'originale
            df = df.copy()
            
            # Assicurati che la data sia in formato datetime con timezone
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(timezone)
            
            # Verifica di avere abbastanza dati
            if len(df) < self.min_candles_required:
                logger.warning(f"Dati insufficienti per calcolare tutti gli indicatori. "
                             f"Richiesti: {self.min_candles_required}, Disponibili: {len(df)}")
            
            # Calcola ATR (Average True Range)
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=self.params['ATR_LENGTH'])
            
            # Calcola SMA (Simple Moving Average) 200
            df['SMA_200'] = ta.sma(df['close'], length=self.params['SMA_LENGTH'])
            
            # Calcola Williams %R
            df['WILLR_10'] = ta.willr(df['high'], df['low'], df['close'], length=self.params['WILLR_LENGTH'])
            
            df.to_csv(self.data_file, index=False)
            self.db.save_candles(df, self.symbol)
            return df
            
        except Exception as e:
            logger.error(f"Errore nel calcolo degli indicatori: {e}")
            return df
        
    def calculate_incremental(self, df):
        """
        Calcola gli indicatori solo per le ultime 5 righe.
        
        Args:
            df: DataFrame completo
            
        Returns:
            DataFrame con indicatori aggiornati
        """
        try:
            return self.calculate_all(df)
            df = df.copy()
            
            # Identifica se gli indicatori esistono già
            indicator_columns = ['ATR_14', 'SMA_200', 'WILLR_10']
            has_indicators = all(col in df.columns for col in indicator_columns)
            
            if not has_indicators or len(df) < 200:
                # Prima volta o dati insufficienti, calcola tutto
                logger.info("Calcolo completo degli indicatori...")
                return self.calculate_all(df)
            
            # Calcola solo per le ultime 5 righe
            start_idx = max(0, len(df) - 250)  # Assicurati di avere dati sufficienti per gli indicatori
            end_idx = len(df)
            
            # Prendi un subset sufficiente per il calcolo
            subset = df.iloc[start_idx:end_idx].copy()
            
            # Calcola indicatori sul subset
            subset['ATR_14'] = ta.atr(subset['high'], subset['low'], subset['close'], length=14)
            subset['SMA_200'] = ta.sma(subset['close'], length=200)
            subset['WILLR_10'] = ta.willr(subset['high'], subset['low'], subset['close'], length=10)
            
            # Aggiorna solo le ultime 5 righe nel DataFrame originale
            last_5_start = max(0, len(df) - 5)
            
            df.loc[df.index[last_5_start:], 'ATR_14'] = subset.iloc[-5:]['ATR_14'].values
            df.loc[df.index[last_5_start:], 'SMA_200'] = subset.iloc[-5:]['SMA_200'].values
            df.loc[df.index[last_5_start:], 'WILLR_10'] = subset.iloc[-5:]['WILLR_10'].values
            
            logger.info(f"Aggiornati indicatori per le ultime {min(5, len(df))} righe")
            df.to_csv(self.data_file, index=False)
            return df
            
        except Exception as e:
            logger.error(f"Errore nel calcolo incrementale: {e}")
            return self.calculate_all(df)