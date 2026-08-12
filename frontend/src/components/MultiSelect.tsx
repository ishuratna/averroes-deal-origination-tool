'use client';

// One multi-select for every filter in the app (doctrine: same intent, same
// component). Semantics: an EMPTY selection means "All" — no filtering — so
// pages can treat `selected.length === 0` as unfiltered without special cases.
// Styles live in globals.css (.ms-*).

import React, { useEffect, useRef, useState } from 'react';

export interface MultiSelectProps {
  label: string;                 // shown when nothing is selected, e.g. "All stages"
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  optionLabel?: (v: string) => string;   // optional display transform
  width?: number;
  className?: string;
}

export default function MultiSelect({ label, options, selected, onChange, optionLabel, width, className }: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, []);

  const show = (v: string) => (optionLabel ? optionLabel(v) : v);
  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter(x => x !== v) : [...selected, v]);

  const visible = q
    ? options.filter(o => show(o).toLowerCase().includes(q.toLowerCase()))
    : options;

  const summary = selected.length === 0
    ? label
    : selected.length === 1
      ? show(selected[0])
      : `${show(selected[0])} +${selected.length - 1}`;

  return (
    <div className={`ms-wrap ${className || ''}`} ref={boxRef} style={width ? { width } : undefined}>
      <button type="button" className={`ms-trigger ${selected.length ? 'active' : ''}`}
        onClick={() => setOpen(o => !o)} title={selected.length ? selected.map(show).join(', ') : label}>
        <span className="ms-summary">{summary}</span>
        {selected.length > 1 && <span className="ms-count">{selected.length}</span>}
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 3l4 4 4-4" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round"/></svg>
      </button>
      {open && (
        <div className="ms-menu">
          {options.length > 8 && (
            <input className="ms-search" placeholder="Filter…" value={q} autoFocus
              onChange={e => setQ(e.target.value)} />
          )}
          <div className="ms-actions">
            <button type="button" onClick={() => onChange([])}>All / clear</button>
            {visible.length > 1 && (
              <button type="button" onClick={() => onChange(Array.from(new Set([...selected, ...visible])))}>Select shown</button>
            )}
          </div>
          <div className="ms-list">
            {visible.length === 0 && <span className="ms-empty">No matches</span>}
            {visible.map(o => (
              <label className="ms-opt" key={o}>
                <input type="checkbox" checked={selected.includes(o)} onChange={() => toggle(o)} />
                <span>{show(o)}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
