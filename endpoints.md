# Bayleaf Agents API Endpoints

This document is the implementation-level API contract for client apps and client-side agents that call the Bayleaf Agents service in this repository.

It reflects the FastAPI routers, request/response schemas, auth dependency behavior, and important runtime caveats.

## Base URL and version

Local development base URL is usually:

`http://localhost:8080`

This service currently reports version `0.1.0` in the FastAPI app metadata.

## Transport and canonical URLs

- JSON endpoints expect `Content-Type: application/json` unless explicitly marked as multipart upload.
- Any path that ends with a trailing slash is redirected to the canonical non-trailing form with HTTP `308`.
- Query strings are preserved on redirects.

Example:

- `GET /health/` -> `308 Location: /health`
- `GET /agents/user-metadata/?foo=bar` -> `308 Location: /agents/user-metadata?foo=bar`

## Authentication

All `/agents/*` endpoints require a bearer token:

```http
Authorization: Bearer <token>
```

Current auth behavior:

- Missing or non-bearer header returns `401 {"detail":"missing_token"}`.
- The token payload is decoded without signature verification (best-effort claim extraction).
- An `exp` claim in the past returns `401 {"detail":{"error":"token_expired"}}` immediately, before any request processing (including async chat submission).
- `user_id` is read from `user_id` claim, falling back to `sub`.
- Endpoints that require an owner identity return `401 {"detail":"missing_user_id_claim"}` when neither claim is present.
- Optional scope checks are implemented in the auth dependency but are not currently required by these routes.

## Common response patterns

### Offset pagination shape

Several list endpoints use offset pagination:

```json
{
  "items": [],
  "pagination": {
    "total": 0,
    "limit": 20,
    "offset": 0,
    "has_more": false
  }
}
```

### Error shape

Most framework-level errors follow FastAPI style:

```json
{ "detail": "some_error_code" }
```

Document service errors are wrapped as:

```json
{
  "detail": {
    "error": "error_code",
    "details": {}
  }
}
```

## Agent chat endpoints

Base prefix: `/agents`

The chat endpoint is dynamic per discovered agent class:

`POST /agents/{agent_slug}/chat`

Currently discovered slugs in this codebase are:

- `appointment`
- `treatment`
- `labcopilot`

Chat is **asynchronous**: this endpoint only submits the request and returns immediately.
It does not wait for the assistant reply. Callers must poll the request lifecycle
endpoint below until the request reaches a terminal state.

> **Migrating from the old synchronous contract:** if your client previously read
> `reply`/`used_tools`/`citations`/`trace_id` straight off the `POST /chat` response, that
> response body no longer exists. Instead: (1) submit as before, but only keep the
> returned `id` and `conversation_id`; (2) poll `GET /agents/requests/{id}` (e.g. every
> 1-2s) until `state` is `succeeded`, `failed`, or `cancelled`; (3) find the assistant's
> reply as the last `role: "assistant"` entry in `messages`, and read `cited_documents`/
> `citations`/`retrieval_trace` off that same message. There is no `trace_id` field
> anymore — use the `id` (agent request id) for correlation instead.

### Request body

```json
{
  "channel": "bayleaf_app",
  "message": "I have a headache.",
  "conversation_id": "optional-client-or-server-conversation-id",
  "group_id": "optional-conversation-group-id",
  "document_uuids": ["optional", "document", "scope"],
  "lang": "pt-BR"
}
```

Field semantics:

- `channel` (required): one of `bayleaf_app`, `whatsapp`, `partner`.
- `message` (required): min length 1.
- `conversation_id` (optional): may be a client external id or an internal conversation UUID returned previously.
- `group_id` (optional): conversation group to bind context and optional forced document scope.
- `document_uuids` (optional): caller-provided document scope, deduplicated and merged with group documents when `group_id` is used.
- `lang` (optional): locale hint passed to the agent and PHI redaction flow (defaults to `pt-BR` when omitted).

### Response body

Returns the created `AgentRequest` immediately, in `waiting` or `processing` state.
The conversation is resolved (or created, if `conversation_id` was omitted) synchronously
during this call, so `conversation_id` is already known on the response. The final assistant
reply is not present yet; poll `GET /agents/requests/{id}` for it.

