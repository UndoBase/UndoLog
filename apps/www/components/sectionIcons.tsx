const SectionIcon = ({ children, className }: { children: React.ReactNode; className?: string }) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className={className}>
    {children}
  </svg>
);

export const sectionIcons: Record<string, React.ReactNode> = {
  "getting-started": (
    <SectionIcon>
      <path d="M8 1.5L8 14.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M3 5.5L8 1.5L13 5.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 10.5L8 14.5L13 10.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </SectionIcon>
  ),
  guides: (
    <SectionIcon>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 5L8 11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5 8L11 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.3" />
    </SectionIcon>
  ),
  reference: (
    <SectionIcon>
      <rect x="2.5" y="1.5" width="11" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.5 5L10.5 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5.5 8L10.5 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5.5 11L8.5 11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </SectionIcon>
  ),
  explanation: (
    <SectionIcon>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 6V9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="8" cy="11.5" r="0.8" fill="currentColor" />
    </SectionIcon>
  ),
  adr: (
    <SectionIcon>
      <rect x="6" y="1.5" width="8" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 4L5.5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M2.5 7L5.5 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M2.5 10L5.5 10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M8 4.5L11.5 4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M8 7.5L11.5 7.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </SectionIcon>
  ),
  contributing: (
    <SectionIcon>
      <path d="M8 2C6.5 2 5 3 5 4.5C5 6.5 8 9 8 9C8 9 11 6.5 11 4.5C11 3 9.5 2 8 2Z" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3 14C3 11.5 5.5 10 8 10C10.5 10 13 11.5 13 14" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </SectionIcon>
  ),
  changelog: (
    <SectionIcon>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 5V8.5L10 10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </SectionIcon>
  ),
};
