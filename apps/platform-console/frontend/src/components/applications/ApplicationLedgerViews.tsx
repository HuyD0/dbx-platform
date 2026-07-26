import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { currency } from "../../lib/format";
import type {
  ApplicationCostDriver,
  ApplicationTrendPoint,
  CostLedger,
} from "../../types/applications";
import { Badge, Card, EmptyState, SectionTitle } from "../ui";

function humanize(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function methodTone(method: ApplicationCostDriver["attribution_method"]) {
  if (
    method === "DIRECT_METADATA" ||
    method === "DIRECT_RESOURCE" ||
    method === "DIRECT_TAG"
  ) {
    return "good" as const;
  }
  if (method === "CONFLICT") return "serious" as const;
  return "warning" as const;
}

export function ApplicationLedgerCards({ ledgers }: { ledgers: CostLedger[] }) {
  if (ledgers.length === 0) {
    return (
      <EmptyState
        positive={false}
        message="No exact application cost is attributable in this window."
      />
    );
  }
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-grid bg-page/35 p-3 text-xs leading-5 text-ink-2">
        Each card preserves its source currency and pricing basis. These amounts
        may overlap and are never combined into one cross-cloud total.
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {ledgers.map((ledger) => (
          <Card key={ledger.id} className="flex h-full flex-col">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-muted">
                  {ledger.title}
                </p>
                <p className="mt-2 break-words text-2xl font-semibold tabular-nums text-ink">
                  {currency(ledger.amount, ledger.currency)}
                </p>
              </div>
              <Badge tone="info">{humanize(ledger.pricing_basis)}</Badge>
            </div>
            {ledger.trusted === false && (
              <div className="mt-3 rounded-lg border border-warning-accent bg-warning-surface px-2.5 py-2 text-[11px] leading-4 text-status-warning">
                {humanize(ledger.status ?? "partial")} evidence. This observed
                amount is visible for audit but is not a current exact claim.
              </div>
            )}
            <dl className="mt-auto grid grid-cols-2 gap-x-3 gap-y-1 pt-4 text-[11px]">
              <dt className="text-muted">Source</dt>
              <dd className="truncate text-right text-ink-2">{ledger.source}</dd>
              <dt className="text-muted">Coverage</dt>
              <dd className="text-right text-ink-2">
                {ledger.coverage_start && ledger.coverage_end
                  ? `${ledger.coverage_start}–${ledger.coverage_end}`
                  : "Not reported"}
              </dd>
              <dt className="text-muted">Scope</dt>
              <dd className="break-words text-right text-ink-2">
                {Array.isArray(ledger.scope)
                  ? ledger.scope.join(", ")
                  : ledger.scope || "Not reported"}
              </dd>
            </dl>
          </Card>
        ))}
      </div>
    </div>
  );
}

