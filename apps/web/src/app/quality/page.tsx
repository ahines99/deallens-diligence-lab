import { api, loadOrUnavailable } from "@/lib/serverApi";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import type { ModelQuality, QualitySection } from "@/lib/types";

/** G56 — the model-quality dashboard: measurement made visible.
 *
 * Every section renders its OWN status: an unavailable source says so explicitly (and why)
 * rather than showing zeros — the same honest-degradation rule the data pages follow. */
export default async function QualityPage() {
  const { data: quality, unavailable } = await loadOrUnavailable<ModelQuality | null>(
    api.getModelQuality(),
    null,
  );
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Model operations"
        title="Model quality & measurement"
        subtitle="Judge-evaluated faithfulness, retrieval quality against the committed baseline, abstention calibration, and the hashed prompt registry — the evidence that generation stays grounded."
      />
      {unavailable && (
        <Callout tone="warning" title="Quality data unavailable">
          The model-ops quality report could not be loaded from the API. This is a data outage,
          not a clean bill of health — retry once the service is reachable.
        </Callout>
      )}
      {quality && (
        <div className="grid gap-6 xl:grid-cols-2">
          <SectionCard
            eyebrow="LLM-as-judge (G05)"
            title="Faithfulness evaluations"
            section={quality.judge_evals}
          >
            {quality.judge_evals.status === "available" && (
              <div className="space-y-3 text-xs text-body">
                <p>
                  <strong className="text-ink">{quality.judge_evals.faithful}</strong> of{" "}
                  <strong className="text-ink">{quality.judge_evals.total}</strong> persisted
                  answers judged faithful (
                  {Math.round((quality.judge_evals.faithful_rate ?? 0) * 100)}%).
                </p>
                <table className="min-w-full text-2xs">
                  <thead>
                    <tr className="text-left uppercase tracking-eyebrow text-faint">
                      <th className="py-1 pr-3">Model</th>
                      <th className="py-1 pr-3">Prompt</th>
                      <th className="py-1 pr-3 text-right">Runs</th>
                      <th className="py-1 text-right">Faithful</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(quality.judge_evals.groups ?? []).map((group) => (
                      <tr key={`${group.model_version}-${group.prompt_version}`} className="border-t border-line-faint">
                        <td className="py-1 pr-3 font-mono">{group.model_version ?? "—"}</td>
                        <td className="py-1 pr-3 font-mono">{group.prompt_version ?? "—"}</td>
                        <td className="py-1 pr-3 text-right">{group.count}</td>
                        <td className="py-1 text-right">{Math.round(group.faithful_rate * 100)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
          <SectionCard
            eyebrow="Retrieval evals (G03)"
            title="Recall & MRR vs committed baseline"
            section={quality.retrieval_metrics}
          >
            {quality.retrieval_metrics.status === "available" && (
              <div className="space-y-2 text-xs text-body">
                <p>
                  Golden set of <strong className="text-ink">{quality.retrieval_metrics.num_questions}</strong>{" "}
                  questions; CI fails on regression below the committed floor.
                </p>
                <table className="min-w-full text-2xs">
                  <thead>
                    <tr className="text-left uppercase tracking-eyebrow text-faint">
                      <th className="py-1 pr-3">Ranker</th>
                      {(quality.retrieval_metrics.recall_ks ?? []).map((k) => (
                        <th key={k} className="py-1 pr-3 text-right">recall@{k}</th>
                      ))}
                      <th className="py-1 text-right">MRR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(quality.retrieval_metrics.rankers ?? {}).map(([ranker, metrics]) => (
                      <tr key={ranker} className="border-t border-line-faint">
                        <td className="py-1 pr-3 font-mono">{ranker}</td>
                        {(quality.retrieval_metrics.recall_ks ?? []).map((k) => (
                          <td key={k} className="py-1 pr-3 text-right">
                            {(metrics[`recall@${k}`] ?? 0).toFixed(3)}
                          </td>
                        ))}
                        <td className="py-1 text-right">{(metrics.mrr ?? 0).toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
          <SectionCard
            eyebrow="Abstention calibration (G06)"
            title="Answer / abstain thresholds"
            section={quality.calibration}
          >
            {quality.calibration.status === "available" && (
              <div className="space-y-1 text-xs text-body">
                <p>
                  Answers below <strong className="text-ink">{quality.calibration.partial_coverage_threshold}</strong>{" "}
                  term coverage are labeled partial; zero-overlap questions abstain outright.
                </p>
                <p className="text-2xs text-faint">Justified by the committed study: {quality.calibration.study}</p>
              </div>
            )}
          </SectionCard>
          <SectionCard
            eyebrow="Prompt registry (G10)"
            title="Versioned, hashed prompts"
            section={quality.prompts}
          >
            {quality.prompts.status === "available" && (
              <ul className="space-y-1 text-2xs">
                {(quality.prompts.prompts ?? []).map((prompt) => (
                  <li key={prompt.prompt_id} className="flex flex-wrap items-baseline gap-2">
                    <span className="font-semibold text-ink">{prompt.prompt_id}</span>
                    <span className="text-muted">{prompt.prompt_version}</span>
                    <span className="font-mono text-faint" title={prompt.prompt_hash}>
                      {prompt.prompt_hash.slice(0, 12)}…
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
          <SectionCard
            eyebrow="Extractor comparison (G52)"
            title="LLM vs deterministic scanner"
            section={quality.extraction_comparison}
          />
        </div>
      )}
    </div>
  );
}

function SectionCard({
  eyebrow,
  title,
  section,
  children,
}: {
  eyebrow: string;
  title: string;
  section: QualitySection;
  children?: React.ReactNode;
}) {
  return (
    <Card
      eyebrow={eyebrow}
      title={title}
      right={
        <Badge tone={section.status === "available" ? "green" : "amber"}>{section.status}</Badge>
      }
    >
      {section.status === "unavailable" ? (
        <p className="text-xs text-muted">{section.note ?? "No data recorded yet."}</p>
      ) : (
        children
      )}
    </Card>
  );
}
