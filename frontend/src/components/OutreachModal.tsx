'use client';

// Shared founder-outreach modal — used by both the Universe table and the
// Pipeline kanban. Three modes, decided by outreachMode() from the SAME fields
// that drive the button, so button and modal can never disagree:
//   outreach  first email: AI-drafted, personalised, saved for review
//   followup  Contacted + no reply yet: the approved 14-day template, in the
//             SAME thread (Re: the original subject), zero AI
//   compose   Responded and beyond: a blank draft — the conversation is live
//             and only Ishu knows what to say next
// Behaviour contract:
//   • A previously saved first draft opens instantly for review (no AI rerun)
//   • "Generate New Draft" explicitly replaces it (uses AI credits) — first
//     email only; a template needs no regenerating and a blank has nothing to
//   • Sending only ever moves a company FORWARD (backend rule): a first send
//     sets Contacted, a follow-up keeps Contacted, a send from Responded or
//     later never changes the stage.

import { useState, useEffect, useCallback, useRef } from 'react';
import { dealApi } from '@/services/api';
import { outreachMode } from '@/lib/outreach';
import CompanyCardHeader from './CompanyCardHeader';

interface Draft { to: string; subject: string; body: string; company: string; from?: string; }

// The same modal serves founders (entity 'company', Bea's mailbox) and
// investors (entity 'investor', the LP mailbox). Same three modes, same
// button states, same forward-only send rule; only the endpoints behind the
// dealApi calls and the From line differ. Never fork this per entity.
type Entity = 'company' | 'investor';
const API = {
  company: {
    draft: (n: string) => dealApi.draftOutreach(n),
    followup: (n: string) => dealApi.getFollowupDraft(n),
    compose: (n: string) => dealApi.getComposeDraft(n),
    send: (to: string, s: string, b: string, n: string) => dealApi.sendOutreach(to, s, b, n),
    from: 'Beatrice Carrara <beatrice@averroescapital.com>',
    noun: 'company',
  },
  investor: {
    draft: (n: string) => dealApi.draftInvestorOutreach(n),
    followup: (n: string) => dealApi.getInvestorFollowupDraft(n),
    compose: (n: string) => dealApi.getInvestorComposeDraft(n),
    send: (to: string, s: string, b: string, n: string) => dealApi.sendInvestorOutreach(to, s, b, n),
    from: 'the investor mailbox',
    noun: 'investor',
  },
};

// QUEUE MODE (Ishu, 5 Nov 2026: "it takes a lot of time to send one email
// ... the window refreshes ... I want a slider where I can go left and
// right"). The parent passes the ordered column (`queue`) and the current
// position; the modal shows "3 of 42", steps with the arrows or the keyboard,
// and after a send advances by itself to the next company that still needs
// one. Nothing in the modal reloads the page: `onSentLocal` lets the parent
// patch the one row in place (doctrine 1: the row is still the only truth,
// the patch mirrors exactly what the send path wrote), and the parent
// refreshes quietly when the modal closes.
//   ← →            previous / next company
//   Cmd/Ctrl+Enter send
//   Esc            close
export default function OutreachModal({
  company,
  onClose,
  onSent,
  onSentLocal,
  queue,
  index,
  onIndexChange,
  entity = 'company',
}: {
  company: any | null;
  onClose: () => void;
  onSent?: () => void;
  onSentLocal?: (name: string, mode: 'outreach' | 'followup' | 'compose') => void;
  queue?: any[];
  index?: number;
  onIndexChange?: (i: number) => void;
  entity?: Entity;
}) {
  const api = API[entity];
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);
  // Companies sent in THIS session, so the auto-advance skips them even
  // before the parent's patch lands, and the counter can say "3 sent".
  const doneRef = useRef<Set<string>>(new Set());
  const [doneCount, setDoneCount] = useState(0);
  // Drafts fetched ahead for the next company (first-email mode only; the
  // follow-up and compose drafts are already on the row or instant).
  const prefetched = useRef<Map<string, Draft>>(new Map());
  const inQueue = Array.isArray(queue) && queue.length > 0 && typeof index === 'number' && !!onIndexChange;

  // "Still needs an email": a first email or the one follow-up, and not sent
  // in this session. Compose rows (live conversations) are never auto-stepped
  // into: a blank email is a decision, not a queue item.
  const needsEmail = (c: any) => {
    if (!c || doneRef.current.has(c.name)) return false;
    const m = outreachMode(c);
    return m === 'outreach' || m === 'followup';
  };
  const nextActionable = (from: number, dir: 1 | -1): number => {
    if (!inQueue) return -1;
    for (let i = from + dir; i >= 0 && i < queue!.length; i += dir) {
      if (needsEmail(queue![i])) return i;
    }
    return -1;
  };
  const step = (dir: 1 | -1) => {
    if (!inQueue) return;
    // Plain stepping walks every card (so one can be skipped or revisited);
    // only the post-send auto-advance insists on the next actionable one.
    const i = index! + dir;
    if (i >= 0 && i < queue!.length) onIndexChange!(i);
  };

  const generateDraft = useCallback(async (c: any) => {
    setLoading(true);
    try {
      const d = await api.draft(c.name);
      setDraft({
        to: d.to || c.contact_email || '',
        subject: d.subject || '',
        body: d.body || '',
        company: c.name,
        from: d.from,
      });
    } catch (err: any) {
      alert(`Failed to generate draft: ${err.message}`);
      onClose();
    } finally { setLoading(false); }
  }, [onClose, api]);

  const mode = company ? outreachMode(company) : 'outreach';

  useEffect(() => {
    if (!company) return;
    setDraft(null);
    setSent(false);
    if (mode === 'compose') {
      // Recipient and subject come from the conversation itself: the address
      // their last genuine reply came FROM (contact_email can lag it when a
      // founder answers from a personal address), threaded as Re: under their
      // last subject. Only the body is blank — the words are Ishu's.
      setLoading(true);
      api.compose(company.name)
        .then(d => setDraft({ to: d.to || company.contact_email || '', subject: d.subject || '',
                              body: '', company: company.name, from: d.from }))
        .catch(() => setDraft({ to: company.contact_email || '', subject: '', body: '', company: company.name }))
        .finally(() => setLoading(false));
      return;
    }
    if (mode === 'followup') {
      // The follow-up is generic (no AI) and the pipeline row already carries
      // it (followup_draft, built server-side from the same template), so the
      // modal opens instantly. The request below is only the fallback for a
      // row that arrived without it (the slim universe, investors).
      const pre = (company as { followup_draft?: { to?: string; subject?: string; body?: string } }).followup_draft;
      if (pre?.body) {
        setDraft({ to: pre.to || company.contact_email || '', subject: pre.subject || '',
                   body: pre.body, company: company.name });
        return;
      }
      setLoading(true);
      api.followup(company.name)
        .then(d => setDraft({ to: d.to || company.contact_email || '', subject: d.subject || '',
                              body: d.body || '', company: company.name, from: d.from }))
        .catch((err: any) => { alert(`Failed to load the follow-up template: ${err.message}`); onClose(); })
        .finally(() => setLoading(false));
      return;
    }
    // A previously generated draft opens instantly for review — no regeneration
    if (company.outreach_drafted_at && company.outreach_draft_body) {
      setDraft({
        to: company.outreach_draft_to || company.contact_email || '',
        subject: company.outreach_draft_subject || '',
        body: company.outreach_draft_body || '',
        company: company.name,
      });
      return;
    }
    const pre = prefetched.current.get(company.name);
    if (pre) { setDraft(pre); return; }
    generateDraft(company);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.name]);

  // PREFETCH: while this one is being read, draft the next first-email row
  // that has no stored draft, so stepping forward never waits on the AI.
  // One at a time, never further ahead: a draft costs AI credit, and the
  // person may close the modal after this one.
  useEffect(() => {
    if (!inQueue || !company) return;
    const ni = nextActionable(index!, 1);
    if (ni < 0) return;
    const nx = queue![ni];
    if (outreachMode(nx) !== 'outreach' || nx.outreach_draft_body || prefetched.current.has(nx.name)) return;
    let cancelled = false;
    api.draft(nx.name).then(d => {
      if (cancelled) return;
      prefetched.current.set(nx.name, { to: d.to || nx.contact_email || '', subject: d.subject || '',
                                         body: d.body || '', company: nx.name, from: d.from });
    }).catch(() => { /* the row opens the normal way when reached */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.name, index]);

  // KEYBOARD. Arrows only when the cursor is not inside a field (they move
  // the caret there); Cmd/Ctrl+Enter sends from anywhere; Esc closes.
  useEffect(() => {
    if (!company) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName || '';
      const inField = tag === 'INPUT' || tag === 'TEXTAREA';
      if (e.key === 'Escape') { onClose(); return; }
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void handleSend(); return; }
      if (inField || !inQueue) return;
      if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.name, draft, sending, sent, index]);

  if (!company) return null;

  const handleSend = async () => {
    if (!draft?.to || sending || sent || loading) return;
    setSending(true);
    try {
      await api.send(draft.to, draft.subject, draft.body, draft.company);
      setSent(true);
      doneRef.current.add(draft.company);
      setDoneCount(doneRef.current.size);
      prefetched.current.delete(draft.company);
      if (onSentLocal) {
        // Queue mode: the parent patches the one row; no reload.
        onSentLocal(draft.company, mode);
        if (inQueue) {
          const ni = nextActionable(index!, 1);
          if (ni >= 0) setTimeout(() => onIndexChange!(ni), 650);
        }
      } else {
        onSent?.();  // legacy: stage bumps to Contacted — let the parent reload
      }
    } catch (err: any) {
      alert(`Send failed: ${err.message}`);
    } finally { setSending(false); }
  };

  const handleCopy = () => {
    if (!draft) return;
    navigator.clipboard.writeText(`To: ${draft.to}\nSubject: ${draft.subject}\n\n${draft.body}`);
    alert('Draft copied to clipboard!');
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content outreach-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{mode === 'followup' ? 'Follow up' : mode === 'compose' ? 'Email' : 'Outreach'} — {company.name}</h3>
          <div className="om-nav">
            {inQueue && (
              <>
                <button className="om-arrow" onClick={() => step(-1)} disabled={index! <= 0} title="Previous (←)">&#8249;</button>
                <span className="om-pos" title={`${queue![0]?.status || ''} column, in board order. ${doneCount} sent this session.`}>
                  {index! + 1} of {queue!.length}{doneCount ? ` · ${doneCount} sent` : ''}
                </span>
                <button className="om-arrow" onClick={() => step(1)} disabled={index! >= queue!.length - 1} title="Next (→)">&#8250;</button>
              </>
            )}
            <button className="modal-close" onClick={onClose} title="Close (Esc)">&times;</button>
          </div>
        </div>
        <div className="modal-body">
          {/* The company, in exactly the card's own format (Ishu, 5 Nov
              2026): name, sector and city, band, fit, owner, Revenue /
              EBITDA / Team, so what is being written to is in view before
              the send. Founders only: an investor row has other fields. */}
          {entity === 'company' && !sent && (
            <div className="om-card"><CompanyCardHeader company={company} compact /></div>
          )}
          {loading ? (
            <div className="outreach-loading">
              <div className="spinner"></div>
              {mode === 'outreach' ? (
                <>
                  <p>Drafting personalised email with AI...</p>
                  <p className="loading-sub">Personalising the intro to {company.name} from what the tool holds</p>
                </>
              ) : (
                <>
                  <p>{mode === 'followup' ? 'Loading the follow-up template...' : 'Opening the thread...'}</p>
                  <p className="loading-sub">No AI involved: {mode === 'followup' ? 'the approved 14-day wording, threaded under the first email' : 'recipient and subject come from the conversation'}</p>
                </>
              )}
            </div>
          ) : sent ? (
            <div className="outreach-sent">
              <div className="sent-icon">&#10003;</div>
              <h4>Email Sent</h4>
              <p>Sent to <strong>{draft?.to}</strong> from {draft?.from || api.from}.</p>
              <p className="sent-sub">
                {mode === 'compose'
                  ? 'The stage is unchanged — a send never moves a company backward.'
                  : mode === 'followup'
                    ? 'Still Contacted; the 14-day follow-up clock has restarted from this send.'
                    : `The ${api.noun}'s stage has moved to Contacted.`}
                {inQueue && nextActionable(index!, 1) >= 0 ? ' Moving to the next one…' : inQueue ? ' That was the last one in this column.' : ''}
              </p>
            </div>
          ) : draft ? (
            <div className="outreach-form">
              <div className="form-row">
                <label>To</label>
                <input type="email" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
              </div>
              {/* The waterfall does not always land on the founder. Say so here,
                  at the moment of sending, not only on the company card. */}
              {company.contact_email_kind && company.contact_email_kind !== 'founder' && (
                <p className="om-recip-warn">
                  {company.contact_email_kind === 'colleague'
                    ? `This is ${company.contact_email_name || 'someone'} who works at ${company.name}, not the founder. The email is addressed to them.`
                    : `This is the company's shared enquiries inbox. The email is addressed to ${company.contact_name || 'the founder'} so whoever reads it can pass it on.`}
                  {company.contact_email_source ? ` (${company.contact_email_source})` : ''}
                </p>
              )}
              <div className="form-row">
                <label>Subject</label>
                <input type="text" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
              </div>
              <div className="form-row">
                <label>Body</label>
                <textarea rows={12} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
              </div>
              <div className="form-row from-row">
                <span className="from-label">From: {draft.from || api.from} · Full signature (name, title, logo, disclaimer) is added automatically on send</span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="modal-footer">
          {sent ? (
            <>
              {inQueue && nextActionable(index!, 1) >= 0 && (
                <button className="outreach-cancel-btn" onClick={() => onIndexChange!(nextActionable(index!, 1))}>Next now (→)</button>
              )}
              <button className="modal-ok-btn" onClick={onClose}>Done</button>
            </>
          ) : draft && !loading ? (
            <>
              <button className="outreach-cancel-btn" onClick={onClose}>{inQueue ? 'Close' : 'Cancel'}</button>
              {inQueue && nextActionable(index!, 1) >= 0 && (
                <button className="outreach-cancel-btn" onClick={() => onIndexChange!(nextActionable(index!, 1))} title="Leave this one and move on (→)">Skip</button>
              )}
              <button className="outreach-copy-btn" onClick={handleCopy}>Copy Draft</button>
              {/* Regeneration is a first-email concept: the follow-up is a fixed
                  template and compose is deliberately blank. */}
              {mode === 'outreach' && (
                <button className="outreach-copy-btn" onClick={() => generateDraft(company)} disabled={sending}
                  title="Discard this draft and generate a fresh one (uses AI credits)">
                  Generate New Draft
                </button>
              )}
              <button className="outreach-send-btn" onClick={handleSend} disabled={!draft.to || sending}
                title={!draft.to ? 'No recipient email — type one above or run SmartFill to find contacts' : ''}>
                {sending ? 'Sending…' : inQueue ? 'Send (⌘↵)' : 'Send Email'}
              </button>
            </>
          ) : null}
        </div>
      </div>

      <style jsx>{`
        .modal-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.3); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: #fff; border-radius: 12px; max-width: 90vw; max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,0.12); overflow: hidden; }
        .outreach-modal { width: 640px; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 1.5rem; border-bottom: 1px solid #e2e8f0; }
        .modal-header h3 { font-size: 1rem; font-weight: 800; color: #0f172a; margin: 0; }
        .modal-close { background: none; border: none; font-size: 1.4rem; color: #94a3b8; cursor: pointer; }
        .om-nav { display: flex; align-items: center; gap: 0.35rem; }
        .om-card { margin-bottom: 1rem; }
        .om-arrow { background: #f1f5f9; border: 1px solid #e2e8f0; color: #0f172a; width: 28px; height: 28px; border-radius: 6px; font-size: 1.1rem; line-height: 1; cursor: pointer; }
        .om-arrow:hover:not(:disabled) { background: #e2e8f0; }
        .om-arrow:disabled { opacity: 0.35; cursor: default; }
        .om-pos { font-size: 0.74rem; color: #64748b; font-weight: 700; min-width: 5.5rem; text-align: center; }
        .modal-body { padding: 1.5rem; overflow-y: auto; flex: 1; }
        .modal-footer { padding: 1rem 1.5rem; display: flex; justify-content: flex-end; gap: 0.5rem; border-top: 1px solid #f1f5f9; }
        .modal-ok-btn { background: #2563eb; color: white; border: none; padding: 0.5rem 1.5rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem; cursor: pointer; }
        .modal-ok-btn:hover { opacity: 0.9; }
        .spinner { width: 36px; height: 36px; border: 3px solid #e2e8f0; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .outreach-loading { text-align: center; padding: 2.5rem 1.5rem; }
        .outreach-loading p { color: #64748b; margin-top: 0.75rem; font-size: 0.95rem; }
        .outreach-loading .loading-sub { font-size: 0.82rem; color: #94a3b8; margin-top: 0.15rem; }
        .outreach-sent { text-align: center; padding: 1.5rem; }
        .sent-icon { font-size: 2.5rem; color: #16a34a; margin-bottom: 0.75rem; }
        .outreach-sent h4 { font-size: 1.15rem; color: #0f172a; margin-bottom: 0.4rem; }
        .outreach-sent p { color: #475569; font-size: 0.9rem; }
        .outreach-sent .sent-sub { font-size: 0.82rem; color: #94a3b8; margin-top: 0.4rem; }
        .outreach-form { display: flex; flex-direction: column; gap: 0.85rem; }
        .form-row { display: flex; flex-direction: column; gap: 0.25rem; }
        .form-row label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-weight: 700; }
        .form-row input { padding: 0.5rem 0.65rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.88rem; color: #0f172a; background: #f8fafc; }
        .form-row textarea { padding: 0.65rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.88rem; color: #0f172a; background: #f8fafc; resize: vertical; line-height: 1.6; font-family: inherit; }
        .form-row input:focus, .form-row textarea:focus { outline: none; border-color: #2563eb; background: #fff; }
        .from-row { flex-direction: row; }
        .from-label { font-size: 0.78rem; color: #94a3b8; font-style: italic; }
        .outreach-cancel-btn { background: transparent; border: 1px solid #e2e8f0; color: #64748b; padding: 0.5rem 1.25rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
        .outreach-copy-btn { background: transparent; border: 1px solid #2563eb; color: #2563eb; padding: 0.5rem 1.25rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
        .outreach-copy-btn:hover { background: #eff6ff; }
        .outreach-send-btn { background: #d97706; color: white; border: none; padding: 0.5rem 1.5rem; border-radius: 6px; font-weight: 800; font-size: 0.85rem; cursor: pointer; }
        .outreach-send-btn:hover:not(:disabled) { opacity: 0.9; }
        .outreach-send-btn:disabled { opacity: 0.4; }
      `}</style>
    </div>
  );
}
