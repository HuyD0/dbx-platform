# Platform Console design review — cost, governance, risk & enablement fit

**Date:** 2026-07-19 · **Scope:** the Platform Console app (`apps/platform-console/`),
its API layer, and how both surface (or fail to surface) what the `dbx_platform`
package and scheduled jobs already collect. Every claim below carries a file
reference and was verified against the code, not inferred from screenshots.

**Reviewed against the operator's five jobs:** cost management, data governance,
AI governance, risk, and enablement — for one Azure Databricks workspace plus Azure
AI Foundry/OpenAI, run by a small team that is new to both platforms and needs the
console to automate and *proactively* catch problems.

**Domain bar used:**
[Azure Databricks cost management](https://learn.microsoft.com/en-us/azure/databricks/admin/usage/)
(system.billing.usage: `workspace_id`, `custom_tags`, `sku_name`,
`identity_metadata`, `usage_metadata`; budgets with tag/workspace filters; tag
enforcement via compute policies) and
[Plan and manage costs for Azure AI Foundry](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/manage-costs)
(Cost Management group-by Meter/Resource; the auto-applied `project` tag for
chargeback; fine-tuned hosting billed hourly even when idle; no hard spend cap on
Azure OpenAI — alerts and automation are the only backstop).

---

## 1. Verdict

**The console is a well-engineered approval console bolted onto an under-designed
observability product.** The governed-action architecture (immutable hashed plans,
15-minute TTL, two-step confirmation, dedicated executors, append-only events) is
genuinely excellent and should not be touched. But the screens that are supposed to
answer the five jobs above fail them — and the failure is *not* missing data. The
scheduled jobs and SQL already collect nearly every dimension the operator needs;
the API layer aggregates it away and the information architecture hides what
survives. The fix is mostly rerouting and un-collapsing, not new engineering.

The reviewer's instinct — "I can't even tell workspaces or tags" — is correct and
specific:

- The sidebar's "Current workspace" card renders only the environment string
  (`frontend/src/App.tsx:128-134`). The numeric `workspace_id` is never rendered
  anywhere in the live UI, even though it is a GROUP BY column in nearly every cost
  query the product runs.
- No general Databricks cost query selects `custom_tags` at all
  (`src/dbx_platform/queries/product_spend.sql`, `usage_last_30d.sql`,
  `job_run_cost.sql` — zero matches). There is no "spend by team/project/tag" view
  anywhere in the product.

## 2. Scorecard against the five jobs

| Job | Grade | One-line reason |
|---|---|---|
| Cost management | **D+** | Cost in currency with honest bases (good), but no attribution dimension (workspace/tag/team), no trends, no budget meters, Azure frozen at service-level 30-day totals |
| Data governance | **C−** | Tag *compliance* findings exist, but buried as a tab inside "Security & Risk"; tag *payoff* (spend by team) absent; UC posture is keyword-filtered findings |
| AI governance | **F** | The entire AI/ML surface is unrouted dead code; the AI model catalog and access graph the pipeline builds are never read by the app |
| Risk | **C** | Honest `source_status: partial` labeling (good), but most "Risk signals" screens are keyword filters over one findings table dressed as detectors |
| Enablement | **F** | Does not exist as a concept: no glossary, no "why this matters," no path from a finding to learning, dashboards page dead |

## 3. The core indictment: the data exists, the design hides it

This table is the heart of the review. Column two is what the pipeline **already
collects and stores**; column three is why the operator cannot see it.

| What the operator needs | Where it already exists (verified) | Why it's invisible |
|---|---|---|
| Which workspace am I looking at? | `workspace_id` in the GROUP BY of `usage_last_30d.sql`, `job_run_cost.sql`, `gpu_spend.sql`, `untagged_usage.sql`…; a `workspace_reference` table provisioned for dashboards | Sidebar shows only `health?.environment` (`App.tsx:128-134`); no endpoint returns a workspace name; the ID is displayed nowhere |
| Spend by team / project / tag | `system.billing.usage.custom_tags` is in every source table; a `team_name_from_tags` SQL UDF ships for dashboards; **every** cluster policy in `policies/*.json` makes `custom_tags.team` + `custom_tags.project` mandatory | No general cost SQL selects `custom_tags`; the only tag signal is `untagged_usage.sql`, which reduces the entire tag map to one boolean `untagged_pct`. Tag enforcement exists with zero payoff view |
| Azure AI Foundry / OpenAI cost | `azure_cost.service_bucket()` explicitly buckets `foundry_ai` (OpenAI, Foundry, Cognitive, AML); `azure_cost_details` stores resource-ID + meter grain (per-deployment attribution); `cost_forecasts` carries a per-series `foundry_ai` P10/P50/P90 forecast | `/api/cost/azure` hardcodes `by="service"` (`backend/routers/cost.py:90`) although `report_sql` supports `bucket`/`resource-group`; `azure_cost_details` is never read by any router; the frontend has **zero** occurrences of "foundry", "openai", or "cognitive" |
| AI inventory & access ("who can invoke which model, from where") | `ai_model_catalog` + `ai_model_access` tables: unified inventory across UC registered models, serving endpoints, and Azure OpenAI/Foundry accounts + deployments, with subscription, region, resource group, owner, `key_auth_enabled`, and a normalized principal → access-level → via-scope graph (`src/dbx_platform/ai_catalog.py`; read functions exist) | **Zero references in the entire app** (backend and frontend). Findings like `ai-catalog/azure-key-auth` (HIGH) reach the console only as reason strings inside `platform_findings` |
| AI app monitoring (tokens, errors, p95 by app) | `ai_app_monitoring` table: per-endpoint/per-app daily requests, errors, tokens, distinct requesters, p95 latency, 400-day retention (`src/dbx_platform/ai_monitor.py`) | Never read by the app; Lakeview-dashboard-only |
| Right-sizing evidence | `cluster_utilization.sql` returns avg/p95 CPU, avg memory, observed vs configured workers, node type | Classification collapses to `{cluster, creator, cost, reason, action}` — the numbers survive only inside a prose `reason` string, rendered by an auto-column table |
| Period-over-period change | `product_spend.sql` computes a full previous-period row set | The API keeps only `period == 'current'` (`backend/routers/overview.py`, `routers/cost.py`) |
| Request-level AI telemetry (TTFT, p95/p99, 429s, per-request tags) | Staged in `apps/platform-console/config/queries/*.obo.sql` | Wired to nothing — no router serves them |

The pattern is consistent: **rich SQL → classified/aggregated to a summary → rendered
by a generic auto-column table.** Each layer throws away the dimension the next layer
would have needed.

## 4. Information architecture and site-map failures

### 4.1 Dead pages — surfaces silently dropped from navigation

Four page components exist in the codebase but are imported by nothing
(verified — no imports in `App.tsx` or elsewhere):

| Dead file | What it contained | What happens instead |
|---|---|---|
| `pages/AiMl.tsx` | The **entire AI/ML governance surface**: serving endpoint audit, stale endpoints, model registry hygiene, GPU cluster audit, vector search audit, serving cost, token usage, GPU spend share | `/ai-ml` redirects to `/cost?tab=llm` — a cost view. The eight `/api/ml/*` endpoints still run; no screen calls them |
| `pages/Housekeeping.tsx` | Stale clusters, orphaned jobs, jobs-on-all-purpose-compute findings tables | `/housekeeping` redirects to Action Center, which shows only the *planners*, not the findings |
| `pages/Dashboards.tsx` | Embedded AI/BI (Lakeview) dashboards | No route at all |
| `pages/Overview.tsx` | Old landing page | Superseded by Mission Control (this one is a legitimate kill) |

For a manager whose job list includes *AI governance*, the product's AI-governance
screens being unreachable dead code is the single most damning IA fact.

### 4.2 The nav doesn't map to the operator's jobs

Current sidebar: Mission Control · Action Center · Cost & Value · Security & Risk ·
Performance · Resources & Runtime · Automations · Assistant (`App.tsx:45-59`).

- **AI governance has no home.** Its live remnant is one tab (`LLM & AI`) inside
  Cost — a spend view, not a governance view.
- **Data governance is a buried tab** (`/security?tab=governance`) inside a section
  named for a different job.
- **Enablement doesn't exist** — no glossary, no explainers beyond tooltips, no
  learning path, and the dashboards that could teach are unrouted.
- **Performance and Resources & Runtime are utilities promoted to top-level** while
  two of the operator's five jobs have no top-level presence.

### 4.3 Two disconnected products for one platform

Eight Lakeview dashboards ship in the same bundle (`dashboards/`), and they uniquely
surface exactly what the app omits: the AI model catalog with key-auth exposure and
top principals by reachable models, per-app token/requester monitoring, Azure cost by
resource group and service bucket, and lineage/catalog utilization. The app never
links to a specific dashboard from a related screen; the dashboards page is dead.
A new team member has no way to discover that the answer to their question exists
one surface over.

### 4.4 Screens that look like detectors but aren't

The "Risk signals" tabs (privilege drift, service principal scope, network & egress,
audit anomalies) and all three Performance screens are **keyword filters over the
single `platform_findings` table**, each honestly self-labeled
`source_status: "partial"` (`backend/routers/security.py`, `performance.py`). The
honesty is commendable — the placement is misleading. A page named "Network and
egress" that greps a findings table for the word "egress" is not a network detector,
and a new operator cannot tell the difference.

### 4.5 Presentation-layer poverty

- Most screens render through one generic `DataTable` whose **columns are
  auto-discovered from raw API keys** with regex-based formatting
  (`components/DataTable.tsx:124-133`). Whatever the backend happens to return *is*
  the schema. Only Action Center, Audit, and the product-spend drill have designed
  columns.
- **No charting library exists.** No timeseries, no trend line, no budget gauge, no
  forecast fan — the P10/P50/P90 forecast renders as a table of numbers. For a cost
  product, "is spend trending toward budget?" is unanswerable at a glance.
- **Budgets are inputs without feedback.** The budget form has warning/critical
  thresholds (80/100), but there is no consumption meter or threshold status anywhere;
  native Databricks budgets are a link-out paragraph.
- The Azure tab is frozen at 30 days while Databricks and Performance get 7/30/90
  toggles.

### 4.6 The proactivity gap

The stated goal is *proactively catching things*. Today, Azure spend anomalies,
budget evaluation, and forecast breaches are computed **only when someone loads the
page** (`/api/cost/azure-anomalies` classifies at request time; `/api/llm-cost/budgets`
evaluates at request time). Nothing persists these as findings, so Mission Control's
ranking, the digest, and the approval pipeline — the product's best machinery — never
see them. The product has an alerting-shaped hole exactly where its architecture is
strongest.

### 4.7 Even the redesign mockups miss the domain

`docs/ui-redesign-mockups/` contains ten thoughtful IA studies (decision-queue-first,
evidence drawers, calm zero-states — worth keeping), but they are generic multi-cloud
concepts (AWS Organizations, S3, KMS) and **none of them depict cost-by-tag, per-team
attribution, workspace identity, or Foundry/OpenAI costs**. The domain gap the
reviewer sensed is visible in the design artifacts themselves: the IA was designed
around *governed approvals in the abstract*, not around what a Databricks + Azure AI
platform team actually asks on a Tuesday morning.

## 5. What is genuinely good — keep, don't rebuild

1. **The governed-action pipeline.** Immutable single-use plans (SHA-256 fingerprint,
   15-minute TTL, revalidation, STALE/EXPIRED states), two-step confirmation,
   Impact/Rollback/Verification blocks, append-only events, dedicated executors.
   This is better than most commercial tools.
2. **Fail-closed honesty.** `ErrorState` distinguishes "not connected" from real
   errors; `FindingsSection` distinguishes "checked and clean" from "couldn't check";
   Mission Control's zero state says "no approval is waiting — not that every check
   passed." Rare and valuable.
3. **Cost-basis and currency integrity.** DATABRICKS_LIST vs AZURE_ACTUAL vs
   PROVIDER_ESTIMATE are never summed; currencies are explicit. Do not "fix" this
   into one blended number.
4. **The LLM cost subsystem** — the one place attribution works: persisted daily
   ledger with provider/model/endpoint/principal/team/use_case dimensions, coverage
   records, and budget evaluation. It is the pattern the rest of the product should
   copy, not an exception.
5. **Source-health metadata** (`coverage` records, `source_status`, Mission Control
   `data_health[]`), viewer identity redaction, deep-linkable query-param tabs, and
   real accessibility testing (axe/Playwright).

## 6. Target design

### 6.1 Proposed site map (nav re-organized around the five jobs)

```
Mission Control      /                 unchanged role + a real scope header
Action Center        /actions          unchanged
Cost                 /cost             Overview | Databricks | Attribution* |
                                       Azure & Foundry* | LLM & AI | Budgets & Forecasts
Data Governance      /data-governance  Tag funnel* | Compliance | Policy drift | UC hygiene
AI Governance        /ai-governance    Inventory & Access* | Usage monitor* | Serving hygiene†
Risk                 /risk             Security & Risk minus governance tab, honest labels
Operations           /operations       Performance + Runtime + Hygiene†
Automations          /automations      unchanged (Jobs | AI briefings | Playbooks)
Learn                /learn            glossary | task modules | embedded dashboards† | digest
---
Settings, Audit      utility, unchanged
```
`*` new screens over existing tables · `†` resurrected dead pages. Keep every legacy
redirect; retarget `/ai-ml → /ai-governance` and `/governance → /data-governance`.
The standalone Assistant nav item is unnecessary (the slide-over panel is available
everywhere); keep the route for deep links.

Key placements:
- **Attribution** (Cost tab): spend by team/project/tag/workspace — the headline new
  screen (§6.2).
- **Azure & Foundry** (Cost tab): bucket view first (`foundry_ai` isolated at last),
  then per-deployment drill from `azure_cost_details`, anomalies inline, real day
  selector, and a cross-link explaining that AZURE_ACTUAL rows also appear in the
  LLM view (same money, provider lens — prevents double-count confusion).
- **AI Governance › Inventory & Access**: the app's biggest unlock at the lowest
  cost — `ai_catalog.read_catalog` / `read_access` already exist as functions; only
  a router and a page are missing. Answers "which models exist across UC + serving +
  Azure, who can invoke them, via what scope, and which Azure accounts still allow
  key auth."
- **Dashboards vs app division of labor**: app screens are for *decisions*
  (thresholds, findings, plans, approvals, teaching); Lakeview dashboards are for
  *exploration* (ad-hoc slicing). Every app screen links to its matching dashboard;
  the app never rebuilds a pivot a dashboard already has.

### 6.2 The attribution spine

1. **Scope header** — workspace name + numeric ID + environment + data freshness,
   in the sidebar card and atop every cost screen. Backend: pass `workspace_id`
   through `/api/health` from `deps.control_plane_scope()` (it already returns it) —
   a one-line change that alone answers "I can't even tell workspaces."
2. **Group-by params on cost endpoints:**
   - `/api/cost/azure?by=bucket|service|resource-group` — pure pass-through; the
     allowlist already exists in `azure_cost.report_sql` (`azure_cost.py:408-430`).
   - New `/api/cost/azure-detail?by=resource|meter|resource-group` over
     `azure_cost_details` — a new pure `report_detail_sql` in the same style.
   - New `/api/cost/attribution?dimension=team|project|workspace|untagged` backed by
     a new `queries/usage_by_tag.sql` (COALESCE `custom_tags['team']` →
     `'unallocated'`, joined to `list_prices` exactly like `product_spend.sql`),
     validated against a frozen allowlist copying the `BREAKDOWN_DIMENSIONS` pattern
     from `llm_cost.py:32`.
3. **Tag coverage funnel** (Data Governance): three stages, each from an existing
   endpoint, each linking to its fix —
   **enforced by policy** (`/api/governance/policy-drift` + `/tag-compliance`; the
   policies already mandate team/project) → **tagged spend %**
   (`/api/governance/untagged-spend`) → **spend by team** (the new attribution
   endpoint). This makes the payoff of tag enforcement visible for the first time,
   and teaches the team *why* the policy nags them.
4. **Honest gap, labeled:** Azure/Foundry rows stay `team='unallocated'` until Azure
   resource tags are ingested (Phase 3). Show that as a `CapabilityNotice` with the
   remediation, don't hide it.

### 6.3 The proactive layer — "an alert is a `platform_findings` row"

No new notification system, no new mutation path. The scheduled jobs persist what
they already compute as findings, and the existing machinery (Mission Control
ranking → digest → plan → approval) delivers them:

- **Azure spikes**: `classify_azure_spend` already returns finding-shaped rows with
  `action: investigate-spend-spike` — write them in the ingestion job instead of
  computing only at page load.
- **Budget breaches**: `evaluate_budgets` already emits warning/critical states —
  persist as findings with `financial_impact_usd` = overage.
- **Forecast-vs-budget**: new pure classifier comparing `cost_forecasts` P50/P90 per
  series against budget rows → `forecast-exceeds-budget` finding, run inside the
  existing forecast job.
- **Utilization breaches**: the cluster/warehouse classifiers are already
  threshold-driven from Settings; persist their output.

Accepted trade-off: alerts move at job cadence, not real time — correct for T+1
billing data, and it preserves the read-only-app invariant. The UI should also state
the Azure OpenAI reality explicitly: **there is no hard spend cap**; alerts plus
approval-gated action is the ceiling.

### 6.4 Enablement

- **Glossary** (~40 entries: DBU, list vs actual cost, meter, resource group, PTU vs
  PAYG, `billing_origin_product`, TTFT…) wired into the existing `HelpTip` on every
  KPI label.
- **"Why this matters" explainers** on section titles, with links to Microsoft
  Learn/Databricks docs and to the matching dashboard (e.g., on Azure & Foundry:
  "Azure OpenAI bills under 'Cognitive Services'; fine-tuned hosting bills hourly
  even when idle — check the deployment drill below").
- **Learn page**: task modules ("Read your first bill", "Tag your workloads",
  "Govern AI access", "Approve an action safely"), embedded dashboards, the latest
  digest, the glossary index.
- **Assistant prompt starters per page** ("Why is foundry_ai up this week?",
  "Explain this finding") — the citation and proposal-card machinery is already the
  right teaching surface.

## 7. Phased roadmap

**Phase 1 — wire what exists** (~2 weeks, low risk; no new data collection, no schema
changes): nav/IA restructure and redirects in `App.tsx`; resurrect the dead pages
into AI Governance / Operations / Learn; new thin router over
`ai_catalog.read_catalog`/`read_access` and `ai_monitor.read_monitoring`; `by=` param
on `/api/cost/azure`; workspace ID through `/api/health` into a scope header;
un-collapse product spend (prior-period + resource rows the SQL already returns);
day selector on Azure; forecast grouped by series.

**Phase 2 — new endpoints over existing tables** (~3–4 weeks, medium risk):
`usage_by_tag.sql` + `/api/cost/attribution` with strict dimension allowlists;
`report_detail_sql` + `/api/cost/azure-detail` (Foundry deployment drill); the tag
funnel screen; findings-ification of spikes/budgets/forecast/utilization inside the
existing scheduled jobs (new query files land in `src/dbx_platform/queries/`, which
is already force-included in the wheel; offline tests for every pure classifier and
SQL builder, per repo convention).

**Phase 3 — genuinely new collection + presentation foundation** (~4–6 weeks,
highest risk). Only three things truly require *new* data rather than surfacing:
1. **Azure resource-tag ingestion** (extend the existing Resource Graph fetch; join
   `resource_id → team/project` — including Foundry's auto-applied `project` tag —
   into the LLM rollup so AZURE_ACTUAL rows stop being `unallocated`). This closes
   the one real data gap in the attribution story.
2. **Native Databricks budget / budget-policy read-through** so the Budgets tab shows
   configured-vs-actual meters instead of a link-out (read-only; creation stays in
   the account console).
3. **The OBO telemetry server** to un-strand `config/queries/*.obo.sql`
   (request-level latency/TTFT/429s/per-request tags) — a separate infra decision,
   deferrable.
Plus one small chart library for timeseries and budget meters — accepted dependency;
the persona's core question ("trending toward budget?") is unanswerable in tables.

## 8. What NOT to build

- **Budget CRUD or invoice-grade drill** — Azure Cost Management and the Databricks
  account console own creation, exports, and reconciliation; the console reads and
  alerts.
- **Ad-hoc slicing UI** — the Lakeview dashboards own exploration; embed and link.
- **A blended "total AI spend" number** — cost-basis/currency separation is a
  feature; the Cost Overview juxtaposes, never sums.
- **Multi-workspace switching** — single workspace is by design; the fix is
  *visibility* (scope header), not a switcher.
- **Hard caps or auto-remediation** — Azure OpenAI has no hard cap, and
  auto-anything would break the plan/approve invariant; state the ceiling honestly.
- **Tag writing from the console** — recommendations flow into the plan/approve
  path; no direct tag mutation, ever.

---

*Method note: findings compiled from three parallel code audits (frontend IA,
API/data layer, package/SQL/jobs layer) plus a design synthesis pass, with all
load-bearing claims re-verified directly: the `by="service"` hardcode
(`backend/routers/cost.py:90`), the environment-only workspace card
(`frontend/src/App.tsx:128-134`), zero `custom_tags` selections in the general cost
SQL, zero app references to `ai_model_catalog`/`ai_model_access`/`ai_app_monitoring`,
and zero frontend occurrences of foundry/openai/cognitive.*
