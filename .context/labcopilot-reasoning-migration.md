# Labcopilot reasoning migration plan

## Decision

Remove the `agents/reasoning` inheritance layer. `LabcopilotAgent` will inherit
directly from `BaseAgent` and own its document-routing and retrieval behavior.

Move `DocumentDeciderAgent` into a new application-code package:

```text
src/bayleaf_agents/skills/
├── __init__.py
└── document_decider.py
```

Here, `skills` means small capabilities used by agents. It is a Python package,
not a Codex `SKILL.md` directory.

## Target architecture

```text
BaseAgent
├── AppointmentAgent
├── TreatmentAgent
└── LabcopilotAgent
    └── uses DocumentDeciderAgent from bayleaf_agents.skills
```

Responsibilities after the migration:

- `BaseAgent` owns the common conversation lifecycle, persistence, tool-call
  execution, PHI handling, state handling, and citation plumbing. It remains
  unchanged in this migration.
- `LabcopilotAgent` owns the retrieval routing, evidence reuse, and prefetching
  currently implemented by `ReasoningBaseAgent`.
- `DocumentDeciderAgent` remains a focused LLM-backed capability that selects
  whether document retrieval is needed and identifies candidate documents.
- `services/factories.py` continues to construct the optional decider provider;
  this is infrastructure configuration rather than agent reasoning.

## Migration steps

### 1. Create the skills package

- Add `src/bayleaf_agents/skills/__init__.py`.
- Move `agents/reasoning/document_decider_agent.py` to
  `skills/document_decider.py`.
- Export `DocumentDeciderAgent` from `skills/__init__.py` only if a stable
  package-level import improves readability; otherwise import from the module
  explicitly.
- Update relative imports for `Principal`, `LLMProvider`, `Message`, and
  `DocumentsToolset`.

### 2. Make Labcopilot the concrete reasoning owner

- Change `LabcopilotAgent` to inherit directly from `BaseAgent`.
- Move the retrieval methods currently implemented by `ReasoningBaseAgent`
  into `LabcopilotAgent`:
  - decider construction
  - tokenization and recent-evidence checks
  - high-risk question detection
  - query-shift and stale-evidence guards
  - prefetch result merging and deduplication
  - the `chat()` orchestration override
- Import `DocumentDeciderAgent` from the new `skills` package.
- Preserve the existing constructor contract, including `decider_provider`, so
  router/factory wiring does not change.
- Preserve current routing trace fields and retrieval behavior during this
  structural migration. Policy changes should be made separately so failures
  can be attributed clearly.

### 3. Remove the reasoning package

- Delete `agents/reasoning/base_agent.py`.
- Delete `agents/reasoning/__init__.py`.
- Delete the now-empty `agents/reasoning/` directory.
- Confirm there are no remaining imports or references to
  `ReasoningBaseAgent` or `bayleaf_agents.agents.reasoning`.

### 4. Align tests with the real owner

- Rename `tests/test_reasoning_retrieval_policy.py` to a Labcopilot-specific
  name such as `tests/test_labcopilot_retrieval_policy.py`.
- Stop creating `TestReasoningAgent` as a synthetic subclass.
- Instantiate `LabcopilotAgent` directly with stub providers, document tools,
  and Bayleaf client.
- Keep assertions covering:
  - forced retrieval when the decider skips and no evidence exists
  - reuse of relevant recent evidence
  - general plus candidate-focused prefetch behavior
  - candidate IDs, `top_k`, and document scope
- Add a small import/registry regression test if needed to ensure the removed
  base class cannot be discovered as a concrete agent.

### 5. Verify behavior and package cleanliness

Run, in order:

```bash
pytest -q tests/test_labcopilot_retrieval_policy.py
pytest -q tests/test_chat_research_documents.py tests/test_conversation_messages_citations.py
pytest -q
```

Then perform static checks:

```bash
rg "ReasoningBaseAgent|agents\.reasoning|reasoning\.base_agent" src tests
python -m compileall -q src
```

The `rg` command should return no matches.

## Invariants to preserve

- API request and response shapes remain unchanged.
- Agent slug remains `labcopilot`.
- Existing conversation ownership and group scoping remain unchanged.
- Forced document IDs still take precedence over model-selected candidates.
- Retrieval traces remain attached to the user message.
- Prefetched chunks remain available for citations.
- Decider and main response providers can still be configured independently.
- Labcopilot continues to expose only `query_documents` from its enabled tools.
- `BaseAgent` is not modified as part of this migration.

## Execution order to preserve

`LabcopilotAgent.chat()` must execute the existing orchestration in exactly the
same order:

1. Resolve the existing conversation, when an external conversation ID is
   provided, so the routing policy can inspect prior retrieval messages.
2. Build the `DocumentDeciderAgent` when document tools are available.
3. Load the most recent `query_documents` result and calculate:
   - explicit recent-evidence overlap
   - high-risk question status
   - query shift
   - user turns since the previous retrieval
4. Ask the document decider for its routing decision and candidate document
   IDs.
5. Apply the deterministic retrieval guards in their current precedence:
   - decider requests retrieval
   - high-risk question
   - query shift
   - stale reuse window
   - no explicit recent evidence
   - reuse recent evidence
   - no available catalog
6. If retrieval is required, run the general prefetch first.
7. If candidate document IDs exist, run the candidate-focused prefetch second.
8. Merge results with focused results first and general results second, keeping
   the existing deduplication behavior and `top_k` values.
9. Add prefetched chunks and their trace to `group_context.retrieval_context`.
10. Call `BaseAgent.chat()` once with the same candidate IDs, route trace,
    group context, forced document IDs, and remaining arguments.
11. Let `BaseAgent.chat()` perform its existing lifecycle unchanged, including
    persistence, model/tool calls, citations, and response construction.

No method cleanup, policy simplification, renaming of trace fields, reordered
conditions, or retrieval optimization should be combined with this move.

## Risks and controls

- **Behavior drift while moving `chat()`:** copy the implementation first,
  update imports/inheritance, and avoid refactoring the copied logic.
- **Execution-order drift:** add ordered call recording to the retrieval test
  doubles and assert general prefetch occurs before focused prefetch and both
  occur before the main provider call.
- **Tests passing against the wrong class:** instantiate `LabcopilotAgent`
  directly in retrieval-policy tests.
- **Import/discovery leakage:** search all source and test files after removing
  the package and inspect the discovered agent slugs.
- **Ambiguous meaning of `skills/`:** keep this as an ordinary Python package;
  do not introduce Codex skill manifests or `SKILL.md` files.

## Completion criteria

- `src/bayleaf_agents/agents/reasoning/` no longer exists.
- `LabcopilotAgent(BaseAgent)` contains all behavior moved from
  `ReasoningBaseAgent`.
- `DocumentDeciderAgent` is importable from `bayleaf_agents.skills`.
- `BaseAgent` has no changes from this migration.
- Focused retrieval tests and the complete test suite pass.
- No stale reasoning-package references remain.
