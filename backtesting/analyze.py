import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Calculate winning/losing streaks
def get_streak_stats(pnl_series):
    # Create a series of 1 (winning) and -1 (losing)
    streaks = (pnl_series > 0).astype(int)
    
    # Identify streak changes
    changes = streaks.diff().fillna(0) != 0
    
    # Assign an ID to each streak
    streak_id = changes.cumsum()
    
    # Group by streak and count lengths
    streak_lengths = streaks.groupby(streak_id).size()
    
    # Separate winning and losing streaks
    winning_streaks = streak_lengths[streaks.groupby(streak_id).first() == 1]
    losing_streaks = streak_lengths[streaks.groupby(streak_id).first() == 0]
    
    return {
        'max_winning_streak': winning_streaks.max() if len(winning_streaks) > 0 else 0,
        'avg_winning_streak': winning_streaks.mean() if len(winning_streaks) > 0 else 0,
        'max_losing_streak': losing_streaks.max() if len(losing_streaks) > 0 else 0,
        'avg_losing_streak': losing_streaks.mean() if len(losing_streaks) > 0 else 0
    }

df = pd.read_csv('./data/QQQ_5min.csv')
df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert('America/New_York')
# df = df[df['date'].dt.year >= 2022].reset_index(drop=True)

trading_results = pd.read_csv('output/trades_log.csv')
trading_results['entry_date'] = pd.to_datetime(trading_results['entry_date'], utc=True).dt.tz_convert('America/New_York')
trading_results['exit_date'] = pd.to_datetime(trading_results['exit_date'], utc=True).dt.tz_convert('America/New_York')
#trading_results = trading_results[trading_results['entry_date'].dt.year >= 2017].reset_index(drop=True)

STARTING_CAPITAL = 10000

