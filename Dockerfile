# API image. Migrations are applied manually (python -m migrations) before
# deploying. Ingestion runs inline in the web process, so no worker or Redis is
# needed.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
