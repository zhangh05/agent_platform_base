import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Link, MemoryRouter, useLocation } from "../router";

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{location.pathname}{location.search}</span>;
}

describe("application router", () => {
  it("navigates only inside the application origin", () => {
    render(
      <MemoryRouter initialEntries={["/workbench"]}>
        <Link to="/data?producer_id=run-1">数据</Link>
        <LocationProbe />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("link", { name: "数据" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/data?producer_id=run-1");
  });

  it("rejects protocol-relative and backslash targets", () => {
    expect(() => render(<MemoryRouter initialEntries={["//evil.example"]}><div /></MemoryRouter>)).toThrow();
    expect(() => render(<MemoryRouter initialEntries={["/\\evil"]}><div /></MemoryRouter>)).toThrow();
  });
});
