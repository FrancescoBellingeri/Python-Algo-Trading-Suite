import pandas as pd
import numpy as np
import math
from termcolor import colored as cl
import seaborn as sns
import matplotlib.pyplot as plt

#==============================================================================
# PART 1: YOUR BACKTEST FUNCTION, MODIFIED TO RETURN RESULTS
#==============================================================================
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

def run_backtest(df, investment, risk_per_trade_pct, atr_multiplier, max_risk_dollars, verbose=True):
    """
    Modified backtest function.
    - Accepts 'max_risk_dollars' as a parameter.
    - Has a 'verbose' option to disable prints during loops.
    - Calculates and returns key metrics like ROI, Sharpe, and Max Drawdown.
    """
    in_position = False
    equity = investment
    trades = []
    entry_price = 0
    entry_date = None
    no_of_shares = 0
    trailing_stop_price = 0
    dollar_risk = 0

    # To calculate drawdown
    equity_history = [investment]

    if verbose:
        print(cl(f"\nSTART BACKTEST: Capital=${investment}, Risk={risk_per_trade_pct*100}%, ATR Mult={atr_multiplier}, Max Risk=${max_risk_dollars}", attrs=['bold']))

    for i in range(1, len(df)):
        if in_position:
            exit_triggered = False
            if df.low[i] <= trailing_stop_price:
                exit_triggered = True
                exit_price = trailing_stop_price

            if df['WILLR_10'][i] > -20 and df['close'][i] < df['SMA_200'][i]:
                exit_triggered = True
                exit_price = df.close[i]

            if exit_triggered:
                commission = ibkr_commission(no_of_shares)
                equity += (no_of_shares * exit_price) - commission
                
                pnl = ((exit_price - entry_price) * no_of_shares) - commission
                trades.append({
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": df["date"][i],
                    "exit_price": df['close'][i],
                    "shares": no_of_shares,
                    "pnl": pnl,
                    "equity_post_trade": equity,
                    "fees": ibkr_commission(no_of_shares)
                })
                equity_history.append(equity)
                
                in_position = False

            potential_stop = df.close[i] - (df['ATR_14'][i] * atr_multiplier)
            trailing_stop_price = max(trailing_stop_price, potential_stop)
        if not in_position:
            if df['WILLR_10'][i] < -80 and df['close'][i] > df['SMA_200'][i]:
                entry_date = df['date'][i]
                # --- CALCULATE POSITION SIZING BASED ON ATR AND RISK ---
                entry_price = df.close[i]
                
                atr_value = df['ATR_14'][i]
                if atr_value <= 0: continue # Avoid division by zero if ATR is zero
                
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
                
                # Trade execution
                equity -= (no_of_shares * entry_price) 
                
                in_position = True

    # --- CALCULATE FINAL METRICS ---
    if not trades:
        return {"ROI": 0, "Sharpe": 0, "Max_Drawdown": 0, "Trades": 0}

    trades_df = pd.DataFrame(trades)
    # trades_df.to_csv(f'trades_log_{atr_multiplier}_{max_risk_dollars}.csv', index=False)
    print(cl(f"\n✅ Saved {len(trades)} trades to 'trades_log_{atr_multiplier}_{max_risk_dollars}.csv'", color="cyan"))
    
    # ROI
    earning = round(equity - investment, 2)
    roi = round(earning / investment * 100, 2)
    print(cl(f'EARNING: ${earning} ; ROI: {roi}%', attrs = ['bold']))
    # Max Drawdown
    eq_series = pd.Series(equity_history)
    running_max = eq_series.cummax()
    drawdown = (eq_series - running_max) / running_max
    max_drawdown = drawdown.min() * 100
    print(cl(f'Max Drawdown: ${max_drawdown}', attrs = ['bold']))

    # Create a complete daily series from the first to the last day
    equity_daily = trades_df[['exit_date', 'equity_post_trade']].set_index('exit_date').resample('B').ffill()

    # Calculate daily returns
    equity_daily['returns'] = equity_daily['equity_post_trade'].pct_change().fillna(0)

    # Now calculate annualized Sharpe ratio
    mean_return = equity_daily['returns'].mean()
    std_return = equity_daily['returns'].std()

    sharpe_ratio = (mean_return / std_return) * np.sqrt(252)
    print(f"Sharpe Ratio: {sharpe_ratio:.2f}")

    if verbose:
        print(cl(f'EARNING: ${round(equity - investment, 2)} ; ROI: {round(roi, 2)}%', attrs = ['bold']))

    return {
        "ROI": round(roi, 2),
        "Sharpe": round(sharpe_ratio, 2),
        "Max_Drawdown": round(max_drawdown, 2),
        "Trades": len(trades_df)
    }

