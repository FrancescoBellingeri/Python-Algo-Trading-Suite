FROM python:3.13-slim

# Imposta workdir
WORKDIR /app

# Variabili d'ambiente per Python
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Installa dipendenze sistema (gcc serve a volte per alcune lib python)
RUN apt-get update && apt-get install -y gcc

# Copia requirements e installa
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il contenuto della cartella backend dentro il container
COPY . .

# Espone la porta (interna al container)
EXPOSE 8000

# Avvia il server usando il file run_server.py o uvicorn direttamente
# Assicuriamoci di puntare a app.main:app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]