'use client';

// Single source of truth for the "Sync Emails" action — used on the Universe
// AND Pipeline headers. One handler, one endpoint, identical behaviour.

import { useState } from 'react';
import { dealApi } from '../services/api';

export default function SyncEmailsButton({ onSynced }: { onSynced?: () => void | Promise<void> }) {
  const [syncing, setSyncing] = useState(false);

  return (
    <button
      className="sync-emails-btn"
      disabled={syncing}
      title="Read Beatrice's mailbox (IMAP), log exchanges with known contacts, classify replies, auto-advance stages"
      onClick={async () => {
        setSyncing(true);
        try {
          const r = await dealApi.syncEmails(30);
          alert(r.message || 'Email sync complete.');
          await onSynced?.();
        } catch (e: any) {
          alert(`Email sync failed: ${e.message}`);
        } finally {
          setSyncing(false);
        }
      }}
    >
      {syncing ? 'Syncing…' : '✉ Sync Emails'}

      <style jsx>{`
        .sync-emails-btn {
          display: flex; align-items: center; gap: 0.4rem;
          padding: 0.5rem 0.9rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: #475569;
          cursor: pointer; transition: border-color 0.15s, color 0.15s;
        }
        .sync-emails-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; }
        .sync-emails-btn:disabled { opacity: 0.5; cursor: wait; }
      `}</style>
    </button>
  );
}
