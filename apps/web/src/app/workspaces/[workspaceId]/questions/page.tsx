import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { GenerateButton } from "@/components/GenerateButton";
import { QuestionList } from "@/components/QuestionList";
import type { DiligenceQuestion } from "@/lib/types";

export default async function QuestionsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let questions: DiligenceQuestion[];
  try {
    questions = await api.getQuestions(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return (
      <Callout tone="warning" title="Can't reach the API">
        {e instanceof ApiError ? e.message : "Failed to load diligence questions."} Start the
        backend service (<code className="font-mono">apps/api</code>) and refresh.
      </Callout>
    );
  }

  if (questions.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Analysis"
          title="Diligence questions"
          subtitle="Prioritized questions to put to management and advisors, organized by workstream."
        />
        <EmptyState
          title="No diligence questions yet"
          description="Generate a prioritized question list across every workstream, grounded in the real red-flag findings and the company's SEC filings."
          action={<GenerateButton kind="questions" workspaceId={id} label="Generate questions" />}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Diligence questions"
        subtitle={`${questions.length} question${questions.length === 1 ? "" : "s"} to put to management and advisors, grouped by workstream.`}
        actions={
          <GenerateButton
            kind="questions"
            workspaceId={id}
            label="Regenerate"
            variant="secondary"
          />
        }
      />

      <Callout tone="info" title="AI-drafted from real SEC filings">
        These questions were drafted from the real red-flag findings and the company's SEC filings.
        They are a starting point for human diligence — not investment advice.
      </Callout>

      <QuestionList questions={questions} workspaceId={id} />
    </div>
  );
}
