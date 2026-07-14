import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ActorProvider } from "./ActorContext";
import { IdentitySwitcher } from "./IdentitySwitcher";

describe("IdentitySwitcher", () => {
  beforeEach(() => window.localStorage.clear());

  it("switches and persists the actor used for audited actions", () => {
    render(<ActorProvider><IdentitySwitcher /></ActorProvider>);
    const select = screen.getByLabelText("Acting identity") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "demo-partner" } });
    expect(select.value).toBe("demo-partner");
    expect(window.localStorage.getItem("deallens.demoActorId")).toBe("demo-partner");
  });
});
