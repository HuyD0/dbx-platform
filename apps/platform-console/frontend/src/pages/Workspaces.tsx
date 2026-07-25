import { useQuery } from "@tanstack/react-query";
import { Building2, CheckCircle2, ShieldCheck, UserRound } from "lucide-react";
import { Badge, Card, ErrorState, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { apiGet } from "../lib/api";
import type { WorkspaceAccessResponse } from "../lib/types";

function relationshipLabel(value: string) {
  return value === "platform_admin" ? "Platform admin" : "Workspace user";
}

export function Workspaces() {
  const access = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => apiGet<WorkspaceAccessResponse>("/api/workspaces"),
    staleTime: 300_000,
    retry: false,
  });

  const isAdmin = access.data?.actor.view === "platform_admin";

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Entitlements"
        title={isAdmin ? "Platform admin workspaces" : "My workspaces"}
        description={
          "Uses Databricks Apps OBO passthrough for the current workspace, then " +
          "shows the management capabilities your verified entitlements enable. " +
          "Mutating actions remain governed by exact approvals."
        }
      />

      {access.isPending ? (
        <Card>
          <Skeleton rows={5} />
        </Card>
      ) : access.isError ? (
        <ErrorState error={access.error} />
      ) : (
        <>
          <Card>
            <SectionTitle
              title="Current view"
              subtitle={access.data.actor.email ?? access.data.actor.actor_id}
              right={
                <Badge tone={isAdmin ? "warning" : "info"}>
                  {relationshipLabel(access.data.actor.view)}
                </Badge>
              }
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-grid bg-page/30 p-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                  {isAdmin ? (
                    <ShieldCheck className="h-4 w-4 text-accent" />
                  ) : (
                    <UserRound className="h-4 w-4 text-accent" />
                  )}
                  {isAdmin ? "Admin portfolio" : "Personal portfolio"}
                </div>
                <p className="mt-1 text-xs leading-5 text-muted">
                  {isAdmin
                    ? "Operator or approver membership unlocks the admin view for governed workspace management."
                    : "Viewer access shows workspace evidence with sensitive identity fields redacted unless additional roles are granted."}
                </p>
              </div>
              <div className="rounded-xl border border-grid bg-page/30 p-3">
                <p className="text-[11px] text-muted">Verified roles</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {access.data.actor.roles.map((role) => (
                    <Badge key={role} tone="info">
                      {role}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <section aria-labelledby="workspace-list-title" className="space-y-3">
            <SectionTitle
              title="Workspace access"
              subtitle={
                `${access.data.source_status.source}: ${
                  access.data.source_status.notes ?? "OBO identity source unavailable."
                }`
              }
            />
            {access.data.workspaces.map((workspace) => (
              <Card key={workspace.workspace_id ?? workspace.name}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <Building2 className="h-4 w-4 text-accent" />
                      <h3 className="text-sm font-semibold text-ink">{workspace.name}</h3>
                    </div>
                    <p className="mt-1 font-mono text-xs text-muted">
                      {workspace.workspace_id ?? "workspace ID unavailable"}
                    </p>
                  </div>
                  <Badge tone={workspace.relationship === "platform_admin" ? "warning" : "info"}>
                    {relationshipLabel(workspace.relationship)}
                  </Badge>
                </div>
                <div className="mt-4 grid gap-2 md:grid-cols-2">
                  {workspace.capabilities.map((capability) => (
                    <div
                      key={capability.id}
                      className="rounded-xl border border-grid bg-page/30 p-3"
                    >
                      <div className="flex items-center gap-2 text-xs font-semibold text-ink">
                        <CheckCircle2
                          className={`h-4 w-4 ${
                            capability.enabled ? "text-status-good" : "text-muted"
                          }`}
                        />
                        {capability.label}
                      </div>
                      <p className="mt-1 text-xs leading-5 text-muted">{capability.description}</p>
                    </div>
                  ))}
                </div>
              </Card>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
