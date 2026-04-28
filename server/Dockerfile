FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source — entire project so all modules are importable
COPY . .

# Single worker — state lives in one process, asyncio handles concurrency
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "5000"]