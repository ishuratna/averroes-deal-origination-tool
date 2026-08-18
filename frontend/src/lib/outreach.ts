// Single source of truth for the outreach button.
// Used by the Universe table AND the Pipeline kanban cards — the same fields
// must always produce the same state, label and explanation everywhere.
//
// The button is ALWAYS actionable now. It used to go inert at "Email sent",
// which meant the natural next actions (a follow-up to a quiet Contacted
// company, a reply to a Responded one) had to happen outside the tool — where
// nothing was logged and the contact-adoption rule could not see corrections.

export interface OutreachButtonState {
  state: 'none' | 'drafted' | 'followup' | 'compose';
  cls: string;      // modifier class: '' | 'drafted' | 'sent'
  label: string;
  title: string;
}

// Which draft the modal opens with. Derived from the same fields as the
// button, so the two can never disagree:
//   outreach  first email: AI-drafted, personalised, saved for review
//   followup  the approved 14-day template, same thread, zero AI
//   compose   blank: the conversation is live, only Ishu knows what to say
export function outreachMode(company: {
  status?: string;
  outreach_sent_at?: string;
}): 'outreach' | 'followup' | 'compose' {
  const s = company.status || '';
  if (['Responded', 'Meeting', 'DD', 'Offer', 'Won'].includes(s)) return 'compose';
  if (company.outreach_sent_at) return 'followup';
  return 'outreach';
}

export function outreachButtonState(company: {
  status?: string;
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
}): OutreachButtonState {
  const mode = outreachMode(company);
  if (mode === 'compose') {
    return {
      state: 'compose',
      cls: 'sent',
      label: '✉ Email',
      title: 'Write an email to this company (blank draft, sends from Bea’s mailbox, stage is never changed by a send from here)',
    };
  }
  if (mode === 'followup') {
    return {
      state: 'followup',
      cls: 'sent',
      label: '↩ Follow up',
      title: `Email sent ${company.outreach_sent_at ? new Date(company.outreach_sent_at).toLocaleString('en-GB') : ''} — opens the follow-up template in the same thread, ready to review and send`,
    };
  }
  if (company.outreach_drafted_at) {
    return {
      state: 'drafted',
      cls: 'drafted',
      label: '✉ Review & Send',
      title: `Draft saved ${company.outreach_drafted_at ? new Date(company.outreach_drafted_at).toLocaleString('en-GB') : ''} — opens for review without regenerating`,
    };
  }
  return {
    state: 'none',
    cls: '',
    label: '✉ Outreach',
    title: 'Generate an AI outreach draft (does not change the stage)',
  };
}
