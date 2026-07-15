"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "deallens.onboarding.dismissed.v1";

interface TourStep {
  title: string;
  body: string;
}

const STEPS: TourStep[] = [
  {
    title: "Welcome to DealLens",
    body: "A private-equity underwriting and diligence workbench on real SEC data. Every material claim traces to a source, and nothing is fabricated — sources report available, partial, or unavailable rather than a false-clean zero.",
  },
  {
    title: "Start with a company or the example deal",
    body: "Search any public company by name or ticker to build a diligence pack live from SEC EDGAR, or load the fictional example private deal to walk the full import → QoE → underwrite → IC workflow.",
  },
  {
    title: "Ask the filings, grounded",
    body: "The 'Ask the filings' tab answers from the real 10-K with verbatim citations — and abstains when the filings don't contain the evidence. Retrieval is measured with a CI-gated eval harness.",
  },
  {
    title: "Governed to the end",
    body: "Import financials, approve QoE add-backs under four-eyes, underwrite base/upside/downside cases, and freeze a hash-verified IC packet. Everything is auditable and reviewable by a human.",
  },
];

/** First-run guided tour (G50). Dismissal persists in localStorage. */
export function OnboardingTour() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    try {
      if (!localStorage.getItem(STORAGE_KEY)) setOpen(true);
    } catch {
      /* storage unavailable — skip the tour */
    }
  }, []);

  function dismiss() {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setOpen(false);
  }

  if (!open) return null;
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div className="w-full max-w-md rounded-lg border border-line bg-panel p-6 shadow-sm">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-2xs uppercase tracking-eyebrow text-faint">
            Step {step + 1} of {STEPS.length}
          </span>
          <button type="button" onClick={dismiss} className="text-xs text-muted hover:text-ink">
            Skip
          </button>
        </div>
        <h2 className="font-serif text-lg font-semibold text-ink">{current.title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">{current.body}</p>
        <div className="mt-5 flex items-center justify-between">
          <div className="flex gap-1.5">
            {STEPS.map((_, i) => (
              <span
                key={i}
                className={`h-1.5 w-1.5 rounded-full ${i === step ? "bg-accent" : "bg-line"}`}
                aria-hidden
              />
            ))}
          </div>
          <div className="flex gap-2">
            {step > 0 && (
              <button
                type="button"
                onClick={() => setStep((s) => s - 1)}
                className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:border-accent/40"
              >
                Back
              </button>
            )}
            <button
              type="button"
              onClick={() => (isLast ? dismiss() : setStep((s) => s + 1))}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:bg-accent/90"
            >
              {isLast ? "Get started" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default OnboardingTour;
