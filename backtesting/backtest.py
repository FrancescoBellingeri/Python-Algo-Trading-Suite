import pandas as pd
from termcolor import colored as cl
import datetime

def ibkr_commission(shares):
    total_fees = shares * 0.005
    return total_fees

def calculate_position_size(entry_price, stop_loss, account_size, risk_per_trade_pct, max_risk_dollars, leverage=4):
    """
    Calcola il numero di contratti (o azioni) da acquistare tenendo conto di:
    - rischio per trade in percentuale,
    - leva finanziaria,
    - perdita massima assoluta consentita in dollari.
    """

    # Rischio per contratto
    R = abs(entry_price - stop_loss)
    if R == 0 or R < 0.01:  # rischio minimo simbolico per evitare divisione per zero
        return 0

    risk_dollars = account_size * risk_per_trade_pct
    allowed_risk = min(risk_dollars, max_risk_dollars)
    risk_based_size = allowed_risk / R
    leverage_based_size = (account_size * leverage) / entry_price
    position_size = int(min(risk_based_size, leverage_based_size))

    return position_size, R * position_size

def implement_atr_strategy(df, investment, risk_per_trade_pct, atr_multiplier, max_risk_dollars):
    in_position = False
    equity = investment
    trades = []
    entry_price = 0
    entry_date = None
    no_of_shares = 0
    trailing_stop_price = 0
    dollar_risk = 0

    print(cl(f"INIZIO BACKTEST con Capitale: ${investment}, Rischio/Trade: {risk_per_trade_pct*100}%, ATR Multiplier: {atr_multiplier}\n", attrs=['bold']))
    
    for i in range(1, len(df)): # Partiamo da 14 per assicurarci che l'ATR sia stabile
        
        # --- LOGICA DI USCITA ---
        if in_position:
            exit_triggered = False
            exit_reason = ""

            if df.low[i] <= trailing_stop_price:
                exit_triggered = True
                exit_reason = "TRAILING_STOP"
                exit_price = trailing_stop_price
            # if df['RSI_10'][i] > 70 and df['close'][i] < df['SMA_200'][i]:
            #     exit_triggered = True
            #     exit_reason = "RSI_SMA"
            if df['WILLR_10'][i] > -20 and df['close'][i] < df['SMA_200'][i]:
                exit_triggered = True
                exit_reason = "WILLR_SMA"
                exit_price = df['close'][i]
            # elif df.date[i].hour == 15 and df.date[i].minute == 59:
            #     exit_triggered = True
            #     exit_reason = "EOD"

            if exit_triggered:
                equity += (no_of_shares * df.close[i])
                in_position = False
                pnl = (exit_price - entry_price) * no_of_shares - ibkr_commission(no_of_shares)
                rr = pnl / dollar_risk
                trades.append({
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": df["date"][i],
                    "exit_price": df['close'][i],
                    "pnl": round(pnl, 2),
                    "R:R": rr,
                    "shares": no_of_shares,
                    "equity_post_trade": equity,
                    "is_winner": rr > 1,
                    "atr_at_entry": df['ATR_14'][i],
                    "sma200_at_entry": df['SMA_200'][i],
                    "exit_reason": exit_reason,
                    "fees": ibkr_commission(no_of_shares),
                })
                continue # Passa al ciclo successivo dopo aver chiuso la posizione

            # Calcola il potenziale nuovo stop e lo aggiorna solo se è più alto
            potential_stop = round(df.close[i] - (df['ATR_14'][i] * atr_multiplier), 2)
            trailing_stop_price = max(trailing_stop_price, potential_stop)
        # --- LOGICA DI ENTRATA ---
        if not in_position:
            # ENTRATA LONG
            # if df['RSI_10'][i] < 30 and df['close'][i] > df['SMA_200'][i]:
            if df['WILLR_10'][i] < -80 and df['close'][i] > df['SMA_200'][i]:
                entry_date = df['date'][i]
                # --- CALCOLO POSITION SIZING BASATO SU ATR E RISCHIO ---
                entry_price = df['open'][i+1]
                
                atr_value = df['ATR_14'][i]
                if atr_value <= 0: continue # Evita divisione per zero se l'ATR è nullo
                
                risk_per_share = atr_value * atr_multiplier
                # Imposta lo stop loss iniziale
                trailing_stop_price = round(entry_price - risk_per_share, 2)

                no_of_shares, dollar_risk = calculate_position_size(
                    entry_price=entry_price,
                    stop_loss=trailing_stop_price,
                    account_size=equity,
                    risk_per_trade_pct=risk_per_trade_pct,
                    max_risk_dollars=max_risk_dollars,
                    leverage=4
                )
                
                # Esecuzione del trade
                equity -= (no_of_shares * entry_price) 
                
                in_position = True
                # print(cl('BUY:           ', color = 'green', attrs = ['bold']), f'{no_of_shares} Shares bought at ${entry_price} on {df.date[i]} (Initial SL: ${trailing_stop_price:.2f})')

    # Chiusura posizione se ancora aperta alla fine
    if in_position:
        equity += (no_of_shares * df.close[i])
        print(cl(f'\nClosing final position at {df.close[i]} on {df.date[i]}', attrs = ['bold']))

    trades_df = pd.DataFrame(trades)
    trades_df.to_csv('trades_log_5min.csv', index=False)
    print(cl(f"\n✅ Salvati {len(trades)} trade in 'trades_log.csv'", color="cyan"))

    earning = round(equity - investment, 2)
    roi = round(earning / investment * 100, 2)
    print('')
    print(cl(f'EARNING: ${earning} ; ROI: {roi}%', attrs = ['bold']))

df = pd.read_csv('data/qqq_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
# df = df[df['date'].dt.year >= 2022].reset_index(drop=True)
# QQQ ATR_MULTIPLIER 8 MAX_RISK_DOLLARS 15000
# SPY ATR_MULTIPLIER 9 MAX_RISK_DOLLARS 15000
# TSLA ATR_MULTIPLIER 9 MAX_RISK_DOLLARS 15000
implement_atr_strategy(df, 100000, risk_per_trade_pct=0.02, atr_multiplier=10, max_risk_dollars=30000)