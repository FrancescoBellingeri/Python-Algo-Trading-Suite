import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# === IB CONNECTION ===
IB_HOST = os.getenv('IB_HOST', 'ib-gateway')
IB_PORT = int(os.getenv('IB_PORT', '4004'))  # 7497 for TWS, 4001 for IB Gateway
IB_CLIENT_ID = int(os.getenv('IB_CLIENT_ID', '1'))

# === DATABASE CONFIGURATION ===
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

# Complete SQLAlchemy connection string
DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

POSTGRES_USER=os.getenv('POSTGRES_USER')
POSTGRES_PASSWORD=os.getenv('POSTGRES_PASSWORD')
POSTGRES_DB=os.getenv('POSTGRES_DB')
POSTGRES_HOST=os.getenv('POSTGRES_HOST')
POSTGRES_PORT=os.getenv('POSTGRES_PORT')

# Complete SQLAlchemy connection string
POSTGRES_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

APP_ENV = os.getenv('APP_ENV', 'dev').lower()

if APP_ENV == 'prod':
    ACTIVE_DB_URL = POSTGRES_URL
else:
    ACTIVE_DB_URL = DATABASE_URL

# === REDIS CONFIGURATION ===
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_CHANNEL = os.getenv('REDIS_CHANNEL', 'trading-bot-channel')
REDIS_COMMANDS_CHANNEL = os.getenv('REDIS_COMMANDS_CHANNEL', 'trading-bot-commands')

# === WEBSOCKET SERVER ===
WEBSOCKET_ENABLED = os.getenv('WEBSOCKET_ENABLED', 'true').lower() == 'true'
WEBSOCKET_UPDATE_INTERVAL = float(os.getenv('WEBSOCKET_UPDATE_INTERVAL', '1.0'))  # seconds

# === DASHBOARD SETTINGS ===
SEND_POSITIONS = os.getenv('SEND_POSITIONS', 'true').lower() == 'true'
SEND_ORDERS = os.getenv('SEND_ORDERS', 'true').lower() == 'true'
SEND_PNL = os.getenv('SEND_PNL', 'true').lower() == 'true'
SEND_LOGS = os.getenv('SEND_LOGS', 'true').lower() == 'true'

# === TRADING PARAMETERS ===
SYMBOL = 'QQQ'
EXCHANGE = 'SMART'
CURRENCY = 'USD'

# Risk Management
MAX_RISK_PER_TRADE = 0.02 # 2%
ATR_MULTIPLIER = 10

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