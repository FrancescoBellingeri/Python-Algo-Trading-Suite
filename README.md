📈 Async Mean Reversion Trading Bot
A professional-grade algorithmic trading system built for Interactive Brokers (IBKR).
It implements a Mean Reversion strategy using Williams %R and Trend Filters, featuring an asynchronous architecture for high-frequency responsiveness and database persistence.

Python
PostgreSQL
Coverage

🚀 Key Features (Why this project matters)
Asynchronous Core: Built on asyncio and ib_insync to handle non-blocking market data streams and order events concurrently.
Robust Persistence: Uses SQLAlchemy (ORM) with a PostgreSQL database to store historical tick data and indicators, ensuring data integrity via upsert logic.
Institutional Execution: Implements Bracket Orders (Parent + Child) to ensure atomic submission of Entry and Stop Loss, minimizing execution risk (naked positions).
Safety First: Includes a comprehensive Unit Test Suite (pytest) validating Money Management logic, Leverage limits, and Signal generation before any live deployment.
🛠️ Architecture
The system is divided into decoupled modules to ensure maintainability:

DataHandler: Manages real-time data ingestion and database synchronization (gap filling).
IndicatorCalculator: Computes technical indicators (ATR, SMA, Will%R) on the fly.
ExecutionHandler: Calculates position sizing based on risk volatility (ATR-based) and manages order lifecycle.
DatabaseHandler: Abstraction layer for PostgreSQL interactions.
📊 Strategy Overview
Concept: Mean Reversion on 5-minute timeframe.
Entry: Buys when the asset is oversold (Will%R < -80) within a bullish trend (Price > SMA 200).
Exit: Trend exhaustion or Stop Loss hit.
Risk Management: Dynamic position sizing based on account equity percentage (1-2%) and volatility (ATR). Hard cap on leverage.
⚙️ Installation
Clone the repository:

bash
git clone https://github.com/tuo-username/mean-reversion-bot.git
cd mean-reversion-bot
Install dependencies:

bash
pip install -r requirements.txt
Database Setup:
Make sure PostgreSQL is running and create a database named trading_bot.

Configuration:
Edit config.py with your IBKR account ID and Database credentials.

Run:

bash
python -m src.main
🧪 Testing
Reliability is key. The project includes unit tests for critical components.

Run the full suite:

bash
pytest tests/
test_money_management.py: Validates risk calculations (never exceed max risk).
test_order_execution.py: Verifies correct Bracket Order structure.
test_strategy_logic.py: Ensures signals are generated only on valid market conditions.
📝 Disclaimer
This software is for educational purposes only. Do not risk money you cannot afford to lose.
