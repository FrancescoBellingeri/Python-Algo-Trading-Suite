import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Calcolo dei giorni consecutivi vincenti/perdenti
def get_streak_stats(pnl_series):
    # Creiamo una serie di 1 (vincenti) e -1 (perdenti)
    streaks = (pnl_series > 0).astype(int)
    
    # Identifichiamo i cambi di streak
    changes = streaks.diff().fillna(0) != 0
    
    # Assegniamo un ID a ogni streak
    streak_id = changes.cumsum()
    
    # Raggruppiamo per streak e contiamo
    streak_lengths = streaks.groupby(streak_id).size()
    
    # Separiamo streak vincenti e perdenti
    winning_streaks = streak_lengths[streaks.groupby(streak_id).first() == 1]
    losing_streaks = streak_lengths[streaks.groupby(streak_id).first() == 0]
    
    return {
        'max_winning_streak': winning_streaks.max() if len(winning_streaks) > 0 else 0,
        'avg_winning_streak': winning_streaks.mean() if len(winning_streaks) > 0 else 0,
        'max_losing_streak': losing_streaks.max() if len(losing_streaks) > 0 else 0,
        'avg_losing_streak': losing_streaks.mean() if len(losing_streaks) > 0 else 0
    }

df = pd.read_csv('./data/qqq_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
# df = df[df['date'].dt.year >= 2022].reset_index(drop=True)

trading_results = pd.read_csv('trades_log_5min.csv')
trading_results['entry_date'] = pd.to_datetime(trading_results['entry_date'], utc=True).dt.tz_convert('America/New_York')
trading_results['exit_date'] = pd.to_datetime(trading_results['exit_date'], utc=True).dt.tz_convert('America/New_York')
#trading_results = trading_results[trading_results['entry_date'].dt.year >= 2017].reset_index(drop=True)

STARTING_CAPITAL = 100000

if len(trading_results) > 0:
    # Calcoliamo l'equity curve della strategia
    trading_results['cumulative_pnl'] = trading_results['pnl'].cumsum()
    trading_results['equity'] = trading_results['equity_post_trade']

    # trading_results['date'] = pd.to_datetime(trading_results['date'])
    
    # Calcola il numero di azioni acquistate all'inizio
    initial_price = df.iloc[0]['close']
    final_price = df.iloc[-1]['close']
    shares = STARTING_CAPITAL / initial_price
    
    # Calcola l'equity curve del buy & hold
    buy_hold_df = pd.DataFrame({
        'date': df['date'],
        'equity': df['close'] * shares
    })
    
    # Creiamo il grafico
    plt.figure(figsize=(20, 10))
    sns.set_style("whitegrid")
    
    plt.plot(trading_results['entry_date'], trading_results['equity'], 
        color='blue', linewidth=1.5, label='Strategia')
    
    plt.plot(df['date'],  buy_hold_df['equity'],
             color='green', linewidth=1.5, label='Buy & Hold')
    
    plt.axhline(y=STARTING_CAPITAL, color='r', linestyle='--', label='Capitale Iniziale')
    
    plt.title('Confronto Strategia vs Buy & Hold', fontsize=14, pad=20)
    plt.xlabel('Data', fontsize=12)
    plt.ylabel('Capitale ($)', fontsize=12)
    plt.legend(fontsize=10)
    
    plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    plt.gcf().autofmt_xdate()
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.tight_layout()
    
    plt.savefig('output/equity_qqq_5min.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # Stampiamo le statistiche
    print("\nStatistiche della Strategia ORB + RelVol:")
    print(f"Capitale Iniziale: ${STARTING_CAPITAL:,.2f}")
    print(f"Capitale Finale: ${trading_results['equity'].iloc[-1]:,.2f}")
    print(f"Profitto Totale: ${trading_results['cumulative_pnl'].iloc[-1]:,.2f}")
    print(f"Rendimento Totale: {((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100):,.2f}%")
    print(f"Numero di Trade: {len(trading_results)}")
    
    wins = trading_results[trading_results['pnl'] > 0]
    losses = trading_results[trading_results['pnl'] <= 0]
    
    print(f"Trade Vincenti: {len(wins)}")
    print(f"Trade Perdenti: {len(losses)}")
    print(f"Win Rate: {(len(wins) / len(trading_results) * 100):,.2f}%")
    
    # Statistiche aggiuntive
    if len(wins) > 0:
        print(f"Media Trade Vincenti: ${wins['pnl'].mean():,.2f}")
        print(f"Massimo Trade Vincente: ${wins['pnl'].max():,.2f}")
    
    if len(losses) > 0:
        print(f"Media Trade Perdenti: ${losses['pnl'].mean():,.2f}")
        print(f"Massima Perdita: ${losses['pnl'].min():,.2f}")
    
    # Profit Factor
    if len(losses) > 0 and losses['pnl'].sum() < 0:
        profit_factor = wins['pnl'].sum() / abs(losses['pnl'].sum())
        print(f"Profit Factor: {profit_factor:.2f}")
    
    # Statistiche sulle uscite
    exit_stats = trading_results['exit_reason'].value_counts()
    print(f"\nStatistiche sulle uscite:")
    for reason, count in exit_stats.items():
        percentage = (count / len(trading_results)) * 100
        print(f"{reason}: {count} ({percentage:.1f}%)")
    
    # Statistiche direzionali
    # long_trades = trading_results[trading_results['type'] == 'LONG']
    # short_trades = trading_results[trading_results['type'] == 'SHORT']
    
    # print(f"\nTrade LONG: {len(long_trades)} ({(len(long_trades) / len(trading_results) * 100):.1f}%)")
    # print(f"Trade SHORT: {len(short_trades)} ({(len(short_trades) / len(trading_results) * 100):.1f}%)")

    # R-multiple medio
    # print(f"\nR:R: {trading_results['R:R'].mean():.2f}")
    
    # Commissioni totali
    # print(f"Commissioni Totali: ${trading_results['fees'].sum():,.2f}")
    
    # Drawdown massimo
    running_max = trading_results['equity'].cummax()
    drawdown = (trading_results['equity'] - running_max) / running_max * 100
    max_drawdown = drawdown.min()
    print(f"Drawdown Massimo: {max_drawdown:.2f}%")
    
    # Crea una serie giornaliera completa dal primo all’ultimo giorno
    equity_daily = trading_results[['exit_date', 'equity']].set_index('exit_date').resample('B').ffill()

    # Calcola i rendimenti giornalieri
    equity_daily['returns'] = equity_daily['equity'].pct_change().fillna(0)

    # Ora calcola Sharpe ratio annualizzato
    mean_return = equity_daily['returns'].mean()
    std_return = equity_daily['returns'].std()

    sharpe_ratio = (mean_return / std_return) * np.sqrt(252)
    print(f"Sharpe Ratio: {sharpe_ratio:.2f}")
    
    # Buy & Hold statistics
    buy_hold_return = ((buy_hold_df['equity'].iloc[-1] - STARTING_CAPITAL) / STARTING_CAPITAL) * 100
    print(f"\n--- Buy & Hold ---")
    print(f"Rendimento Buy & Hold: {buy_hold_return:.2f}%")
    print(f"Capitale finale Buy & Hold: ${buy_hold_df['equity'].iloc[-1]:.2f}")
    
    # Confronto con Buy & Hold
    strategy_return = ((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100)
    excess_return = strategy_return - buy_hold_return
    print(f"\nExcess Return vs Buy & Hold: {excess_return:.2f}%")

    streak_stats = get_streak_stats(trading_results['pnl'])

    print("\n--- Statistiche Streak ---")
    print(f"Massimo numero di trade vincenti consecutivi: {streak_stats['max_winning_streak']}")
    print(f"Media trade vincenti consecutivi: {streak_stats['avg_winning_streak']:.2f}")
    print(f"Massimo numero di trade perdenti consecutivi: {streak_stats['max_losing_streak']}")
    print(f"Media trade perdenti consecutivi: {streak_stats['avg_losing_streak']:.2f}")

    stats = {
        'Capitale Iniziale': [f"${STARTING_CAPITAL:,.2f}"],
        'Capitale Finale': [f"${trading_results['equity'].iloc[-1]:,.2f}"],
        'Profitto Totale': [f"${trading_results['cumulative_pnl'].iloc[-1]:,.2f}"],
        'Rendimento Totale (%)': [f"{((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100):,.2f}"],
        'Numero di Trade': [len(trading_results)],
        'Trade Vincenti': [len(wins)],
        'Trade Perdenti': [len(losses)],
        'Win Rate (%)': [f"{(len(wins) / len(trading_results) * 100):.2f}"],
        'Media Trade Vincenti': [f"${wins['pnl'].mean():,.2f}" if len(wins) > 0 else "0.00"],
        'Massimo Trade Vincente': [f"${wins['pnl'].max():,.2f}" if len(wins) > 0 else "0.00"],
        'Media Trade Perdenti': [f"${losses['pnl'].mean():,.2f}" if len(losses) > 0 else "0.00"],
        'Massima Perdita': [f"${losses['pnl'].min():,.2f}" if len(losses) > 0 else "0.00"],
        'Profit Factor': [f"{(wins['pnl'].sum() / abs(losses['pnl'].sum())):.2f}" if len(losses) > 0 and losses['pnl'].sum() < 0 else "N/A"],
        # 'Uscite SL': [f"{exit_stats.get('SL', 0)} ({(exit_stats.get('SL', 0)/len(trading_results)*100):.1f}%)"],
        # 'Uscite TP': [f"{exit_stats.get('TP', 0)} ({(exit_stats.get('TP', 0)/len(trading_results)*100):.1f}%)"],
        # 'Uscite EOD': [f"{exit_stats.get('EOD', 0)} ({(exit_stats.get('EOD', 0)/len(trading_results)*100):.1f}%)"],
        # 'Trade LONG': [f"{len(long_trades)} ({(len(long_trades) / len(trading_results) * 100):.1f}%)"],
        # 'Trade SHORT': [f"{len(short_trades)} ({(len(short_trades) / len(trading_results) * 100):.1f}%)"],
        # 'R:R Medio': [f"{trading_results['R:R'].mean():.2f}"],
        # 'Commissioni Totali': [f"${trading_results['fees'].sum():,.2f}"],
        'Max Drawdown (%)': [f"{max_drawdown:.2f}"],
        'Sharpe Ratio': [f"{sharpe_ratio:.2f}" if len(trading_results) > 1 else "N/A"],
        'Buy & Hold Return (%)': [f"{buy_hold_return:.2f}"],
        'Capitale finale Buy & Hold': [f"${buy_hold_df['equity'].iloc[-1]:.2f}"],
        'Excess Return vs Buy & Hold (%)': [f"{excess_return:.2f}"],
        'Max Trade Vincenti Consecutivi': [f"{streak_stats['max_winning_streak']}"],
        'Media Trade Vincenti Consecutivi': [f"{streak_stats['avg_winning_streak']:.2f}"],
        'Max Trade Perdenti Consecutivi': [f"{streak_stats['max_losing_streak']}"],
        'Media Trade Perdenti Consecutivi': [f"{streak_stats['avg_losing_streak']:.2f}"]
    }

    # Crea la tabella
    df_stats = pd.DataFrame(stats)

    df_stats.to_csv('output/stats_strategy_qqq_5min.csv', index=False)
    
else:
    print("Nessun trade eseguito con la strategia ORB + RelVol")
    print("Verifica che ci siano segnali di trading validi nei dati")