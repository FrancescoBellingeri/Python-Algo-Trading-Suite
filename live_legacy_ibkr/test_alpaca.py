from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, ReplaceOrderRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from dotenv import load_dotenv
import os
import math

load_dotenv()

API_KEY = os.getenv('ALPACA_API_KEY')
SECRET_KEY = os.getenv('ALPACA_API_SECRET')

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# 1. Recupero Buying Power e Prezzo
account = trading_client.get_account()
buying_power = float(account.buying_power)

latest_quote = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols="QQQ"))
qqq_price = latest_quote["QQQ"].ask_price

# 2. Calcolo Quantità (arrotondata per difetto)
# Usiamo il 90% del buying power per sicurezza operativa
qty = math.floor((buying_power / qqq_price) * 0.9)

# 3. Definizione Stop Loss (-10%)
stop_loss_price = round(qqq_price * 0.90, 2)

print(f"Prezzo QQQ: {qqq_price} | Stop Loss impostato a: {stop_loss_price} | Qty: {qty}")

# 4. Preparazione Market Order con STOP LOSS (Bracket Order)
market_order_data = MarketOrderRequest(
    symbol="QQQ",
    qty=qty,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.GTC, # Good 'Til Cancelled è meglio per gli stop
    order_class="oto",         # Definisce che ci sono ordini collegati
    stop_loss=StopLossRequest(stop_price=stop_loss_price)
)

# Invio ordine
main_order = trading_client.submit_order(order_data=market_order_data)
print(f"Ordine inviato! ID: {main_order.id}")

# --- MODIFICA DELLO STOP LOSS ---
# Nota: Lo stop loss crea un ordine separato collegato al principale.
# Per semplicità, cerchiamo l'ordine di stop loss tra quelli aperti.

input("Premi Invio per modificare lo stop loss al -5% invece del -10%...")

# Recuperiamo gli ordini aperti per trovare quello di stop
all_orders = trading_client.get_orders()
# Cerchiamo l'ordine di tipo 'stop' che appartiene a QQQ
stop_order = next((o for o in all_orders if o.symbol == "QQQ" and o.type == "stop"), None)

if stop_order:
    new_stop_price = round(qqq_price * 0.95, 2)
    replace_data = ReplaceOrderRequest(stop_price=new_stop_price)
    
    trading_client.replace_order_by_id(stop_order.id, replace_data)
    print(f"Stop Loss modificato a: {new_stop_price}")
else:
    print("Non è stato possibile trovare l'ordine di stop da modificare.")