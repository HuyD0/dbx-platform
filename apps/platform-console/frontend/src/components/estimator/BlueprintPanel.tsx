import { Card, SectionTitle } from "../ui";

/** Plain-English architecture description — generated from reviewed
 * templates, not by an AI model. */
export function BlueprintPanel({
  blueprint,
}: {
  blueprint: { title: string; body: string }[];
}) {
  return (
    <Card>
      <SectionTitle
        title="What would actually get built"
        subtitle="The architecture behind the numbers, in plain language."
      />
      <dl className="space-y-3">
        {blueprint.map((section) => (
          <div key={section.title}>
            <dt className="text-sm font-semibold text-ink">{section.title}</dt>
            <dd className="mt-0.5 text-sm leading-6 text-ink-2">{section.body}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