function LedgerTrend({
  ledger,
  points,
}: {
  ledger: CostLedger;
  points: ApplicationTrendPoint[];
}) {
  const data = points
    .filter((point) => point.ledger_id === ledger.id)
    .sort((left, right) => left.usage_date.localeCompare(right.usage_date));
  return (
    <article className="min-w-0 rounded-xl border border-grid bg-page/20 p-3">
      <div className="mb-2">
        <h3 className="text-xs font-semibold text-ink">{ledger.title}</h3>
        <p className="text-[11px] text-muted">
          {ledger.currency} · {humanize(ledger.pricing_basis)}
        </p>
      </div>
      {data.length === 0 ? (
        <div className="grid h-44 place-items-center rounded-lg border border-dashed border-grid text-xs text-muted">
          No daily trend is available.
        </div>
      ) : (
        <div
          className="h-44 min-w-0 w-full sm:h-52"
          role="img"
          aria-label={`${ledger.title} daily cost in ${ledger.currency}, ${humanize(
            ledger.pricing_basis,
          )}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={data}
              margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid
                stroke="var(--color-grid)"
                strokeDasharray="3 5"
                vertical={false}
              />
              <XAxis
                dataKey="usage_date"
                tick={{ fill: "var(--color-muted)", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                minTickGap={28}
                tickFormatter={(value) =>
                  new Date(`${value}T00:00:00`).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                  })
                }
              />
              <YAxis
                tick={{ fill: "var(--color-muted)", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={(value) =>
                  new Intl.NumberFormat("en-US", {
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }).format(Number(value))
                }
              />
              <Tooltip
                contentStyle={{
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-grid)",
                  borderRadius: "12px",
                  color: "var(--color-ink)",
                  fontSize: "12px",
                }}
                formatter={(value) => [
                  currency(value, ledger.currency),
                  ledger.title,
                ]}
                labelFormatter={(label) =>
                  new Date(`${label}T00:00:00`).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })
                }
              />
              <Area
                type="monotone"
                dataKey="cost"
                stroke="var(--color-series-1)"
                fill="var(--color-series-1)"
                fillOpacity={0.22}
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </article>
  );
}

export function ApplicationTrends({
  ledgers,
  points,
}: {
  ledgers: CostLedger[];
  points: ApplicationTrendPoint[];
}) {
  return (
    <Card>
      <SectionTitle
        title="Cost trend by ledger"
        subtitle="Small multiples preserve source currency and basis"
      />
      {ledgers.length === 0 ? (
        <EmptyState
          positive={false}
          message="No exact ledgers are available for a daily trend."
        />
      ) : (
        <div className="grid gap-3 xl:grid-cols-2">
          {ledgers.map((ledger) => (
            <LedgerTrend
              key={ledger.id}
              ledger={ledger}
              points={points}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

export function ApplicationDrivers({
  ledgers,
  drivers,
}: {
  ledgers: CostLedger[];
  drivers: ApplicationCostDriver[];
}) {
  return (
    <Card>
      <SectionTitle
        title="Cost drivers"
        subtitle="Ranked resources and workloads within each non-additive ledger"
      />
      {drivers.length === 0 ? (
        <EmptyState
          positive={false}
          message="No attributed cost drivers are available in this window."
        />
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {ledgers.map((ledger) => {
            const rows = drivers
              .filter((driver) => driver.ledger_id === ledger.id)
              .sort((left, right) => right.cost - left.cost);
            if (rows.length === 0) return null;
            const maximum = Math.max(...rows.map((row) => row.cost), 1);
            const services = Array.from(
              rows.reduce((groups, driver) => {
                const service = driver.service || "Other service";
                const group = groups.get(service) ?? [];
                group.push(driver);
                groups.set(service, group);
                return groups;
              }, new Map<string, ApplicationCostDriver[]>()),
              ([service, serviceRows]) => ({
                service,
                rows: serviceRows,
                cost: serviceRows.reduce((total, row) => total + row.cost, 0),
              }),
            ).sort((left, right) => right.cost - left.cost);
            return (
              <section
                key={ledger.id}
                aria-labelledby={`driver-${ledger.id}`}
                className="min-w-0 rounded-xl border border-grid p-3"
              >
                <h3
                  id={`driver-${ledger.id}`}
                  className="text-xs font-semibold text-ink"
                >
                  {ledger.title}
                </h3>
                <p className="mb-3 text-[11px] text-muted">
                  {ledger.currency} · {humanize(ledger.pricing_basis)}
                </p>
                <ol className="space-y-3" aria-label={`${ledger.title} service hierarchy`}>
                  {services.map((service) => (
                    <li
                      key={service.service}
                      className="min-w-0 rounded-lg border border-grid/70 bg-page/25 p-2.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                            Service
                          </p>
                          <p className="text-xs font-semibold text-ink">
                            {service.service}
                          </p>
                        </div>
                        <span className="text-xs font-semibold tabular-nums text-ink">
                          {currency(service.cost, ledger.currency)}
                        </span>
                      </div>
                      <ul className="mt-2 space-y-2 border-l border-grid pl-3">
                        {service.rows.slice(0, 8).map((driver, index) => (
                          <li
                            key={`${driver.name}-${driver.resource_id ?? index}`}
                            className="min-w-0"
                          >
                            <div className="mb-1 flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p className="truncate text-xs font-medium text-ink-2">
                                  {driver.name}
                                </p>
                                <div className="mt-0.5 flex flex-wrap items-center gap-1">
                                  <span className="text-[10px] text-muted">
                                    Resource · {driver.resource_type || "unclassified"}
                                    {driver.workload
                                      ? ` · Workload: ${driver.workload}`
                                      : ""}
                                  </span>
                                  <Badge tone={methodTone(driver.attribution_method)}>
                                    {humanize(driver.attribution_method)}
                                  </Badge>
                                </div>
                              </div>
                              <span className="shrink-0 text-xs font-medium tabular-nums text-ink">
                                {currency(driver.cost, driver.currency)}
                              </span>
                            </div>
                            <div className="h-1.5 rounded-full bg-hairline">
                              <div
                                className="h-full rounded-full bg-series-1"
                                style={{
                                  width: `${Math.max(
                                    1,
                                    (driver.cost / maximum) * 100,
                                  )}%`,
                                }}
                              />
                            </div>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ol>
              </section>
            );
          })}
        </div>
      )}
    </Card>
  );
}
