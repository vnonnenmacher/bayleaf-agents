from ..config import settings
from ..llm.base import LLMProvider
from ..llm.mock import MockProvider
from ..tools.bayleaf import BayleafClient
from ..tools.documents import DocumentsToolset
from ..services.phi_filter import PHIFilterClient
from ..services.qdrant_documents import QdrantDocumentsService

try:
    from ..llm.openai_provider import OpenAIProvider  # optional
except Exception:
    OpenAIProvider = None

_provider: LLMProvider | None = None
_bayleaf: BayleafClient | None = None
_phi_filter: PHIFilterClient | None = None
_qdrant_documents: QdrantDocumentsService | None = None
_documents_tools: DocumentsToolset | None = None
_decider_provider: LLMProvider | None = None


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


def get_phi_filter() -> PHIFilterClient:
    global _phi_filter
    if _phi_filter is None:
        _phi_filter = PHIFilterClient()
    return _phi_filter


def get_qdrant_documents() -> QdrantDocumentsService:
    global _qdrant_documents
    if _qdrant_documents is None:
        allowed_models = [m.strip() for m in settings.EMBEDDING_MODELS.split(",") if m.strip()]
        if not allowed_models:
            allowed_models = ["intfloat/multilingual-e5-base"]
        default_model = settings.EMBEDDING_DEFAULT_MODEL.strip() or (allowed_models[0] if allowed_models else "")
        _qdrant_documents = QdrantDocumentsService(
            base_url=settings.QDRANT_URL,
            collection_prefix=settings.QDRANT_COLLECTION,
            distance=settings.QDRANT_DISTANCE,
            timeout=settings.QDRANT_TIMEOUT,
            bayleaf=get_bayleaf(),
            allowed_models=allowed_models,
            default_model=default_model,
        )
    return _qdrant_documents


def get_documents_tools() -> DocumentsToolset:
    global _documents_tools
    if _documents_tools is None:
        _documents_tools = DocumentsToolset(get_qdrant_documents())
    return _documents_tools


def get_decider_provider() -> LLMProvider:
    global _decider_provider
    if _decider_provider is not None:
        return _decider_provider

    decider_provider = settings.DECIDER_LLM_PROVIDER.strip().lower()
    if not decider_provider:
        _decider_provider = get_provider()
        return _decider_provider

    if decider_provider == "openai" and OpenAIProvider:
        model = settings.DECIDER_OPENAI_MODEL.strip() or settings.OPENAI_MODEL
        _decider_provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY, model=model)
        return _decider_provider

    _decider_provider = MockProvider()
    return _decider_provider
