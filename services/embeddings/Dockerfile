FROM ollama/ollama:0.40.11 AS ollama

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/var/lib/ollama

WORKDIR /srv

COPY --from=ollama /bin/ollama /usr/local/bin/ollama
COPY services/embeddings/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ ./services/
RUN chmod +x services/embeddings/entrypoint.sh

EXPOSE 8001

CMD ["services/embeddings/entrypoint.sh"]
