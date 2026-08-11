# Base Agent Document Decider Refactor Plan

## Goal
Move the document decider routing pipeline from LabcopilotAgent into BaseAgent so every docs-enabled agent can use it.
Keep Labcopilot-specific force rules (high-risk and query-shift) in LabcopilotAgent overrides.

## Current State
- BaseAgent already has query_documents tool wiring and forced document ID enforcement at execution time.
- LabcopilotAgent has a pre-chat routing pipeline:
  - run decider
  - apply force/fallback policy
  - prefetch chunks
  - inject retrieval_context
  - call super().chat()
- Appointment and Treatment agents do not pass documents_tools today, so they should remain unaffected.

## Refactor Principles
1. BaseAgent owns generic orchestration logic.
2. Domain heuristics are override hooks.
3. Pipeline is gated and inert unless docs are enabled.
4. Forced document IDs keep highest precedence at tool execution.

## Problematic Items and Treatment

### 1) _is_high_risk_question(user_message)
Problem:
- Current patterns are lab and Portuguese focused.
- Not portable to other agents.

Plan:
- Add method to BaseAgent with neutral default: return False.
- Keep lab-specific implementation as override in LabcopilotAgent.

### 2) _has_query_shift(user_message, chunks)
Problem:
- Current shift lexicon is lab-only vocabulary.
- Would cause false behavior outside lab domain.

Plan:
- Add method to BaseAgent with neutral default: return False.
- Keep lab-specific implementation as override in LabcopilotAgent.

### 3) Generic staleness and prefetch helpers
Methods:
- _build_decider
- _tokenize
- _latest_query_documents_message
- _latest_query_documents_chunks
- _has_explicit_recent_evidence
- _user_turns_since_message
- _prefetch_chunk_key
- _merge_prefetch_results

Plan:
- Move to BaseAgent as-is.
- Keep behavior unchanged.

## Activation Guard (Critical)
Run decider/prefetch pipeline only when all are true:
- self.documents_tools is not None
- query_documents tool is enabled via _tool_enabled("query_documents")
- decider instance is available

This prevents behavior changes in AppointmentAgent and TreatmentAgent.

## Implementation Steps
1. BaseAgent __init__
- Add optional decider_provider parameter.
- Store decider provider (default to main provider).

2. BaseAgent methods
- Add neutral hooks:
  - _is_high_risk_question -> False
  - _has_query_shift -> False
- Add moved helper methods listed above.

3. BaseAgent chat orchestration
- Insert routing block before system prompt/message assembly:
  - gather recent retrieval evidence
  - call decider
  - apply policy/fallbacks
  - prefetch and merge
  - inject retrieval_context into effective_group_context
- Continue with existing chat loop using effective_group_context.

4. LabcopilotAgent simplification
- Remove chat override and moved generic helpers.
- Keep constructor-only configuration.
- Keep two overrides:
  - _is_high_risk_question
  - _has_query_shift

5. Regression checks
- Run tests for:
  - lab copilot retrieval policy
  - document scope and citations
  - appointment and treatment smoke/behavior
- Confirm no query_documents calls for agents without documents_tools.

## Expected Outcome
- BaseAgent becomes the single place for retrieval routing orchestration.
- LabcopilotAgent keeps only domain policy hooks and constructor settings.
- Forced document IDs continue to override selection at runtime.
- Non-docs agents preserve current behavior.

## Risks
- Duplicate retrieval if both prefetch and tool call happen; acceptable if existing behavior already tolerates it.
- Prompt/context size growth from retrieval_context chunks; monitor token usage.
- Accidental activation in non-docs agents if guard is incomplete.

## Verification Checklist
- [ ] Labcopilot still performs decider-based routing.
- [ ] Labcopilot force rules still work through overrides.
- [ ] forced_document_ids still take precedence in query_documents execution.
- [ ] AppointmentAgent unchanged (no docs pipeline run).
- [ ] TreatmentAgent unchanged (no docs pipeline run).
- [ ] Existing retrieval and citation tests pass.
