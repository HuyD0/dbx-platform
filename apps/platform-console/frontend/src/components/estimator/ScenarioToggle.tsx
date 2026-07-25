/** Side-by-side deployment scenarios — a comparison, never a mandate. Both
 * estimates arrive in one response, so switching is a pure re-render. */
export function ScenarioToggle({
  scenario,
  onChange,
}: {
  scenario: string;
  onChange: (scenario: string) => void;
}) {
  const options = [
    { key: "databricks", label: "Run it on Databricks" },
    { key: "azure", label: "Run it on Azure services" },
  ];
  return (
    <div role="radiogroup" aria-label="Deployment scenario" className="inline-flex rounded-xl border border-hairline p-1">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          role="radio"
          aria-checked={scenario === option.key}
          onClick={() => onChange(option.key)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            scenario === option.key
              ? "bg-series-1 text-page"
              : "text-ink-2 hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
