import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { CostTabs } from "../components/CostTabs";
import { Card, PageHeader, Skeleton } from "../components/ui";
import { apiGet } from "../lib/api";

interface LakeMeterStatus {
  status: "ready" | "unavailable";
  ready: boolean;
  database_ready: boolean;
  frontend_ready: boolean;
  reason: string | null;
  schema_version: number | null;
  required_schema_version: number;
  upstream_version: string | null;
  pricing_version: string | null;
}

interface MountOptions {
  dark: boolean;
  assistantOpen: boolean;
  onAssistantClose: () => void;
}

interface Controller {
  update(options: MountOptions): void;
  unmount(): void;
}

interface LakeMeterModule {
  mountLakeMeter(container: HTMLElement, options: MountOptions): Controller;
}

export function LakeMeter({
  dark,
  assistantOpen,
  onAssistantClose,
}: MountOptions) {
  const hostRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<Controller | null>(null);
  const optionsRef = useRef<MountOptions>({ dark, assistantOpen, onAssistantClose });
  const [mountError, setMountError] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ["/api/lakemeter/status"],
    queryFn: () => apiGet<LakeMeterStatus>("/api/lakemeter/status"),
    staleTime: 60_000,
    retry: false,
  });

  optionsRef.current = { dark, assistantOpen, onAssistantClose };

  useEffect(() => {
    controllerRef.current?.update(optionsRef.current);
  }, [assistantOpen, dark, onAssistantClose]);

  useEffect(() => {
    if (!status.data?.ready || !hostRef.current) return;
    const host = hostRef.current;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/lakemeter/style.css";
    const mountPoint = document.createElement("div");
    shadow.replaceChildren(stylesheet, mountPoint);
    let disposed = false;

    const entryUrl = "/lakemeter/entry.js";
    import(/* @vite-ignore */ entryUrl)
      .then((module: LakeMeterModule) => {
        if (disposed) return;
        controllerRef.current = module.mountLakeMeter(mountPoint, optionsRef.current);
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setMountError(error instanceof Error ? error.message : String(error));
        }
      });
    return () => {
      disposed = true;
      controllerRef.current?.unmount();
      controllerRef.current = null;
      shadow.replaceChildren();
    };
  }, [status.data?.ready]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="FinOps"
        title="Estimator"
        description="Model future Databricks workloads and connect every estimate to an explicit pricing basis."
      />
      <CostTabs />
      {status.isPending ? (
        <Card>
          <Skeleton rows={6} />
        </Card>
      ) : status.isError || !status.data?.ready ? (
        <Card>
          <div role="status" className="space-y-2">
            <h2 className="text-sm font-semibold text-ink">Estimator setup required</h2>
            <p className="text-sm leading-6 text-ink-2">
              The LakeMeter application code is installed, but its approved Lakebase
              provisioning and schema migration must complete before estimates can be opened.
            </p>
            {status.data && (
              <dl className="grid gap-2 pt-2 text-xs text-muted sm:grid-cols-3">
                <div>
                  <dt>Upstream</dt>
                  <dd className="font-medium text-ink-2">
                    {status.data.upstream_version ?? "unknown"}
                  </dd>
                </div>
                <div>
                  <dt>Required schema</dt>
                  <dd className="font-medium text-ink-2">
                    v{status.data.required_schema_version}
                  </dd>
                </div>
                <div>
                  <dt>State</dt>
                  <dd className="font-medium text-ink-2">
                    {status.data.reason ?? "unavailable"}
                  </dd>
                </div>
              </dl>
            )}
          </div>
        </Card>
      ) : mountError ? (
        <Card>
          <div role="alert" className="text-sm text-status-serious">
            The estimator frontend could not be loaded: {mountError}
          </div>
        </Card>
      ) : (
        <div ref={hostRef} data-testid="lakemeter-shadow-host" />
      )}
    </div>
  );
}
