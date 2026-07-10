// Single source of truth for the 3-state outreach button.
// Used by the Universe table AND the Pipeline kanban cards — the same fields
// must always produce the same state, label and explanation everywhere.

export interface OutreachButtonState {
  state: 'none' | 'drafted' | 'sent';
  cls: string;      // modifier class: '' | 'drafted' | 'sent'
  label: string;
  title: string;
}

export function outreachButtonState(company: {
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
}): OutreachButtonState {
  if (company.outreach_sent_at) {
    return {
      state: 'sent',
      cls: 'sent',
      label: '✓ Email Sent',
      title: `Email sent ${new Date(company.outreach_sent_at).toLocaleString('en-GB')} — click to view the sent draft or send a follow-up`,
    };
  }
  if (company.outreach_drafted_at) {
    return {
      state: 'drafted',
      cls: 'drafted',
      label: '✉ Review & Send',
      title: `Draft saved ${new Date(company.outreach_drafted_at).toLocaleString('en-GB')} — opens for review without regenerating`,
    };
  }
  return {
    state: 'none',
    cls: '',
    label: '✉ Outreach',
    title: 'Generate an AI outreach draft (does not change the stage)',
  };
}
