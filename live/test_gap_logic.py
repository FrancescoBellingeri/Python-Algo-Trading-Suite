
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import pandas as pd
from datetime import datetime

# Add current directory to path so we can import main
sys.path.append(os.getcwd())

# Mock modules that might cause side effects on import
sys.modules['src.redis_publisher'] = MagicMock()
sys.modules['src.logger'] = MagicMock()

# Now we can safely import TradingBot from main
# We need to mock config before importing main because main uses it at module level
with patch.dict('sys.modules', {'config': MagicMock()}):
    import main
    from main import TradingBot

class TestOvernightLogic(unittest.TestCase):
    
    def setUp(self):
        # Setup common mocks
        self.mock_connector = MagicMock()
        self.mock_connector.ib = MagicMock()
        # Mock Data Handler to return valid DF
        self.mock_data_handler = MagicMock()
        # Return a real DataFrame so .empty = False work as expected
        mock_df = pd.DataFrame([{
            'Close': 100, 
            'Open': 100, 
            'High': 100, 
            'Low': 100,
            'ATR_14': 1.0, 
            'SMA_200': 90
        }])
        self.mock_data_handler.download_historical_data.return_value = mock_df
        # update_data also used in other places, good to mock too
        self.mock_data_handler.update_data.return_value = mock_df
        
        self.mock_execution = MagicMock()
        self.mock_db = MagicMock()
        self.mock_indicator = MagicMock()
        self.mock_indicator = MagicMock()
        
        # Instantiate bot but prevent __init__ from doing too much if we can't control it
        # Actually main.TradingBot.__init__ is light, just sets None.
        self.bot = TradingBot()
        
        # Inject mocks
        self.bot.connector = self.mock_connector
        self.bot.data_handler = self.mock_data_handler
        self.bot.execution = self.mock_execution
        self.bot.db = self.mock_db
        self.bot.indicator_calculator = self.mock_indicator
        
        # Default state
        self.bot.in_position = True
        self.bot.execution.current_stop_order = None # Simulate no active stop yet (market closed)

        # Ensure config.SYMBOL is set
        main.config.SYMBOL = 'TEST_SYM'

        # Mock positions to ensure get_open_positions confirms we are in position
        mock_pos = MagicMock()
        mock_pos.contract.symbol = 'TEST_SYM' 
        mock_pos.position = 100
        self.bot.connector.ib.positions.return_value = [mock_pos]


    def test_gap_down(self):
        print("\n\n--- TEST: Gap Down (Price Opens BELOW Stop Loss) ---")
        
        # Manually mock instance methods
        mock_clear = MagicMock()
        self.bot.clear_overnight_state = mock_clear
        
        saved_sl_price = 100.0
        self.bot.load_overnight_state = MagicMock(return_value=saved_sl_price)
        
        # Market Price opens at 95 (Gap Down)
        mock_ticker = MagicMock()
        mock_ticker.marketPrice.return_value = 95.0
        self.bot.connector.ib.reqTickers.return_value = [mock_ticker]
        
        # Execute Routine
        self.bot.pre_market_routine()
        
        # Assertions
        print(f"Scenario: Saved SL={saved_sl_price}, Open Price=95.0")
        
        # Should call close_all_positions
        self.bot.execution.close_all_positions.assert_called_once()
        print("✅ SUCCESS: Bot triggered Emergency Close (Gap Down detected)")
        
        # Should NOT place a new stop loss
        self.bot.execution.place_stop_loss.assert_not_called()
        
        # Should clear state
        mock_clear.assert_called_once()

    def test_no_gap(self):
        print("\n\n--- TEST: Normal Open (Price Opens ABOVE Stop Loss) ---")
        
        # Manually mock instance methods
        saved_sl_price = 100.0
        self.bot.load_overnight_state = MagicMock(return_value=saved_sl_price)
        
        # Market Price opens at 105 (Above SL)
        mock_ticker = MagicMock()
        mock_ticker.marketPrice.return_value = 105.0
        self.bot.connector.ib.reqTickers.return_value = [mock_ticker]
        
        # Execute Routine
        self.bot.pre_market_routine()
        
        # Assertions
        print(f"Scenario: Saved SL={saved_sl_price}, Open Price=105.0")
        
        # Should NOT call close_all_positions
        self.bot.execution.close_all_positions.assert_not_called()
        
        # Should restore stop loss
        self.bot.execution.place_stop_loss.assert_called_once_with(saved_sl_price)
        print("✅ SUCCESS: Bot restored Stop Loss (No Gap risk)")

if __name__ == '__main__':
    unittest.main()
