#!/usr/bin/env bash

set -euo pipefail
alembic upgrade head
exec uvicorn bayleaf_agents.app:create_app --factory --host 0.0.0.0 --port 8080
