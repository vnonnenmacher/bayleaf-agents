import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    APP_ENV: str = Field(default=os.getenv("APP_ENV", "dev"))
    HOST: str = Field(default=os.getenv("HOST", "0.0.0.0"))
    PORT: int = Field(default=int(os.getenv("PORT", "8080")))
    LOG_LEVEL: str = Field(default=os.getenv("LOG_LEVEL", "INFO"))

    # LLM
    LLM_PROVIDER: str = Field(default=os.getenv("LLM_PROVIDER", "mock"))  # mock | openai
    OPENAI_API_KEY: str = Field(default=os.getenv("OPENAI_API_KEY", ""))
    OPENAI_MODEL: str = Field(default=os.getenv("OPENAI_MODEL", "gpt-4o"))

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


settings = Settings()
