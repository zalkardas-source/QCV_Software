# Use a modern Python base image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install system dependencies
# - libgl1 & libglib2.0-0: Required for OpenCV (used by Docling)
# - libreoffice: Required for high-quality DOCX to PDF conversion (used by Docling)
# - curl: For healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libreoffice \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project
COPY . .

# Create directory for SQLite database
RUN mkdir -p /app/backend/data

# Expose the port FastAPI runs on
EXPOSE 8000

# Command to run the application
# We use --host 0.0.0.0 to make it accessible outside the container
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
