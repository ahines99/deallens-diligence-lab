import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ICMeetingMode } from "./ICMeetingMode";
import type { ICPacket } from "@/lib/types";

afterEach(cleanup);

const packet = {
  id: "p1",
  deal_id: "d1",
  version: 1,
  previous_packet_id: null,
  title: "Project Atlas IC Packet",
  status: "submitted",
  scenario_snapshot: {},
  model_snapshot: {},
  evidence_manifest: [{ ref: "EV-001" }, { ref: "EV-002" }],
  thesis_snapshot: [{ point: "a" }],
  risk_snapshot: [{ risk: "x" }, { risk: "y" }, { risk: "z" }],
  decision_request: {},
  readiness_snapshot: {},
  ready_for_submission: true,
  content_hash: "abcdef0123456789",
  created_by_actor_id: null,
  submitted_by_actor_id: null,
  submitted_at: null,
  frozen_at: null,
  created_at: "2026-07-15T00:00:00Z",
  updated_at: "2026-07-15T00:00:00Z",
} as unknown as ICPacket;

describe("ICMeetingMode (G46)", () => {
  it("presents packet sections and captures a decision only with a rationale", () => {
    const onDecision = vi.fn();
    render(<ICMeetingMode packet={packet} onDecision={onDecision} />);

    // Opens on the thesis slide with the packet title + hash.
    expect(screen.getByText(/Project Atlas IC Packet/)).toBeInTheDocument();
    expect(screen.getByText("Investment thesis")).toBeInTheDocument();

    // Step through to the decision slide.
    fireEvent.click(screen.getByText("Next")); // risks
    expect(screen.getByText("Key risks")).toBeInTheDocument();
    expect(screen.getByText(/3 item\(s\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Next")); // evidence
    fireEvent.click(screen.getByText("Next")); // decision
    expect(screen.getByText("Decision request")).toBeInTheDocument();

    // Decision buttons are disabled until a rationale is entered.
    const approve = screen.getByRole("button", { name: "approve" });
    expect(approve).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Decision rationale"), {
      target: { value: "Thesis holds; leverage acceptable." },
    });
    expect(approve).not.toBeDisabled();
    fireEvent.click(approve);
    expect(onDecision).toHaveBeenCalledWith("approve", "Thesis holds; leverage acceptable.");
  });
});
