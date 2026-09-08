import os
import re
from pydantic import BaseModel, Field


class Settings(BaseModel):
    APP_ENV: str = Field(default=os.getenv("APP_ENV", "prod"))
    HOST: str = Field(default=os.getenv("HOST", "0.0.0.0"))
    PORT: int = Field(default=int(os.getenv("PORT", "8080")))
    LOG_LEVEL: str = Field(default=os.getenv("LOG_LEVEL", "DEBUG"))
    ALLOWED_HOSTS: str = Field(default=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,labcopilot.nonnenmacher.tech"))

    # LLM
    LLM_PROVIDER: str = Field(default=os.getenv("LLM_PROVIDER", "mock"))  # mock | openai
    OPENAI_API_KEY: str = Field(default=os.getenv("OPENAI_API_KEY", ""))
    OPENAI_MODEL: str = Field(default=os.getenv("OPENAI_MODEL", "gpt-4o"))
    DECIDER_LLM_PROVIDER: str = Field(default=os.getenv("DECIDER_LLM_PROVIDER", ""))
    DECIDER_OPENAI_MODEL: str = Field(default=os.getenv("DECIDER_OPENAI_MODEL", "gpt-4o"))

    # PHI filter (spaCy + Presidio sidecar)
    PHI_FILTER_URL: str = Field(default=os.getenv("PHI_FILTER_URL", "http://localhost:8001/analyze"))
    PHI_FILTER_TIMEOUT: int = Field(default=int(os.getenv("PHI_FILTER_TIMEOUT", "4")))
    # Default PHI entities; DATE_TIME removed (not treated as PHI in this flow)
    PHI_FILTER_ENTITIES: str = Field(default=os.getenv("PHI_FILTER_ENTITIES", "PERSON,EMAIL_ADDRESS,PHONE_NUMBER,US_SSN"))

    # Bayleaf API
    BAYLEAF_BASE_URL: str = Field(default=os.getenv("BAYLEAF_BASE_URL", "http://localhost:8000"))

    DATABASE_URL: str = Field(default=os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://bayleaf:bayleaf@db:5432/bayleaf_agents"
    ))

    # Async chat execution (Celery + Redis)
    REDIS_URL: str = Field(default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    CELERY_BROKER_URL: str = Field(default=os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")))
    CELERY_RESULT_BACKEND: str = Field(default=os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/0")))
    # Run tasks synchronously in-process (useful for local/dev/tests without a broker)
    CELERY_TASK_ALWAYS_EAGER: bool = Field(
        default=os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").strip().lower() == "true"
    )

    IDP_ISSUER: str = Field(default=os.getenv("IDP_ISSUER", "https://auth.bayleaf"))
    IDP_AUDIENCE_AGENT: str = Field(default=os.getenv("IDP_AUDIENCE_AGENT", "agent"))
    IDP_JWKS_URL: str = Field(default=os.getenv("IDP_JWKS_URL", "https://auth.bayleaf/.well-known/jwks.json"))
    IDP_ALLOWED_ALGS: str = Field(default=os.getenv("IDP_ALLOWED_ALGS", "RS256,ES256"))
    REQUIRED_SCOPES: str = Field(default=os.getenv("REQUIRED_SCOPES", "chat.send"))

    # Outbound auth (Agent -> Bayleaf)
    BAYLEAF_TOKEN_URL: str = Field(default=os.getenv("BAYLEAF_TOKEN_URL", ""))  # e.g., https://.../oauth/token
    BAYLEAF_CLIENT_ID: str = Field(default=os.getenv("BAYLEAF_CLIENT_ID", ""))
    BAYLEAF_CLIENT_SECRET: str = Field(default=os.getenv("BAYLEAF_CLIENT_SECRET", ""))
    BAYLEAF_TOKEN_MODE: str = Field(default=os.getenv("BAYLEAF_TOKEN_MODE", "static"))  # static|client_credentials|obo

    # Qdrant (document indexing)
    QDRANT_URL: str = Field(default=os.getenv("QDRANT_URL", "http://localhost:6333"))
    QDRANT_COLLECTION: str = Field(default=os.getenv("QDRANT_COLLECTION", "documents"))
    QDRANT_DISTANCE: str = Field(default=os.getenv("QDRANT_DISTANCE", "Cosine"))
    QDRANT_TIMEOUT: int = Field(default=int(os.getenv("QDRANT_TIMEOUT", "20")))
    EMBEDDING_MODELS: str = Field(default=os.getenv("EMBEDDING_MODELS", "intfloat/multilingual-e5-base"))
    EMBEDDING_DEFAULT_MODEL: str = Field(default=os.getenv("EMBEDDING_DEFAULT_MODEL", ""))

    def cors_allow_origins(self) -> list[str]:
        hosts = [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]
        if not hosts:
            return []
        if "*" in hosts:
            return ["*"]

        origins: list[str] = []
        for host in hosts:
            normalized = host.rstrip("/")
            if normalized.startswith("http://") or normalized.startswith("https://"):
                origins.append(normalized)
            elif ":" in normalized:
                origins.append(f"http://{normalized}")
                origins.append(f"https://{normalized}")
            else:
                origins.append(f"http://{normalized}")
                origins.append(f"https://{normalized}")

        # Preserve order while removing duplicates.
        return list(dict.fromkeys(origins))

    def cors_allow_origin_regex(self) -> str | None:
        hosts = [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]
        if not hosts or "*" in hosts:
            return None

        plain_hosts: list[str] = []
        for host in hosts:
            normalized = host.rstrip("/")
            if normalized.startswith("http://") or normalized.startswith("https://"):
                continue
            if ":" in normalized:
                continue
            plain_hosts.append(re.escape(normalized))

        if not plain_hosts:
            return None

        return rf"^https?://({'|'.join(plain_hosts)})(:\d+)?$"


settings = Settings()
