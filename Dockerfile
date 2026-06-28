FROM python:3.11-slim

WORKDIR /app

# Копируем файл зависимостей и ставим их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код из текущей папки (backend)
COPY . .

EXPOSE 8000

# Запускаем именно python main.py.
# Он сам внутри себя выполнит миграции и запустит uvicorn.
CMD ["python", "main.py"]
