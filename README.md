# bayleaf-agents

FastAPI service that hosts **agents** (ChatGPT-like, but under your control) and calls **Bayleaf**
tools (REST) via a provider-agnostic LLM layer (OpenAI, Anthropic, local, etc.).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
uvicorn bayleaf_agents.app:create_app --reload --port 8080


Health: GET /health

Chat: POST /chat