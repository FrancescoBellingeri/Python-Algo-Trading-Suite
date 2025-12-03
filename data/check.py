import pandas as pd

# === CONFIGURATION ===
CSV_PATH = "data/qqq_IB_5min.csv"  # Path to your file

# === READING THE FILE ===
df = pd.read_csv(CSV_PATH)
df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")

# === ADD DAY ONLY COLUMN ===
df["day"] = df["date"].dt.date

# === COUNT CANDLES PER DAY ===
counts = df.groupby("day")["date"].count()

# === SHOW RESULTS ===
print("Number of candles per day:")
print(counts)

# === FIND INCOMPLETE DAYS (≠ 390) ===
invalid_days = counts[counts != 78]

if invalid_days.empty:
    print("\n✅ All days have 390 candles.")
else:
    print("\n⚠️ Days with number of candles different from 390:")
    print(invalid_days)
