FROM python:3.12-slim

WORKDIR /app

# Dependências Python
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código-fonte
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Diretório de dados persistentes
RUN mkdir -p /data
ENV DB_PATH=/data/physio_notes.db

EXPOSE 8000

# Roda a partir de /app/backend para que os imports relativos funcionem
WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
