# Platform Console — performance & loading-UX audit

Audited 2026-07-26 on `main` (React/Vite frontend, FastAPI backend under
`apps/platform-console/`). Motivating complaint: *"some screens seem to hang on
load; waiting for a resource to spin up is OK, but the UI must indicate it."*

Method: six parallel code reviews (bundle/delivery, data fetching, loading-state
UX, backend request path, warehouse cold-start visibility, estimator/LakeMeter
integration), each finding independently re-verified against the cited code. A
production Vite build was run to measure real chunk sizes. 54 raw findings
survived verification; they dedupe to the numbered items below.

Severity: **H** = likely cause of "screen hangs with no feedback" or
multi-second waste on every load · **M** = noticeable slowdown or confusing
feedback in common flows · **L** = polish.

---

## TL;DR — why screens hang, in order of blame

1. **Nothing ever says the warehouse is starting.** There is no warehouse-state
   endpoint and no elapsed-time escalation in any loading state; the only
   "warehouse may be waking up" copy lives in the *error* path, after the user
   already waited out a timeout (A1, A2).
2. **A cold warehouse can stall the whole backend.** The in-process cache runs
   loaders while holding per-key locks, callers block sync handlers on the
   shared ~40-token thread pool, and compound endpoints stack multiple 45 s
   statement budgets until the Apps gateway aborts with an opaque 502
   (B1, B2, A3).
3. **Every API call pays an uncached SCIM identity round trip** (~100–500 ms),
   multiplied by the 6–10 requests a typical page fans out (B3).
4. **The first paint costs ~1 MB of JS in one chunk** — all 16 pages, recharts
   (364 kB) and react-markdown (110 kB) included, served uncompressed, in front
   of a blank `#root` with no static shell (C1–C5).
5. **A few flows actively mislead**: TanStack Query's default `retry: 3` turns a
   cold-start estimate into a ~3-minute silent spinner; the estimator library
   renders "No saved estimates yet" *while the query is still loading*; several
   error paths prescribe running the wrong job (C7, A4, A5).

What is **not** broken (verified): the 45 s interactive statement cap is
deployed in prod (`resources/app.yml:84-85`) so single-statement endpoints fail
fast with a typed 504; `FindingsSection` has a complete
skeleton → error → capability-gap → empty → data state machine with an always-on
`AsOf` freshness stamp and `?refresh=true` bypass; 85 of 86 route handlers are
sync `def`, so SDK/warehouse calls run in the thread pool rather than on the
event loop; the what-if sliders are properly debounced with a visible
"recomputing…" status; and the LakeMeter *frontend* is correctly isolated via a
runtime dynamic import.

---

## A. Cold-start & waiting visibility (the explicit ask)

### A1 (H) — No warehouse-state signal anywhere in the API or UI
No code in the repo ever reads warehouse lifecycle state (grep for
`warehouses.get` / `lifecycle_state` in the app: zero hits). `/api/health`
(`backend/routers/meta.py:47-55`) returns only status/version/build flags;
`/api/config` has a static `warehouse_configured` boolean; Settings renders only
`/api/health`. A routine cold start is therefore indistinguishable from an
outage.

**Fix.** Add `GET /api/warehouse-status` in `meta.py` calling
`deps.get_ws().warehouses.get(deps.warehouse_id())` (the app SP already has
`CAN_USE` via `resources/app.yml`), cached ~10 s via `cache.cached`. Frontend: a
`useWarehouseStatus` hook polling ~10 s **only while a data query is pending or
in `query_timeout` error**, feeding a banner in `App.tsx` and the shared loading
state so pending screens can say *"SQL warehouse is starting — first load after
idle can take a few minutes."*

### A2 (H) — Loading skeletons pulse forever with no escalation
The only loading affordance is the static `Skeleton`
(`frontend/src/components/ui.tsx:284-292`). No surface escalates the message
with elapsed time (`MissionControl.tsx:706-719`, `CostValue.tsx:651-652`,
`FindingsSection.tsx:71-72`, `LlmCostView.tsx:138`). The "warehouse may be
waking up… wait a moment, then refresh" copy exists only in `ErrorState`'s
transient branch (`ui.tsx:336-346, 374-376`), which fires only **after** a
502/503/504 — never during the wait the user actually sits through.

