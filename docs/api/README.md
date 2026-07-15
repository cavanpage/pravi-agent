# REST API reference

[`openapi.json`](openapi.json) is the generated OpenAPI 3.1 schema for
pravi's FastAPI app — every REST route, request/response model, and error
shape. Browse it interactively at <http://localhost:8765/docs> (Swagger UI)
or `/redoc` while the web server is running.

**The file is generated, not hand-maintained.** After changing any route or
schema, regenerate and commit it:

```bash
uv run pravi openapi-dump
```

`tests/api/test_openapi_spec_fresh.py` fails CI when the committed file
drifts from the live app, so a stale spec won't merge quietly.
