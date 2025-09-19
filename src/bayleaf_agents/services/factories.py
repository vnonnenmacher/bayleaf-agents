from ..config import settings
from ..llm.base import LLMProvider
from ..llm.mock import MockProvider
from ..tools.bayleaf import BayleafClient

try:
    from ..llm.openai_provider import OpenAIProvider  # optional
except Exception:
    OpenAIProvider = None

_provider: LLMProvider | None = None
_bayleaf: BayleafClient | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        if settings.LLM_PROVIDER == "openai" and OpenAIProvider:
            _provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL)
        else:
            _provider = MockProvider()
    return _provider


def get_bayleaf() -> BayleafClient:
    global _bayleaf
    if _bayleaf is None:
        _bayleaf = BayleafClient(settings.BAYLEAF_BASE_URL)
    return _bayleaf
