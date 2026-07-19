"""AI Solution Cost & TCO estimation engine — pure, deterministic, offline.

Turns a validated set of plain-English requirements into a 3-tier
(prototype / production / fiduciary) × 2-scenario (databricks / azure) cost
matrix with DEV/UAT/PROD environment columns, explicitly separating the cost
of *running* the AI from the cost of *checking* it (the "evaluation tax":
AI-graded reviews plus activity-record storage).

Design rules this module must keep:

- **Pure.** No I/O, no clock, no network. Prices arrive as snapshot rows
  (see estimator_pricing.py); every other number is either a user input or a
  named assumption in ``estimator_data/tiers.json``.
- **Deterministic and transparent.** Same inputs → identical output. Every
  line item carries ``quantity × unit price = cost``, its meter, its pricing
  snapshot date and the named assumptions it used.
- **Missing prices are loud.** A rate key with no snapshot price produces a
  line item with ``monthly_cost: None`` and an entry in ``missing_prices`` —
  never a silent $0.
- **No jargon in user-facing strings.** Labels come from the data files and
  are lint-checked by tests.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from functools import cache
from importlib import resources

ENGINE_VERSION = "1"
ENVS = ("dev", "uat", "prod")
TIERS = ("prototype", "production", "fiduciary")
SCENARIOS = ("databricks", "azure")

RUN_COMPONENTS = (
    "model_tokens",
    "embedding_tokens",
    "vector_storage",
    "serving_compute",
    "state_store",
)
EVAL_TAX_COMPONENTS = ("eval_judge_tokens", "trace_storage")

_HOURS = "hours_per_month"


# --- packaged data ------------------------------------------------------------


def _load_data(name: str) -> dict:
    text = resources.files("dbx_platform.estimator_data").joinpath(name).read_text("utf-8")
    return json.loads(text)


@cache
def load_patterns() -> dict:
    return _load_data("patterns.json")


@cache
def load_tiers() -> dict:
    return _load_data("tiers.json")


@cache
def load_rate_card() -> dict:
    return _load_data("rate_card.json")


@cache
def load_prompt(name: str) -> str:
    return (
        resources.files("dbx_platform.estimator_data")
        .joinpath(f"prompts/{name}.md")
        .read_text("utf-8")
    )


# --- requirements -------------------------------------------------------------


@dataclass(frozen=True)
class Requirements:
    """Validated sizing inputs. 0 means "use the pattern default"."""

    pattern: str
    monthly_requests: int
    avg_input_tokens: int = 0
    avg_output_tokens: int = 0
    corpus_gb: float = 0.0
    corpus_growth_pct_monthly: float = 2.0
    agent_steps: int = 0
    peak_rps: float = 0.0
    needs_memory: bool | None = None
    monthly_active_users: int = 0
    region: str = "eastus"
    currency: str = "USD"


_BOUNDS = {
    "monthly_requests": (1, 1_000_000_000, "requests per month"),
    "avg_input_tokens": (0, 2_000_000, "average request size"),
    "avg_output_tokens": (0, 2_000_000, "average answer size"),
    "corpus_gb": (0, 1_000_000, "document collection size (GB)"),
    "corpus_growth_pct_monthly": (0, 100, "monthly document growth (%)"),
    "agent_steps": (0, 50, "steps per task"),
    "peak_rps": (0, 10_000, "peak requests per second"),
    "monthly_active_users": (0, 10_000_000, "people using it each month"),
}


def validate_requirements(raw: dict, patterns: dict | None = None) -> Requirements:
    """Bounds-check a raw dict into Requirements, with plain-English errors."""

    patterns = patterns or load_patterns()
    pattern = str(raw.get("pattern") or "")
    if pattern not in patterns["patterns"]:
        known = ", ".join(sorted(patterns["patterns"]))
        raise ValueError(f"Unknown solution pattern '{pattern}'. Choose one of: {known}.")
    values: dict = {"pattern": pattern}
    for field, (lo, hi, label) in _BOUNDS.items():
        value = raw.get(field, 0 if field != "corpus_growth_pct_monthly" else 2.0)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'{label}' must be a number, got {value!r}.") from exc
        if not lo <= number <= hi:
            raise ValueError(f"'{label}' must be between {lo:,} and {hi:,}, got {number:,.0f}.")
        values[field] = (
            number
            if field in ("corpus_gb", "corpus_growth_pct_monthly", "peak_rps")
            else int(number)
        )
    needs_memory = raw.get("needs_memory")
    values["needs_memory"] = None if needs_memory is None else bool(needs_memory)
    values["region"] = str(raw.get("region") or "eastus").strip().lower()
    values["currency"] = str(raw.get("currency") or "USD").strip().upper()
    if not values["region"]:
        raise ValueError("A cloud region is required.")
    return Requirements(**values)


def requirements_hash(
    req: Requirements, *, rigor_pct: int, rate_card_version: str, snapshot_date: str
) -> str:
    """Deterministic identity of one estimate: inputs + every version that shaped it."""

    payload = {
        "requirements": asdict(req),
        "rigor_pct": int(rigor_pct),
        "engine_version": ENGINE_VERSION,
        "rate_card_version": rate_card_version,
        "snapshot_date": str(snapshot_date),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scale_bracket(value: float) -> int:
    """Order-of-magnitude bucket used for 'similar past estimates' matching."""

    return int(math.floor(math.log10(value))) if value >= 1 else 0


# --- price book ---------------------------------------------------------------


@dataclass(frozen=True)
class UnitPrice:
    rate_key: str
    unit_price: float
    currency: str
    source: str
    meter_name: str
    snapshot_date: str
    provenance: str = ""


class PriceBook:
    """Snapshot prices keyed by rate key; pure lookups, no I/O."""

    def __init__(self, prices: dict[str, UnitPrice], rate_card: dict, snapshot_date: str):
        self._prices = prices
        self._rate_card = rate_card
        self.snapshot_date = snapshot_date

    def resolve(self, rate_key: str) -> UnitPrice | None:
        return self._prices.get(rate_key)

    def token_price_per_1m(
        self, model_class: str, direction: str, scenario: str
    ) -> UnitPrice | None:
        """Price per 1M text units for a model class, per deployment scenario.

        Azure prices come straight from the snapshot (normalized to per-1M at
        parse time). Databricks pay-per-token endpoints are billed in DBUs, so
        the per-1M DBU draw is a git-versioned rate-card constant (with its
        provenance string) multiplied by the live $/DBU snapshot price.
        """
        model = self._rate_card.get("models", {}).get(model_class)
        if not model:
            return None
        if scenario == "azure":
            rate_key = model.get("azure", {}).get(direction)
            return self.resolve(rate_key) if rate_key else None
        dbx = model.get("databricks", {})
        dbu_price = self.resolve(dbx.get("dbu_rate_key", ""))
        dbu_per_1m = dbx.get(f"dbu_per_1m_{direction}_tokens")
        if dbu_price is None or dbu_per_1m is None:
            return None
        return UnitPrice(
            rate_key=f"{dbx.get('dbu_rate_key')}×{model_class}.{direction}",
            unit_price=dbu_per_1m * dbu_price.unit_price,
            currency=dbu_price.currency,
            source="databricks_list_prices+rate_card",
            meter_name=dbu_price.meter_name,
            snapshot_date=dbu_price.snapshot_date,
            provenance=dbx.get("provenance", ""),
        )


def build_price_book(snapshot_rows: list[dict], rate_card: dict) -> PriceBook:
    """Pick one deterministic price per rate key from snapshot rows. Pure.

    When several meters matched a rate key regex, the cheapest current meter
    wins (ties broken by meter name) so re-runs always pick the same row.
    """
    best: dict[str, UnitPrice] = {}
    snapshot_date = ""
    for row in sorted(
        snapshot_rows,
        key=lambda r: (float(r.get("unit_price") or 0.0), str(r.get("meter_name") or "")),
    ):
        rate_key = str(row.get("rate_key") or "")
        price = row.get("unit_price")
        if not rate_key or price is None:
            continue
        snapshot_date = max(snapshot_date, str(row.get("snapshot_date") or ""))
        if rate_key not in best:
            best[rate_key] = UnitPrice(
                rate_key=rate_key,
                unit_price=float(price),
                currency=str(row.get("currency") or "USD"),
                source=str(row.get("source") or ""),
                meter_name=str(row.get("meter_name") or ""),
                snapshot_date=str(row.get("snapshot_date") or ""),
            )
    return PriceBook(best, rate_card, snapshot_date)


# --- the engine ---------------------------------------------------------------


def _assumption(tiers: dict, name: str) -> float:
    return float(tiers["assumptions"][name]["value"])


def _effective(req: Requirements, patterns: dict) -> dict:
    """Merge user inputs with pattern defaults into the numbers the math uses."""

    defaults = patterns["patterns"][req.pattern]["defaults"]
    needs_kb = bool(defaults.get("needs_knowledge_base"))
    corpus = req.corpus_gb or float(defaults.get("default_corpus_gb", 0) if needs_kb else 0)
    return {
        "t_in": req.avg_input_tokens or int(defaults["avg_input_tokens"]),
        "t_out": req.avg_output_tokens or int(defaults["avg_output_tokens"]),
        "steps": req.agent_steps or int(defaults["agent_steps"]),
        "needs_kb": needs_kb,
        "context_tokens": (
            int(defaults.get("retrieval_chunks", 0)) * int(defaults.get("chunk_tokens", 0))
            if needs_kb
            else 0
        ),
        "corpus_gb": corpus,
        "needs_memory": (
            req.needs_memory
            if req.needs_memory is not None
            else bool(defaults.get("needs_memory"))
        ),
    }


def _env_requests(req: Requirements, tiers: dict) -> dict[str, float]:
    return {
        "dev": _assumption(tiers, "dev_requests_per_month"),
        "uat": req.monthly_requests * _assumption(tiers, "uat_traffic_fraction"),
        "prod": float(req.monthly_requests),
    }


def _line(
    *,
    component: str,
    env: str,
    tier: str,
    scenario: str,
    label: str,
    quantity: float,
    unit: str,
    price: UnitPrice | None,
    assumptions: list[str],
    is_eval_tax: bool = False,
    eval_group: str | None = None,
) -> dict:
    """Build one transparent line item; None price stays loudly unpriced."""

    if price is None:
        cost = None
        formula = f"{quantity:,.2f} {unit} × (price unavailable)"
    else:
        cost = round(quantity * price.unit_price, 2)
        formula = (
            f"{quantity:,.2f} {unit} × {price.unit_price:,.6f} "
            f"{price.currency}/{unit.rstrip('s')} = {cost:,.2f} {price.currency}"
        )
    return {
        "component": component,
        "env": env,
        "tier": tier,
        "scenario": scenario,
        "label": label,
        "quantity": round(quantity, 4),
        "unit": unit,
        "unit_price": None if price is None else price.unit_price,
        "currency": None if price is None else price.currency,
        "price_source": None if price is None else price.source,
        "meter_name": None if price is None else price.meter_name,
        "snapshot_date": None if price is None else price.snapshot_date,
        "provenance": (price.provenance if price and price.provenance else None),
        "monthly_cost": cost,
        "formula": formula,
        "assumptions": assumptions,
        "is_eval_tax": is_eval_tax,
        "eval_group": eval_group,
    }


def compute_estimate(
    req: Requirements,
    *,
    tier: str,
    rigor_pct: int,
    scenario: str,
    price_book: PriceBook,
    tiers: dict | None = None,
    patterns: dict | None = None,
) -> dict:
    """One tier × scenario estimate across DEV/UAT/PROD. Pure."""

    tiers = tiers or load_tiers()
    patterns = patterns or load_patterns()
    if tier not in tiers["tiers"]:
        raise ValueError(f"Unknown tier '{tier}'.")
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'.")
    cfg = tiers["tiers"][tier]
    rigor = max(0, min(100, int(rigor_pct)))
    if cfg["rigor_locked"]:
        rigor = int(cfg["default_rigor_pct"])
    eff = _effective(req, patterns)
    reqs = _env_requests(req, tiers)
    a = lambda name: _assumption(tiers, name)  # noqa: E731
    items: list[dict] = []

    model_cls = cfg["model_class"]
    judge_cls = cfg["judge_class"]
    p_in = price_book.token_price_per_1m(model_cls, "input", scenario)
    p_out = price_book.token_price_per_1m(model_cls, "output", scenario)
    for env in ENVS:
        tokens_in = reqs[env] * eff["steps"] * (eff["t_in"] + eff["context_tokens"])
        tokens_out = reqs[env] * eff["steps"] * eff["t_out"]
        items.append(
            _line(
                component="model_tokens", env=env, tier=tier, scenario=scenario,
                label="Answering requests (AI model, reading)",
                quantity=tokens_in / 1e6, unit="million text units", price=p_in,
                assumptions=["dev_requests_per_month", "uat_traffic_fraction"],
            )
        )
        items.append(
            _line(
                component="model_tokens", env=env, tier=tier, scenario=scenario,
                label="Answering requests (AI model, writing)",
                quantity=tokens_out / 1e6, unit="million text units", price=p_out,
                assumptions=["dev_requests_per_month", "uat_traffic_fraction"],
            )
        )

    if eff["needs_kb"]:
        p_embed = price_book.token_price_per_1m("embedding_standard", "input", scenario)
        corpus_fraction = {
            "dev": a("dev_corpus_fraction"), "uat": a("uat_corpus_fraction"), "prod": 1.0,
        }
        for env in ENVS:
            corpus = eff["corpus_gb"] * corpus_fraction[env]
            monthly_tokens = corpus * a("tokens_per_gb") * (
                1 / a("corpus_amortization_months") + req.corpus_growth_pct_monthly / 100
            )
            items.append(
                _line(
                    component="embedding_tokens", env=env, tier=tier, scenario=scenario,
                    label="Keeping the knowledge base indexed",
                    quantity=monthly_tokens / 1e6, unit="million text units", price=p_embed,
                    assumptions=[
                        "tokens_per_gb", "corpus_amortization_months",
                        "dev_corpus_fraction", "uat_corpus_fraction",
                    ],
                )
            )
            if scenario == "azure":
                sku = cfg["search_sku"]
                units = max(
                    math.ceil(corpus / a("search_partition_gb")) if corpus else 1,
                    int(cfg["search_min_replicas"]) if env == "prod" else 1,
                )
                items.append(
                    _line(
                        component="vector_storage", env=env, tier=tier, scenario=scenario,
                        label="Document search capacity",
                        quantity=units * a(_HOURS), unit="unit-hours",
                        price=price_book.resolve(f"search.{sku}.unit"),
                        assumptions=["search_partition_gb", _HOURS],
                    )
                )
            else:
                units = max(1, math.ceil(corpus / a("vs_gb_per_unit")) if corpus else 1)
                hours = a(_HOURS) if cfg["serving_always_on"] else a("active_hours_per_month")
                items.append(
                    _line(
                        component="vector_storage", env=env, tier=tier, scenario=scenario,
                        label="Document search capacity",
                        quantity=units * a("vs_dbu_per_hour") * hours, unit="DBUs",
                        price=price_book.resolve("dbx.vector_search.dbu"),
                        assumptions=[
                            "vs_gb_per_unit", "vs_dbu_per_hour",
                            _HOURS if cfg["serving_always_on"] else "active_hours_per_month",
                        ],
                    )
                )

    seconds_per_month = 30 * 86400
    for env in ENVS:
        peak_rps = req.peak_rps if (env == "prod" and req.peak_rps) else (
            reqs[env] * a("peak_factor") / seconds_per_month
        )
        concurrency = peak_rps * a("request_latency_seconds")
        units = max(1, math.ceil(concurrency / a("concurrency_per_serving_unit")))
        if env == "prod":
            units = max(units, int(cfg["serving_min_units"]))
        if scenario == "databricks":
            hours = (
                a(_HOURS) * float(cfg["serving_duty_cycle"])
                if cfg["serving_always_on"]
                else a("active_hours_per_month")
            )
            items.append(
                _line(
                    component="serving_compute", env=env, tier=tier, scenario=scenario,
                    label="Serving capacity (answers on demand)",
                    quantity=units * a("serving_dbu_per_unit_hour") * hours, unit="DBUs",
                    price=price_book.resolve("dbx.model_serving.dbu"),
                    assumptions=[
                        "peak_factor", "request_latency_seconds",
                        "concurrency_per_serving_unit", "serving_dbu_per_unit_hour",
                    ],
                )
            )
        else:
            nodes = max(
                int(cfg["aks_min_nodes"]) if env == "prod" else 1,
                math.ceil(units / a("pods_per_node")),
            )
            items.append(
                _line(
                    component="serving_compute", env=env, tier=tier, scenario=scenario,
                    label="Hosting machines (Kubernetes workers)",
                    quantity=nodes * a(_HOURS), unit="machine-hours",
                    price=price_book.resolve("vm.d8s_v5"),
                    assumptions=[
                        "peak_factor", "request_latency_seconds",
                        "concurrency_per_serving_unit", "pods_per_node", _HOURS,
                    ],
                )
            )
            if cfg["aks_cluster_fee"]:
                items.append(
                    _line(
                        component="serving_compute", env=env, tier=tier, scenario=scenario,
                        label="Kubernetes management fee (uptime guarantee)",
                        quantity=a(_HOURS), unit="cluster-hours",
                        price=price_book.resolve("aks.standard_uptime"),
                        assumptions=[_HOURS],
                    )
                )

    if eff["needs_memory"]:
        for env in ENVS:
            replicas = int(cfg["state_replicas"]) if env == "prod" else 1
            if scenario == "databricks":
                cu = a("lakebase_cu_per_tier_base") + (
                    req.monthly_active_users // int(a("memory_users_per_capacity_unit"))
                    if env == "prod"
                    else 0
                )
                hours = a(_HOURS) if cfg["serving_always_on"] else a("active_hours_per_month")
                items.append(
                    _line(
                        component="state_store", env=env, tier=tier, scenario=scenario,
                        label="Remembering users between sessions",
                        quantity=cu * replicas * hours, unit="capacity-unit-hours",
                        price=price_book.resolve("dbx.lakebase.dbu"),
                        assumptions=[
                            "lakebase_cu_per_tier_base", "memory_users_per_capacity_unit",
                        ],
                    )
                )
            else:
                rate_key = cfg["postgres_rate_key"] if env == "prod" else (
                    "postgres.flex.burstable"
                )
                items.append(
                    _line(
                        component="state_store", env=env, tier=tier, scenario=scenario,
                        label="Remembering users between sessions",
                        quantity=replicas * a(_HOURS), unit="instance-hours",
                        price=price_book.resolve(rate_key),
                        assumptions=["postgres_storage_gb", _HOURS],
                    )
                )

    judged_steps = {"none": 0, "final": 1, "trajectory": eff["steps"]}[cfg["judged_steps_mode"]]
    judge_share = float(cfg["llm_judge_share"])
    pj_in = price_book.token_price_per_1m(judge_cls, "input", scenario)
    pj_out = price_book.token_price_per_1m(judge_cls, "output", scenario)
    review_in = eff["t_in"] + eff["t_out"] + a("judge_rubric_tokens")
    review_out = a("judge_output_tokens")
    eval_runs = {"dev": a("ci_runs_per_month"), "uat": a("releases_per_month")}
    suite_steps = max(judged_steps, 1) if tier != "prototype" else 0
    for env in ENVS:
        if env == "prod":
            reviews = reqs["prod"] * (rigor / 100) * judged_steps * judge_share
            group = "production_monitoring"
            run_assumptions = ["judge_rubric_tokens", "judge_output_tokens"]
        else:
            reviews = eval_runs[env] * cfg["eval_set_size"] * suite_steps * judge_share
            group = "improvement_pipeline"
            run_assumptions = [
                "ci_runs_per_month" if env == "dev" else "releases_per_month",
                "judge_rubric_tokens", "judge_output_tokens",
            ]
        for direction, per_review, price in (
            ("reading", review_in, pj_in), ("writing", review_out, pj_out),
        ):
            items.append(
                _line(
                    component="eval_judge_tokens", env=env, tier=tier, scenario=scenario,
                    label=f"AI-graded reviews ({direction})",
                    quantity=reviews * per_review / 1e6, unit="million text units",
                    price=price, assumptions=run_assumptions,
                    is_eval_tax=True, eval_group=group,
                )
            )
        if env != "prod" and tier != "prototype":
            items.append(
                _line(
                    component="eval_judge_tokens", env=env, tier=tier, scenario=scenario,
                    label="Automated quality-check suite (code-based)",
                    quantity=eval_runs[env] * a("eval_harness_dbu_per_run"), unit="DBUs",
                    price=price_book.resolve("dbx.jobs_serverless.dbu"),
                    assumptions=["eval_harness_dbu_per_run"],
                    is_eval_tax=True, eval_group="improvement_pipeline",
                )
            )

    # Activity records land in cloud object storage in both scenarios.
    p_storage = price_book.resolve("storage.hot_gb_month")
    for env in ENVS:
        kb_per_request = a("base_trace_kb") + eff["steps"] * a("step_trace_kb")
        if env == "prod":
            kb_per_request += (rigor / 100) * a("judged_payload_kb")
        gb_months = reqs[env] * kb_per_request / 1e6 * cfg["retention_months"]
        items.append(
            _line(
                component="trace_storage", env=env, tier=tier, scenario=scenario,
                label="Keeping activity records",
                quantity=gb_months, unit="GB-months", price=p_storage,
                assumptions=["base_trace_kb", "step_trace_kb", "judged_payload_kb"],
                is_eval_tax=True, eval_group="production_monitoring",
            )
        )

    totals = {env: 0.0 for env in ENVS}
    run_cost = {env: 0.0 for env in ENVS}
    eval_tax = {env: 0.0 for env in ENVS}
    improvement = {env: 0.0 for env in ENVS}
    missing: list[str] = []
    for item in items:
        if item["monthly_cost"] is None:
            missing.append(f"{item['component']}/{item['env']}: {item['label']}")
            continue
        totals[item["env"]] += item["monthly_cost"]
        if item["is_eval_tax"]:
            eval_tax[item["env"]] += item["monthly_cost"]
            if item["eval_group"] == "improvement_pipeline":
                improvement[item["env"]] += item["monthly_cost"]
        else:
            run_cost[item["env"]] += item["monthly_cost"]
    round2 = lambda d: {k: round(v, 2) for k, v in d.items()}  # noqa: E731
    return {
        "tier": tier,
        "scenario": scenario,
        "rigor_pct": rigor,
        "line_items": items,
        "totals_by_env": round2(totals),
        "run_cost_by_env": round2(run_cost),
        "eval_tax_by_env": round2(eval_tax),
        "improvement_pipeline_by_env": round2(improvement),
        "missing_prices": sorted(set(missing)),
    }


def compute_matrix(
    req: Requirements,
    *,
    rigor_pct: int,
    price_book: PriceBook,
    tiers: dict | None = None,
    patterns: dict | None = None,
) -> dict:
    """All tiers × scenarios, each with an exact affine rigor curve. Pure."""

    tiers = tiers or load_tiers()
    patterns = patterns or load_patterns()
    rate_card = load_rate_card()
    out: dict = {
        "engine_version": ENGINE_VERSION,
        "rate_card_version": rate_card.get("version", ""),
        "snapshot_date": price_book.snapshot_date,
        "requirements": asdict(req),
        "rigor_pct": int(rigor_pct),
        "requirements_hash": requirements_hash(
            req,
            rigor_pct=rigor_pct,
            rate_card_version=rate_card.get("version", ""),
            snapshot_date=price_book.snapshot_date,
        ),
        "blueprint": build_blueprint(req, tiers=tiers, patterns=patterns),
        "tiers": {},
    }
    for tier in TIERS:
        cfg = tiers["tiers"][tier]
        out["tiers"][tier] = {
            "label": cfg["label"],
            "description": cfg["description"],
            "rigor_locked": bool(cfg["rigor_locked"]),
            "rigor_locked_reason": cfg["rigor_locked_reason"],
            "default_rigor_pct": int(cfg["default_rigor_pct"]),
            "scenarios": {},
        }
        for scenario in SCENARIOS:
            kwargs = dict(tier=tier, scenario=scenario, price_book=price_book,
                          tiers=tiers, patterns=patterns)
            estimate = compute_estimate(req, rigor_pct=rigor_pct, **kwargs)
            at0 = compute_estimate(req, rigor_pct=0, **kwargs)
            at100 = compute_estimate(req, rigor_pct=100, **kwargs)
            estimate["rigor_curve"] = {
                "pinned": bool(cfg["rigor_locked"]),
                "by_env": {
                    env: {
                        "total_fixed": at0["totals_by_env"][env],
                        "total_slope_per_pct": round(
                            (at100["totals_by_env"][env] - at0["totals_by_env"][env]) / 100, 6
                        ),
                        "eval_fixed": at0["eval_tax_by_env"][env],
                        "eval_slope_per_pct": round(
                            (at100["eval_tax_by_env"][env] - at0["eval_tax_by_env"][env]) / 100,
                            6,
                        ),
                    }
                    for env in ENVS
                },
            }
            out["tiers"][tier]["scenarios"][scenario] = estimate
    return out


def build_blueprint(
    req: Requirements, *, tiers: dict | None = None, patterns: dict | None = None
) -> list[dict]:
    """Plain-English architecture description. Template-driven, no AI involved."""

    patterns = patterns or load_patterns()
    pattern = patterns["patterns"][req.pattern]
    sections = [dict(s) for s in pattern.get("blueprint", [])]
    sections.append(
        {
            "title": "How big this is",
            "body": (
                f"Sized for about {req.monthly_requests:,} requests per month in production, "
                "with a small fixed development environment and an acceptance-testing "
                "environment at roughly a tenth of production traffic. Every number and "
                "assumption behind the estimate is listed in the cost breakdown."
            ),
        }
    )
    sections.extend(dict(s) for s in patterns.get("shared_blueprint", []))
    return sections
