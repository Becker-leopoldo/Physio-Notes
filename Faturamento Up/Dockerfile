FROM python:3.12-slim

WORKDIR /app

# Contexto de build = raiz do repositório (docker-compose usa context: ..)
# Forma JSON array é necessária para paths com espaços no nome da pasta

# Dependências primeiro (melhor uso de cache)
COPY ["Faturamento Up/requirements.txt", "."]
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY ["Faturamento Up/app.py", "Faturamento Up/database.py", "./"]
COPY ["Faturamento Up/frontend/", "./frontend/"]

# CSS global do design system (pasta assets/ na raiz do repositório)
COPY ["assets/", "./assets/"]

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
