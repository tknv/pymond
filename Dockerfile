FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY exporter.py .
COPY app.py .
COPY templates/ templates/

# Create directory for CSV file
RUN mkdir -p /app

EXPOSE 8080

CMD ["python", "app.py"]