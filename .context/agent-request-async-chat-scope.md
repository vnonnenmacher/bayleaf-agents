# AgentRequest async chat scope

## Goal
Convert chat to asynchronous request processing without introducing a generic Task model.

The client must:
- call chat
- receive an immediate serialized AgentRequest
- poll a retrieve endpoint until state is terminal

## Explicit decisions captured
1. Keep chat and task concepts separate for now.
2. Implement only chat in this phase.
3. Introduce AgentRequest as the lifecycle owner.
4. AgentRequest owns processing state, not Message.
5. AgentRequest serializer must include related messages.
6. Add one retrieve endpoint for AgentRequest.
7. Allow only one active AgentRequest per conversation.
8. Add cancel endpoint and cancelled state.
9. No automatic retries.
10. Store execution error on request for UI display.
11. Add retry endpoint (manual retry trigger only).
12. Keep database migration simple and additive (no deletions).
13. Do not implement generic tasks in database or API.
14. Rename current synchronous chat execution method to _process_chat.
15. Introduce a new chat method that submits AgentRequest, schedules _process_chat asynchronously, and returns serialized AgentRequest immediately.
16. Keep existing conversation resolution and creation behavior exactly as it is today.
17. Retrieve response should include the full AgentRequest with messages.
18. Keep endpoints:
	- GET /agents/requests/{id}
	- POST /agents/requests/{id}/cancel
	- POST /agents/requests/{id}/retry
19. Use Celery + Redis for async execution of _process_chat.
20. Retrieve response includes internal tool/debug/state-linked messages by default.

## Why this model is needed
Current chat execution writes multiple Message rows in one chat call (user, assistant, optional tool rows, optional internal rows).
A single lifecycle state on Message is not sufficient to represent a full chat request lifecycle.

AgentRequest solves this by giving one parent lifecycle with many child messages.

## In scope
1. New AgentRequest persistence model.
2. Link Message rows to AgentRequest.
3. Async execution lifecycle for chat.
4. Retrieve endpoint to poll by agent_request_id.
5. Cancel endpoint.
6. Manual retry endpoint.
7. Request serializer including child messages.
8. Update API documentation in endpoints.md to reflect the new async chat contract.

## Out of scope
1. Generic Task abstraction/table/API.
2. Non-chat async workloads (classifier/extractor) in this phase.
3. Streaming transport (SSE/websocket).
4. Automatic retry policies/backoff.
5. Message content model redesign.

## Domain model changes (additive)
### New enum
AgentRequestState:
- waiting
- processing
- succeeded
- failed
- cancelled

### New model
AgentRequest:
- id
- conversation_id (FK conversations.id)
- user_id
- agent_slug
- channel
- state
- error_message (nullable)
- created_at
- started_at (nullable)
- finished_at (nullable)
- cancelled_at (nullable)

### Message extension
Add nullable FK:
- agent_request_id (FK agent_requests.id)

No existing columns removed.

## API scope
### 1) Chat create (existing endpoint behavior change)
POST /agents/{slug}/chat

New behavior:
- validates ownership and active request rule
- creates conversation if needed using current conversation resolution rules
- creates AgentRequest with state=waiting
- creates initial user message linked to AgentRequest
- schedules _process_chat asynchronously
- returns serialized AgentRequest immediately

Clarification:
- The API route remains POST /agents/{slug}/chat.
- The implementation must not wait for full assistant completion in this request.
- Rename current chat logic method to _process_chat.
- Introduce a new chat method responsible for:
	- creating AgentRequest
	- linking initial message(s)
	- scheduling async execution by calling _process_chat
	- returning serialized AgentRequest immediately

## Method split (authoritative)
1. _process_chat
- Contains the current end-to-end chat processing logic that today is blocking.
- Runs in background for this new architecture.
- Updates AgentRequest state transitions: waiting -> processing -> succeeded|failed|cancelled.
- Persists additional assistant/tool/internal messages linked by agent_request_id.

2. chat
- Becomes submit-only entrypoint.
- Resolves/creates conversation exactly as current behavior.
- Creates AgentRequest and initial user message.
- Triggers _process_chat asynchronously.
- Returns serialized AgentRequest immediately.

### 2) Retrieve request (new endpoint)
GET /agents/requests/{agent_request_id}

Behavior:
- ownership enforced by authenticated user
- returns AgentRequest serializer
- serializer includes ordered related messages

### 3) Cancel request (new endpoint)
POST /agents/requests/{agent_request_id}/cancel

Behavior:
- if state in waiting/processing, move to cancelled
- best-effort cancellation if already processing
- idempotent if already terminal

### 4) Retry request (new endpoint)
POST /agents/requests/{agent_request_id}/retry

Behavior:
- allowed only for failed/cancelled requests
- creates a new AgentRequest reusing same conversation context
- links new messages to new request
- returns new serialized AgentRequest

## Documentation scope
The implementation must update endpoints.md in the same change set.

Required documentation updates:
1. Chat endpoint contract:
- POST /agents/{slug}/chat is submit-only and returns serialized AgentRequest immediately.
- Clarify polling flow for completion.

2. New request lifecycle endpoints:
- GET /agents/requests/{id}
- POST /agents/requests/{id}/cancel
- POST /agents/requests/{id}/retry

3. State model and polling guidance:
- Document AgentRequest states: waiting, processing, succeeded, failed, cancelled.
- Document terminal states and expected client polling behavior.

4. Conflict and lifecycle behavior:
- Document one-active-request-per-conversation rule and conflict response.
- Document cancel and retry behavior at a contract level.

## Lifecycle and transitions
Allowed transitions:
- waiting -> processing
- processing -> succeeded
- processing -> failed
- waiting -> cancelled
- processing -> cancelled

Terminal states:
- succeeded
- failed
- cancelled

## One active request rule
Only one active AgentRequest per conversation where state in:
- waiting
- processing

When creating chat or retry:
- reject with conflict if an active request already exists for the same conversation

## Serialization contract (request)
AgentRequest serializer must return:
- request metadata fields
- state and error_message
- timestamps
- messages: array ordered by created_at asc

Each message item should include at minimum:
- id
- role
- content
- redacted_content
- tool_name
- tool_args
- tool_result
- retrieval_trace
- cited_documents
- citations
- created_at

## Error model
If background processing fails:
- set AgentRequest.state=failed
- store error_message
- keep any messages already persisted

No automatic retries in worker logic.

## Observability
Keep existing logging and add minimal lifecycle logs:
- agent_request_created
- agent_request_state_changed
- agent_request_cancelled
- agent_request_failed

Each log should include:
- agent_request_id
- conversation_id
- user_id
- agent_slug
- trace_id when available

## Migration plan
1. Add AgentRequest table and state enum.
2. Add messages.agent_request_id nullable FK.
3. Backfill not required for old rows.
4. Deploy additive migration.
5. Switch chat endpoint to new async flow.
6. Add retrieve/cancel/retry endpoints.

## Acceptance criteria
1. Chat returns immediately with AgentRequest in waiting or processing state.
2. Poll endpoint eventually returns succeeded, failed, or cancelled.
3. Exactly one active request per conversation is enforced.
4. Final assistant reply appears in messages for that AgentRequest.
5. Failure is visible via request state and error_message.
6. Cancel endpoint moves active request to cancelled.
7. Retry endpoint creates a new AgentRequest and starts processing.
8. endpoints.md is updated to match implemented async chat and request lifecycle contracts.
