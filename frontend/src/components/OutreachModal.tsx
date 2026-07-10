'use client';

// Shared founder-outreach modal — used by both the Universe table and the
// Pipeline kanban. Behaviour contract:
//   • A previously saved draft opens instantly for review (no AI regeneration)
//   • "Generate New Draft" explicitly replaces it (uses AI credits)
//   • Only SEND changes the company's stage (backend moves it to Engaged);
//     drafting alone never touches the stage.

import { useState, useEffect, useCallback } from 'react';
import { dealApi } from '@/services/api';

interface Draft { to: string; subject: string; body: string; company: string; }

export default function OutreachModal({
  company,
  onClose,
  onSent,
}: {
  company: any | null;
  onClose: () => void;
  onSent?: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);

  const generateDraft = useCallback(async (c: any) => {
    setLoading(true);
    try {
      const d = await dealApi.draftOutreach(c.name);
      setDraft({
        to: d.to || c.contact_email || '',
        subject: d.subject || '',
        body: d.body || '',
        company: c.name,
      });
    } catch (err: any) {
      alert(`Failed to generate draft: ${err.message}`);
      onClose();
    } finally { setLoading(false); }
  }, [onClose]);

  useEffect(() => {
    if (!company) return;
    setDraft(null);
    setSent(false);
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
    generateDraft(company);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.name]);

  if (!company) return null;

  const handleSend = async () => {
    if (!draft?.to) return;
    setSending(true);
    try {
      await dealApi.sendOutreach(draft.to, draft.subject, draft.body, draft.company);
      setSent(true);
      onSent?.();  // stage bumps to Engaged — let the parent reload
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
          <h3>Outreach — {company.name}</h3>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          {loading ? (
            <div className="outreach-loading">
              <div className="spinner"></div>
              <p>Drafting personalised email with AI...</p>
              <p className="loading-sub">Researching {company.name} to craft the perfect intro</p>
            </div>
          ) : sent ? (
            <div className="outreach-sent">
              <div className="sent-icon">&#10003;</div>
              <h4>Email Sent</h4>
              <p>Sent to <strong>{draft?.to}</strong> from Beatrice Carrara &lt;beatrice@averroescapital.com&gt;.</p>
              <p className="sent-sub">The company&apos;s stage has moved to Engaged.</p>
            </div>
          ) : draft ? (
            <div className="outreach-form">
              <div className="form-row">
                <label>To</label>
                <input type="email" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
              </div>
              <div className="form-row">
                <label>Subject</label>
                <input type="text" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
              </div>
              <div className="form-row">
                <label>Body</label>
                <textarea rows={12} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
              </div>
              <div className="form-row from-row">
                <span className="from-label">From: Beatrice Carrara &lt;beatrice@averroescapital.com&gt; · Full signature (name, title, phone, logo) is added automatically on send</span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="modal-footer">
          {sent ? (
            <button className="modal-ok-btn" onClick={onClose}>Done</button>
          ) : draft && !loading ? (
            <>
              <button className="outreach-cancel-btn" onClick={onClose}>Cancel</button>
              <button className="outreach-copy-btn" onClick={handleCopy}>Copy Draft</button>
              <button className="outreach-copy-btn" onClick={() => generateDraft(company)} disabled={sending}
                title="Discard this draft and generate a fresh one (uses AI credits)">
                Generate New Draft
              </button>
              <button className="outreach-send-btn" onClick={handleSend} disabled={!draft.to || sending}
                title={!draft.to ? 'No recipient email — type one above or run SmartFill to find contacts' : ''}>
                {sending ? 'Sending…' : 'Send Email'}
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
