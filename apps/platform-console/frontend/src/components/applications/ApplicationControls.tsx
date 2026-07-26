import { Search } from "lucide-react";
import { useId } from "react";
import type {
  ApplicationFacets,
  ApplicationWindow,
} from "../../types/applications";

export const APPLICATION_WINDOWS: ApplicationWindow[] = [7, 30, 90];

export function ApplicationWindowPicker({
  value,
  onChange,
}: {
  value: ApplicationWindow;
  onChange: (window: ApplicationWindow) => void;
}) {
  return (
    <div
      className="inline-flex rounded-xl border border-grid bg-hairline/20 p-1"
      aria-label="Cost window"
    >
      {APPLICATION_WINDOWS.map((window) => (
        <button
          key={window}
          type="button"
          aria-pressed={value === window}
          onClick={() => onChange(window)}
          className={`rounded-lg px-2.5 py-1.5 text-xs font-medium ${
            value === window
              ? "bg-surface text-ink shadow-sm"
              : "text-muted hover:text-ink"
          }`}
        >
          {window}d
        </button>
      ))}
    </div>
  );
}

export function ApplicationFilters({
  query,
  environment,
  source,
  facets,
  onQueryChange,
  onEnvironmentChange,
  onSourceChange,
}: {
  query: string;
  environment: string;
  source: string;
  facets: ApplicationFacets;
  onQueryChange: (value: string) => void;
  onEnvironmentChange: (value: string) => void;
  onSourceChange: (value: string) => void;
}) {
  const searchId = useId();
  const environmentId = useId();
  const sourceId = useId();
  return (
    <div className="grid gap-2 md:grid-cols-[minmax(14rem,1fr)_12rem_12rem]">
      <label
        htmlFor={searchId}
        className="flex items-center gap-2 rounded-xl border border-grid bg-page/40 px-3 py-2 text-sm focus-within:border-accent"
      >
        <Search className="h-4 w-4 shrink-0 text-muted" />
        <span className="sr-only">Search applications</span>
        <input
          id={searchId}
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search applications…"
          className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-muted"
        />
      </label>
      <label htmlFor={environmentId} className="sr-only">
        Environment
      </label>
      <select
        id={environmentId}
        aria-label="Environment"
        value={environment}
        onChange={(event) => onEnvironmentChange(event.target.value)}
        className="rounded-xl border border-grid bg-page/40 px-3 py-2 text-sm text-ink"
      >
        <option value="">All environments</option>
        {facets.environments.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
      <label htmlFor={sourceId} className="sr-only">
        Source
      </label>
      <select
        id={sourceId}
        aria-label="Source"
        value={source}
        onChange={(event) => onSourceChange(event.target.value)}
        className="rounded-xl border border-grid bg-page/40 px-3 py-2 text-sm text-ink"
      >
        <option value="">All sources</option>
        {facets.sources.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
    </div>
  );
}
