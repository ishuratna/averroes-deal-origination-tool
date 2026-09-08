'use client';

// The investor card: ONE place per LP, opened from the Investor Universe and
// the Investor Pipeline alike (doctrine: same intent, same component). It is
// a VIEW of the investors row plus its email_log thread and connections;
// every action here goes through the same endpoints the pages use
// (OutreachModal entity='investor', InvestorStageControl, addInvestorNote).

import { useEffect, useState } from 'react';
import { dealApi } from '@/services/api';
import { Investor } from '@/types';
import OutreachModal from './OutreachModal';
import InvestorStageControl from './InvestorStageControl';
import { outreachButtonState } from '@/lib/outreach';
import { PriorityChip, PriorityBreakdown, TagEditor } from './InvestorPriority';

interface Email { direction: string; counterparty_email: string; subject: string; snippet: string;
                  classification?: string; summary?: string; sent_at: string; }

// The notes column is the investor audit trail: "[YYYY-MM-DD HH:MM] text\n"
// lines appended by every writer (stage moves, sends, replies, InvestorFill,
// human notes). Parsed here, newest first; never a second store.
function parseNotes(notes?: string): { at: string; text: string }[] {
  if (!notes) return [];
  const out: { at: string; text: string }[] = [];
  for (const line of notes.split('\n')) {
    const m = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*(.*)$/);
    if (m) out.push({ at: m[1], text: m[2] });
    else if (line.trim() && out.length) out[out.length - 1].text += ' ' + line.trim();
    else if (line.trim()) out.push({ at: '', text: line.trim() });
  }
  return out.reverse();
}

const fmtM = (v?: number | null) => (v == null ? null : `$${v >= 1000 ? (v / 1000).toFixed(1) + 'bn' : v.toFixed(0) + 'M'}`);
const fmtDate = (s?: string) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '');

