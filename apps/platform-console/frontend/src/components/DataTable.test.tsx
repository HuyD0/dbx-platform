import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect, test, vi } from "vitest";
import { DataTable } from "./DataTable";

expect.extend(toHaveNoViolations);

const rows = [
  { name: "Zulu", spend: 3.5, currency: "CAD", status: "READY" },
  { name: "Alpha", spend: 2, currency: "CAD", status: "PAUSED" },
  { name: "Bravo", spend: 1, status: "READY" },
];

test("table is sortable, filterable, paginated, exportable, and currency-safe", async () => {
  const user = userEvent.setup();
  const { container } = render(
    <DataTable
      rows={rows}
      pageSize={2}
      caption="LLM cost allocations"
      exportName="allocations"
      columns={["name", "spend", "status"]}
    />,
  );

  const table = screen.getByRole("table", { name: "LLM cost allocations" });
  const nameHeader = within(table).getByRole("columnheader", { name: /name/i });
  const nameSort = within(nameHeader).getByRole("button");
  expect(nameHeader).toHaveAttribute("aria-sort", "none");

  await user.click(nameSort);
  expect(nameHeader).toHaveAttribute("aria-sort", "ascending");
  expect(within(table).getAllByRole("row")[1]).toHaveTextContent("Alpha");

  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();

  await user.type(screen.getByRole("searchbox", { name: "Filter table" }), "Bravo");
  expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(table).toHaveTextContent("1 UNKNOWN");

  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  await user.click(screen.getByRole("button", { name: "Export CSV" }));
  expect(URL.createObjectURL).toHaveBeenCalledOnce();
  expect(anchorClick).toHaveBeenCalledOnce();

  expect(await axe(container)).toHaveNoViolations();
});
