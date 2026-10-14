'use client';

// Single source of truth for the "Sync Calls" action: the same Granola sync
// the 7 AM job runs, on demand. One handler, one endpoint (POST /calls/sync).
// Shown beside Sync Emails on the Pipeline and Responded headers.

import { useState } from 'react';
import { dealApi } from '../services/api';

export default function SyncCallsButton({ onSynced }: { onSynced?: () => void | Promise<void> }) {
  const [syncing, setSyncing] = useState(false);

  return (
    <button
      className="sync-calls-btn"
      disabled={syncing}
      title="Read Granola (last 7 days), attach each meeting to the Responded-or-later company it was about, move a first call from Responded to Meeting"
      onClick={async () => {
        setSyncing(true);
        try {
          const r = await dealApi.syncCallNotes(7);
          if (r.status === 'Unconfigured') { alert(r.message); return; }
          const promoted = (r.promoted || []) as string[];
          alert((r.message || 'Call sync complete.')
            + (promoted.length ? `\nMoved to Meeting: ${promoted.join(', ')}` : '')
            + (r.weak_matches?.length ? `\n${r.weak_matches.length} meeting(s) mention a company only in passing and were left for you to check.` : ''));
          await onSynced?.();
        } catch (e: any) {
          alert(`Call sync failed: ${String(e?.message || e)}`);
        } finally {
          setSyncing(false);
        }
      }}
    >
      {syncing ? 'Syncing…' : '☎ Sync Calls'}

      <style jsx>{`
        .sync-calls-btn {
          display: flex; align-items: center; gap: 0.4rem;
          padding: 0.5rem 0.9rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: #475569;
          cursor: pointer; transition: border-color 0.15s, color 0.15s;
        }
        .sync-calls-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; }
        .sync-calls-btn:disabled { opacity: 0.5; cursor: wait; }
      `}</style>
    </button>
  );
}