export default function InvestorProfile({ investor, onClose, onChanged }: {
  investor: Investor | null;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [emails, setEmails] = useState<Email[]>([]);
  const [conn, setConn] = useState<any>(null);
  const [outreachOpen, setOutreachOpen] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [saving, setSaving] = useState(false);
  const [filling, setFilling] = useState(false);

  useEffect(() => {
    if (!investor) return;
    setEmails([]); setConn(null);
    dealApi.getInvestorEmails(investor.name).then(r => setEmails(r.emails || [])).catch(() => {});
    dealApi.getInvestorConnections(investor.name).then(setConn).catch(() => setConn({ companies: [], co_investors: [] }));
  }, [investor?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!investor) return null;
  const inv = investor;
  const ob = outreachButtonState(inv);
  const trail = parseNotes(inv.notes);

  const saveNote = async () => {
    if (!noteText.trim()) return;
    setSaving(true);
    try { await dealApi.addInvestorNote(inv.name, noteText.trim()); setNoteText(''); await onChanged(); }
    catch (e: any) { alert(e?.message || 'Note failed'); }
    finally { setSaving(false); }
  };
  const runFill = async () => {
    setFilling(true);
    try { await dealApi.investorFill(inv.name); await onChanged(); }
    catch (e: any) { alert(e?.message || 'InvestorFill failed'); }
    finally { setFilling(false); }
  };

  const fact = (k: string, v?: string | number | null) => (v == null || v === '' ? null :
    <div className="ip-kv" key={k}><span className="k">{k}</span><span className="v">{String(v)}</span></div>);

  return (
    <div className="cp-overlay" onClick={onClose}>
      <div className="cp-shell ip-shell" onClick={e => e.stopPropagation()}>
        <div className="cp-topbar">
          <div className="cp-actions">
            <button className="cp-chip-btn" disabled={filling} onClick={runFill}>{filling ? 'Working…' : 'InvestorFill'}</button>
            <button className={`cp-chip-btn primary ${ob.cls}`} title={ob.title} onClick={() => setOutreachOpen(true)}>{ob.label}</button>
            <InvestorStageControl name={inv.name} status={inv.status} className="ip-stage" onChanged={onChanged} />
          </div>
          <button className="cp-close" onClick={onClose}>✕</button>
        </div>

        <div className="ip-head">
          <h2>{inv.name}</h2>
          <div className="ip-sub">
            {[inv.investor_type, inv.hq_city, inv.hq_country || inv.global_region].filter(Boolean).join(' · ')}
            <PriorityChip inv={inv} />
            {inv.lp_fit_score != null && <span className="ip-fit">LP fit {Math.round(inv.lp_fit_score * 100)}</span>}
            {inv.park_reason && <span className="ip-park" title={inv.park_reason_detail || ''}>Parked: {inv.park_reason}</span>}
          </div>
          {inv.description && <p className="ip-desc">{inv.description}</p>}
        </div>

        <div className="cp-two-col">
          <div>
            <div className="cp-section-title" style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem' }}>
              Priority for the raise
              <button className="cp-chip-btn" style={{ fontSize: '0.68rem' }}
                      onClick={async () => { try { await dealApi.recomputeInvestorPriority(inv.name); await onChanged(); } catch (e: any) { alert(e?.message || 'Recompute failed'); } }}>
                Recompute
              </button>
            </div>
            <div className="cp-card">
              <PriorityBreakdown inv={inv} />
              <div className="cp-section-title" style={{ marginTop: '0.8rem' }}>Warm-path tags</div>
              <TagEditor inv={inv} onChanged={onChanged} />
            </div>

            <div className="cp-section-title">Profile</div>
            <div className="cp-card">
              {fact('Contact', [inv.contact_name, inv.contact_title].filter(Boolean).join(', '))}
              {fact('Email', inv.contact_email)}
              {inv.bounced_email && fact('Bounced address', inv.bounced_email)}
              {fact('AUM', fmtM(inv.aum_m))}
              {fact('Ticket', (inv.ticket_min_m != null || inv.ticket_max_m != null) ? `${fmtM(inv.ticket_min_m) ?? '?'} – ${fmtM(inv.ticket_max_m) ?? '?'}` : null)}
              {fact('PE strategy', inv.strategy_preferences)}
              {fact('Geo mandate', inv.geo_preferences)}
              {fact('First-time funds', inv.open_to_first_time)}
              {fact('PE commitments', inv.num_pe_commitments != null ? `${inv.num_pe_commitments}${inv.total_pe_commitments_m ? ` · ${fmtM(inv.total_pe_commitments_m)}` : ''}` : null)}
              {fact('Website', inv.website)}
              {fact('Source', inv.source)}
            </div>

            <div className="cp-section-title">Outreach</div>
            <div className="cp-card">
              {fact('Drafted', fmtDate(inv.outreach_drafted_at))}
              {fact('First email', fmtDate(inv.contacted_at))}
              {fact('Last email sent', fmtDate(inv.outreach_sent_at))}
              {fact('Last reply', inv.last_reply_at ? `${fmtDate(inv.last_reply_at)}${inv.reply_classification ? ` · ${inv.reply_classification}` : ''}` : null)}
              {!inv.outreach_drafted_at && !inv.outreach_sent_at && <p className="cp-empty">No outreach yet. The button above drafts the LP introduction.</p>}
            </div>

            <div className="cp-section-title">Connections</div>
            <div className="cp-card">
              {!conn ? <p className="cp-empty">Loading…</p> : (
                <>
                  {(conn.companies || []).length === 0 && <p className="cp-empty">No portfolio overlap with our universe mapped yet.</p>}
                  {(conn.companies || []).map((c: any, i: number) => (
                    <div className="cp-kv" key={i}><span className="k">{c.company_name}</span><span className="v">{c.pct != null ? `${c.pct}% · ` : ''}{String(c.link_type || '').replace(/_/g, ' ')}</span></div>
                  ))}
                  {(conn.co_investors || []).length > 0 && <p className="ip-coinv">Co-investors: {(conn.co_investors || []).slice(0, 8).map((c: any) => c.investor_name).join(', ')}</p>}
                </>
              )}
            </div>
          </div>

          <div>
            <div className="cp-section-title">Emails</div>
            <div className="cp-card">
              {emails.length === 0 && <p className="cp-empty">No emails logged with this investor yet.</p>}
              {emails.map((e, i) => (
                <div className={`ip-mail ${e.direction}`} key={i}>
                  <div className="ip-mail-head">
                    <span className="dir">{e.direction === 'sent' ? '→ Sent' : '← Received'}</span>
                    <span className="who">{e.counterparty_email}</span>
                    <span className="when">{fmtDate(e.sent_at)}</span>
                    {e.classification && <span className="cls">{e.classification}</span>}
                  </div>
                  <div className="ip-mail-subj">{e.subject || '(no subject)'}</div>
                  {(e.summary || e.snippet) && <div className="ip-mail-snip">{e.summary || e.snippet}</div>}
                </div>
              ))}
            </div>

            <div className="cp-section-title">Activity Log</div>
            <div className="cp-card">
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.6rem' }}>
                <input value={noteText} onChange={e => setNoteText(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveNote(); }} placeholder="Add a note…"
                  style={{ flex: 1, padding: '0.5rem 0.75rem', border: '1px solid #e2e8f0', borderRadius: 8, fontSize: '0.82rem', background: '#f8fafc' }} />
                <button className="cp-chip-btn" onClick={saveNote} disabled={!noteText.trim() || saving}>Add</button>
              </div>
              {trail.length === 0 && <p className="cp-empty">No activity recorded yet.</p>}
              {trail.map((t, i) => (
                <div className="cp-feed-item" key={i}>
                  <span className="ip-feed-at">{t.at}</span>
                  <span className="ip-feed-text">{t.text}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {outreachOpen && (
          <div onClick={e => e.stopPropagation()}>
            <OutreachModal entity="investor" company={inv} onClose={() => setOutreachOpen(false)} onSent={onChanged} />
          </div>
        )}
      </div>
    </div>
  );
}
