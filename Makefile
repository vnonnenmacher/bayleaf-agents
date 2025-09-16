run:
	uvicorn bayleaf_agents.app:create_app --reload --port 8080

lint:
	ruff check .

test:
	pytest -q
