# bayleaf-agents

FastAPI service that hosts **LLM agents** (ChatGPT-like, but under your control) and calls **Bayleaf** tools.
Provider-agnostic (OpenAI / mock / others), with Postgres for conversation history.

## Features

* Pluggable LLM provider (`mock` for dev, `openai` ready)
* Tool calls to Bayleaf REST (e.g., `patient_summary`, `list_medications`)
* Persistent conversations (PostgreSQL + SQLAlchemy + Alembic)
* Structured JSON logs (structlog)

## Quick start

### Local (Docker Compose)

```bash
cp .env.example .env   # set your envs (OPTIONAL for mock)
docker compose --profile dev up --build agents-dev
```

This also brings up a Presidio analyzer sidecar (spaCy-based) listening on `http://presidio-analyzer:3000/analyze` and exposed locally on `http://localhost:8001/analyze`. The agent calls it via `PHI_FILTER_URL`.
Qdrant is also started on `http://localhost:6333` for document indexing APIs.

If you hit it manually, include `language`:

```bash
curl -sS -X POST http://localhost:8001/analyze \
  -H "Content-Type: application/json" \
  -d '{"text":"Alice email alice@example.com", "language":"en"}'
```

### Health

```bash
curl -sS http://localhost:8080/health | jq
```

### Chat (stateless)

```bash
TOKEN="<bearer-token-with-user_id-claim>"

curl -sS -X POST http://localhost:8080/agents/treatment/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "channel":"bayleaf_app",
    "message":"Tenho dor de cabeça desde ontem.",
    "lang":"pt-BR"
  }' | jq
```

### Chat with conversation memory

```bash
TOKEN="<bearer-token-with-user_id-claim>"

curl -sS -X POST http://localhost:8080/agents/labcopilot/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "channel":"bayleaf_app",
    "conversation_id":"demo-1",
    "message":"Sim, tenho náusea e sensibilidade à luz.",
    "lang":"pt-BR"
  }' | jq
```

## API

* `GET /health` → `{ status, env, provider }`
* `POST /agents/{agent_slug}/chat` → authenticated chat per agent (for example, `treatment`, `appointment`, `labcopilot`)
* `POST /agents/documents/index` → index by `document_uuid`
* `POST /agents/documents/index/upload` → index uploaded file (`multipart/form-data`)
* `GET /agents/documents-available` → list indexed documents from Qdrant
* `GET /agents/documents/{uuid}` → indexed document status from Qdrant
* `POST /agents/documents/query` → semantic chunk retrieval
* `POST /agents/documents/{uuid}/reindex` → reindex document in Qdrant
* `GET /agents/conversations` and `GET /agents/conversations/{conversation_id}/messages` → conversation history
* `POST|PATCH|PUT|GET /agents/conversation-groups` → conversation group lifecycle
* `PUT|GET /agents/user-metadata` → user metadata store

All `/agents/*` endpoints require `Authorization: Bearer <token>`.

Chat body:

  ```json
  {
    "channel": "bayleaf_app | whatsapp | partner",
    "message": "string",
    "conversation_id": "optional string",
    "group_id": "optional string",
    "document_uuids": ["optional", "list"],
    "lang": "optional locale, e.g. pt-BR"
  }
  ```

  response (abridged):

  ```json
  { "reply": "...", "used_tools": [], "trace_id": "chat_xxx", "conversation_id": "demo-1" }
  ```

## Configuration

Environment variables (see `.env.example`):

```
APP_ENV=dev
HOST=0.0.0.0
PORT=8080
ALLOWED_HOSTS=localhost,127.0.0.1,labcopilot.nonnenmacher.tech
LLM_PROVIDER=mock          # mock | openai
OPENAI_API_KEY=            # if LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o
PHI_FILTER_URL=http://localhost:8001/analyze  # spaCy + Presidio sidecar
PHI_FILTER_TIMEOUT=4
PHI_FILTER_ENTITIES=PERSON,EMAIL_ADDRESS,PHONE_NUMBER,US_SSN
BAYLEAF_BASE_URL=https://bayleaf.nonnenmacher.tech
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=documents
QDRANT_DISTANCE=Cosine
QDRANT_TIMEOUT=20
EMBEDDING_MODELS=intfloat/multilingual-e5-base,BAAI/bge-m3
EMBEDDING_DEFAULT_MODEL=intfloat/multilingual-e5-base
DATABASE_URL=postgresql+psycopg://bayleaf:bayleaf@db:5432/bayleaf_agents
LOG_LEVEL=INFO
```

## Development

### Run locally (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn bayleaf_agents.app:create_app --factory --reload --port 8080
```

### Tests

```bash
docker compose --profile dev run --rm agents-dev pytest
```

or locally:

```bash
pytest -q
```

## Database & Migrations

* Postgres runs via Docker Compose (`db` service).
* Alembic runs automatically on container start.
* Manual migration commands (if needed):

  ```bash
  alembic revision -m "desc"
  alembic upgrade head
  ```

## Production deployment

Pushes to `main` run the test suite and then deploy to the VPS over SSH. The
production `.env` stays at `/home/bayleaf/bayleaf-agents/.env` on the server.
Configure these GitHub Actions secrets:

* `VPS_HOST`
* `VPS_USER`
* `VPS_SSH_KEY`
* `VPS_PORT` (optional; defaults to `22`)

The production service binds to `127.0.0.1:8080` for the host nginx upstream.
To start it manually:

```bash
docker compose --profile prod up -d --build --remove-orphans
```

## Switch LLM provider

* Dev/default: `LLM_PROVIDER=mock`
* OpenAI:

  ```
  LLM_PROVIDER=openai
  OPENAI_API_KEY=sk-...
  OPENAI_MODEL=gpt-4o
  ```

*(Provider adapters live under `src/bayleaf_agents/llm/`.)*
