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
docker compose up --build
```

This also brings up a Presidio analyzer sidecar (spaCy-based) listening on `http://presidio-analyzer:3000/analyze` and exposed locally on `http://localhost:8001/analyze`. The agent calls it via `PHI_FILTER_URL`.

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
curl -sS -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "channel":"bayleaf_app",
    "patient_id":"uuid-demo",
    "message":"Tenho dor de cabeça desde ontem.",
    "locale":"pt-BR",
    "metadata":{}
  }' | jq
```

### Chat with conversation memory

```bash
curl -sS -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "channel":"bayleaf_app",
    "patient_id":"uuid-demo",
    "conversation_id":"demo-1",
    "message":"Sim, tenho náusea e sensibilidade à luz."
  }' | jq
```

## API

* `GET /health` → `{ status, env, provider }`
* `POST /chat` → body:

  ```json
  {
    "channel": "bayleaf_app | whatsapp | partner",
    "patient_id": "string",
    "message": "string",
    "locale": "pt-BR",
    "metadata": {},
    "conversation_id": "optional string"
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
LLM_PROVIDER=mock          # mock | openai
OPENAI_API_KEY=            # if LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o
PHI_FILTER_URL=http://localhost:8001/analyze  # spaCy + Presidio sidecar
PHI_FILTER_TIMEOUT=4
PHI_FILTER_ENTITIES=PERSON,EMAIL_ADDRESS,PHONE_NUMBER,US_SSN
BAYLEAF_BASE_URL=https://bayleaf.nonnenmacher.tech
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

## Switch LLM provider

* Dev/default: `LLM_PROVIDER=mock`
* OpenAI:

  ```
  LLM_PROVIDER=openai
  OPENAI_API_KEY=sk-...
  OPENAI_MODEL=gpt-4o
  ```

*(Provider adapters live under `src/bayleaf_agents/llm/`.)*
