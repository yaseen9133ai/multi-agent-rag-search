# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the pyproject.toml and main.py
COPY pyproject.toml main.py ./

# Install dependencies
RUN pip install --no-cache-dir .

# Copy the data files
COPY files/ ./ingestion/files/

# Run the ingestion script
CMD ["python", "main.py"]
