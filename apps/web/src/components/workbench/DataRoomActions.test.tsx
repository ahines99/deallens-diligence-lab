import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActorProvider } from "@/components/identity/ActorContext";
import { api } from "@/lib/api";
import { AccountMappingForm } from "./DataRoomActions";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AccountMappingForm", () => {
  it("reports success as success: no error banner, form cleared (audit H5)", async () => {
    // Regression: resetting via event.currentTarget after the await threw a TypeError
    // inside the try block, so a mapping that WAS created server-side rendered as
    // "the request could not be completed" and invited a duplicate retry.
    const create = vi
      .spyOn(api, "createAccountMapping")
      .mockResolvedValue({} as Awaited<ReturnType<typeof api.createAccountMapping>>);

    render(
      <ActorProvider>
        <AccountMappingForm workspaceId="workspace-1" />
      </ActorProvider>,
    );
    fireEvent.change(screen.getByLabelText("Raw account"), { target: { value: "Sales - Products" } });
    fireEvent.change(screen.getByLabelText("Canonical account"), { target: { value: "product_revenue" } });
    const form = screen.getByRole("button", { name: "Approve mapping" }).closest("form");
    fireEvent.submit(form as HTMLFormElement);

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect((screen.getByLabelText("Raw account") as HTMLInputElement).value).toBe(""),
    );
    expect(screen.queryByText(/could not be completed/i)).not.toBeInTheDocument();
  });
});
