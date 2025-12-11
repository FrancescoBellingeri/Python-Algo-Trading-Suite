from ib_insync import *
from datetime import datetime
import pandas as pd
import time

# Start connection to TWS or IB Gateway
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)
# df_existing = pd.read_csv('data/qqq_IB_5min.csv')
# df_existing['date'] = pd.to_datetime(df_existing['date'])
# Define QQQ contract
contract = Stock('QQQ', 'SMART', 'USD')
all_data = []
# Use 2018-11-16 as start date
# end_date = datetime(2011, 8, 29, 0, 0, 0)
end_date = datetime.now()
for i in range(1, 45):
    endDateTime = end_date.strftime('%Y%m%d %H:%M:%S')

    # Request historical daily data
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=endDateTime,              # until now  20151231 23:59:59
        durationStr='4 M',           # last 12 months (you can use '5 Y', '6 M', etc.)
        barSizeSetting='5 mins',      # daily timeframe
        whatToShow='TRADES',         # trade prices
        useRTH=False,                 # only regular trading hours
        formatDate=1
    )

    if bars:
        # Convert to DataFrame
        df = util.df(bars)
        print(f"Fetched {len(df)} bars ending on {df['date'].iloc[-1]}")
        all_data.append(df)
        end_date = pd.to_datetime(df['date'].iloc[0])
    
    time.sleep(1)

if all_data:
    final_df = pd.concat(all_data, ignore_index=True)
    final_df = final_df.drop_duplicates(subset=['date'], keep='first')
    final_df = final_df.sort_values('date').reset_index(drop=True)
    # Merge the two DataFrames, keep the most recent row in case of duplicates on the 'date' column
    # combined = pd.concat([df_existing, final_df], ignore_index=True)
    # combined = combined.drop_duplicates(subset=['date'], keep='last')
    # combined = combined.sort_values('date').reset_index(drop=True)
    # final_df = combined
    
final_df.to_csv('data/qqq_rth.csv', index=False)

ib.disconnect()