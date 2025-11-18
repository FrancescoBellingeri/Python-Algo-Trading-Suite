"""Test del flusso completo con callback."""

from src.main import TradingBot
from src.logger import logger
import time

logger.info("=== TEST FLUSSO CON CALLBACK ===")

bot = TradingBot()

if bot.initialize_components():
    # Test 1: Pre-market
    logger.info("\n--- Test Pre-Market ---")
    bot.pre_market_routine()
    
    if bot.today_prediction:
        # Test 2: Setup monitor
        logger.info("\n--- Test Setup Monitor ---")
        bot.setup_candle_monitor()
        
        # Lascia attivo per qualche secondo
        logger.info("Monitor attivo per 10 secondi...")
        for i in range(10):
            bot.connector.ib.sleep(1)
            print(f"\r{i+1}/10s", end='')
        
        # Test 3: EOD
        logger.info("\n\n--- Test EOD ---")
        bot.end_of_day_routine()
    
    bot.shutdown()