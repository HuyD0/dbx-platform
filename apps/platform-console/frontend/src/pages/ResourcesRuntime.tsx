import { useQuery } from "@tanstack/react-query";
import { AppWindow, Database, Moon, Power, ServerCog, TimerReset } from "lucide-react";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { DataTable } from "../components/DataTable";
import {
  AsOf,
  Badge,
  Card,
  CapabilityNotice,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  statusTone,
} from "../components/ui";
import { apiGet, isUnavailable } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Envelope, Row, RuntimeState } from "../lib/types";

interface InventoryData {
  resources?: Row[];
  exclusions?: Row[];
}

function normalizedInventory(data: Row[] | InventoryData): InventoryData {
  return Array.isArray(data) ? { resources: data, exclusions: [] } : data;
}

export function ResourcesRuntime() {
  const state = useQuery({
    queryKey: ["/api/runtime/state"],
    queryFn: async () => {
      const response = await apiGet<Envelope<RuntimeState> | RuntimeState>("/api/runtime/state");
      return "data" in response ? response : {
        data: response,
        count: null,
        as_of: response.updated_at ?? "",
        cached: false,
      };
    },
    staleTime: 15_000,
    retry: false,
  });
  const inventory = useQuery({
    queryKey: ["/api/runtime/inventory"],
    queryFn: () => apiGet<Envelope<Row[] | InventoryData>>("/api/runtime/inventory"),
    staleTime: 60_000,
    retry: false,
  });

  const runtime = state.data?.data;
  const resources = inventory.data ? normalizedInventory(inventory.data.data).resources ?? [] : [];
  const exclusions = inventory.data ? normalizedInventory(inventory.data.data).exclusions ?? [] : [];
  const unavailable =
    (state.isError && isUnavailable(state.error)) ||
    (inventory.isError && isUnavailable(inventory.error));
  const sleeping = runtime?.desired_state === "SLEEPING" || runtime?.current_state === "SLEEPING";

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Lifecycle"
        title="Resources & Runtime"
        description="See exactly what this toolkit owns, protect everything else, and hibernate it through a reversible human-approved plan."
        actions={
          sleeping ? (
            <PlanActionButton
              action="wake"
              label="Plan wake"
              allowLegacy={false}
              tone="primary"
            />
          ) : (
            <PlanActionButton
              action="hibernate"
              label="Plan hibernate"
              allowLegacy={false}
              tone="danger"
            />
          )
        }
      />

      {unavailable && (
        <CapabilityNotice
          title="Runtime controller is not connected yet"
          description="Inventory and plan surfaces are ready, but all lifecycle actions fail closed until the durable runtime state and power-controller job are available."
        />
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <div className="flex items-center justify-between gap-2">
            <span className="rounded-xl bg-accent/10 p-2 text-accent">
              {sleeping ? <Moon className="h-4 w-4" /> : <Power className="h-4 w-4" />}
            </span>
            <Badge tone={statusTone(runtime?.current_state)}>
              {runtime?.current_state?.toLowerCase() ?? "unknown"}
            </Badge>
          </div>
          <p className="mt-3 text-xs text-muted">Observed runtime</p>
          <p className="mt-1 text-lg font-semibold text-ink">
            {runtime?.current_state?.replaceAll("_", " ") ?? "Not reported"}
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-accent">
            <TimerReset className="h-4 w-4" />
            <span className="text-xs text-muted">Desired state</span>
          </div>
          <p className="mt-3 text-lg font-semibold text-ink">
            {runtime?.desired_state ?? "Not recorded"}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {runtime?.updated_at ? `changed ${timeAgo(runtime.updated_at)}` : "persists across deploys"}
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-accent">
            <ServerCog className="h-4 w-4" />
            <span className="text-xs text-muted">Owned resources</span>
          </div>
          <p className="mt-3 text-lg font-semibold text-ink">{resources.length || "—"}</p>
          <p className="mt-1 text-[11px] text-muted">Exact deployment IDs, never name matching</p>
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-accent">
            <Database className="h-4 w-4" />
            <span className="text-xs text-muted">Data retained</span>
          </div>
          <p className="mt-3 text-lg font-semibold text-ink">Always</p>
          <p className="mt-1 text-[11px] text-muted">Unity Catalog, dashboards and storage stay intact</p>
        </Card>
      </div>

      <Card>
        <SectionTitle
          title="Managed inventory"
          subtitle="The hibernate planner targets only explicit bundle outputs with stoppability metadata"
          right={
            inventory.data ? (
              <AsOf
                asOf={inventory.data.as_of}
                cached={inventory.data.cached}
                onRefresh={() => inventory.refetch()}
                refreshing={inventory.isFetching}
              />
            ) : undefined
          }
        />
        {inventory.isPending ? (
          <Skeleton rows={5} />
        ) : inventory.isError && !isUnavailable(inventory.error) ? (
          <ErrorState error={inventory.error} />
        ) : resources.length > 0 ? (
          <DataTable
            rows={resources}
            exportName="managed-resource-inventory"
            caption="Resources explicitly owned by dbx-platform"
          />
        ) : (
          <EmptyState
            message="Managed resource IDs will appear after the bundle inventory migration runs."
            positive={false}
          />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Hibernate boundary" subtitle="Intended v1 ownership" />
          <ul className="space-y-2 text-xs text-ink-2">
            <li className="flex items-center gap-2">
              <AppWindow className="h-4 w-4 text-accent" />
              Platform Console app
            </li>
            <li className="flex items-center gap-2">
              <TimerReset className="h-4 w-4 text-accent" />
              Eleven bundle-declared schedules, restoring only prior unpaused state
            </li>
            <li className="flex items-center gap-2">
              <Database className="h-4 w-4 text-accent" />
              Dedicated dbx-platform XXS serverless warehouse
            </li>
          </ul>
          <p className="mt-3 rounded-lg bg-hairline/30 p-2 text-[11px] leading-5 text-muted">
            Active runs drain for up to 15 minutes. Remaining activity aborts and restores
            schedule state; cancellation needs a new approved plan.
          </p>
        </Card>
        <Card>
          <SectionTitle title="Protected resources" subtitle="Never included in toolkit hibernation" />
          {exclusions.length > 0 ? (
            <DataTable
              rows={exclusions}
              pageSize={6}
              searchable={false}
              exportable={false}
              caption="Resources excluded from hibernation"
            />
          ) : (
            <ul className="space-y-2 text-xs text-ink-2">
              {[
                "Databricks workspace and Azure resource group",
                "Shared Starter warehouse and unrelated compute",
                "Unity Catalog data, dashboards, models and storage",
                "Network resources and other projects",
                "The unscheduled power-controller job itself",
              ].map((item) => (
                <li key={item} className="flex items-start gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-status-good" />
                  {item}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {runtime?.active_operation && (
        <Card>
          <SectionTitle title="Active lifecycle operation" />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={statusTone(runtime.operation_status)}>
              {runtime.operation_status ?? "in progress"}
            </Badge>
            <span className="font-medium text-ink">{runtime.active_operation}</span>
          </div>
        </Card>
      )}
      {runtime?.wake_instructions && (
        <Card>
          <SectionTitle title="Wake procedure" />
          <p className="whitespace-pre-wrap text-xs leading-5 text-ink-2">{runtime.wake_instructions}</p>
        </Card>
      )}
    </div>
  );
}
