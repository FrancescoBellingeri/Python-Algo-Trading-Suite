import pandas as pd

# === CONFIGURAZIONE ===
CSV_PATH = "data/qqq_IB_5min.csv"  # Percorso del tuo file

# === LETTURA DEL FILE ===
df = pd.read_csv(CSV_PATH)
df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")

# === AGGIUNTA COLONNA SOLO GIORNO ===
df["day"] = df["date"].dt.date

# === CONTEGGIO CANDELE PER GIORNO ===
counts = df.groupby("day")["date"].count()

# === MOSTRA I RISULTATI ===
print("Numero di candele per giorno:")
print(counts)

# === TROVA I GIORNI NON COMPLETI (≠ 390) ===
invalid_days = counts[counts != 78]

if invalid_days.empty:
    print("\n✅ Tutti i giorni hanno 390 candele.")
else:
    print("\n⚠️ Giorni con numero di candele diverso da 390:")
    print(invalid_days)