**Fix.** A shared `useSlowPending(query)` / `<LoadingState>` wrapper: after
~5–8 s of `isPending`/`isFetching`, swap the bare skeleton for the cold-start
copy (reusing ErrorState's wording), ideally gated on A1's warehouse state. Wire
it through `FindingsSection`, `CostValue`, `MissionControl`, `LlmCostView` so
every warehouse-backed view gets it for free.

### A3 (H) — Compound endpoints stack 45 s budgets past the gateway timeout
`DBX_PLATFORM_STATEMENT_TIMEOUT_SECONDS=45` caps **one** statement, but
endpoints run several sequentially, each with a fresh budget:
`/api/mission-control` = findings loader + four `list_actions` calls
(`backend/routers/control_plane.py:756-808`), worst case 90 s+; `/api/overview`
three sections (`overview.py:55-81`), worst case ~135 s; `/api/cost/overview`
three reads + three source cards (`cost.py:316-439`). On a cold warehouse these
hold the connection until the Databricks Apps gateway aborts with an opaque
502 — the typed `query_timeout` contract never reaches the browser exactly where
it matters most.

**Fix.** Share one deadline per HTTP request: pass remaining budget as
`timeout_seconds` into each `run_query` (it already accepts it,
`src/dbx_platform/system_tables.py:65`), or short-circuit remaining sections
after the first `TimeoutError` (if one statement timed out on a starting
warehouse, the rest will too).

### A4 (M) — Cold-start timeouts swallowed into misleading error codes
Several routes catch `Exception` broadly and map a `TimeoutError` (warehouse
starting) to setup-style errors: estimator sections tell users to run migration
or prices-pull jobs (`backend/routers/estimator.py:90` area), cost sections
report "attribution temporarily unavailable", and the landing page's
`DataHealthList` relays the wrong prescriptions. Users can be induced to run
governed jobs to "fix" a warehouse that just needed a minute.

**Fix.** Catch `TimeoutError` before the blanket handlers and either re-raise
(letting `errors.py` return the typed 504) or emit a distinct
`warehouse_starting` status in section/health payloads.

### A5 (M) — `query_timeout` renders as a terminal red failure with a dead hint
`errors.py:105-115` already drops the informative "warehouse may be starting"
message from `system_tables.py:108-109` in favor of generic copy, and the
frontend styles the one error that means "just wait" as a serious failure with
no auto-retry; its hint points to a Settings section that doesn't exist
(`ui.tsx:337` area).

**Fix.** Treat `query_timeout`/504 as transient-neutral in `ErrorState` with the
cold-start copy and a bounded auto-refetch (e.g. every 15–30 s up to ~3 min,
gated on A1's status); pass the original message through the 504 payload.

### A6 (M) — Degraded cold-start results are cached for the full TTL
`/api/cost/overview` caches section-level failures inside the 300 s cache entry
(`cost.py:360` area), so a user who waited out the cold start keeps seeing
"attribution temporarily unavailable" for up to 5 more minutes after the
warehouse is running unless they find the manual refresh.

**Fix.** Don't cache failed sections (move try/except outside `cache.cached`,
re-raise `TimeoutError`), or cache degraded results under a 10–30 s TTL.

### A7 (H) — Estimator panels claim "empty" while the query is still running
`EstimateLibrary.tsx:75-83` renders "No saved estimates yet…" whenever
`query.data` is unset — including during `isPending` on a never-cached,
warehouse-backed endpoint (`estimator.py:293-301`) and after errors.
`DeploymentsPanel.tsx:159-184` shows "No deployments linked yet" during load,
with only a small badge after errors. Actively wrong feedback during the exact
cold-start window the audit is about.

**Fix.** Branch on `isPending` → `Skeleton` (with A2's slow-load copy) and
`isError` → `ErrorState`; show `EmptyState` only on a successful zero-row
response — the `FindingsSection` pattern.

### A8 (M) — Error branches that prescribe the wrong remedy
`CompliancePosture.tsx:204` area maps **every** error (including cold-start
timeouts and auth blips) to "run the AI catalog sync job"; `ZdrEnforcer` has no
retry affordance at all (verifier note: `ComplianceRadarCard`'s always-rendered
`AsOf` does provide refresh). `LakeMeter.tsx:98-135` maps any status error to a
"setup required" card, telling users to re-run provisioning that already
succeeded, with a blank void below the tabs while the remote module loads.

**Fix.** Reserve capability notices for `isUnavailable(error)` (404/405/501, as
`ActionCenter.tsx:235-241` already does); render `ErrorState` for everything
else; keep the skeleton up until the sub-app actually mounts.

### A9 (M) — Error states dead-end without a retry affordance
`ErrorState` (`ui.tsx:329` area) has no retry button, and several pages hide the
`AsOf` refresh control in error/empty branches (`ActionCenter`, `Audit`, `Jobs`,
`Dashboards`, `CostValue`, `Workspaces`, `Settings`). A single cold-start 504 on
first load leaves a terminal error card whose text says "refresh" while offering
no way to do it. (`MissionControl` self-heals via its 30 s poll — see C8.)

**Fix.** Add `onRetry` to `ErrorState` wired to `query.refetch`, and/or render
`AsOf` unconditionally (it already tolerates `asOf === undefined`,
`ui.tsx:546-551`).

### A10 (L) — Small frame/freshness gaps during waits
`CostAnomaly.tsx:40` drops the whole page frame (header + back link) while
pending; the Costs → forecast tab hides the `as_of` stamp and refresh when the
budget list is empty or errored (`CostValue.tsx:337` area).

---

## B. Backend latency mechanics

### B1 (H) — Cold cache + per-key locks + 40-token thread pool = app-wide hang
`cache.cached` (`backend/cache.py:38-45`) runs the loader synchronously while
holding the key's `threading.Lock`. Every blocked caller is a sync-`def` route
occupying one of anyio's ~40 default thread-pool tokens; the identity middleware
consumes another token per request (`app.py:59`); `POST /api/chat` holds one for
an entire agent run (`chat.py:170-241`). First page view after warehouse
auto-stop: 6–10 endpoints × up-to-45 s loaders, duplicates queueing on locks —
at ~40 in-flight requests the pool is exhausted and **even cheap, unrelated
endpoints stop responding**, because their identity verification can't get a
thread. This is the most direct mechanism for "the whole app hangs."

**Fix.** (a) Stale-while-revalidate: serve an existing stale value immediately
and refresh in one background thread. (b) For a truly empty key, single-flight:
one caller loads (non-blocking try-lock); others get a typed 202 "warming"
payload the UI renders via A2. (c) Raise the anyio limiter at startup
(`anyio.to_thread.current_default_thread_limiter().total_tokens = 100+`).

### B2 (H) — Landing endpoint runs 6–7 sequential warehouse statements on 15/30 s TTLs
`/api/mission-control` (`control_plane.py:744-979`): findings under a 30 s TTL
plus **four separate `list_actions`** statements under a 15 s TTL, all
sequential, plus a `DESCRIBE TABLE` per findings read
(`control_plane_repository.py:1095`). The 15 s TTL guarantees the four-statement
load is cold on virtually every navigation: 3–10 s server-side *warm*, and the
place cold starts get paid first.

**Fix.** One SQL statement with `status IN (...)` split client-side; run
findings/actions loaders concurrently; lengthen TTLs (the envelope already
carries `as_of`/`cached` so the UI can show freshness) or stale-while-revalidate
via B1.

### B3 (H) — Uncached SCIM round trip + fresh `WorkspaceClient` on every `/api` call
`app.py:53-76` verifies identity on every `/api/*` request (except
`/api/health`); `verify()` builds a new `WorkspaceClient`
(`identity.py:15-23, 119`) and issues `GET /api/2.0/preview/scim/v2/Me`
(`identity.py:120-123`) with **no memoization** — even for endpoints that touch
no workspace state (`/api/config`, `/api/estimator/patterns`). Adds a fixed
~100–500 ms to every call and one thread-pool token; a page fanning out 8
requests performs 8 identical SCIM calls. The LakeMeter mount burst (30–50
requests, `integrations/lakemeter/frontend/src/entry.tsx:63`) pays it 30–50
times.

**Fix.** TTL-cache the verified `Actor` keyed by a hash of the forwarded token
(~60 s) — **for read-only requests only**, and make that decision **at the
middleware verification itself**. The `verified_api_boundary` middleware's
flag-less `verify()` call (`app.py:59`) is the only verification that runs in
production: `require_verified_user`/`require_operator` (`deps.py:189-209`)
reuse the `Actor` already stored on `request.state` and never re-verify, and no
production code passes `verify(require_approver=True)`. So a cache bypass keyed
on those flags would never fire — instead, have the middleware skip the cache
for non-safe methods (every governed mutation — plan/approve/reject at
`control_plane.py:1041-1125` — is a POST), or maintain an explicit no-cache
route list. Rationale: the cached `Actor` carries approver/proposer roles, and a
user removed from `dbx-platform-approvers` mid-TTL would otherwise still pass
authorization (group removal does not revoke the token, so no downstream 401
ever fires); the safety model requires *current* membership at approval time.
Independently, reuse one resolved host/`ApiClient` config instead of a new
`WorkspaceClient` per request.

### B4 (M) — `control_plane_scope()` makes a live `get_workspace_id()` call per request
`deps.py:82` reaches the workspace API on every call at ~38 router call sites —
another 100–300 ms stacked on B3 before the cache is even consulted.

**Fix.** Memoize on success (module-level, like `meta.py`'s pattern) or inject
`DATABRICKS_WORKSPACE_ID` via `resources/app.yml` env.

### B5 (M) — First `/api/v1` request imports the whole vendored LakeMeter backend on the event loop
`LazyLakeMeterApp` (`backend/lakemeter_integration.py:123`) builds the upstream
app synchronously inside the ASGI call — the import chain (SQLAlchemy etc.)
freezes the **entire event loop**, stalling every request from every user for
the duration (typically 1–3 s) after each app restart.

**Fix.** `await run_in_threadpool(build_upstream_app)` inside the lock, and/or
warm it from a startup hook in a background thread.

### B6 (M) — Viewer-masking middleware buffers and re-encodes JSON on the event loop
For non-operator viewers, `app.py:88-108` buffers the full body, then
parse/mask/serialize runs as event-loop CPU work — stalling all concurrent
requests for its duration, and double-masking endpoints that already mask in the
route (e.g. `overview.py:109`).

**Fix.** Offload buffer+mask+serialize with `run_in_threadpool` (verify is
already offloaded); skip re-masking for routes that mask themselves (header
flag).

### B7 (M) — Redundant warehouse statements from cache-key fragmentation
- Security & Performance routes re-run the **identical** pillar findings query
  under 4 separate cache keys (8 statements cold where 2 would do), and
  Performance keys include `days` the loader ignores
  (`backend/routers/security.py:14` area).
- LLM-cost `/summary` uses a different cache key than the sibling endpoints, so
  the screen's first load runs two full 4-query ledger loads
  (`llm_cost.py:142`).
- Every findings read pays a `DESCRIBE TABLE` round trip; `list_approvals` /
  `list_events` re-fetch the action row they were just handed
  (`control_plane_repository.py:1095` area) — an action detail is ~7 serial
  round trips.
- `GET /api/jobs/{id}/runs` bypasses the jobs cache: a warehouse statement plus
  a full `jobs.list()` per poll (`jobs.py:106`), fighting the 5-minute
  auto-stop.
- Cold `/api/cost/overview` runs up to 6 warehouse statements + 2 Jobs-API calls
  strictly sequentially (`cost.py:316-439`).

**Fix.** Cache per pillar and filter after; load ledgers once at the widest
window and slice; cache the DESCRIBE result per process; pass the fetched action
down; route the `/runs` authorization check through the cached jobs entry;
parallelize the cost-overview loaders and share the `usage_report` result.

---

## C. Frontend delivery & data fetching

### C1 (H) — No route-level code splitting: one 1,032 kB entry chunk (301 kB gzip)
`App.tsx:37-52` statically imports all 16 pages and pre-instantiates them as JSX
in `NAV` (`App.tsx:61-78`); no `React.lazy`/`Suspense` anywhere;
`vite.config.ts` has no `manualChunks`. Measured build: single
`index-*.js` at 1,031.92 kB minified. Every visitor parses ~1 MB before any
screen renders — and again after every deploy, since one hash covers everything.

**Fix.** `React.lazy(() => import("./pages/X"))` with component references in
`NAV`, `<Suspense fallback={<Skeleton rows={6}/>}>` around `<Routes>`. Moves
~700 kB out of the entry chunk; Vite emits per-route chunks and modulepreload
links automatically.

### C2 (H) — recharts + d3/redux stack: 364 kB (35 % of bundle) for two pages
`recharts` is imported by exactly one component
(`CostTrendChart.tsx:1-10`), used only by `CostValue` and `CostAnomaly`, but
lands in the entry chunk via C1. Source-map attribution: 364 kB minified.

**Fix.** Falls out of C1 (lazy the cost routes), or `React.lazy` the chart
component itself.

### C3 (M) — react-markdown stack (~110 kB) ships eagerly for a closed panel
`ChatThread.tsx:12` → `AssistantPanel.tsx:15` → eager import in `App.tsx:31`,
though the assistant starts closed; `Digest.tsx:4` adds the same stack to the
Automations path.

**Fix.** Lazy-mount `AssistantPanel`/`ChatThread` when first opened; lazy
`Digest` inside Automations.

### C4 (M) — Bundle served uncompressed with no cache headers
`create_app` adds no `GZipMiddleware`; `/assets` is a bare `StaticFiles` mount
(`app.py:142`) — no `Cache-Control`, so the content-hashed files aren't marked
immutable, and the 1 MB JS transfers raw unless the Apps proxy happens to
compress.

**Fix.** `GZipMiddleware(minimum_size=1000)` (also compresses large JSON API
responses) + `Cache-Control: public, max-age=31536000, immutable` on `/assets`.

### C5 (M) — Blank page until React mounts
`index.html:16-19` is an empty `#root` + module script. Nothing paints during
the biggest single wait in the app's lifecycle. (The inline theme script at
lines 7–14 proves inline bootstrap is acceptable here.)

**Fix.** Inline a minimal static shell inside `#root` (theme-aware via the
existing `html.dark` class); React replaces it on mount.

### C6 (H) — Costs page: whole-page gate + tab waterfall
`CostValue.tsx:637-653` renders a full-page skeleton until
`/api/cost/overview` resolves; all 8 tabs mount only afterwards, then fire their
own queries (Databricks tab: 7 more; AI tab: 5 more) even though every input
they need (`days`, tab) is already in the URL (`CostValue.tsx:636`).
Deep-linking to a tab on a cold warehouse = two full cold waits in series.

**Fix.** Pass URL-derived `days` down instead of `data.period.days`; prefetch
tab queries in parallel with overview; render the tab shell with per-section
skeletons — only the Overview tab needs the overview payload.

### C7 (H) — Default `retry: 3` turns cold starts into a ~3-minute silent spinner
`main.tsx:8-12` sets only `refetchOnWindowFocus: false`, so TanStack Query's
default retry (3 retries, exponential backoff, retries 503/504 too) applies to
every query that doesn't opt out. Most hand-written queries set `retry: false` —
but the estimator suite doesn't (`CostPlanner.tsx:84-88, 119-129`,
`DeploymentsPanel.tsx:154`, `EstimateLibrary.tsx:70`, `PricingFreshness.tsx:9`,
`SimilarEstimates.tsx:22`), and `FindingsSection`'s manual-refresh
`fetchQuery` path inherits it too (`FindingsSection.tsx:46-51`). Cold warehouse:
4 × 45 s + backoff ≈ 3 minutes of "recomputing…" before any error shows.

**Fix.** `queries: { retry: false }` (or retry-network-errors-once) in the
`QueryClient` defaults; keep explicit opt-ins where retries are wanted.

### C8 (M) — MissionControl polls every 30 s into 15 s backend TTLs
`MissionControl.tsx:664` sets `refetchInterval: 30_000` against an endpoint
whose action TTL is 15 s (B2): a perpetual warehouse-query loop while the tab is
open — the prod warehouse can never reach its 5-minute auto-stop (standing
cost), and each poll competes for locks/threads with real user loads. The AsOf
spinner also spins on every background poll, reading as constant activity.

**Fix.** Lengthen to 120–300 s aligned with meaningful TTLs, or drop polling for
manual refresh + refetch-on-navigation; key the spinner on manual refresh only.

### C9 (M) — Window/param changes drop populated pages back to skeletons
No `placeholderData: keepPreviousData` anywhere: switching the 7/30/90-day
window on Performance blanks 7 populated cards (6 `FindingsSection`s +
`GatewayTelemetry`) and fires 7 fresh warehouse-backed requests
(`FindingsSection.tsx:39`).

**Fix.** `placeholderData: keepPreviousData` on window-parameterized queries +
a subtle `isFetching` indicator instead of unmounting content.

### C10 (M) — Fetch layer ignores `AbortSignal` and has no timeout
`api.ts:3-21` never threads TanStack Query's signal into `fetch`, so abandoned
navigations/window-switches leave zombie requests holding browser connections
(6-per-origin on HTTP/1.1) and backend threads; a hung upstream pends ~5 min
silently.

**Fix.** `apiGet(path, params, signal)` from the queryFn context, plus a
generous `AbortSignal.timeout` mapped to the transient error class.

### C11 (M) — Estimator interaction costs
- The rigor slider is the one control that escaped debouncing: every drag tick
  enters the query key → ~50 POST `/estimate` calls per drag
  (`CostPlanner.tsx:120`; contrast the correctly debounced what-if sliders,
  `CostPlanner.tsx:49-56`).
- The LakeMeter sub-app boot renders a bare shadow-host div with no indicator
  between "status ready" and actual mount (`LakeMeter.tsx:137`), and the
  `entry.js` download is serialized behind the status query
  (`LakeMeter.tsx:56`) though only the *mount* needs readiness.

**Fix.** Debounce `rigorPct` (~250–300 ms) or refetch on drag-end; keep the
skeleton until `mountLakeMeter` resolves; start
`import("/lakemeter/entry.js")` in parallel with the status query.

### C12 (L) — Small duplications & mislabels
- Data Governance fetches `/api/governance/untagged-spend` twice under two keys
  (`DataGovernance.tsx:62`).
- `LiveRatesIndicator` is pinned to the 30-day window, so its freshness badge
  can mislabel the 7/90-day charts (`Performance.tsx:15`).
- Job run history lacks `staleTime`, causing redundant background refetches on
  re-expand (`Jobs.tsx:11`).
- No vendor chunking: every deploy invalidates the entire cached bundle
  (`vite.config.ts:7`).

---

## Suggested order of attack

**Quick wins (hours, high leverage):**
1. `retry: false` QueryClient default (C7) — deletes the 3-minute silent spinner.
2. Static shell in `index.html` (C5) + `GZipMiddleware` + immutable
   `Cache-Control` on `/assets` (C4).
3. TTL-cache identity verification per token for read-only requests — decided
   in the middleware by HTTP method, since that is the only verification point;
   mutations (POSTs) always re-verify (B3) — and memoize the workspace id
   (B4). Removes 1–2 network round trips from *every* read call.
4. `isPending`/`isError` branches in `EstimateLibrary`/`DeploymentsPanel` (A7);
   debounce the rigor slider (C11).

**The cold-start visibility ask (1–2 days):**
5. `GET /api/warehouse-status` + `useWarehouseStatus` + slow-load escalation in
   a shared `<LoadingState>` (A1, A2) — pending screens explicitly say the
   warehouse is starting.
6. Treat `query_timeout` as transient with bounded auto-retry and honest copy
   (A5); stop swallowing `TimeoutError` into setup errors (A4); don't cache
   degraded sections for 5 minutes (A6); `onRetry` on `ErrorState` (A9).

**Structural latency (2–4 days):**
7. Route-level code splitting with Suspense fallbacks (C1–C3).
8. `cache.py` stale-while-revalidate + single-flight + bigger thread limiter
   (B1); request-level statement deadline (A3).
9. Mission Control loader consolidation and TTL alignment (B2, C8); Costs page
   tab-query hoisting (C6); backend cache-key dedup (B7).
10. Thread-pool the LakeMeter backend import / warm at startup (B5); offload
    viewer masking (B6).
