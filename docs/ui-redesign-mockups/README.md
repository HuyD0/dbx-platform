# Mission Control redesign concepts

These ten exploratory mockups use the current Mission Control screen as a content reference.
They are intentionally different information architectures, not production-ready visual specs.
Generated UI text and sample data must be reconciled with the real API and safety model before
implementation.

## Recommended direction

Use **01 Decision Queue First** as the home-screen foundation, then incorporate:

- the traceable evidence drawer from **04 Evidence Graph**;
- the immutable approval history and expiry treatment from **05 Decision Timeline**;
- the useful zero-findings treatment from **09 Calm Healthy State**;
- the read-only, cited assistant behavior from **08 Copilot Split View**.

This hybrid best matches the product's central job: help an operator understand and review the
next decision while keeping evidence, approval, execution, and verification visibly separate.

## Concepts

1. **Decision Queue First** — best general-purpose home screen; ranks review work above healthy
   telemetry.
2. **Executive Morning Brief** — best for platform leaders who visit once or twice a day.
3. **Incident Command Center** — best for urgent anomalies, on-call coordination, and dark mode.
4. **Evidence Graph** — best for explainability, lineage, and cross-domain correlation.
5. **Decision Timeline** — best expression of expiring approvals and append-only execution history.
6. **Risk Matrix and Portfolio** — best for governance committees comparing value and control risk.
7. **Domain Workbench** — best for analysts who work deeply within one domain at a time.
8. **Copilot Split View** — best for evidence-grounded conversational investigation.
9. **Calm Healthy State** — best zero-findings state; healthy telemetry recedes without hiding work.
10. **Dense Operator Console** — best for expert operators, keyboard workflows, and large displays.

## Design guidance

- Put pending decisions, their consequences, and the next safe action at the top of the page.
- Treat healthy domain status as supporting context; do not give four healthy cards more area than
  the approval queue.
- Pair every severity color with an icon and explicit label. Status pills are labels, not buttons.
- Keep source freshness adjacent to the evidence it qualifies. A global timestamp alone is not
  enough when sources refresh independently.
- Show the workflow explicitly: evidence, immutable plan, human approval, dedicated executor,
  verification. Never imply that the assistant can execute a change.
- Make the full decision row actionable with a single clear destination; reserve secondary actions
  for a menu or detail panel.
- Use progressive disclosure: headline impact and confidence in the queue, full evidence and audit
  history in the selected-decision panel.
- A zero-findings state should explain what was checked, when it was checked, and what work remains.
- Maintain visible keyboard focus, adequate target sizes, non-color status cues, and programmatic
  status announcements.

## Research references

