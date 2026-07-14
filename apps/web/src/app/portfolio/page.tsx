import { PortfolioCommandCenter } from "@/components/portfolio/PortfolioCommandCenter";
import { PageHeader } from "@/components/ui/PageHeader";

export default function PortfolioPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Firm command center"
        title="Portfolio oversight"
        subtitle="Monitor pipeline shape, deal readiness, committee cadence, execution exceptions, modeled returns, and source integrity across the active portfolio."
      />
      <PortfolioCommandCenter />
    </div>
  );
}
