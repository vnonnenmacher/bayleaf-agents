import hashlib
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
import structlog

from ..auth.deps import Principal
from ..tools.bayleaf import BayleafClient


class DocumentServiceError(Exception):
    def __init__(self, status_code: int, message: str, details: Any = None):
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(message)


class QdrantDocumentsService:
    def __init__(
        self,
        base_url: str,
        collection_prefix: str,
        distance: str,
        timeout: int,
        bayleaf: BayleafClient,
        allowed_models: List[str],
        default_model: str,
    ):
        self.base = base_url.rstrip("/")
        self.collection_prefix = collection_prefix
        self.distance = distance
        self.timeout = timeout
        self.bayleaf = bayleaf
        self.allowed_models = [m.strip() for m in allowed_models if m.strip()]
        if not self.allowed_models:
            raise RuntimeError("allowed_models must not be empty")
        self.default_model = default_model if default_model in self.allowed_models else self.allowed_models[0]
        self.log = structlog.get_logger("qdrant_documents")
        self._embedders: Dict[str, Any] = {}
        self._model_dims: Dict[str, int] = {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            response = requests.request(
                method=method,
                url=f"{self.base}{path}",
                json=json_data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DocumentServiceError(503, "qdrant_unavailable", str(exc)) from exc

        try:
            body = response.json()
        except ValueError:
            body = {"status": "error", "result": None, "error": response.text}

        if response.status_code >= 400:
            raise DocumentServiceError(response.status_code, "qdrant_request_failed", body)
        return body

    def _resolve_model(self, model_used: Optional[str]) -> str:
        model = model_used or self.default_model
        if model not in self.allowed_models:
            raise DocumentServiceError(
                400,
                "invalid_model_used",
                {"model_used": model, "allowed_models": self.allowed_models},
            )
        return model

    def _collection_name(self, model_used: str) -> str:
        safe_model = re.sub(r"[^a-z0-9]+", "-", model_used.lower()).strip("-")
        safe_model = safe_model[:32] if safe_model else "model"
        suffix = hashlib.sha1(model_used.encode("utf-8")).hexdigest()[:8]
        return f"{self.collection_prefix}_{safe_model}_{suffix}"

    def _get_embedder(self, model_used: str) -> Any:
        embedder = self._embedders.get(model_used)
        if embedder is not None:
            return embedder
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise DocumentServiceError(
                500,
                "embedding_dependency_missing",
                "Install sentence-transformers to use local embedding models.",
            ) from exc
        try:
            embedder = SentenceTransformer(model_used)
        except Exception as exc:
            raise DocumentServiceError(500, "embedding_model_load_failed", str(exc)) from exc
        self._embedders[model_used] = embedder
        return embedder

    def _embed(self, text: str, model_used: str) -> List[float]:
        embedder = self._get_embedder(model_used)
        try:
            vector = embedder.encode(text, normalize_embeddings=True)
        except TypeError:
            vector = embedder.encode(text)
        except Exception as exc:
            raise DocumentServiceError(500, "embedding_failed", str(exc)) from exc

        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if isinstance(vector, list) and vector and isinstance(vector[0], list):
            vector = vector[0]
        if not isinstance(vector, list):
            raise DocumentServiceError(500, "embedding_invalid_output")
        return [float(v) for v in vector]

    def _model_dim(self, model_used: str) -> int:
        cached = self._model_dims.get(model_used)
        if cached is not None:
            return cached
        dim = len(self._embed("dim_probe", model_used=model_used))
        self._model_dims[model_used] = dim
        return dim

    def _ensure_collection(self, model_used: str) -> str:
        collection = self._collection_name(model_used)
        try:
            self._request(
                "PUT",
                f"/collections/{collection}",
                json_data={
                    "vectors": {
                        "size": self._model_dim(model_used),
                        "distance": self.distance,
                    }
                },
            )
        except DocumentServiceError as exc:
            if exc.status_code != 409:
                raise
        return collection

    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
        clean = " ".join(text.split())
        if not clean:
            return []
        out: List[str] = []
        start = 0
        while start < len(clean):
            end = min(len(clean), start + chunk_size)
            out.append(clean[start:end])
            if end >= len(clean):
                break
            start = max(0, end - overlap)
        return out

    def _extract_text(self, content: bytes, filename: str, mime_type: Optional[str]) -> Tuple[str, str]:
        mime = (mime_type or "").lower()
        text = ""
        status = "indexed"
        is_pdf = mime == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf:
            try:
                from pypdf import PdfReader
            except Exception as exc:
                raise DocumentServiceError(
                    500,
                    "pdf_dependency_missing",
                    "Install pypdf to extract text from PDF files.",
                ) from exc
            try:
                reader = PdfReader(io.BytesIO(content))
                pages: List[str] = []
                for page in reader.pages:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        pages.append(page_text)
                if pages:
                    text = "\n".join(pages)
                    return text, status
                # Common for scanned PDFs without embedded text layer.
                status = "indexed_pdf_no_text"
            except Exception as exc:
                raise DocumentServiceError(422, "pdf_text_extraction_failed", str(exc)) from exc

        if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
            text = content.decode("utf-8", errors="ignore")
        else:
            decoded = content.decode("utf-8", errors="ignore")
            if decoded.strip():
                text = decoded
            else:
                digest = hashlib.sha256(content).hexdigest()
                text = f"binary document {filename} sha256 {digest}"
                status = "indexed_metadata_only"
        return text, status

    def _delete_document_points(self, collection: str, document_uuid: str) -> None:
        self._request(
            "POST",
            f"/collections/{collection}/points/delete",
            json_data={
                "filter": {
                    "must": [
                        {
                            "key": "document_uuid",
                            "match": {"value": document_uuid},
                        }
                    ]
                }
            },
        )

    def _index_payload(
        self,
        *,
        document_uuid: str,
        filename: str,
        mime_type: Optional[str],
        source_type: str,
        bayleaf_document_uuid: Optional[str],
        text: str,
        status: str,
        content_sha256: str,
        model_used: str,
    ) -> Dict[str, Any]:
        collection = self._ensure_collection(model_used)
        chunks = self._chunk_text(text)
        if not chunks:
            chunks = [f"empty document {filename}"]
            status = "indexed_empty"

        indexed_at = datetime.now(timezone.utc).isoformat()
        points: List[Dict[str, Any]] = []
        chunk_count = len(chunks)
        for idx, chunk in enumerate(chunks):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{model_used}:{document_uuid}:{idx}"))
            points.append(
                {
                    "id": point_id,
                    "vector": self._embed(chunk, model_used=model_used),
                    "payload": {
                        "document_uuid": document_uuid,
                        "name": filename,
                        "description": None,
                        "mime_type": mime_type,
                        "source_type": source_type,
                        "is_bayleaf": source_type == "bayleaf",
                        "bayleaf_document_uuid": bayleaf_document_uuid,
                        "model_used": model_used,
                        "status": status,
                        "indexed_at": indexed_at,
                        "content_sha256": content_sha256,
                        "chunk_index": idx,
                        "chunk_count": chunk_count,
                        "text_chunk": chunk,
                    },
                }
            )

        self._delete_document_points(collection, document_uuid)
        self._request(
            "PUT",
            f"/collections/{collection}/points?wait=true",
            json_data={"points": points},
        )

        return {
            "uuid": document_uuid,
            "name": filename,
            "description": None,
            "status": status,
            "is_bayleaf": source_type == "bayleaf",
            "chunks": chunk_count,
            "source_type": source_type,
            "indexed_at": indexed_at,
            "model_used": model_used,
        }

    def _download_file(self, url: str) -> Tuple[bytes, str, Optional[str]]:
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise DocumentServiceError(502, "document_download_failed", str(exc)) from exc

        parsed = urlparse(url)
        filename = parsed.path.rsplit("/", 1)[-1] or "document.bin"
        disposition = r.headers.get("content-disposition", "")
        if "filename=" in disposition:
            filename = disposition.split("filename=", 1)[1].strip().strip('"')
        return r.content, filename, r.headers.get("content-type")

    def _extract_download_url(self, data: Dict[str, Any]) -> Optional[str]:
        def _pick_url(payload: Dict[str, Any]) -> Optional[str]:
            for key in ("download_url", "url", "file_url", "download"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        direct = _pick_url(data)
        if direct:
            return direct

        for key in ("data", "result", "details"):
            nested = data.get(key)
            if isinstance(nested, dict):
                nested_url = _pick_url(nested)
                if nested_url:
                    return nested_url
            if isinstance(nested, str) and nested.strip().startswith("http"):
                return nested.strip()

        return None

    def index_document(
        self,
        document_uuid: str,
        principal: Principal,
        model_used: Optional[str] = None,
    ) -> Dict[str, Any]:
        model = self._resolve_model(model_used)
        data = self.bayleaf.document_download_url(
            document_uuid=document_uuid,
            principal=principal,
        )
        if not isinstance(data, dict):
            raise DocumentServiceError(502, "invalid_download_url_response", data)
        if data.get("error"):
            raise DocumentServiceError(502, "download_url_request_failed", data)

        download_url = self._extract_download_url(data)
        if not download_url:
            raise DocumentServiceError(502, "missing_download_url", data)

        content, filename, mime_type = self._download_file(str(download_url))
        text, status = self._extract_text(content, filename, mime_type)
        digest = hashlib.sha256(content).hexdigest()
        return self._index_payload(
            document_uuid=document_uuid,
            filename=filename,
            mime_type=mime_type,
            source_type="bayleaf",
            bayleaf_document_uuid=document_uuid,
            text=text,
            status=status,
            content_sha256=digest,
            model_used=model,
        )

    def index_uploaded_document(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: Optional[str],
        model_used: Optional[str] = None,
    ) -> Dict[str, Any]:
        model = self._resolve_model(model_used)
        document_uuid = str(uuid.uuid4())
        text, status = self._extract_text(content, filename, mime_type)
        digest = hashlib.sha256(content).hexdigest()
        return self._index_payload(
            document_uuid=document_uuid,
            filename=filename,
            mime_type=mime_type,
            source_type="uploaded",
            bayleaf_document_uuid=None,
            text=text,
            status=status,
            content_sha256=digest,
            model_used=model,
        )

    def _scroll_collection(
        self,
        collection: str,
        scroll_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        points: List[Dict[str, Any]] = []
        offset: Any = None
        while True:
            payload: Dict[str, Any] = {
                "limit": 256,
                "with_payload": True,
                "with_vector": False,
            }
            if scroll_filter:
                payload["filter"] = scroll_filter
            if offset is not None:
                payload["offset"] = offset
            try:
                data = self._request(
                    "POST",
                    f"/collections/{collection}/points/scroll",
                    json_data=payload,
                )
            except DocumentServiceError as exc:
                if exc.status_code == 404:
                    return []
                raise

            result = data.get("result") or {}
            batch = result.get("points") or []
            points.extend(batch)
            offset = result.get("next_page_offset")
            if offset is None:
                break
        return points

    def _find_latest_document_points(self, document_uuid: str) -> Tuple[str, List[Dict[str, Any]]]:
        latest_model = ""
        latest_points: List[Dict[str, Any]] = []
        latest_ts = ""
        for model in self.allowed_models:
            collection = self._collection_name(model)
            points = self._scroll_collection(
                collection,
                {
                    "must": [
                        {
                            "key": "document_uuid",
                            "match": {"value": document_uuid},
                        }
                    ]
                },
            )
            if not points:
                continue
            ts = str(((points[0] or {}).get("payload") or {}).get("indexed_at", ""))
            if ts >= latest_ts:
                latest_ts = ts
                latest_model = model
                latest_points = points
        if not latest_points:
            raise DocumentServiceError(404, "document_not_found")
        return latest_model, latest_points

    def documents_available(self) -> List[Dict[str, Any]]:
        docs: Dict[str, Dict[str, Any]] = {}
        for model in self.allowed_models:
            collection = self._collection_name(model)
            for point in self._scroll_collection(collection):
                payload = point.get("payload") or {}
                doc_uuid = payload.get("document_uuid")
                if not doc_uuid:
                    continue
                current = docs.get(doc_uuid)
                if current is None or str(payload.get("indexed_at", "")) > str(current.get("indexed_at", "")):
                    docs[doc_uuid] = {
                        "uuid": doc_uuid,
                        "name": payload.get("name"),
                        "description": payload.get("description"),
                        "status": payload.get("status"),
                        "is_bayleaf": bool(payload.get("is_bayleaf")),
                        "source_type": payload.get("source_type"),
                        "indexed_at": payload.get("indexed_at"),
                        "chunks": payload.get("chunk_count") or 0,
                        "model_used": payload.get("model_used"),
                    }
        return sorted(docs.values(), key=lambda d: d.get("indexed_at") or "", reverse=True)

    def _extract_document_uuids(self, payload: Any) -> Set[str]:
        collected: Set[str] = set()
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            flags=re.IGNORECASE,
        )

        def _walk(node: Any):
            if isinstance(node, dict):
                lowered_keys = {str(key).lower() for key in node.keys()}
                is_document_item_with_id = "id" in lowered_keys and "doc_key" in lowered_keys
                for key, value in node.items():
                    key_text = str(key).lower()
                    if isinstance(value, str):
                        value_text = value.strip()
                        if uuid_pattern.match(value_text):
                            if "uuid" in key_text:
                                collected.add(value_text)
                            elif key_text == "id" and is_document_item_with_id:
                                collected.add(value_text)
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(payload)
        return collected

    def document_uuids_for_doc_key(
        self,
        *,
        doc_key: str,
        principal: Optional[Principal],
    ) -> List[str]:
        if not doc_key.strip():
            self.log.info(
                "doc_key_uuid_resolution",
                doc_key=doc_key,
                principal_user_id=(principal.user_id if principal else None),
                source_items_count=0,
                extracted_uuids_count=0,
            )
            return []
        docs = self.bayleaf.documents_by_doc_key(doc_key=doc_key.strip(), principal=principal)
        uuids: Set[str] = set()
        for item in docs:
            uuids.update(self._extract_document_uuids(item))
        resolved = sorted(uuids)
        self.log.info(
            "doc_key_uuid_resolution",
            doc_key=doc_key.strip(),
            principal_user_id=(principal.user_id if principal else None),
            source_items_count=len(docs),
            extracted_uuids_count=len(resolved),
        )
        return resolved

    def get_document(self, document_uuid: str) -> Dict[str, Any]:
        _, points = self._find_latest_document_points(document_uuid=document_uuid)
        payload = (points[0] or {}).get("payload") or {}
        return {
            "uuid": document_uuid,
            "name": payload.get("name"),
            "description": payload.get("description"),
            "status": payload.get("status"),
            "is_bayleaf": bool(payload.get("is_bayleaf")),
            "source_type": payload.get("source_type"),
            "indexed_at": payload.get("indexed_at"),
            "chunks": payload.get("chunk_count") or len(points),
            "model_used": payload.get("model_used"),
        }

    def reindex_document(
        self,
        document_uuid: str,
        principal: Principal,
        model_used: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_model, source_points = self._find_latest_document_points(document_uuid=document_uuid)
        target_model = self._resolve_model(model_used or source_model)

        payload = (source_points[0] or {}).get("payload") or {}
        source_type = payload.get("source_type")
        bayleaf_document_uuid = payload.get("bayleaf_document_uuid") or payload.get("bayleaf_document_version_uuid")
        if source_type == "bayleaf" and bayleaf_document_uuid:
            return self.index_document(
                document_uuid=str(bayleaf_document_uuid),
                principal=principal,
                model_used=target_model,
            )

        sorted_points = sorted(source_points, key=lambda p: (p.get("payload") or {}).get("chunk_index", 0))
        text = "\n".join((p.get("payload") or {}).get("text_chunk", "") for p in sorted_points).strip()
        filename = payload.get("name") or f"{document_uuid}.txt"
        mime_type = payload.get("mime_type")
        digest = payload.get("content_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        status = payload.get("status") or "indexed"
        return self._index_payload(
            document_uuid=document_uuid,
            filename=filename,
            mime_type=mime_type,
            source_type="uploaded",
            bayleaf_document_uuid=None,
            text=text or f"document {document_uuid}",
            status=status,
            content_sha256=digest,
            model_used=target_model,
        )

    def _query_collection(
        self,
        *,
        collection: str,
        vector: List[float],
        limit: int,
        query_filter: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if query_filter:
            payload["filter"] = query_filter
        try:
            data = self._request("POST", f"/collections/{collection}/points/search", json_data=payload)
        except DocumentServiceError as exc:
            if exc.status_code == 404:
                return []
            raise
        return data.get("result") or []

    def _build_query_filter(
        self,
        *,
        document_uuid: Optional[str],
        document_uuids: Optional[List[str]],
        source_type: Optional[str],
        is_bayleaf: Optional[bool],
    ) -> Optional[Dict[str, Any]]:
        must: List[Dict[str, Any]] = []
        should: List[Dict[str, Any]] = []
        if document_uuid:
            must.append({"key": "document_uuid", "match": {"value": document_uuid}})
        elif document_uuids:
            should = [{"key": "document_uuid", "match": {"value": doc_id}} for doc_id in document_uuids]
        if source_type:
            must.append({"key": "source_type", "match": {"value": source_type}})
        if is_bayleaf is not None:
            must.append({"key": "is_bayleaf", "match": {"value": is_bayleaf}})
        if not must and not should:
            return None
        out: Dict[str, Any] = {}
        if must:
            out["must"] = must
        if should:
            out["should"] = should
        return out

    def query_documents(
        self,
        *,
        query: str,
        top_k: int = 5,
        model_used: Optional[str] = None,
        document_uuid: Optional[str] = None,
        document_uuids: Optional[List[str]] = None,
        source_type: Optional[str] = None,
        is_bayleaf: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not query.strip():
            raise DocumentServiceError(400, "query_required")
        if top_k < 1 or top_k > 50:
            raise DocumentServiceError(400, "invalid_top_k", {"top_k": top_k, "min": 1, "max": 50})

        model = self._resolve_model(model_used)
        collection = self._ensure_collection(model)
        query_filter = self._build_query_filter(
            document_uuid=document_uuid,
            document_uuids=document_uuids,
            source_type=source_type,
            is_bayleaf=is_bayleaf,
        )
        vector = self._embed(query, model_used=model)
        matches = self._query_collection(
            collection=collection,
            vector=vector,
            limit=top_k,
            query_filter=query_filter,
        )

        trace_id = f"retr_{uuid.uuid4().hex[:12]}"
        retrieved_at = datetime.now(timezone.utc).isoformat()
        chunks: List[Dict[str, Any]] = []
        for item in matches:
            payload = item.get("payload") or {}
            chunks.append(
                {
                    "score": item.get("score"),
                    "document_uuid": payload.get("document_uuid"),
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "chunk_index": payload.get("chunk_index"),
                    "chunk_count": payload.get("chunk_count"),
                    "text_chunk": payload.get("text_chunk"),
                    "model_used": payload.get("model_used"),
                    "source_type": payload.get("source_type"),
                    "is_bayleaf": payload.get("is_bayleaf"),
                    "indexed_at": payload.get("indexed_at"),
                }
            )

        return {
            "query": query,
            "top_k": top_k,
            "model_used": model,
            "chunks": chunks,
            "trace": {
                "trace_id": trace_id,
                "retrieved_at": retrieved_at,
                "collection": collection,
                "model_used": model,
                "query_filter": query_filter,
                "requested_top_k": top_k,
                "returned_chunks": len(chunks),
            },
        }
