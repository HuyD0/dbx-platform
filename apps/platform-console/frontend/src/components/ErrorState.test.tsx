import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ApiError } from "../lib/types";
import { ErrorState } from "./ui";

test("an opaque gateway 502 reads as temporarily unavailable, not a broken source", () => {
  // api.ts labels a non-JSON gateway response with the generic http_error code.
  const gateway502 = new ApiError(502, { error: "http_error", message: "502 " });
  render(<ErrorState error={gateway502} />);

  expect(screen.getByText("The data source is temporarily unavailable.")).toBeTruthy();
  expect(screen.getByText(/waking up/i)).toBeTruthy();
  // The alarming generic copy is not shown for a transient gateway error.
  expect(screen.queryByText("The data source could not be read.")).toBeNull();
  // The raw status is still available for debugging.
  expect(screen.getByText("Technical detail")).toBeTruthy();
});

test("a typed app 503 keeps its specific guidance", () => {
  const typed503 = new ApiError(503, {
    error: "system_tables_unavailable",
    message: "Required Databricks system-table data is unavailable.",
  });
  render(<ErrorState error={typed503} />);

  expect(
    screen.getByText("System tables are not enabled or not granted to the app's identity."),
  ).toBeTruthy();
  expect(screen.queryByText("The data source is temporarily unavailable.")).toBeNull();
});
