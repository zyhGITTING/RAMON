FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY frontend ./frontend
COPY backend ./backend

RUN mkdir -p /app/data

EXPOSE 8128

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8128"]
