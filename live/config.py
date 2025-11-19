import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# === IB CONNECTION ===
IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT', '7497'))  # 7497 for TWS, 4001 for IB Gateway
IB_CLIENT_ID = int(os.getenv('IB_CLIENT_ID', '1'))

# === DATABASE CONFIGURATION ===
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

# Complete SQLAlchemy connection string
DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# === TRADING PARAMETERS ===
SYMBOL = 'QQQ'
EXCHANGE = 'SMART'
CURRENCY = 'USD'

# Risk Management
MAX_RISK_PER_TRADE = 0.02 # 2%

# === TIMING ===
MARKET_OPEN_TIME = "09:30"
MARKET_CLOSE_TIME = "16:00"
PRE_MARKET_START = "08:30"
END_OF_DAY_CLOSE = "15:45"

# === LOGGING ===
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = 'logs/trading_system.log'

# === TELEGRAM (optional) ===
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')