import Hero from "./Hero";
import SectionDecorator from "./SectionDecorator";
import SectionAgentRun from "./SectionAgentRun";
import SectionApproval from "./SectionApproval";

/**
 * ConceptScreens: Composes all product page sections (hero, decorator,
 * agent run, approval) with dividers between them.
 */
export default function ConceptScreens() {
  return (
    <div>
      <Hero />
      <div className="section-divider" />
      <SectionDecorator />
      <div className="section-divider" />
      <SectionAgentRun />
      <div className="section-divider" />
      <SectionApproval />
    </div>
  );
}
