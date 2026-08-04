'use client';

// ONE sidebar for every page (doctrine: same intent, same component).
// Grouped by workflow: Deals and Investors each get their Pipeline +
// Master Universe pair; Intelligence holds the cross-cutting tools.
// Canonical styles live in globals.css (page-scoped styled-jsx cannot
// style an imported component's elements).

import Link from 'next/link';

export type NavKey =
  | 'deal-pipeline' | 'deal-universe'
  | 'investor-pipeline' | 'investor-universe'
  | 'chat' | 'analytics' | 'quick-tools';

const GROUPS: { label: string; items: { key: NavKey; href: string; label: string; icon: React.ReactNode }[] }[] = [
  {
    label: 'Deals',
    items: [
      {
        key: 'deal-pipeline', href: '/', label: 'Pipeline',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 2h3v12H2zM6.5 2h3v8h-3zM11 2h3v10h-3z" fill="currentColor" opacity="0.7"/></svg>,
      },
      {
        key: 'deal-universe', href: '/universe', label: 'Master Universe',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 8h12M8 2c-2 2-2 10 0 12M8 2c2 2 2 10 0 12" stroke="currentColor" strokeWidth="1" fill="none"/></svg>,
      },
    ],
  },
  {
    label: 'Investors',
    items: [
      {
        key: 'investor-pipeline', href: '/investors/pipeline', label: 'Pipeline',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 2h3v12H2zM6.5 2h3v8h-3zM11 2h3v10h-3z" fill="currentColor" opacity="0.7"/></svg>,
      },
      {
        key: 'investor-universe', href: '/investors', label: 'Master Universe',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>,
      },
    ],
  },
  {
    label: 'Quick Tools',
    items: [
      {
        key: 'quick-tools', href: '/tools', label: 'Company Deep Research',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><path d="M7 4.5v5M4.5 7h5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>,
      },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      {
        key: 'chat', href: '/chat', label: 'Intelligence Chat',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3.5C2 2.7 2.7 2 3.5 2h9c.8 0 1.5.7 1.5 1.5v6c0 .8-.7 1.5-1.5 1.5H8l-3.5 3v-3h-1C2.7 11 2 10.3 2 9.5v-6z" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>,
      },
      {
        key: 'analytics', href: '/analytics', label: 'Analytics',
        icon: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 13.5h12M4 11V7m4 4V4m4 7V6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
      },
    ],
  },
];

export default function SideNav({ active }: { active: NavKey }) {
  return (
    <aside className="sidebar">
      <div className="logo-section">
        <div className="logo">AVERROES<span>INTEL</span></div>
      </div>
      <nav className="sidebar-nav">
        {GROUPS.map(g => (
          <div className="nav-group" key={g.label}>
            <span className="group-label">{g.label}</span>
            {g.items.map(it => (
              <Link href={it.href} key={it.key} className={`nav-item ${active === it.key ? 'active' : ''}`}>
                {it.icon}
                {it.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="user-profile">
          <div className="avatar">IR</div>
          <div className="user-info">
            <span className="user-name">Ishu Ratna</span>
            <span className="user-role">Associate</span>
          </div>
          <button className="sign-out-btn" title="Sign out" onClick={() => {
            localStorage.removeItem('averroes_id_token');
            sessionStorage.removeItem('averroes_auth_on');
            window.location.reload();
          }}>Sign out</button>
        </div>
      </div>
    </aside>
  );
}