```json
{
  "id": "agent-request-uuid",
  "user_id": "349",
  "agent_slug": "treatment",
  "channel": "bayleaf_app",
  "conversation_id": "conv-id-or-external-id",
  "state": "waiting",
  "error_message": null,
  "created_at": "ISO-8601",
  "started_at": null,
  "finished_at": null,
  "cancelled_at": null,
  "messages": [
    {
      "id": "message-uuid",
      "conversation_id": "conv-id-or-external-id",
      "role": "user",
      "content": "I have a headache.",
      "redacted_content": null,
      "tool_name": null,
      "tool_args": null,
      "tool_result": null,
      "retrieval_trace": null,
      "cited_documents": [],
      "citations": [],
      "created_at": "ISO-8601"
    }
  ]
}
```

### Chat-specific errors and constraints

- `401` if missing token, or if the token's `exp` claim is already in the past (see Authentication section).
- `404 {"detail":"group_not_found"}` if `group_id` does not belong to the caller.
- `422 {"detail":"group_inactive"}` if `group_id` exists but is inactive.
- `409 {"detail":"conversation_group_mismatch"}` if an existing conversation is reused with a different group than originally bound.
- `409 {"detail":"active_request_conflict"}` if the resolved conversation (whether passed explicitly via `conversation_id` or freshly created) already has another active (`waiting`/`processing`) `AgentRequest` for the same caller. This check always runs, since the conversation is now always resolved synchronously before submission; a brand-new conversation can never conflict.

## Agent request lifecycle

`AgentRequest` is the async lifecycle owner for a chat submission. It is not a conversation
and does not store a `conversation_id` column directly (per the ownership model), but the
conversation is resolved synchronously in the same call that creates the request, so
`conversation_id` is available immediately on both the request and its initial message.

### States

- `waiting`: created, not yet picked up by a worker.
- `processing`: a worker is actively running the chat logic.
- `succeeded`: terminal. Assistant reply and any tool/internal messages are in `messages`.
- `failed`: terminal. `error_message` holds the failure reason. Any messages persisted before the failure remain.
- `cancelled`: terminal. Set via the cancel endpoint; best-effort if already `processing`.

Allowed transitions: `waiting -> processing`, `processing -> succeeded`, `processing -> failed`,
`waiting -> cancelled`, `processing -> cancelled`.

Clients should poll `GET /agents/requests/{id}` until `state` is one of the terminal states above.
There is no automatic retry of failed requests.

### `GET /agents/requests/{agent_request_id}`

Returns the current `AgentRequestResponse` (same shape as the chat submit response above),
including all messages linked to the request ordered by `created_at` ascending.

Ownership is enforced: a request only resolves for the authenticated caller that created it
(matched by `user_id` claim), otherwise `404 {"detail":"agent_request_not_found"}`.

### `POST /agents/requests/{agent_request_id}/cancel`

Moves an active (`waiting`/`processing`) request to `cancelled`. Idempotent if the request is
already in a terminal state — returns the request unchanged. Same ownership rule as above
(`404` if not owned by the caller).

### `POST /agents/requests/{agent_request_id}/retry`

Creates a **new** `AgentRequest` reusing the original submission payload (including the
resolved `conversation_id`) and schedules it for processing. Returns the new `AgentRequestResponse`.

- Only allowed when the original request is `failed` or `cancelled`; otherwise
  `409 {"detail":"retry_not_allowed"}`.
- `409 {"detail":"active_request_conflict"}` if the original request's conversation (once
  resolvable) already has another active request for the same caller.
- Same ownership rule as retrieve/cancel (`404` if the original request is not owned by the caller).

## Conversation groups

Conversation groups let callers store reusable context and document scope per authenticated owner.

### `POST /agents/conversation-groups`

Create a group.

Request:

```json
{
  "type": "project",
  "metadata": {},
  "document_uuids": ["doc-1", "doc-2"],
  "is_active": true
}
```

Rules:

- `type` is required and must be `project` or `event`.
- `document_uuids` are normalized (trimmed, deduplicated, empty values removed).

Response `200`:

