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

## OTPP brand-color mockups

These ten additional mockups reinterpret the Mission Control concepts with the supplied OTPP slide
palette. They are intended as implementable visual directions for the Platform Console rather than
pixel-perfect production screens. The concepts keep primary red as the brand anchor, reserve deep
maroon for immersive focus areas, and use warm tints to make dense operational content feel less
clinical.

### Palette application rules

- Use **Primary red `#F00037`** for the main decision rail, priority indicators, active navigation,
  and one primary action per view.
- Use **Deep maroon `#240B15`** for dark-mode canvases, command headers, and high-focus approval
  or incident states.
- Use **Mid red `#8B001F`** for secondary emphasis, section dividers, and less urgent risk states.
- Use **Light bg `#FBF7F8`**, **Pink tint `#F9EAED`**, and **Light gold `#FFF8E1`** for card surfaces,
  empty states, and explanatory wells.
- Use **Gold `#FFCD67`** for deadlines, business value, and review windows; **Teal `#00AAAD`** for
  evidence freshness and data lineage; **Green `#72BF44`** for verified healthy states only.
- Use **Muted rose-grey `#B79AA3`** and **Sand border `#E4D7DB`** for supporting text, borders,
  separators, and disabled or historical content.

### OTPP concepts

11. **Command Ribbon** — a slide-inspired executive console with a bold maroon command band,
    primary-red decision rail, and gold KPI strip.
12. **Evidence Cards** — a lighter card system where evidence freshness, source health, and plan
    confidence are scannable without overwhelming the decision queue.
13. **Approval Stage** — a focused approval screen that visually separates evidence, immutable plan,
    confirmation, executor handoff, and verification.
14. **Portfolio Heatmap** — a governance committee view balancing red risk intensity against teal
    control coverage and green verified health.
15. **Agent Copilot** — a split-view investigation surface where the assistant remains visibly
    read-only and every answer has a cited evidence card.
16. **Morning Brief** — an editorial daily brief with large red numerals, warm backgrounds, and
    gold business-impact highlights.
17. **Incident Pulse** — a dark operational view that uses red for urgency while teal telemetry and
    gold checkpoints prevent a monochrome alarm wall.
18. **Cost Lens** — a finance-forward cost planner with light-gold analysis wells, red variance
    callouts, and teal attribution paths.
19. **Policy Map** — a policy-as-code navigation model using maroon structure, red exception lanes,
    and gold review checkpoints.
20. **Calm Zero State** — a warm healthy-state treatment that keeps red in the brand system without
    implying a false alarm.

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
