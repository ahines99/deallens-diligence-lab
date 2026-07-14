import { PageHeader } from "@/components/ui/PageHeader";
import { PipelineBoard } from "@/components/workbench/PipelineBoard";

export default function PipelinePage() {
  return <div className="space-y-6"><PageHeader eyebrow="Deal portfolio" title="Investment pipeline" subtitle="Manage organization- and fund-scoped opportunities from sourcing through close, with explicit stage gates and a direct handoff into each underwriting workspace." /><PipelineBoard /></div>;
}
