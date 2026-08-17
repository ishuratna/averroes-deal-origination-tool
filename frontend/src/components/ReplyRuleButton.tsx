'use client';

// THE REPLY RULE, as one button and one modal.
//
//   Qualified = qualified from the Master Universe, no outreach sent yet
//   Contacted = we emailed them, no genuine reply has come back yet
//   Responded = we emailed them AND they genuinely replied
//
// An out-of-office autoresponder is not a reply: it returns the company to
// Contacted and only defers the follow-up reminder.
//
// Single source of truth for this action, used on the Pipeline AND Responded
// headers. One handler, one endpoint, identical behaviour, exactly like
// SyncEmailsButton. Never fork this per page.
//
// Always PREVIEWS first. Automatic moves (a real reply exists, or email-sync
// made the move it is now reversing) are applied on confirm. Anything a PERSON
// moved into Responded is listed separately and needs an explicit answer per
// company, because they may know the founder rang instead of writing:
//
//   Move it back  -> passed to the backend in `confirm`, demoted to Contacted
//   Keep it       -> pinned via reply-exempt, and never asked about again

import { useState } from 'react';
import { dealApi } from '../services/api';
import { ReplyRuleResult } from '../types';

export default function ReplyRuleButton({ onDone }: { onDone?: () => void | Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ReplyRuleResult | null>(null);
  // Per-company answers for the ambiguous rows. Absent = not yet answered.
  const [answers, setAnswers] = useState<Record<string, 'move' | 'keep'>>({});
  const [error, setError] = useState('');

  const ask = async () => {
    setBusy(true);
    setError('');
    try {
      const r = await dealApi.reconcileReplyRule(false);
      setAnswers({});
      setPreview(r);
    } catch (e: any) {
      setError(e.message || 'Could not check the reply rule');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    setError('');
    try {
      // "Keep it" is written FIRST. The exemption is what stops the reconcile
      // pass from selecting the company at all, so writing it after would race
      // the demotion and could move a company the user chose to keep.
      const keep = preview.needs_confirmation
        .filter(m => answers[m.name] === 'keep').map(m => m.name);
      for (const name of keep) await dealApi.setReplyExempt(name, true);

      const move = preview.needs_confirmation
        .filter(m => answers[m.name] === 'move').map(m => m.name);
      const r = await dealApi.reconcileReplyRule(true, move);

      setPreview(null);
      setAnswers({});
      await onDone?.();
      alert(r.message || 'Stages reconciled.');
    } catch (e: any) {
      setError(e.message || 'Could not apply the reply rule');
    } finally {
      setBusy(false);
    }
  };

  const ambiguous = preview?.needs_confirmation || [];
  const unanswered = ambiguous.filter(m => !answers[m.name]).length;
  const nothingToDo = preview
    && !preview.promote.length && !preview.demote.length && !ambiguous.length;

  return (
    <>
      <button
        className="rr-btn"
        disabled={busy}
        title="Check that every company's stage matches the reply rule: Contacted = emailed with no reply yet, Responded = they replied"
        onClick={ask}
      >
        {busy && !preview ? 'Checking…' : '⚖ Check stages'}
      </button>

      {preview && (
        <div className="rr-overlay" onClick={e => { if (e.target === e.currentTarget) setPreview(null); }}>
          <div className="rr-modal">
            <header>
              <div>
                <h3>Stage check</h3>
                <p className="rule">
                  <b>Contacted</b> = we emailed them, no reply yet.
                  <b> Responded</b> = they replied. An out-of-office is not a reply.
                </p>
              </div>
              <button className="x" onClick={() => setPreview(null)} aria-label="Close">×</button>
            </header>

            <div className="rr-body">
              {error && <div className="rr-error">{error}</div>}

              {nothingToDo && (
                <p className="ok">Every stage already matches the rule. Nothing to change.</p>
              )}

              {!!preview.promote.length && (
                <section>
                  <h4>Move forward to Responded <span>{preview.promote.length}</span></h4>
                  <p className="why">A genuine reply is on record but the stage never caught up.</p>
                  <ul>
                    {preview.promote.map(m => (
                      <li key={m.name}><b>{m.name}</b><span className="meta">{m.reason}</span></li>
                    ))}
                  </ul>
                </section>
              )}

              {!!preview.demote.length && (
                <section>
                  <h4>Move back <span>{preview.demote.length}</span></h4>
                  <p className="why">
                    No genuine reply on record, and the move into Responded was made
                    automatically. Corrected without asking.
                  </p>
                  <ul>
                    {preview.demote.map(m => (
                      <li key={m.name}>
                        <b>{m.name}</b><span className="meta">{m.from} → {m.to}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {!!ambiguous.length && (
                <section className="ask">
                  <h4>Needs your answer <span>{ambiguous.length}</span></h4>
                  <p className="why">
                    These sit in Responded with no reply from them in the email log.
                    If they replied by phone, or from an address we do not track, keep
                    them and the rule will not ask again.
                  </p>
                  {/* Answering one at a time is right when the cases differ, but a
                      value-rename migration mislabels whole batches identically, so
                      the honest answer is often the same for every row. */}
                  {ambiguous.length > 1 && (
                    <div className="bulk">
                      <span>Same answer for all {ambiguous.length}?</span>
                      <button onClick={() => setAnswers(Object.fromEntries(
                        ambiguous.map(m => [m.name, 'move' as const])))}>
                        Move all back
                      </button>
                      <button className="keep" onClick={() => setAnswers(Object.fromEntries(
                        ambiguous.map(m => [m.name, 'keep' as const])))}>
                        Keep all
                      </button>
                      <button className="plain" onClick={() => setAnswers({})}>Clear</button>
                    </div>
                  )}
                  <ul>
                    {ambiguous.map(m => (
                      <li key={m.name} className="row">
                        <div className="who">
                          <b>{m.name}</b>
                          <span className="meta">moved by {m.moved_by}</span>
                        </div>
                        <div className="choices">
                          <button
                            className={answers[m.name] === 'move' ? 'sel' : ''}
                            onClick={() => setAnswers(a => ({ ...a, [m.name]: 'move' }))}
                          >Move to {m.to}</button>
                          <button
                            className={answers[m.name] === 'keep' ? 'sel keep' : 'keep'}
                            onClick={() => setAnswers(a => ({ ...a, [m.name]: 'keep' }))}
                          >Keep in Responded</button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>

            <footer>
              <span className="pending">
                {unanswered > 0
                  ? `${unanswered} still unanswered — they will be left exactly as they are.`
                  : ''}
              </span>
              <div>
                <button className="ghost" onClick={() => setPreview(null)} disabled={busy}>Cancel</button>
                <button className="go" onClick={apply} disabled={busy || !!nothingToDo}>
                  {busy ? 'Applying…' : 'Apply'}
                </button>
              </div>
            </footer>
          </div>
        </div>
      )}

      <style jsx>{`
        .rr-btn {
          display: flex; align-items: center; gap: 0.4rem;
          padding: 0.5rem 0.9rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: #475569;
          cursor: pointer; transition: border-color 0.15s, color 0.15s;
        }
        .rr-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; }
        .rr-btn:disabled { opacity: 0.5; cursor: wait; }

        .rr-overlay {
          position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
          display: flex; align-items: center; justify-content: center;
          z-index: 1000; padding: 2rem;
        }
        .rr-modal {
          background: #fff; border-radius: 12px; width: 100%; max-width: 720px;
          max-height: 86vh; display: flex; flex-direction: column;
          box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
        }
        header {
          display: flex; justify-content: space-between; align-items: flex-start;
          gap: 1rem; padding: 1.1rem 1.3rem; border-bottom: 1px solid #eef2f7;
        }
        h3 { margin: 0 0 0.3rem; font-size: 1rem; color: #0f172a; }
        .rule { margin: 0; font-size: 0.76rem; color: #64748b; line-height: 1.5; }
        .rule b { color: #334155; }
        .x {
          background: none; border: none; font-size: 1.4rem; line-height: 1;
          color: #94a3b8; cursor: pointer; padding: 0 0.2rem;
        }
        .rr-body { overflow-y: auto; padding: 1.1rem 1.3rem; }
        .rr-error {
          background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c;
          padding: 0.6rem 0.8rem; border-radius: 8px; font-size: 0.8rem;
          margin-bottom: 0.9rem;
        }
        .ok { font-size: 0.85rem; color: #059669; margin: 0.4rem 0; }
        section { margin-bottom: 1.4rem; }
        section:last-child { margin-bottom: 0; }
        h4 {
          margin: 0 0 0.3rem; font-size: 0.8rem; text-transform: uppercase;
          letter-spacing: 0.04em; color: #334155;
          display: flex; align-items: center; gap: 0.5rem;
        }
        h4 span {
          background: #f1f5f9; color: #475569; border-radius: 999px;
          padding: 0.1rem 0.5rem; font-size: 0.72rem; letter-spacing: 0;
        }
        .ask h4 span { background: #fef3c7; color: #92400e; }
        .why { margin: 0 0 0.6rem; font-size: 0.76rem; color: #64748b; line-height: 1.5; }
        .bulk {
          display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
          margin-bottom: 0.6rem; font-size: 0.74rem; color: #64748b;
        }
        .bulk span { margin-right: 0.2rem; }
        .bulk button {
          padding: 0.28rem 0.6rem; border: 1px solid #cbd5e1; background: #fff;
          border-radius: 6px; font-size: 0.73rem; font-weight: 700; color: #334155;
          cursor: pointer; font-family: inherit;
        }
        .bulk button:hover { border-color: #94a3b8; background: #f8fafc; }
        .bulk button.keep { color: #047857; border-color: #bcd9c8; }
        .bulk button.plain { font-weight: 500; color: #94a3b8; border-color: #e2e8f0; }
        ul { list-style: none; margin: 0; padding: 0; }
        li {
          padding: 0.5rem 0.7rem; border: 1px solid #eef2f7; border-radius: 8px;
          margin-bottom: 0.35rem; font-size: 0.82rem; color: #0f172a;
          display: flex; align-items: center; gap: 0.6rem;
        }
        li.row { justify-content: space-between; flex-wrap: wrap; }
        .who { display: flex; flex-direction: column; gap: 0.1rem; }
        .meta { font-size: 0.72rem; color: #94a3b8; font-weight: 500; }
        .choices { display: flex; gap: 0.35rem; }
        .choices button {
          padding: 0.32rem 0.6rem; border: 1px solid #e2e8f0; background: #fff;
          border-radius: 6px; font-size: 0.74rem; font-weight: 600;
          color: #475569; cursor: pointer;
        }
        .choices button:hover { border-color: #94a3b8; }
        .choices button.sel { background: #1e293b; border-color: #1e293b; color: #fff; }
        .choices button.sel.keep { background: #047857; border-color: #047857; }
        footer {
          display: flex; justify-content: space-between; align-items: center;
          gap: 1rem; padding: 0.9rem 1.3rem; border-top: 1px solid #eef2f7;
        }
        .pending { font-size: 0.74rem; color: #92400e; }
        footer div { display: flex; gap: 0.5rem; margin-left: auto; }
        .ghost {
          padding: 0.5rem 0.9rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 8px; font-size: 0.8rem; font-weight: 600; color: #475569;
          cursor: pointer;
        }
        .go {
          padding: 0.5rem 1.1rem; background: #2563eb; border: 1px solid #2563eb;
          border-radius: 8px; font-size: 0.8rem; font-weight: 700; color: #fff;
          cursor: pointer;
        }
        .go:disabled, .ghost:disabled { opacity: 0.5; cursor: not-allowed; }
      `}</style>
    </>
  );
}
