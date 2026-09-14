FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (separate layer - only rebuilds when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .
