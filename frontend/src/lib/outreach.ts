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
  contacted_at?: string;
  sent_count?: number;
}): 'outreach' | 'followup' | 'compose' {
  const s = company.status || '';
  // Company stages and investor stages (Committed) alike: the conversation is live.
  if (['Responded', 'Meeting', 'DD', 'Offer', 'Won', 'Committed'].includes(s)) return 'compose';
  if (company.outreach_sent_at || (company.sent_count ?? 0) > 0) {
    // ONE FOLLOW-UP ONLY (Ishu, 24 Sep 2026): once the nudge has gone, the
    // template is never offered again. Anything further is a deliberate,
    // blank email in the same thread.
    return hasFollowedUp(company) ? 'compose' : 'followup';
  }
  return 'outreach';
}

// Our last outbound email, wherever it was sent from. email_log's
// last_sent_at knows about the inbox; outreach_sent_at knows only the tool.
export function lastSentAt(company: {
  outreach_sent_at?: string;
  last_sent_at?: string;
}): string | undefined {
  const a = company.last_sent_at ? new Date(company.last_sent_at).getTime() : 0;
  const b = company.outreach_sent_at ? new Date(company.outreach_sent_at).getTime() : 0;
  if (!a && !b) return undefined;
  return a >= b ? company.last_sent_at : company.outreach_sent_at;
}

// Has a follow-up already gone out? DERIVED, never stored (doctrine: no second
// copy of a fact). THE RULE (Ishu, 24 Sep 2026): the Follow up button shows
// only while exactly ONE email has ever been sent to the company. Ishu had
// been following up from his inbox, and the card kept offering a follow-up
// on the follow-up, because the row's outreach_sent_at only knows about
// sends from the tool. `sent_count` comes from email_log, which the sync
// fills from Gmail's Sent folder, so it counts both. Two or more sends =
// followed up, whoever pressed send and wherever.
// Fallback, when a row arrives without sent_count (an older endpoint): the
// first send stamps contacted_at and outreach_sent_at together, every later
// send refreshes only outreach_sent_at, so a gap over 60s means a follow-up
// went out from the tool.
export function hasFollowedUp(company: {
  outreach_sent_at?: string;
  contacted_at?: string;
  sent_count?: number;
}): boolean {
  if (typeof company.sent_count === 'number') return company.sent_count >= 2;
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
  last_sent_at?: string;
}): boolean {
  if (!company.last_reply_at) return false;
  const sent = lastSentAt(company);
  if (!sent) return true;
  return new Date(company.last_reply_at).getTime() > new Date(sent).getTime();
}

export function outreachButtonState(company: {
  status?: string;
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
  contacted_at?: string;
  last_reply_at?: string;
  sent_count?: number;
  last_sent_at?: string;
}): OutreachButtonState {
  const mode = outreachMode(company);
  const sentAt = lastSentAt(company);
  if (mode === 'compose') {
    // Contacted, already followed up: say so, and do NOT offer the template
    // again. The click opens a blank email in the same thread, for the rare
    // case there is something new to say.
    if (company.status === 'Contacted' && hasFollowedUp(company)) {
      const n = company.sent_count;
      return {
        state: 'compose',
        cls: 'sent',
        label: '✓ Followed up',
        title: `${n ? `${n} emails sent` : 'Followed up'}${sentAt ? `, last on ${new Date(sentAt).toLocaleDateString('en-GB')}` : ''} (from the tool or the inbox; the sync counts both). No further follow-up is offered; moves to Responded automatically if they reply. Click only to write something new in the same thread.`,
      };
    }
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
      title: 'Write an email (blank draft, in their thread; the stage is never changed by a send from here)',
    };
  }
  if (mode === 'followup') {
    // Exactly one email has gone out and nothing has come back: the one
    // follow-up is on offer. (Two or more sends never reach here; see
    // outreachMode.)
    return {
      state: 'followup',
      cls: 'followup',
      label: '↩ Follow up',
      title: `Email sent ${sentAt ? new Date(sentAt).toLocaleString('en-GB') : ''} — opens the follow-up template in the same thread, ready to review and send`,
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
