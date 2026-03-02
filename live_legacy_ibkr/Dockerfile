FROM python:3.13-slim

WORKDIR /app

# Installa librerie di sistema
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Copia requirements e installa
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il progetto
COPY . .

# Comando di avvio (Nota: ora non c'è più "src/" davanti)
CMD ["python", "main.py"]