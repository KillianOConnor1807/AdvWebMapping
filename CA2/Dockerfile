FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_ROOT=/static

WORKDIR /app

# Install system dependencies for GIS
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        gdal-data \
        python3-gdal \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Run migrations, collect static files and seed data before the server starts
# (run via sh so it works on bind mounts without +x)
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
# Production WSGI server; the dev compose file swaps this for runserver
CMD ["gunicorn", "european_mapping.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
