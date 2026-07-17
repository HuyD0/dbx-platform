# Runbook

Every check runs two ways from the same code: ad-hoc via the CLI and on a
schedule via the bundle-deployed jobs. Scheduled jobs are **report-only**;
destructive actions are deliberate local commands with `--apply --yes`.

Reports print to the task's run output (Jobs UI → run → task output). A failed
task emails `${var.notification_email}`. Exit codes: `0` success (findings are
not failures), `1` runtime error, `2` refused `--apply` without confirmation,
`3` system tables unavailable.

## Job catalog

| Job | Schedule (UTC) | Tasks | Needs |
|---|---|---|---|
| `cost-usage-report` | daily 07:00 | `cost report`, `cost top-jobs`, `cluster-utilization`, `failed-run-waste`, `warehouse-utilization` | warehouse + system.billing/lakeflow/compute/query |
| `housekeeping-report` | daily 05:30 | `stale-clusters`, `orphaned-jobs`, `jobs-on-all-purpose` | REST APIs only |
| `security-audit` | weekly Mon 06:00 | `token-audit`, `inactive-users` | admin; warehouse + system.access |
| `governance-check` | weekly Mon 06:30 | `policy-sync` (drift), `tag-compliance` | warehouse + system.billing |
| `ml-serving-report` | daily 07:30 | `endpoint-audit`, `serving-cost` | warehouse + system.billing (+ system.serving where enabled) |
| `ml-hygiene-report` | weekly Mon 07:00 | `model-hygiene`, `gpu-audit`, `vector-search-audit` | REST APIs (+ warehouse for GPU spend) |
| `platform-digest` | weekly Mon 08:00 | `ai-digest` | warehouse + an `ai_query`-capable foundation-model endpoint |

Thresholds are task parameters in `resources/*.yml` — change them in git, not
in the Jobs UI, so the config stays reviewable.

## Acting on findings

### Stale / long-running clusters

```bash
dbx-platform housekeeping stale-clusters                  # re-check (dry run)
dbx-platform housekeeping stale-clusters --apply --yes    # terminate/permanently delete
```

- Terminated ≥ 30d (non-pinned) → **permanent delete**; running ≥ 24h or
  autotermination disabled → **terminate** (recoverable).
- The pinned check relies on the API exposing `pinned_by_user_name`. Review the
  dry-run list before your first `--apply`, and keep the scheduled job
  report-only.

### Orphaned jobs

`--apply` only **pauses** schedules/triggers — it never deletes a job. Reassign
ownership in the Jobs UI (or `databricks jobs update`) and unpause.

### Token audit

```bash
dbx-platform security token-audit --apply --yes   # revokes tokens OVER the age limit only
```

"Expiring soon" findings are rotation reminders, never revoked. Warn owners
before revoking — running integrations break immediately.

### Inactive users

Report-only by design. Deactivation belongs in your IdP/SCIM flow: removing a
user here does not reassign their jobs or UC objects (that's exactly how
orphans are created). Use the orphaned-jobs report after any offboarding.

### Policy drift

```bash
dbx-platform governance policy-sync                # drift report
dbx-platform governance policy-sync --apply --yes  # create/update from policies/*.json
```

Policies present in the workspace but not in git are listed as **unmanaged**
and never touched. To adopt one into git: copy its JSON into `policies/`,
re-run the drift report, confirm "unchanged".

### Tag compliance

Fix by adding `custom_tags` (clusters) / `tags` (jobs). The cluster policies in
`policies/` make `team` and `project` mandatory for new compute, so the list
should shrink over time.

### Serving endpoints (ml endpoint-audit)

Report-only on purpose: any `update_config` call redeploys the endpoint, and
`environment_vars` with secret references do not round-trip through a GET.
Act via the Serving UI or an IaC change, endpoint by endpoint:

- **enable-scale-to-zero** — small CPU workloads idling between calls.
- **enable-inference-table** — without it there is no payload/audit trail;
  required before any quality monitoring.