- [Carbon dashboard guidance](https://carbondesignsystem.com/data-visualization/dashboards/) —
  prioritize information, limit metrics, use consistent color, and use whitespace to establish
  hierarchy.
- [GOV.UK task list](https://design-system.service.gov.uk/components/task-list/) — use short task
  names, concise statuses, and link the whole task row while making status labels non-interactive.
- [GOV.UK tag guidance](https://design-system.service.gov.uk/components/tag/) — tags communicate
  status and should not look or behave like controls.
- [Atlassian messaging guidance](https://atlassian.design/foundations/content/designing-messages/)
  — place empty, warning, and status messages in the appropriate scope rather than treating all
  messages as global banners.
- [Carbon empty-state pattern](https://carbondesignsystem.com/patterns/empty-states-pattern/) — an
  empty region should explain why data is absent and identify a corrective or next action.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) — preserve contrast, visible focus, keyboard access,
  non-color meaning, and programmatically determinable status messages.

## Files

- `01-decision-queue-first.png`
- `02-executive-morning-brief.png`
- `03-incident-command-center.png`
- `04-evidence-graph.png`
- `05-decision-timeline.png`
- `06-risk-matrix-portfolio.png`
- `07-domain-workbench.png`
- `08-copilot-split-view.png`
- `09-calm-healthy-state.png`
- `10-dense-operator-console.png`

Generated with the built-in image-generation workflow using the supplied screenshot as the visual
and content reference.

## Institutional neutral mockups

These ten additional mockups use a restrained operator-console system intended for an institutional
platform team. White, graphite, black, and cool grey establish the hierarchy. Muted semantic color
appears only when it conveys risk, warning, verified health, or evidence linkage.

### Visual system

- Use **ink `#111315`**, white, and **canvas grey `#F4F5F5`** for the main hierarchy.
- Use compact typography, tabular numerals, hairline dividers, and 3–6 px corner radii.
- Prefer registers, timelines, and structured fields over decorative cards and oversized metrics.
- Reserve **muted red `#B4232C`** for actual risk or approval urgency.
- Keep healthy, warning, and evidence states desaturated and pair color with explicit text.
- Preserve a visible read-only boundary and the full evidence → plan → approval → confirmation →
  execution → verification workflow.

### OTPP concepts

11. **Command Overview** — a dense production overview led by the decision queue and operating
    signal.
12. **Evidence Register** — a source-health register with freshness, coverage, and exact run
    attestations.
13. **Approval Review** — a focused review screen separating immutable plan scope, eligibility,
    expiry, and confirmation.
14. **Control Portfolio** — governance coverage and exception expiry across operational domains.
15. **Agent Copilot** — a split-view investigation surface where the assistant remains visibly
    read-only and every answer has a cited evidence card.
16. **Morning Brief** — a quiet editorial summary for platform leadership.
17. **Incident Pulse** — a monochrome dark operations view with one restrained severity signal.
18. **Cost Lens** — attributed spend, forecast variance, and evidence-backed opportunities.
19. **Policy Map** — policy source hierarchy, assignment coverage, exceptions, and drift.
20. **Calm Zero State** — a useful zero-findings state with explicit coverage and remaining work.

### OTPP files

- `11-otpp-command-ribbon.svg`
- `12-otpp-evidence-cards.svg`
- `13-otpp-approval-stage.svg`
- `14-otpp-portfolio-heatmap.svg`
- `15-otpp-agent-copilot.svg`
- `16-otpp-morning-brief.svg`
- `17-otpp-incident-pulse.svg`
- `18-otpp-cost-lens.svg`
- `19-otpp-policy-map.svg`
- `20-otpp-calm-zero.svg`

## Brand-balanced drafts

These five comparison drafts restore the OTPP red and maroon identity while keeping the interface
institutional and operational. All five use the same core content so the visual direction—not the
information architecture—can be compared directly.

21. **Editorial Burgundy** — warm paper, a burgundy masthead, and a thin red brand rule.
22. **Redline Grid** — predominantly black and white, with a precise red navigation spine.
23. **Rose Ledger** — pale rose atmosphere and a dense, finance-inspired decision ledger.
24. **Dark Maroon Command** — immersive dark brand canvas with restrained semantic accents.
25. **Brand Spine** — white operational canvas organized by a strong maroon-and-red vertical spine.

### Brand-balanced files

- `21-brand-editorial-burgundy.svg`
- `22-brand-redline-grid.svg`
- `23-brand-rose-ledger.svg`
- `24-brand-dark-command.svg`
- `25-brand-spine.svg`

## Creative brand directions

These five concepts move beyond conventional sidebar-and-card dashboard composition while
retaining the institutional red, burgundy, maroon, rose, cream, and neutral brand system.

26. **Decision Ribbon** — the six controlled action states become the page architecture.
27. **Evidence Constellation** — source provenance and freshness orbit the immutable plan.
28. **Annual-report Brief** — editorial hierarchy inspired by an institutional annual report.
29. **Twin Lens** — business consequence and exact operator mechanics share one decisive seam.
30. **Command Monolith** — a central branded object carries the priority decision through the view.

### Creative files

- `26-creative-decision-ribbon.svg`
- `27-creative-evidence-constellation.svg`
- `28-creative-annual-brief.svg`
- `29-creative-twin-lens.svg`
- `30-creative-command-monolith.svg`
