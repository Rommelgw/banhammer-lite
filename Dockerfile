FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY core/ ./core/
COPY server.py .

# Порты
EXPOSE 9999 8080

CMD ["python", "server.py"]
