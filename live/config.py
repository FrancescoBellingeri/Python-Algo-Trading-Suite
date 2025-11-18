import os
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# === CONNESSIONE IB ===
IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT', '7497'))  # 7497 per TWS, 4001 per IB Gateway
IB_CLIENT_ID = int(os.getenv('IB_CLIENT_ID', '1'))

# === TRADING PARAMETERS ===
SYMBOL = 'QQQ'
EXCHANGE = 'SMART'
CURRENCY = 'USD'

# Risk Management
MAX_RISK_PER_TRADE = 0.02  # 2% del capitale per trade

# === TIMING ===
MARKET_OPEN_TIME = "09:30"
MARKET_CLOSE_TIME = "16:00"
PRE_MARKET_START = "08:30"
END_OF_DAY_CLOSE = "15:45"  # Chiudi posizioni 15 min prima della chiusura

# === LOGGING ===
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = 'logs/trading_system.log'

# === TELEGRAM (opzionale) ===
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')