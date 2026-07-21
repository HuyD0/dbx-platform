# AI Cost Planner implementation plan

Subsystem: `src/dbx_platform/llm_cost.py`, `/api/llm-cost/*`, and the Platform Console cost/approval UI.

## Goal

Add an AI Cost Planner that turns existing normalized LLM cost, usage, tokenomics,
budget, and source-health evidence into deterministic, human-approved cost-control
proposals. The planner must remain read-only: it can explain savings opportunities
and draft immutable approval plans, but it must not execute model, endpoint,
budget, or workspace mutations directly.

## Design guardrails

- Keep Databricks list cost, Azure actuals, and provider estimates separate by
  `cost_basis` and `currency`; never combine them into a single invoice total.
- Use only persisted/offline evidence already exposed by the LLM cost ledger,
  source-health rows, budget evaluations, and existing deterministic summaries.
- Route any budget write through the existing `configure-budget` action flow.
- Route any future endpoint/model/runtime mutation through a new approved action
  type with immutable targets, revalidation, a dedicated executor, and append-only
  events.
- Treat missing source health as a planning constraint, not as permission to invent
  recommendations.

## Proposed phases

### Phase 1: Read-only planning output

1. Add a pure planner function in the LLM cost package that accepts normalized cost
   rows, usage rows, evaluated budgets, source-health rows, and planner settings.
2. Emit typed recommendations for:
   - budget breach review;
   - retry amplification;
   - low cache utilization;
   - unallocated provider/model/team/use-case spend;
   - high context-window cost per successful request.
3. Include evidence references, affected dimensions, estimated financial basis,
   confidence, and whether the recommendation requires a governed action.
4. Add offline unit tests that verify financial bases/currencies remain separated
   and unavailable sources lower confidence instead of creating false precision.

### Phase 2: API and UI surfacing

1. Add an `/api/llm-cost/planner` route backed by the pure planner.
2. Reuse the existing ledger-loading path so the route stays credential-free in
   tests and relies on cached, persisted evidence at runtime.
3. Add a Cost page card for planner recommendations with clear source-health and
   cost-basis labels.
4. Add UI tests for empty, partial-source, and recommendation states.

### Phase 3: Governed budget plans

1. For budget recommendations, connect the card CTA to the existing budget plan
   dialog and `configure-budget` action type.
2. Pre-fill budget scope, cost basis, currency, month, and thresholds from planner
   evidence while preserving operator edit/review before plan creation.
3. Add backend tests proving budget plan requests still reject unknown parameters,
   unsupported cost bases, past months, and invalid thresholds.

### Phase 4: Future governed optimizations

1. Define separate action types for any endpoint/model configuration changes only
   after confirming the executor contract, schema, and Databricks job resources.
2. Add revalidation that recomputes the exact candidate target state before claim
   and execution.
3. Keep resource deletion unsupported; suggested cleanups should pause, resize, or
   propose owner follow-up only when a dedicated executor exists.

## Narrow validation plan

- `uv run python -m pytest tests/test_llm_cost.py tests/test_llm_cost_api.py tests/test_control_plane.py`
- `uv run python -m ruff check docs/ai-cost-planner-implementation-plan.md`

## Open questions before implementation

- Which recommendation classes should be eligible for auto-drafted governed action
  plans in the first shipped version?
- What minimum source-health status should be required before displaying savings
  estimates to operators?
- Should planner settings live in application configuration, a governed Unity
  Catalog table, or both?
