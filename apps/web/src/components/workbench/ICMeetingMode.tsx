"use client";

import { useState } from "react";
import type { ICPacket } from "@/lib/types";

type Decision = "approve" | "conditional" | "defer" | "decline";

interface Slide {
  key: string;
  title: string;
  render: (packet: ICPacket) => number;
}

const SLIDES: Slide[] = [
  { key: "thesis", title: "Investment thesis", render: (p) => p.thesis_snapshot.length },
  { key: "risks", title: "Key risks", render: (p) => p.risk_snapshot.length },
  { key: "evidence", title: "Evidence manifest", render: (p) => p.evidence_manifest.length },
  { key: "decision", title: "Decision request", render: () => 0 },
];

/**
 * Full-screen IC packet presentation with inline decision capture (G46).
 * Read-only content bound to a frozen packet; the decision is captured at the end.
 */
export function ICMeetingMode({
  packet,
  onDecision,
  onExit,
}: {
  packet: ICPacket;
  onDecision: (decision: Decision, rationale: string) => void;
  onExit?: () => void;
}) {
  const [slide, setSlide] = useState(0);
  const [rationale, setRationale] = useState("");
  const current = SLIDES[slide];
  const onDecisionSlide = current.key === "decision";

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-ink text-white" role="dialog" aria-label="IC meeting mode">
      <header className="flex items-center justify-between border-b border-white/15 px-6 py-3">
        <div>
          <span className="text-2xs uppercase tracking-eyebrow text-white/50">IC meeting · {packet.title}</span>
          <div className="font-mono text-2xs text-white/40">hash {packet.content_hash.slice(0, 12)}…</div>
        </div>
        <button type="button" onClick={onExit} className="text-sm text-white/70 hover:text-white" aria-label="Exit meeting mode">
          Exit
        </button>
      </header>

      <div className="flex flex-1 flex-col items-center justify-center px-8">
        <span className="text-2xs uppercase tracking-eyebrow text-white/40">
          {slide + 1} / {SLIDES.length}
        </span>
        <h2 className="mt-2 font-serif text-3xl font-semibold">{current.title}</h2>
        {onDecisionSlide ? (
          <div className="mt-6 w-full max-w-lg">
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              rows={3}
              placeholder="Decision rationale (required)…"
              className="w-full rounded-md border border-white/20 bg-white/5 p-3 text-sm text-white placeholder:text-white/40"
              aria-label="Decision rationale"
            />
            <div className="mt-4 grid grid-cols-2 gap-2">
              {(["approve", "conditional", "defer", "decline"] as Decision[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  disabled={!rationale.trim()}
                  onClick={() => onDecision(d, rationale.trim())}
                  className="rounded-md border border-white/25 px-3 py-2 text-sm font-semibold capitalize disabled:opacity-40 hover:bg-white/10"
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <p className="mt-4 text-white/70">{current.render(packet)} item(s) in this section.</p>
        )}
      </div>

      <footer className="flex items-center justify-between border-t border-white/15 px-6 py-3">
        <button
          type="button"
          onClick={() => setSlide((s) => Math.max(0, s - 1))}
          disabled={slide === 0}
          className="rounded-md border border-white/25 px-4 py-1.5 text-sm disabled:opacity-40"
        >
          Back
        </button>
        {!onDecisionSlide && (
          <button
            type="button"
            onClick={() => setSlide((s) => Math.min(SLIDES.length - 1, s + 1))}
            className="rounded-md bg-white px-4 py-1.5 text-sm font-semibold text-ink"
          >
            Next
          </button>
        )}
      </footer>
    </div>
  );
}

export default ICMeetingMode;
