# Headspace

The repository separates deployable application code from design exploration:

```text
app/                 Python application package
  main.py             API entrypoint and upload/status/retry endpoints
  db.py               Database engine and session setup
  models.py           Application-owned PostgreSQL models
  storage.py          Local or object-storage helpers
  jobs/
    queue.py           Redis/RQ configuration and enqueue helpers
    tasks.py           Background job entrypoints
    stages.py          Pipeline stage orchestration
prototypes/sql/       Disposable SQL schema experiments
Design Docs/          Product and architecture notes (local/ignored)
```

Prototype SQL is reference material, not part of the runtime application. Once
a schema is adopted, express it as versioned production migrations rather than
having the app execute prototype `CREATE` scripts.
