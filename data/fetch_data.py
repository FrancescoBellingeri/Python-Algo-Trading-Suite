from ib_insync import *
from datetime import datetime, timedelta
import pandas as pd
import time

# Avvia la connessione alla TWS o IB Gateway
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
# df_existing = pd.read_csv('data/qqq_IB_5min.csv')
# df_existing['date'] = pd.to_datetime(df_existing['date'])
# Definisci il contratto QQQ
contract = Stock('QQQ', 'SMART', 'USD')
all_data = []
# Usa 2018-11-16 come data iniziale
# end_date = datetime(2011, 8, 29, 0, 0, 0)
end_date = datetime.now()
for i in range(1, 35):
    endDateTime = end_date.strftime('%Y%m%d %H:%M:%S')

    # Richiedi dati storici giornalieri
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=endDateTime,              # fino ad ora  20151231 23:59:59
        durationStr='6 M',           # ultimi 12 mesi (puoi usare '5 Y', '6 M', ecc.)
        barSizeSetting='5 mins',      # timeframe giornaliero
        whatToShow='TRADES',         # prezzi di scambio
        useRTH=True,                 # solo orario regolare di trading
        formatDate=1
    )

    if bars:
        # Converti in DataFrame
        df = util.df(bars)
        print(f"Fetched {len(df)} bars ending on {df['date'].iloc[-1]}")
        all_data.append(df)
        end_date = pd.to_datetime(df['date'].iloc[0])
    
    time.sleep(1)

if all_data:
    final_df = pd.concat(all_data, ignore_index=True)
    final_df = final_df.drop_duplicates(subset=['date'], keep='first')
    final_df = final_df.sort_values('date').reset_index(drop=True)
    # Unisci i due DataFrame, tieni la riga più recente in caso di duplicati sulla colonna 'date'
    # combined = pd.concat([df_existing, final_df], ignore_index=True)
    # combined = combined.drop_duplicates(subset=['date'], keep='last')
    # combined = combined.sort_values('date').reset_index(drop=True)
    # final_df = combined
    
final_df.to_csv('data/qqq_IB_5min.csv', index=False)

ib.disconnect()