- **add-ai-gateway-rate-limits / enable-usage-tracking** — external and
  foundation-model endpoints are pay-per-token; unlimited callers are an
  unbounded bill.
- GPU endpoints are exempt from the scale-to-zero flag (cold starts).

### Model hygiene / GPU / vector search

All report-only: archiving models, terminating someone's GPU cluster, and
deleting a vector search endpoint are owner decisions. GPU terminations can
reuse `housekeeping stale-clusters --apply` once confirmed. Note:
`system.compute.node_timeline` has no GPU metrics, so GPU right-sizing stays
at the spend/idle level.

### Right-sizing (cluster/warehouse utilization, jobs on all-purpose)

Findings are ranked by list cost — work top-down. `downsize-node-or-workers`
and `lower-autoscale-max` are cluster-spec changes owned by the cluster's
team; `move-to-job-cluster` is a job-spec change (all-purpose compute costs
roughly double the job-compute DBU rate). Warehouse findings cut both ways:
idle spend → shorter auto-stop or smaller size; sustained queueing →
undersized.

### AI digest & triage loop

`report ai-digest` needs the `platform_digest`/`platform_findings` tables —
created by `dbx-platform dashboards setup` — and a pay-per-token
foundation-model endpoint (`DBX_PLATFORM_DIGEST_MODEL`, default
`databricks-claude-sonnet-4-5`; list candidates under Serving → built-in).
If `ai_query` is unavailable the digest degrades to raw findings and still
exits 0. The weekly `platform-triage.yml` workflow files findings into a
rolling `platform-triage` GitHub issue and asks `@claude` for fixes **as
pull requests** — never workspace mutations.

### Platform Console app

Deployed by the bundle (`resources/app.yml`). After the first deploy, grant
the app's service principal CAN_MANAGE_RUN on the `[dbx-platform]` jobs so
the Actions page can trigger them. The deploy workflow stages the wheel into
`apps/platform-console/wheels/` (git-ignored) before `bundle deploy`; for a
manual deploy run the same copy step first. Note: apps are currently
prod-target resources — if `bundle deploy -t dev` rejects the app name
prefix, deploy the app from prod only.

### Served agent (optional)

`pip install -e ".[agent]"`, then `python agents/platform_agent/deploy_agent.py`
with workspace credentials. The agent's tools are read-only by construction;
verify the mlflow `ResponsesAgent` interface matches your workspace's mlflow
version at deploy time. Chat via AI Playground or the endpoint review app.

## Serverless fallback

Jobs use serverless job compute (`environments` block). If serverless jobs
aren't available in your region/workspace, in each `resources/*_jobs.yml`
replace the `environments:` block with:

```yaml
      job_clusters:
        - job_cluster_key: single_node
          new_cluster:
            spark_version: 15.4.x-scala2.12
            node_type_id: Standard_D4ds_v5
            num_workers: 0
            spark_conf:
              spark.databricks.cluster.profile: singleNode
              spark.master: "local[*]"
            custom_tags: {ResourceClass: SingleNode, team: platform, project: dbx-platform}
```

and on each task swap `environment_key: default` for:

```yaml
          job_cluster_key: single_node
          libraries:
            - whl: ./dist/*.whl
```

## Updating dashboards

Upstream templates live in `dashboards/templates/` (pristine, with
`{catalog}.{schema}` placeholders). Rendered, deployable copies live in
`dashboards/`. To move helper objects to a different catalog/schema:

```bash
dbx-platform dashboards render --catalog analytics --schema platform_obs
dbx-platform dashboards setup  --catalog analytics --schema platform_obs
git add dashboards && git commit -m "Move dashboard helpers"
databricks bundle deploy
```

If you customize a dashboard in the UI, export its JSON back into
`dashboards/templates/` (re-inserting `{catalog}.{schema}` where applicable) so
git stays the source of truth.