if len(trading_results) > 0:
    # Compute the strategy’s equity curve
    trading_results['cumulative_pnl'] = trading_results['pnl'].cumsum()
    trading_results['equity'] = trading_results['equity']
    
    # Compute number of shares for buy & hold
    initial_price = df.iloc[0]['close']
    final_price = df.iloc[-1]['close']
    shares = STARTING_CAPITAL / initial_price
    
    # Compute buy & hold equity curve
    buy_hold_df = pd.DataFrame({
        'date': df['date'],
        'equity': df['close'] * shares
    })
    
    # Create performance comparison chart
    plt.figure(figsize=(16, 8))
    plt.style.use('seaborn-v0_8-darkgrid')
    
    # Plot Strategy
    plt.step(trading_results['entry_date'], trading_results['equity'], 
        where='post', color='blue', linewidth=1.5, label='Strategy')
    
    # Plot Buy & Hold
    plt.plot(df['date'],  buy_hold_df['equity'],
             color='green', linewidth=1.5, label='Buy & Hold')
    
    # Initial Capital Line
    plt.axhline(y=STARTING_CAPITAL, color='red', linestyle=':', alpha=0.5)
    
    # Formatting
    plt.title('Performance: Strategy vs Benchmark (QQQ)', fontsize=16, fontweight='bold', pad=15)
    plt.xlabel('Year', fontsize=12)
    plt.ylabel('Capital ($)', fontsize=12)
    plt.legend(fontsize=12, loc='upper left', frameon=True, facecolor='white', framealpha=0.9)
    plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    plt.savefig('output/equity_curve.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # Print statistics
    print("\nStrategy Statistics:")
    print(f"Starting Capital: ${STARTING_CAPITAL:,.2f}")
    print(f"Final Capital: ${trading_results['equity'].iloc[-1]:,.2f}")
    print(f"Total Profit: ${trading_results['cumulative_pnl'].iloc[-1]:,.2f}")
    print(f"Total Return: {((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100):,.2f}%")
    print(f"Number of Trades: {len(trading_results)}")
    
    wins = trading_results[trading_results['pnl'] > 0]
    losses = trading_results[trading_results['pnl'] <= 0]
    
    print(f"Winning Trades: {len(wins)}")
    print(f"Losing Trades: {len(losses)}")
    print(f"Win Rate: {(len(wins) / len(trading_results) * 100):,.2f}%")
    
    if len(wins) > 0:
        print(f"Average Winning Trade: ${wins['pnl'].mean():,.2f}")
        print(f"Largest Winning Trade: ${wins['pnl'].max():,.2f}")
    
    if len(losses) > 0:
        print(f"Average Losing Trade: ${losses['pnl'].mean():,.2f}")
        print(f"Largest Loss: ${losses['pnl'].min():,.2f}")
    
    # Profit Factor
    if len(losses) > 0 and losses['pnl'].sum() < 0:
        profit_factor = wins['pnl'].sum() / abs(losses['pnl'].sum())
        print(f"Profit Factor: {profit_factor:.2f}")
    
    # Exit statistics
    exit_stats = trading_results['exit_reason'].value_counts()
    print(f"\nExit Statistics:")
    for reason, count in exit_stats.items():
        percentage = (count / len(trading_results)) * 100
        print(f"{reason}: {count} ({percentage:.1f}%)")
    
    # Maximum drawdown
    running_max = trading_results['equity'].cummax()
    drawdown = (trading_results['equity'] - running_max) / running_max * 100
    max_drawdown = drawdown.min()
    print(f"Maximum Drawdown: {max_drawdown:.2f}%")
    
    # Build daily equity series
    equity_daily = trading_results[['exit_date', 'equity']].set_index('exit_date').resample('B').ffill()

    # Daily returns
    equity_daily['returns'] = equity_daily['equity'].pct_change().fillna(0)

    # Annualized Sharpe ratio
    mean_return = equity_daily['returns'].mean()
    std_return = equity_daily['returns'].std()

    sharpe_ratio = (mean_return / std_return) * np.sqrt(252)
    print(f"Sharpe Ratio: {sharpe_ratio:.2f}")
    
    # Buy & Hold stats
    buy_hold_return = ((buy_hold_df['equity'].iloc[-1] - STARTING_CAPITAL) / STARTING_CAPITAL) * 100
    print(f"\n--- Buy & Hold ---")
    print(f"Buy & Hold Return: {buy_hold_return:.2f}%")
    print(f"Final Buy & Hold Capital: ${buy_hold_df['equity'].iloc[-1]:.2f}")
    
    # Excess return vs buy & hold
    strategy_return = ((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100)
    excess_return = strategy_return - buy_hold_return
    print(f"\nExcess Return vs Buy & Hold: {excess_return:.2f}%")

    streak_stats = get_streak_stats(trading_results['pnl'])

    print("\n--- Streak Statistics ---")
    print(f"Max Consecutive Winning Trades: {streak_stats['max_winning_streak']}")
    print(f"Average Consecutive Winning Trades: {streak_stats['avg_winning_streak']:.2f}")
    print(f"Max Consecutive Losing Trades: {streak_stats['max_losing_streak']}")
    print(f"Average Consecutive Losing Trades: {streak_stats['avg_losing_streak']:.2f}")

    stats = {
        'Starting Capital': [f"${STARTING_CAPITAL:,.2f}"],
        'Final Capital': [f"${trading_results['equity'].iloc[-1]:,.2f}"],
        'Total Profit': [f"${trading_results['cumulative_pnl'].iloc[-1]:,.2f}"],
        'Total Return (%)': [f"{((trading_results['equity'].iloc[-1] / STARTING_CAPITAL - 1) * 100):,.2f}"],
        'Number of Trades': [len(trading_results)],
        'Winning Trades': [len(wins)],
        'Losing Trades': [len(losses)],
        'Win Rate (%)': [f"{(len(wins) / len(trading_results) * 100):.2f}"],
        'Avg Winning Trade': [f"${wins['pnl'].mean():,.2f}" if len(wins) > 0 else "0.00"],
        'Max Winning Trade': [f"${wins['pnl'].max():,.2f}" if len(wins) > 0 else "0.00"],
        'Avg Losing Trade': [f"${losses['pnl'].mean():,.2f}" if len(losses) > 0 else "0.00"],
        'Largest Loss': [f"${losses['pnl'].min():,.2f}" if len(losses) > 0 else "0.00"],
        'Profit Factor': [f"{(wins['pnl'].sum() / abs(losses['pnl'].sum())):.2f}" if len(losses) > 0 and losses['pnl'].sum() < 0 else "N/A"],
        'Max Drawdown (%)': [f"{max_drawdown:.2f}"],
        'Sharpe Ratio': [f"{sharpe_ratio:.2f}" if len(trading_results) > 1 else "N/A"],
        'Buy & Hold Return (%)': [f"{buy_hold_return:.2f}"],
        'Final Buy & Hold Capital': [f"${buy_hold_df['equity'].iloc[-1]:.2f}"],
        'Excess Return vs Buy & Hold (%)': [f"{excess_return:.2f}"],
        'Max Winning Streak': [f"{streak_stats['max_winning_streak']}"],
        'Avg Winning Streak': [f"{streak_stats['avg_winning_streak']:.2f}"],
        'Max Losing Streak': [f"{streak_stats['max_losing_streak']}"],
        'Avg Losing Streak': [f"{streak_stats['avg_losing_streak']:.2f}"]
    }

    # Create table
    df_stats = pd.DataFrame(stats)

    df_stats.to_csv('output/stats_strategy.csv', index=False)
    
else:
    print("No trades executed with the ORB + RelVol strategy")
    print("Check that valid trade signals exist in the data")