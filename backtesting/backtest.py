import pandas as pd
import pandas_ta as ta
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from ib_insync import *
from datetime import datetime
import time
import os

def ibkr_commission(shares):
    total_fees = shares * 0.005
    return total_fees

def calculate_position_size(entry_price, stop_loss, account_size, risk_per_trade_pct, max_risk_dollars, leverage=4):
    """
    Calculate the number of contracts (or shares) to buy, taking into account:
    - risk per trade as a percentage,
    - leverage,
    - maximum allowed absolute loss in dollars.
    """

    # Risk per contract
    R = abs(entry_price - stop_loss)
    if R == 0 or R < 0.01:  # minimal symbolic risk to avoid division by zero
        return 0

    risk_dollars = account_size * risk_per_trade_pct
    allowed_risk = min(risk_dollars, max_risk_dollars)
    risk_based_size = allowed_risk / R
    leverage_based_size = (account_size * leverage) / entry_price
    position_size = int(min(risk_based_size, leverage_based_size))

    return position_size, R * position_size

def run_backtest(df, investment, risk_per_trade_pct, atr_multiplier, max_risk_dollars):
    equity = investment
    trades = []
    
    in_position = False
    entry_price = 0
    entry_date = None
    no_of_shares = 0
    trailing_stop_price = 0
    dollar_risk = 0
    entry_idx = 0
    fees = 0

    for i in range(1, len(df)):
        
        # --- EXIT LOGIC ---
        if in_position:
            exit_triggered = False
            exit_reason = ""

            if df['low'].iloc[i] <= trailing_stop_price:
                exit_triggered = True
                exit_reason = "TRAILING_STOP"
                exit_price = trailing_stop_price
            if df['WILLR_10'].iloc[i] > -20 and df['close'].iloc[i] < df['SMA_200'].iloc[i]:
                exit_triggered = True
                exit_reason = "WILLR_SMA"
                exit_price = df['close'].iloc[i]

            if exit_triggered:
                pnl = (exit_price - entry_price) * no_of_shares - fees
                equity += (no_of_shares * exit_price) - fees
                rr = pnl / dollar_risk
                
                trades.append({
                    "entry_date": entry_date,
                    "exit_date": df["date"].iloc[i],
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": round(pnl, 2),
                    "R:R": rr,
                    "shares": no_of_shares,
                    "equity": equity,
                    "atr_at_entry": df['ATR_14'].iloc[entry_idx-1],
                    "sma200_at_entry": df['SMA_200'].iloc[entry_idx-1],
                    "willr10_at_entry": df['WILLR_10'].iloc[entry_idx-1],
                    "exit_reason": exit_reason,
                    "fees": fees,
                })
                
                in_position = False
                no_of_shares = 0
                continue # Skip to the next loop iteration after closing the position

            # Calculate potential new stop and update only if higher
            potential_stop = round(df['close'][i] - (df['ATR_14'].iloc[i] * atr_multiplier), 2)
            trailing_stop_price = max(trailing_stop_price, potential_stop)
        # --- ENTRY LOGIC ---
        if not in_position:
            if df['WILLR_10'].iloc[i] < -80 and df['close'].iloc[i] > df['SMA_200'].iloc[i]:
                entry_date = df['date'].iloc[i]

                entry_price = df['open'].iloc[i+1]
                
                atr_value = df['ATR_14'].iloc[i]
                if atr_value <= 0: 
                    continue # Avoid division by zero if ATR is zero
                
                # --- POSITION SIZING BASED ON ATR AND RISK ---
                risk_per_share = atr_value * atr_multiplier
                # Set initial stop loss
                trailing_stop_price = round(entry_price - risk_per_share, 2)

                no_of_shares, dollar_risk = calculate_position_size(
                    entry_price=entry_price,
                    stop_loss=trailing_stop_price,
                    account_size=equity,
                    risk_per_trade_pct=risk_per_trade_pct,
                    max_risk_dollars=max_risk_dollars,
                    leverage=4
                )

                if no_of_shares > 0:
                    in_position = True
                    fees = ibkr_commission(no_of_shares) * 2
                    entry_idx = i + 1
                
                # Execute trade
                equity -= (no_of_shares * entry_price) 
                

    # Close position if still open at the end
    if in_position:
        equity += (no_of_shares * df['close'].iloc[i])

    earning = round(equity - investment, 2)
    roi = round(earning / investment * 100, 2)

    print(f'EARNING: ${earning} ; ROI: {roi}%')
    return pd.DataFrame(trades)
   

STARTING_CAPITAL = 10000

df = pd.read_csv('data/QQQ_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')

# Execute backtest
trades_df = run_backtest(df, STARTING_CAPITAL, risk_per_trade_pct=0.02, atr_multiplier=10, max_risk_dollars=30000)
trades_df['exit_date'] = pd.to_datetime(trades_df['exit_date'], utc=True).dt.tz_convert('America/New_York')

trades_df.to_csv('trades_log_5min.csv', index=False)
print(f"\n✅ Saved {len(trades_df)} trades to 'trades_log_5min.csv'")