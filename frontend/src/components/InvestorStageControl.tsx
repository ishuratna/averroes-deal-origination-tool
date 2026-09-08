'use client';

// ONE stage control for investors, used by the Investor Universe table AND
// the Investor Pipeline board (doctrine: same intent, same logic). A move to
// a parked stage (Passed / Talk Later) asks for the reason bucket - the same
// PARK_REASONS list companies use - and the backend refuses the move without
// one. Unparking clears the reason server-side.

import { useState } from 'react';
import { dealApi } from '@/services/api';
import { INVESTOR_STAGES, INVESTOR_PARKED, PARK_REASONS } from '@/types';

export const INVESTOR_STAGE_COLORS: Record<string, string> = {
  Identified: '#64748b', Researched: '#2563eb', Contacted: '#8b5cf6', Responded: '#0ea5e9',
  Meeting: '#f59e0b', Committed: '#16a34a', Passed: '#dc2626', 'Talk Later': '#a16207',
};

export default function InvestorStageControl({
  name, status, onChanged, className = 'stage-select',
}: {
  name: string;
  status?: string;
  onChanged: (newStatus: string) => void | Promise<void>;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);   // parked stage awaiting a reason
  const [reason, setReason] = useState('');
  const [detail, setDetail] = useState('');

  const apply = async (target: string, why?: string, whyDetail?: string) => {
    setBusy(true);
    try {
      await dealApi.updateInvestorStatus(name, target, why, whyDetail);
      await onChanged(target);
      setPending(null); setReason(''); setDetail('');
    } catch (e: any) { alert(e?.message || 'Status update failed'); }
    finally { setBusy(false); }
  };

  const current = status || 'Identified';
  return (
    <>
      <select
        className={className}
        style={{ color: INVESTOR_STAGE_COLORS[current] || '#64748b' }}
        value={current}
        disabled={busy}
        onChange={e => {
          const target = e.target.value;
          if (target === current) return;
          if ((INVESTOR_PARKED as readonly string[]).includes(target)) { setPending(target); return; }
          apply(target);
        }}
      >
        {INVESTOR_STAGES.map(s => <option key={s} value={s}>{s}</option>)}
      </select>

      {pending && (
        <div className="isc-overlay" onClick={() => !busy && setPending(null)}>
          <div className="isc-modal" onClick={e => e.stopPropagation()}>
            <h3>{pending}: {name}</h3>
            <p>Why? Pick the bucket (required); add detail if it helps the next person.</p>
            <div className="isc-grid">
              {PARK_REASONS.map(r => (
                <button key={r.bucket} className={`isc-chip${reason === r.bucket ? ' on' : ''}`}
                        title={r.description} onClick={() => setReason(r.bucket)}>{r.bucket}</button>
              ))}
            </div>
            <input className="isc-input" placeholder="Optional detail (e.g. 'no first-time funds until 2027')"
                   value={detail} onChange={e => setDetail(e.target.value)} />
            <div className="isc-foot">
              <button className="isc-btn" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
              <button className="isc-btn primary" disabled={busy || !reason}
                      onClick={() => apply(pending, reason, detail.trim())}>
                {busy ? 'Saving…' : `Move to ${pending}`}
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .isc-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.35); z-index: 1100; display: flex; align-items: center; justify-content: center; }
        .isc-modal { background: #fff; border-radius: 12px; width: min(560px, 92vw); padding: 1.2rem 1.4rem; box-shadow: 0 20px 60px rgba(0,0,0,0.15); }
        .isc-modal h3 { margin: 0 0 0.3rem; font-size: 0.98rem; font-weight: 800; color: #0f172a; }
        .isc-modal p { margin: 0 0 0.8rem; font-size: 0.8rem; color: #64748b; }
        .isc-grid { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.8rem; }
        .isc-chip { border: 1px solid #e2e8f0; background: #f8fafc; color: #334155; border-radius: 999px; padding: 0.3rem 0.7rem; font-size: 0.76rem; font-weight: 600; cursor: pointer; }
        .isc-chip.on { background: #0f172a; color: #fff; border-color: #0f172a; }
        .isc-input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.84rem; background: #f8fafc; }
        .isc-foot { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.9rem; }
        .isc-btn { border: 1px solid #e2e8f0; background: #fff; color: #64748b; border-radius: 6px; padding: 0.45rem 1rem; font-weight: 700; font-size: 0.8rem; cursor: pointer; }
        .isc-btn.primary { background: #0f172a; color: #fff; border-color: #0f172a; }
        .isc-btn:disabled { opacity: 0.5; cursor: default; }
      `}</style>
    </>
  );
}
