from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth.deps import Principal, require_auth
from ..services.factories import get_documents_tools, get_qdrant_documents
from ..services.qdrant_documents import DocumentServiceError


class IndexedDocument(BaseModel):
    uuid: str
    name: str | None = None
    status: str
    is_bayleaf: bool
    chunks: int
    source_type: str | None = None
    indexed_at: str | None = None
    model_used: str | None = None


class DocumentsAvailableResponse(BaseModel):
    documents: list[IndexedDocument]


class DocumentIndexRequest(BaseModel):
    document_version_uuid: str
    model_used: str | None = None


class DocumentReindexRequest(BaseModel):
    model_used: str | None = None


class DocumentQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    model_used: str | None = None
    document_uuid: str | None = None
    source_type: str | None = None
    is_bayleaf: bool | None = None


class RetrievedChunk(BaseModel):
    score: float | None = None
    document_uuid: str | None = None
    name: str | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None
    text_chunk: str | None = None
    model_used: str | None = None
    source_type: str | None = None
    is_bayleaf: bool | None = None
    indexed_at: str | None = None


class DocumentQueryResponse(BaseModel):
    query: str
    top_k: int
    model_used: str
    chunks: list[RetrievedChunk]
    trace: dict


router = APIRouter(prefix="/agents", tags=["documents"])


def _raise_document_error(exc: DocumentServiceError) -> None:
    detail = {"error": exc.message}
    if exc.details is not None:
        detail["details"] = exc.details
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post("/documents/index/", response_model=IndexedDocument)
async def index_document(
    req: DocumentIndexRequest,
    principal: Principal = Depends(require_auth()),
):
    service = get_qdrant_documents()
    try:
        indexed = service.index_document_version(
            document_version_uuid=req.document_version_uuid,
            principal=principal,
            model_used=req.model_used,
        )
        return IndexedDocument(**indexed)
    except DocumentServiceError as exc:
        _raise_document_error(exc)


@router.post("/documents/index/upload/", response_model=IndexedDocument)
async def index_document_upload(
    file: UploadFile = File(...),
    model_used: str | None = Form(default=None),
    principal: Principal = Depends(require_auth()),
):
    _ = principal
    service = get_qdrant_documents()
    try:
        content = await file.read()
        indexed = service.index_uploaded_document(
            filename=file.filename or "uploaded_document",
            content=content,
            mime_type=file.content_type,
            model_used=model_used,
        )
        return IndexedDocument(**indexed)
    except DocumentServiceError as exc:
        _raise_document_error(exc)


@router.get("/documents-available/", response_model=DocumentsAvailableResponse)
async def documents_available(
    principal: Principal = Depends(require_auth()),
):
    _ = principal
    service = get_qdrant_documents()
    try:
        docs = service.documents_available()
        return DocumentsAvailableResponse(documents=[IndexedDocument(**d) for d in docs])
    except DocumentServiceError as exc:
        _raise_document_error(exc)


@router.get("/documents/{document_uuid}/", response_model=IndexedDocument)
async def get_document(
    document_uuid: str,
    principal: Principal = Depends(require_auth()),
):
    _ = principal
    service = get_qdrant_documents()
    try:
        doc = service.get_document(document_uuid=document_uuid)
        return IndexedDocument(**doc)
    except DocumentServiceError as exc:
        _raise_document_error(exc)


@router.post("/documents/query/", response_model=DocumentQueryResponse)
async def query_documents(
    req: DocumentQueryRequest,
    principal: Principal = Depends(require_auth()),
):
    _ = principal
    tools = get_documents_tools()
    try:
        result = tools.query_documents(
            query=req.query,
            top_k=req.top_k,
            model_used=req.model_used,
            document_uuid=req.document_uuid,
            source_type=req.source_type,
            is_bayleaf=req.is_bayleaf,
        )
        return DocumentQueryResponse(
            query=result["query"],
            top_k=result["top_k"],
            model_used=result["model_used"],
            chunks=[RetrievedChunk(**chunk) for chunk in result["chunks"]],
            trace=result["trace"],
        )
    except DocumentServiceError as exc:
        _raise_document_error(exc)


@router.post("/documents/{document_uuid}/reindex/", response_model=IndexedDocument)
async def reindex_document(
    document_uuid: str,
    req: DocumentReindexRequest = DocumentReindexRequest(),
    principal: Principal = Depends(require_auth()),
):
    service = get_qdrant_documents()
    try:
        doc = service.reindex_document(
            document_uuid=document_uuid,
            principal=principal,
            model_used=req.model_used,
        )
        return IndexedDocument(**doc)
    except DocumentServiceError as exc:
        _raise_document_error(exc)
