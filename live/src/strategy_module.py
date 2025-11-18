import pandas as pd
import numpy as np
import pandas_ta as ta
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler
from src.logger import logger
from config import HMM_WINDOW_SIZE, HMM_N_STATES

class RegimePredictor:
    """Predice il regime di mercato usando Hidden Markov Models."""
    
    def __init__(self, window_size=HMM_WINDOW_SIZE, n_components=HMM_N_STATES):
        """
        Inizializza il predittore.
        
        Args:
            window_size: Giorni di training per il modello (default 504 = ~2 anni)
            n_components: Numero di stati nascosti nell'HMM (default 2)
        """
        self.window_size = window_size
        self.n_components = n_components
        self.model = None
        self.scaler = StandardScaler()
        
    def prepare_data(self, df):
        """
        Prepara i dati calcolando le feature necessarie.
        
        Args:
            df: DataFrame con colonne OHLCV
            
        Returns:
            DataFrame con le feature calcolate
        """
        df = df.copy()
        
        # Calcola ATR usando pandas_ta
        df.ta.atr(length=14, append=True)
        
        # Return 14 giorni
        df['return'] = np.log(df['open'] / df['open'].shift(14)) * 100
        
        # Rimuovi NaN
        df.dropna(inplace=True)
        
        return df
    
    def train_predict(self, data):
        """
        Addestra il modello e predice lo stato per il prossimo giorno.
        
        Args:
            data: DataFrame con dati OHLCV
            
        Returns:
            Dict con previsione e informazioni aggiuntive
        """
        try:
            # Prepara i dati
            df = self.prepare_data(data)
            
            if len(df) < self.window_size:
                logger.error(f"Dati insufficienti: {len(df)} < {self.window_size}")
                return None
            
            # Usa gli ultimi window_size giorni per il training
            train_data = df.tail(self.window_size).copy()
            
            # Prepara le feature
            X_train = train_data[['ATRr_14', 'return']].values
            
            # Normalizza
            X_train_scaled = self.scaler.fit_transform(X_train)
            
            # Addestra il modello HMM
            logger.info("Addestramento HMM in corso...")
            self.model = hmm.GaussianHMM(
                n_components=self.n_components,
                covariance_type='tied',
                n_iter=100,
                random_state=0
            )
            
            self.model.fit(X_train_scaled)
            
            # Calcola le probabilità posteriori
            posteriors = self.model.predict_proba(X_train_scaled)
            pi_t = posteriors[-1]  # Probabilità dell'ultimo giorno
            
            # Predici la distribuzione del prossimo stato
            pi_next = pi_t @ self.model.transmat_
            
            # Identifica gli stati
            states = self.model.predict(X_train_scaled)
            train_data['state'] = states
            
            # Calcola le caratteristiche medie per stato
            state_characteristics = train_data.groupby('state')[['ATRr_14', 'return']].mean()
            
            # Identifica quale stato è BULL (quello con return medio più alto)
            state_bull = state_characteristics['return'].idxmax()
            
            # Predici lo stato più probabile per domani
            predicted_state = np.argmax(pi_next)
            
            # Determina la predizione finale
            if predicted_state == state_bull:
                final_prediction = 'BULL'
            else:
                final_prediction = 'BEAR'
            
            # Prepara il risultato
            result = {
                'prediction': final_prediction,
                'pi_current': pi_t,
                'pi_next': pi_next,
                'predicted_state': predicted_state,
                'bull_state': state_bull,
                'state_returns': state_characteristics[['return', 'ATRr_14']].to_dict(),
                'confidence': pi_next[predicted_state],
                'last_atrr': train_data['ATRr_14'].iloc[-1],
                'last_return': train_data['return'].iloc[-1]
            }
            
            logger.info(f"Stato corrente: {pi_t}")
            logger.info(f"Probabilità prossimo stato: {pi_next}")
            logger.info(f"Stati - Returns medi: {state_characteristics['return'].to_dict()}")
            logger.info(f"Bull state identificato: {state_bull}")
            logger.info(f"Stato predetto: {predicted_state}")
            logger.info(f"PREDIZIONE FINALE: {final_prediction}")
            
            return result
            
        except Exception as e:
            logger.error(f"Errore nel training/predizione: {e}")
            return None