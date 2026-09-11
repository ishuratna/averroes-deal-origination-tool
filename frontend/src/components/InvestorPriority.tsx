'use client';

// The co-investment priority, rendered ONE way everywhere (Universe table,
// Pipeline cards, the investor card): a tier chip with the score, and on the
// card the full breakdown from priority_details (each component's score,
// weight and the rule that produced it). Nothing is computed here; the
// backend's ai/lp_priority.py is the one definition.

import { useState } from 'react';
import { Investor, NETWORK_TAG_SUGGESTIONS, parseTags } from '@/types';
import { dealApi } from '@/services/api';

export function PriorityChip({ inv, compact = false }: { inv: Investor; compact?: boolean }) {
  const tier = inv.priority_tier;
  if (!tier) return <span className="pri-chip none" title="Not yet prioritised. InvestorFill, a tag or an upload computes it.">—</span>;
  const label = compact ? tier : `${tier}${inv.priority_score != null ? ` · ${Math.round(inv.priority_score)}` : ''}`;
  return (
    <span className={`pri-chip t-${tier.toLowerCase()}`}
          title={tier === 'A' ? 'Tier A: fits the co-investment lens and is contactable. Reach out.'
               : tier === 'B' ? 'Tier B: fits but not contactable yet (run InvestorFill), or a partial fit.'
               : tier === 'C' ? 'Tier C: weak fit on current evidence.'
               : 'Parked (Passed / Talk Later).'}>{label}</span>
  );
}

export function TagChips({ inv }: { inv: Investor }) {
  const tags = parseTags(inv.network_tags);
  if (!tags.length) return null;
  return <span className="tag-chips">{tags.map(t => <span key={t} className={`tag-chip ${t.toLowerCase() === 'gcc' ? 'gcc' : ''}`}>{t}</span>)}</span>;
}

export function PriorityBreakdown({ inv }: { inv: Investor }) {
  let d: any = null;
  try { d = inv.priority_details ? JSON.parse(inv.priority_details) : null; } catch { d = null; }
  if (!d) return <p className="cp-empty">Not prioritised yet. Run InvestorFill, add a tag, or press Recompute.</p>;
  const rows = ['coinvest', 'ticket', 'size', 'geography', 'affinity', 'recency', 'readiness'];
  const labels: Record<string, string> = { coinvest: 'Co-invest appetite', ticket: 'Ticket fit (£200K–10M)', size: 'Size (smaller is better)',
    affinity: 'Software affinity', geography: 'Home geography', recency: 'Recency', readiness: 'Readiness (contact)' };
  const sg = d.size_gate;
  return (
    <div className="pri-break">
      {sg && sg.pass === false && (
        <div className="pri-row boost" style={{ color: 'var(--danger, #b42318)' }}>
          <span className="k">Size layer</span>
          <span className="bar" />
          <span className="v">cap 25</span>
          <span className="why">Too big for our cheque, so tier C whatever else is true{sg.capped_from != null ? ` (would otherwise score ${Math.round(sg.capped_from)})` : ''}. {sg.why}</span>
        </div>
      )}
      {rows.map(k => d[k] && (
        <div className="pri-row" key={k}>
          <span className="k">{labels[k]}<small> ×{d[k].weight}</small></span>
          <span className="bar"><i style={{ width: `${Math.round((d[k].score || 0) * 100)}%` }} /></span>
          <span className="v">{Math.round((d[k].score || 0) * 100)}</span>
          <span className="why">{d[k].why}</span>
        </div>
      ))}
      <div className="pri-row boost">
        <span className="k">Warm path</span>
        <span className="bar" />
        <span className="v">+{d.warm_boost?.points ?? 0}</span>
        <span className="why">{(d.warm_boost?.why || []).join(' · ')}</span>
      </div>
    </div>
  );
}

export function TagEditor({ inv, onChanged }: { inv: Investor; onChanged: () => void | Promise<void> }) {
  const [tags, setTags] = useState<string[]>(parseTags(inv.network_tags));
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (next: string[]) => {
    setBusy(true);
    try { await dealApi.setInvestorTags(inv.name, next); setTags(next); await onChanged(); }
    catch (e: any) { alert(e?.message || 'Tag update failed'); }
    finally { setBusy(false); }
  };
  const add = (t: string) => { const v = t.trim(); if (!v || tags.some(x => x.toLowerCase() === v.toLowerCase())) return; save([...tags, v]); setDraft(''); };
  return (
    <div className="tag-editor">
      <div className="tag-chips">
        {tags.map(t => (
          <span key={t} className={`tag-chip ${t.toLowerCase() === 'gcc' ? 'gcc' : ''}`}>
            {t}<button disabled={busy} onClick={() => save(tags.filter(x => x !== t))} title="Remove">×</button>
          </span>
        ))}
        {tags.length === 0 && <span className="cp-empty" style={{ margin: 0 }}>No warm-path tags.</span>}
      </div>
      <div className="tag-add">
        <input value={draft} placeholder="Add tag (GCC, Bea, Partner…)" disabled={busy}
               onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add(draft); }} />
        {NETWORK_TAG_SUGGESTIONS.filter(sg => !tags.some(x => x.toLowerCase() === sg.toLowerCase())).map(sg => (
          <button key={sg} className="tag-suggest" disabled={busy} onClick={() => add(sg)}>+ {sg}</button>
        ))}
      </div>
    </div>
  );
}
