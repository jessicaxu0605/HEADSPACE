# Headspace Embedding Service

An internal HTTP service that converts text into normalized vectors for
Headspace search projections and semantic queries.

The service owns the Ollama model interaction, document/query prefixes, vector
validation, and normalization. Other Headspace services call this API; they do
not call Ollama directly.

## Required environment variables

The embedding service requires exactly these deployment settings:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768
EMBEDDING_DOCUMENT_PREFIX="search_document: "
EMBEDDING_QUERY_PREFIX="search_query: "
```

The model and dimensions must agree. Headspace currently stores vectors in
PostgreSQL `vector(768)` columns, so changing the dimensions also requires a
database migration.

Optional settings:

```env
EMBEDDING_SERVICE_TOKEN=<shared-secret>
PORT=8001
OLLAMA_MODELS=/var/lib/ollama
```

- `EMBEDDING_SERVICE_TOKEN` enables bearer-token authentication. Callers must
  use the same value.
- `PORT` defaults to `8001`.
- `OLLAMA_MODELS` is already set to `/var/lib/ollama` by the Docker image.

The prefixes belong to the configured model. They may be empty for models that
do not use task prefixes, but both variables must still be present.

## Run locally

Create the service's local environment file from its own template:

```bash
cp services/embeddings/.env.example services/embeddings/.env.local
```

Start Ollama and pull the configured model:

```bash
brew services start ollama
ollama pull nomic-embed-text
```

From the repository root, launch the HTTP service using the project's local
environment file:

```bash
venv/bin/dotenv -f services/embeddings/.env.local run -- \
  venv/bin/python -m services.embeddings
```

The service listens on `http://127.0.0.1:8001` by default.

## Run with Docker

Build from the repository root because the Dockerfile copies the top-level
`services` Python package:

```bash
docker build \
  -f services/embeddings/Dockerfile \
  -t headspace-embeddings .
```

Run the image with a named volume so the model survives container replacement:

```bash
docker run --rm \
  -p 8001:8001 \
  --env-file services/embeddings/.env.local \
  -v headspace-ollama-models:/var/lib/ollama \
  headspace-embeddings
```

The container starts Ollama, waits for it to become ready, pulls the configured
model if needed, and then starts the HTTP API. If either Ollama or the API exits,
the container exits so the hosting platform can restart it.

## HTTP API

### Health

```http
GET /health
```

Example response:

```json
{
  "status": "ok",
  "model": "nomic-embed-text",
  "dimensions": 768
}
```

### Generate document vectors

```http
POST /v1/embeddings
Content-Type: application/json
Authorization: Bearer <token>
```

```json
{
  "input": ["Sarah Chen works at Acme."],
  "input_type": "document"
}
```

Document requests accept between 1 and 128 non-blank strings.

### Generate a query vector

```json
{
  "input": ["Where does Sarah work?"],
  "input_type": "query"
}
```

Query requests accept exactly one non-blank string.

Successful responses contain the active model contract and normalized vectors:

```json
{
  "model": "nomic-embed-text",
  "dimensions": 768,
  "embeddings": [[0.0123, -0.0456]]
}
```

The example vector is abbreviated; production responses contain all 768 values.
The `Authorization` header is required only when
`EMBEDDING_SERVICE_TOKEN` is configured.

## Render deployment

Deploy this container as a Render **private service**, not a public web service.
Configure:

- Dockerfile path: `services/embeddings/Dockerfile`
- Docker build context: repository root (`.`)
- Required environment variables: `OLLAMA_BASE_URL`, `EMBEDDING_MODEL`,
  `EMBEDDING_DIMENSIONS`, `EMBEDDING_DOCUMENT_PREFIX`, and
  `EMBEDDING_QUERY_PREFIX`
- Persistent disk mount: `/var/lib/ollama`
- Sufficient memory for the configured model; the current configuration uses
  1 CPU and 2 GB RAM

After deployment, configure the main API with the private service address:

```env
EMBEDDING_SERVICE_URL=http://<render-private-host>:8001
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768
```

If service-token authentication is enabled, also set the same
`EMBEDDING_SERVICE_TOKEN` value on the main API.

## Tests

From the repository root:

```bash
venv/bin/python -m unittest \
  tests.test_embedding_service \
  tests.test_embedding_service_client
```
