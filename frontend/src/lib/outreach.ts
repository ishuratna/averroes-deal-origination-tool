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

// Has a follow-up already gone out? DERIVED, never stored (doctrine: no second
// copy of a fact): the first send stamps contacted_at and outreach_sent_at in
// the same statement, so they are equal. Every later send refreshes only
// outreach_sent_at. A meaningful gap between them therefore means at least one
// follow-up has been sent. The 60s tolerance absorbs clock jitter without ever
// mistaking a first send for a follow-up.
export function hasFollowedUp(company: {
  outreach_sent_at?: string;
  contacted_at?: string;
}): boolean {
  if (!company.outreach_sent_at || !company.contacted_at) return false;
  return new Date(company.outreach_sent_at).getTime()
       - new Date(company.contacted_at).getTime() > 60_000;
}

// Does the company await OUR answer? Derived from the two stamps the sync and
// the send path already maintain: their last genuine reply vs our last send.
// Same comparison the backend's 7-day reminder makes, so the card and the
// follow-up queue can never disagree about who owes whom.
export function owesReply(company: {
  last_reply_at?: string;
  outreach_sent_at?: string;
}): boolean {
  if (!company.last_reply_at) return false;
  if (!company.outreach_sent_at) return true;
  return new Date(company.last_reply_at).getTime()
       > new Date(company.outreach_sent_at).getTime();
}

export function outreachButtonState(company: {
  status?: string;
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
  contacted_at?: string;
  last_reply_at?: string;
}): OutreachButtonState {
  const mode = outreachMode(company);
  if (mode === 'compose') {
    // Same principle as the Contacted column: amber = the ball is with us,
    // green = we answered and the ball is with them. On Responded cards the
    // distinction is "Reply" vs "Email"; deeper stages just say Email, since
    // conversations there are managed by their owner, not by this button.
    if (company.status === 'Responded' && owesReply(company)) {
      return {
        state: 'compose',
        cls: 'followup',
        label: '↩ Reply',
        title: 'They wrote last and we have not answered — opens a reply in their thread (their address, their subject).',
      };
    }
    return {
      state: 'compose',
      cls: 'sent',
      label: '✉ Email',
      title: 'Write an email to this company (blank draft, sends from Bea’s mailbox, stage is never changed by a send from here)',
    };
  }
  if (mode === 'followup') {
    // Already followed up: say so, so the column reads at a glance who has had
    // the nudge and who is still waiting for one. Still clickable — a second
    // follow-up is a legitimate (if rare) move, and the modal opens the same
    // template in the same thread.
    if (hasFollowedUp(company)) {
      return {
        state: 'followup',
        cls: 'sent',
        label: '✓ Followed up',
        title: `Followed up ${company.outreach_sent_at ? new Date(company.outreach_sent_at).toLocaleString('en-GB') : ''} — moves to Responded automatically if they reply. Click to send another follow-up in the same thread.`,
      };
    }
    return {
      state: 'followup',
      cls: 'followup',
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
