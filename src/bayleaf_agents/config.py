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

    # Bayleaf API
    BAYLEAF_BASE_URL: str = Field(default=os.getenv("BAYLEAF_BASE_URL", "http://localhost:8000"))
    BAYLEAF_TOKEN: str = Field(default=os.getenv("BAYLEAF_TOKEN", ""))


settings = Settings()
