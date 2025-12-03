import sys
import os
from datetime import datetime, timedelta, timezone
from backend.app.database import DatabaseHandler

# Dati Fittizi
DUMMY_TRADES = [
    {
        "symbol": "NVDA",
        "entry_price": 450.00,
        "exit_price": 465.50,
        "quantity": 10,
        "entry_time": datetime.now(timezone.utc) - timedelta(days=5, hours=4),
        "exit_time": datetime.now(timezone.utc) - timedelta(days=5, hours=2),
        "exit_reason": "TRAILING_STOP"
    },
    {
        "symbol": "TSLA",
        "entry_price": 240.00,
        "exit_price": 235.00,
        "quantity": 20,
        "entry_time": datetime.now(timezone.utc) - timedelta(days=4, hours=3),
        "exit_time": datetime.now(timezone.utc) - timedelta(days=4, hours=1),
        "exit_reason": "EMA_CROSS"
    },
    {
        "symbol": "AAPL",
        "entry_price": 175.50,
        "exit_price": 178.20,
        "quantity": 50,
        "entry_time": datetime.now(timezone.utc) - timedelta(days=3, hours=5),
        "exit_time": datetime.now(timezone.utc) - timedelta(days=3, hours=1),
        "exit_reason": "TRAILING_STOP"
    },
    {
        "symbol": "AMD",
        "entry_price": 105.00,
        "exit_price": 102.50,
        "quantity": 30,
        "entry_time": datetime.now(timezone.utc) - timedelta(days=2, hours=6),
        "exit_time": datetime.now(timezone.utc) - timedelta(days=2, hours=5),
        "exit_reason": "EMA_CROSS"
    },
    {
        "symbol": "MSFT",
        "entry_price": 320.00,
        "exit_price": 328.00,
        "quantity": 15,
        "entry_time": datetime.now(timezone.utc) - timedelta(days=1, hours=2),
        "exit_time": datetime.now(timezone.utc) - timedelta(days=1),
        "exit_reason": "TRAILING_STOP"
    }
]

def seed_database():
    print("🌱 Inizializzazione DatabaseHandler...")
    try:
        db = DatabaseHandler()
    except Exception as e:
        print(f"❌ Errore connessione DB: {e}")
        return

    print(f"🔄 Inserimento di {len(DUMMY_TRADES)} trade fittizi...")
    
    for t in DUMMY_TRADES:
        # Calcolo automatico PnL
        pnl_dollar = (t["exit_price"] - t["entry_price"]) * t["quantity"]
        pnl_percent = ((t["exit_price"] - t["entry_price"]) / t["entry_price"]) * 100
        
        trade_id = db.save_trade(
            symbol=t["symbol"],
            entry_price=t["entry_price"],
            exit_price=t["exit_price"],
            quantity=t["quantity"],
            entry_time=t["entry_time"],
            exit_time=t["exit_time"],
            pnl_dollar=round(pnl_dollar, 2),
            pnl_percent=round(pnl_percent, 2),
            exit_reason=t["exit_reason"]
        )
        
        if trade_id:
            status = "✅ WIN" if pnl_dollar > 0 else "❌ LOSS"
            print(f"   Inserito Trade {t['symbol']}: {status} (${pnl_dollar:.2f})")
    
    print("\n✨ Completato! Ora avvia il server e controlla la dashboard.")

if __name__ == "__main__":
    seed_database()