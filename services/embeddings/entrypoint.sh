#!/usr/bin/env bash
set -euo pipefail

ollama serve &
ollama_pid=$!
api_pid=""

shutdown() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  kill "$ollama_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown EXIT INT TERM

for _ in $(seq 1 60); do
  if ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! ollama list >/dev/null 2>&1; then
  echo "Ollama did not become ready within 60 seconds" >&2
  exit 1
fi

ollama pull "${EMBEDDING_MODEL:?EMBEDDING_MODEL is required}"

python -m uvicorn services.embeddings.api:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "${PORT:-8001}" &
api_pid=$!

wait -n "$ollama_pid" "$api_pid"
