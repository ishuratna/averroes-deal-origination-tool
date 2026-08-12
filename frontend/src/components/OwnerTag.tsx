'use client';

// ONE owner tag for every page (doctrine: same intent, same component).
// Shows who is managing a company. Used on the Responded page, the Master
// Universe table, the Pipeline kanban cards and the company profile, so the
// tag can never look or behave differently depending on where you are.
//
// There is a single `owner` field and it CHANGES HANDS: Ishu holds a company
// through triage, then the tag moves to Issam or Marianna on loop-in (Track B)
// or to Bea (Track A). See docs/Averroes_Deal_Pipeline_Process.pdf.
//
// Canonical styles live in globals.css — page-scoped styled-jsx cannot style
// an imported component's elements.

import { DealOwner, OWNER_ROLES } from '../types';

// Initials keep the tag compact enough for a table cell or a kanban card.
const INITIALS: Record<string, string> = {
  Bea: 'B', Ishu: 'IR', Issam: 'IS', Marianna: 'M',
};

export default function OwnerTag({
  owner,
  size = 'sm',
  showName = true,
}: {
  owner?: string | null;
  size?: 'sm' | 'md';
  showName?: boolean;
}) {
  const name = (owner || '').trim();
  if (!name) {
    return <span className={`owner-tag owner-tag-none owner-tag-${size}`} title="No owner assigned yet">Unassigned</span>;
  }
  const role = OWNER_ROLES[name as DealOwner];
  return (
    <span
      className={`owner-tag owner-tag-${name.toLowerCase()} owner-tag-${size}`}
      title={role ? `${name} — ${role}` : name}
    >
      <span className="owner-tag-dot">{INITIALS[name] || name.slice(0, 2).toUpperCase()}</span>
      {showName && <span className="owner-tag-name">{name}</span>}
    </span>
  );
}
