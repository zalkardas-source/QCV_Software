# Backend-only image: FastAPI + Docling
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# System deps:
# - libgl1, libglib2.0-0: OpenCV runtime (used by Docling)
# - libreoffice: DOCX -> PDF conversion (used by Docling)
# - libpango/libharfbuzz/shared-mime-info/fonts-dejavu: WeasyPrint PDF rendering
# - curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libreoffice \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        shared-mime-info \
        fonts-dejavu \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (better layer caching)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source
COPY backend ./backend

# SQLite data dir (mount-point for the named volume in docker-compose)
RUN mkdir -p /app/backend/data

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