#==============================================================================
# PART 2: NEW SECTION TO CREATE AND VISUALIZE THE HEATMAP
#==============================================================================

def run_sensitivity_analysis(df, investment):
    print(cl("Starting sensitivity analysis... This process will take time.", "yellow"))

    # Define parameter ranges to test
    atr_multipliers = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21]
    risk_caps = [30000]

    # Prepare DataFrames to save results
    results_sharpe = pd.DataFrame(index=atr_multipliers, columns=risk_caps, dtype=float)
    results_roi = pd.DataFrame(index=atr_multipliers, columns=risk_caps, dtype=float)
    results_drawdown = pd.DataFrame(index=atr_multipliers, columns=risk_caps, dtype=float)

    # Run backtest loop
    total_runs = len(atr_multipliers) * len(risk_caps)
    run_count = 0
    for atr_mult in atr_multipliers:
        for risk_cap in risk_caps:
            run_count += 1
            print(f"--> Execution {run_count}/{total_runs}: ATR Mult={atr_mult}, Risk Cap=${risk_cap}")
            
            try:
                metrics = run_backtest(df, 
                                       investment=investment, 
                                       risk_per_trade_pct=0.02, 
                                       atr_multiplier=atr_mult,
                                       max_risk_dollars=risk_cap,
                                       verbose=False) # Disable prints to avoid cluttering output
                
                results_sharpe.loc[atr_mult, risk_cap] = metrics["Sharpe"]
                results_roi.loc[atr_mult, risk_cap] = metrics["ROI"]
                results_drawdown.loc[atr_mult, risk_cap] = metrics["Max_Drawdown"]
            except Exception as e:
                print(f"Error during execution with ATR={atr_mult}, Risk Cap={risk_cap}: {e}")
                results_sharpe.loc[atr_mult, risk_cap] = 0
                results_roi.loc[atr_mult, risk_cap] = 0
                results_drawdown.loc[atr_mult, risk_cap] = 0

    print(cl("\nAnalysis complete. Generating heatmaps...", "green"))

    # Visualize Heatmaps
    plt.style.use('dark_background')
    fig, axes = plt.subplots(1, 3, figsize=(24, 7))
    
    # 1. Sharpe Ratio Heatmap (the most important)
    sns.heatmap(results_sharpe, ax=axes[0], annot=True, fmt=".2f", cmap="viridis", linewidths=.5)
    axes[0].set_title("Sharpe Ratio", fontsize=16)
    axes[0].set_xlabel("Max Risk Cap ($)")
    axes[0].set_ylabel("ATR Multiplier")

    # 2. ROI Heatmap
    sns.heatmap(results_roi, ax=axes[1], annot=True, fmt=".0f", cmap="plasma", linewidths=.5)
    axes[1].set_title("Total Return (%)", fontsize=16)
    axes[1].set_xlabel("Max Risk Cap ($)")
    axes[1].set_ylabel("ATR Multiplier")

    # 3. Max Drawdown Heatmap
    sns.heatmap(results_drawdown, ax=axes[2], annot=True, fmt=".1f", cmap="coolwarm_r", linewidths=.5)
    axes[2].set_title("Max Drawdown (%)", fontsize=16)
    axes[2].set_xlabel("Max Risk Cap ($)")
    axes[2].set_ylabel("ATR Multiplier")
    
    fig.suptitle('Strategy Sensitivity Analysis', fontsize=20)
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.show()

#==============================================================================
# PART 3: SCRIPT EXECUTION
#==============================================================================

# Load and prepare data (as you did before)
df = pd.read_csv('data/qqq_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')

# --- Run sensitivity analysis ---
run_sensitivity_analysis(df, investment=100000)

# --- If you want to run only a single test, you can use this line ---
# run_backtest(df, investment=100000, risk_per_trade_pct=0.01, atr_multiplier=8, max_risk_dollars=10000, verbose=True)