```json
{
  "id": "group-uuid",
  "owner_id": "user-1",
  "type": "project",
  "is_active": true,
  "metadata": {},
  "document_uuids": ["doc-1", "doc-2"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### `PATCH /agents/conversation-groups/{group_id}`

Partial update of group fields.

Allowed fields:

- `is_active`
- `metadata`
- `document_uuids` (re-normalized)

Returns updated `ConversationGroupSummary`.

### `PUT /agents/conversation-groups/{group_id}`

Full replacement of mutable group content.

Request:

```json
{
  "metadata": {},
  "document_uuids": []
}
```

Behavior:

- Replaces `metadata` entirely.
- Replaces `document_uuids` entirely (after normalization).

### `GET /agents/conversation-groups`

List caller-owned groups with filters.

Query parameters:

- `type` (optional): `project` or `event`.
- `is_active` (optional): boolean.
- `limit` (default `20`, min `1`, max `100`).
- `offset` (default `0`, min `0`).

Sort order:

- `updated_at` descending, then `created_at` descending.

Errors:

- `422 {"detail":"invalid_group_type_filter"}` when `type` is invalid.

## Conversations and messages

### `GET /agents/conversations`

List conversations owned by the authenticated user with activity metadata.

Query parameters:

- `agent_slug` (optional)
- `channel` (optional)
- `group_id` (optional)
- `without_group` (optional boolean, default `false`)
- `limit` (default `20`, min `1`, max `100`)
- `offset` (default `0`, min `0`)

Group filtering details:

- `group_id=null` or `group_id=none` (case-insensitive) filters to conversations without a group.
- Any other `group_id` value must resolve to a group owned by caller, else `404 group_not_found`.
- `without_group=true` filters to `group_id IS NULL` when `group_id` is not supplied.

Response item fields:

- `conversation_id` (external id when present, else internal id)
- `name`
- `external_id`
- `channel`
- `agent_slug`
- `group_id`
- `created_at`
- `last_message_at`
- `message_count`

### `GET /agents/conversations/{conversation_id}/messages`

List messages for one conversation owned by caller.

Path parameter:

- `conversation_id`: can be either external id or internal id.

Query parameters:

- `role` (optional): one of stored message roles (`system`, `user`, `assistant`, `tool`).
- `include_tools` (optional boolean, default `false`).
- `channel` (optional): narrows conversation lookup.
- `agent_slug` (optional): narrows conversation lookup.
- `limit` (default `50`, min `1`, max `200`).
- `offset` (default `0`, min `0`).

Default visibility behavior:

- If `role` is not provided and `include_tools=false`, only user/assistant messages are returned and tool messages are excluded.

Response fields per message:

- `id`
- `role`
- `content`
- `created_at`
- `tool_name` (nullable)
- `cited_documents` (list)
- `citations` (list)

Errors:

- `404 {"detail":"conversation_not_found"}` when no owned conversation matches the identifier/filter combination.
- `422 {"detail":"invalid_role_filter"}` when `role` is invalid.

## User metadata

User metadata is a single per-owner JSON object stored in the service DB.

### `PUT /agents/user-metadata`

Upsert metadata for caller.

Request:

```json
{ "metadata": { "theme": "dark" } }
```

Behavior:

- Creates a record if missing.
- Replaces metadata object entirely when record exists.

Response:

```json
{
  "owner_id": "user-1",
  "metadata": { "theme": "dark" },
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### `GET /agents/user-metadata`

Fetch metadata for caller.

Behavior:

- If no record exists, one is created automatically with `{}` and returned.

## Document indexing and retrieval endpoints

All routes below require bearer auth and are under `/agents`.

These endpoints are backed by Qdrant and local embedding models configured by environment variables.

Indexed document shape:

```json
{
  "uuid": "document-uuid",
  "name": "report.pdf",
  "description": null,
  "status": "indexed",
  "is_bayleaf": true,
  "chunks": 12,
  "source_type": "bayleaf",
  "indexed_at": "ISO-8601",
  "model_used": "intfloat/multilingual-e5-base"
}
```

### `POST /agents/documents/index`

Index a Bayleaf-hosted document by its Bayleaf document UUID.

Request:

```json
{
  "document_uuid": "bayleaf-document-uuid",
  "model_used": "optional-embedding-model"
}
```

Flow summary:

1. Calls Bayleaf `/api/documents/{uuid}/download-url/` through service credentials.
2. Downloads content.
3. Extracts text (PDF/text/binary fallback).
4. Chunks and embeds text.
5. Stores vectors/payload in model-specific Qdrant collection.

### `POST /agents/documents/index/upload`

Index an uploaded file directly.

Content type: `multipart/form-data`

Fields:

- `file` (required file)
- `model_used` (optional string)

Returns `IndexedDocument` for the generated UUID.

### `GET /agents/documents-available`

Return currently indexed document summaries across configured models.

Response:

```json
{ "documents": [/* IndexedDocument */] }
```

### `GET /agents/documents/{document_uuid}`

Return the latest indexed metadata for one document UUID.

Returns `404` when no indexed points exist for that UUID.

### `POST /agents/documents/query`

Semantic retrieval over indexed chunks.

Request:

```json
{
  "query": "headache management",
  "top_k": 5,
  "model_used": "optional-model",
  "document_uuid": "optional-single-doc-filter",
  "document_uuids": ["optional", "multi-doc", "filter"],
  "source_type": "bayleaf",
  "is_bayleaf": true
}
```

Rules:

- `query` is required and must be non-empty.
- `top_k` accepted default is `5`.
- Runtime validation enforces `1 <= top_k <= 50`.
- `source_type` filter may be `bayleaf` or `uploaded`.
- Filter strategy:
  - `document_uuid` creates a strict `must` filter.
  - `document_uuids` creates `should` filters for any of the listed UUIDs.

Response:

```json
{
  "query": "headache management",
  "top_k": 5,
  "model_used": "intfloat/multilingual-e5-base",
  "chunks": [
    {
      "score": 0.91,
      "document_uuid": "doc-1",
      "name": "Clinical Guide",
      "chunk_index": 0,
      "chunk_count": 12,
      "text_chunk": "...",
      "model_used": "intfloat/multilingual-e5-base",
      "source_type": "bayleaf",
      "is_bayleaf": true,
      "indexed_at": "ISO-8601"
    }
  ],
  "trace": {
    "trace_id": "retr_...",
    "retrieved_at": "ISO-8601",
    "collection": "documents_...",
    "model_used": "intfloat/multilingual-e5-base",
    "query_filter": {},
    "requested_top_k": 5,
    "returned_chunks": 1
  }
}
```

### `POST /agents/documents/{document_uuid}/reindex`

Reindex an existing document, optionally with another embedding model.

Request body:

```json
{ "model_used": "optional-target-model" }
```

Behavior:

- If the source is Bayleaf-backed and contains upstream UUID metadata, the service redownloads and reindexes from Bayleaf.
- Otherwise, it rebuilds from stored chunk text.

## Health endpoint

### `GET /health`

Public health endpoint.

Response:

```json
{
  "status": "ok",
  "env": "dev",
  "provider": "mock"
}
```

## Interactive API docs and schema

FastAPI default docs are available unless explicitly disabled by deployment settings:

- `GET /docs` (Swagger UI)
- `GET /redoc` (ReDoc)
- `GET /openapi.json` (OpenAPI schema)

## Client-agent integration notes

1. Prefer storing and reusing `conversation_id` returned by chat responses (present on both `AgentRequestResponse` and each `AgentRequestMessage`).
2. Treat `conversation_id` as opaque; it may be either external id or internal UUID depending on how the conversation started.
3. Use conversation groups to inject stable project/event context and document scopes.
4. For document retrieval, pass `document_uuids` whenever a strict scope is desired.
5. Handle `308` redirects if your HTTP client does not follow redirects automatically.
6. Expect downstream dependency failures from Bayleaf/Qdrant as structured errors under `detail.error`.

## Known contract caveats

1. Auth dependency currently decodes bearer JWT payloads without cryptographic verification.
2. Chat agent endpoint set is dynamic and depends on discovered classes under `bayleaf_agents.agents`.
3. `POST /agents/documents/query` has dual validation of `top_k`: schema default plus runtime bounds check.
4. `GET /agents/user-metadata` has a side effect: it creates an empty metadata row for first-time callers.
5. Canonical URL policy is non-trailing-slash across the service.