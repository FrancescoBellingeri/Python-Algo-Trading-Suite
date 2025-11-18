import pandas as pd
import pandas_ta as ta
import numpy as np
import datetime

# Carica il dataset
df = pd.read_csv('data/qqq_IB_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')

# Step 2: Calcola RENDIMENTI GIORNALIERI (per la volatilità)
# df['daily_return'] = np.log(df['open'] / df['open'].shift(1)) * 100

# # Step 4: Calcola VOLATILITÀ sui rendimenti giornalieri
# df['volatility'] = df['return'].rolling(window=21).std()
# df.ta.ema(length=50, append=True)
#df.ta.tsi(long=25, short=13, append=True)
# f.ta.ema(close=df['TSI_25_13'], length=7, append=True, suffix='_TSI_SIGNAL')
df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
df['SMA_200'] = ta.sma(df['close'], length=200)
# df['RSI_10'] = ta.rsi(df['close'], length=10)
df.ta.willr(length=10, append=True)

df.dropna(inplace=True)
print(df.tail())

# Salva
df.to_csv('data/qqq_5min.csv', index=False)