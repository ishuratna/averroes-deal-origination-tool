'use client';

// Deal Intelligence Chat — asks questions over the database (companies + LPs).
// Data-only answers; never guesses. When the database can't answer, the
// assistant offers a live web search which runs ONLY when the user presses
// the button (one grounded Gemini call, enforced against the daily budget).

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { dealApi } from '../../services/api';
import AuthGate from '../../components/AuthGate';

interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  webSearchOffer?: boolean;   // assistant couldn't answer — offer the button
  fromWeb?: boolean;          // answer came from a live web search
  question?: string;          // the user question a web-search offer belongs to
}

// Lightweight renderer for the assistant's consultant-style formatting:
// **Header** lines become section headers, "- " lines become bullets,
// inline **bold** becomes <strong>.
function renderInline(text: string, key: number) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return (
    <span key={key}>
      {parts.map((p, i) => (i % 2 === 1 ? <strong key={i}>{p}</strong> : p))}
    </span>
  );
}

function MessageBody({ content }: { content: string }) {
  const lines = content.split('\n');
  const out: React.ReactNode[] = [];
  let bullets: string[] = [];
  const flushBullets = (key: string) => {
    if (!bullets.length) return;
    out.push(
      <ul className="chat-ul" key={key}>
        {bullets.map((b, i) => <li key={i}>{renderInline(b, i)}</li>)}
      </ul>
    );
    bullets = [];
  };
  lines.forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    const bullet = line.match(/^[-•]\s+(.*)$/);
    if (bullet) { bullets.push(bullet[1]); return; }
    flushBullets(`ul-${i}`);
    const header = line.match(/^\*\*(.+?)\*\*:?\s*$/);
    if (header) {
      out.push(<p className="chat-h" key={i}>{header[1]}</p>);
    } else {
      out.push(<p key={i}>{renderInline(line, i)}</p>);
    }
  });
  flushBullets('ul-end');
  return <>{out}</>;
}

const SUGGESTIONS = [
  'Which companies are in the Target Band with a fit score above 70?',
  'What do we know about the ownership of our qualified companies?',
  'Who are our highest-fit LPs open to first-time funds?',
  'Which companies replied to outreach and what did they say?',
];

export default function ChatPage() {
  return <AuthGate><ChatInner /></AuthGate>;
}

function ChatInner() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const [searching, setSearching] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, thinking]);

  const historyFor = (msgs: ChatMsg[]) =>
    msgs.map(m => ({ role: m.role, content: m.content }));

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || thinking) return;
    setInput('');
    const withUser: ChatMsg[] = [...messages, { role: 'user', content: q }];
    setMessages(withUser);
    setThinking(true);
    try {
      const res = await dealApi.chat(q, historyFor(messages));
      setMessages([...withUser, {
        role: 'assistant',
        content: res.reply,
        webSearchOffer: !!res.needs_web_search,
        question: q,
      }]);
    } catch (e: any) {
      setMessages([...withUser, { role: 'assistant', content: `Something went wrong: ${e.message}` }]);
    } finally { setThinking(false); }
  };

  const runWebSearch = async (question: string, idx: number) => {
    if (searching) return;
    setSearching(true);
    try {
      const res = await dealApi.chat(question, historyFor(messages.slice(0, idx)), true);
      setMessages(prev => {
        const next = [...prev];
        next[idx] = { ...next[idx], webSearchOffer: false };  // offer consumed
        return [...next, { role: 'assistant', content: res.reply, fromWeb: true }];
      });
    } catch (e: any) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Web search failed: ${e.message}` }]);
    } finally { setSearching(false); }
  };

  return (
    <div className="layout-wrapper">
      <aside className="sidebar">
        <div className="logo-section"><div className="logo">AVERROES<span>INTEL</span></div></div>
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item">Deal Pipeline</Link>
            <Link href="/universe" className="nav-item">Master Universe</Link>
            <Link href="/investors" className="nav-item">Investors (LPs)</Link>
            <Link href="/chat" className="nav-item active">Intelligence Chat</Link>
          </div>
        </nav>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>Intelligence Chat</h1>
            <p className="subtitle">Ask anything about your companies and LPs — answers come strictly from your database, never guessed</p>
          </div>
        </header>

        <section className="chat-shell">
          <div className="chat-scroll">
            {messages.length === 0 && (
              <div className="chat-empty">
                <p className="empty-title">What would you like to know?</p>
                <p className="empty-sub">I answer from the database only. If I don&apos;t have something, I&apos;ll say so and offer a live web search — I never guess.</p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={i} className="suggestion" onClick={() => ask(s)}>{s}</button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`msg-row ${m.role}`}>
                <div className={`bubble ${m.role} ${m.fromWeb ? 'web' : ''}`}>
                  {m.fromWeb && <span className="web-tag">🔍 Live web search</span>}
                  <MessageBody content={m.content} />
                  {m.webSearchOffer && (
                    <button className="web-search-btn" disabled={searching}
                      onClick={() => runWebSearch(m.question || '', i)}>
                      {searching ? 'Searching the web…' : '🔍 Run web search'}
                    </button>
                  )}
                </div>
              </div>
            ))}

            {thinking && (
              <div className="msg-row assistant">
                <div className="bubble assistant thinking">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="chat-input-row">
            <input
              type="text"
              placeholder="Ask about any company or LP in your database…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') ask(input); }}
              disabled={thinking}
            />
            <button className="send-btn" onClick={() => ask(input)} disabled={thinking || !input.trim()}>
              {thinking ? '…' : 'Ask'}
            </button>
          </div>
        </section>
      </main>

      <style jsx>{`
        .layout-wrapper { display: flex; min-height: 100vh; background: #f8fafc; }
        .sidebar { width: 260px; background: #fff; border-right: 1px solid #e2e8f0; position: fixed; height: 100vh; z-index: 100; }
        .logo-section { padding: 1.5rem 1.25rem; border-bottom: 1px solid #e2e8f0; }
        .logo { font-weight: 800; font-size: 1rem; letter-spacing: 0.05em; color: #0f172a; }
        .logo span { color: #2563eb; }
        .sidebar-nav { padding: 1.25rem 0.75rem; }
        .nav-group { display: flex; flex-direction: column; gap: 0.25rem; }
        .group-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.15em; color: #94a3b8; padding-left: 0.75rem; margin-bottom: 0.5rem; font-weight: 700; }
        .sidebar-nav :global(.nav-item) { padding: 0.6rem 0.75rem; border-radius: 8px; color: #475569; font-size: 0.85rem; font-weight: 600; text-decoration: none; }
        .sidebar-nav :global(.nav-item:hover) { background: #f1f5f9; }
        .sidebar-nav :global(.nav-item.active) { color: #2563eb; background: #eff6ff; }

        .main-content { flex: 1; margin-left: 260px; padding: 1.75rem 2rem; max-width: calc(100vw - 260px); min-width: 0; display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; }
        .page-header { margin-bottom: 1.25rem; }
        .page-header h1 { font-size: 1.45rem; font-weight: 800; color: #0f172a; margin: 0 0 0.2rem; }
        .subtitle { font-size: 0.85rem; color: #94a3b8; margin: 0; }

        .chat-shell {
          flex: 1; display: flex; flex-direction: column; min-height: 0;
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          box-shadow: 0 1px 3px rgba(15,23,42,0.04); overflow: hidden;
        }
        .chat-scroll { flex: 1; overflow-y: auto; padding: 1.5rem; display: flex; flex-direction: column; gap: 0.85rem; }

        .chat-empty { text-align: center; margin: auto; max-width: 560px; }
        .empty-title { font-size: 1.05rem; font-weight: 800; color: #0f172a; margin: 0 0 0.35rem; }
        .empty-sub { font-size: 0.85rem; color: #94a3b8; margin: 0 0 1.25rem; line-height: 1.6; }
        .suggestions { display: flex; flex-direction: column; gap: 0.5rem; }
        .suggestion {
          background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
          padding: 0.65rem 1rem; font-size: 0.82rem; color: #475569; font-weight: 600;
          cursor: pointer; text-align: left; transition: border-color 0.15s, color 0.15s;
        }
        .suggestion:hover { border-color: #2563eb; color: #2563eb; }

        .msg-row { display: flex; }
        .msg-row.user { justify-content: flex-end; }
        .msg-row.assistant { justify-content: flex-start; }
        .bubble {
          max-width: 78%; padding: 0.7rem 0.95rem; border-radius: 14px;
          font-size: 0.87rem; line-height: 1.6;
        }
        .bubble :global(p) { margin: 0 0 0.4rem; }
        .bubble :global(p:last-child) { margin-bottom: 0; }
        .bubble :global(.chat-h) {
          font-size: 0.72rem; font-weight: 800; text-transform: uppercase;
          letter-spacing: 0.07em; color: #2563eb; margin: 0.7rem 0 0.25rem;
        }
        .bubble :global(.chat-h:first-child) { margin-top: 0; }
        .bubble :global(.chat-ul) { margin: 0 0 0.45rem; padding-left: 1.1rem; display: flex; flex-direction: column; gap: 0.2rem; }
        .bubble :global(.chat-ul li) { line-height: 1.55; }
        .bubble :global(.chat-ul li::marker) { color: #2563eb; }
        .bubble :global(strong) { font-weight: 800; }
        .bubble.user { background: #2563eb; color: #fff; border-bottom-right-radius: 4px; }
        .bubble.assistant { background: #f1f5f9; color: #0f172a; border-bottom-left-radius: 4px; }
        .bubble.web { background: #eff6ff; border: 1px solid #bfdbfe; }
        .web-tag { display: block; font-size: 0.66rem; font-weight: 800; color: #2563eb; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.35rem; }

        .web-search-btn {
          display: block; margin-top: 0.6rem;
          background: #fff; border: 1px solid #2563eb; color: #2563eb;
          padding: 0.4rem 0.85rem; border-radius: 8px; font-size: 0.78rem; font-weight: 700; cursor: pointer;
        }
        .web-search-btn:hover:not(:disabled) { background: #eff6ff; }
        .web-search-btn:disabled { opacity: 0.5; cursor: wait; }

        .bubble.thinking { display: flex; gap: 0.3rem; padding: 0.85rem 1rem; }
        .dot { width: 7px; height: 7px; background: #94a3b8; border-radius: 50%; animation: bounce 1s infinite; }
        .dot:nth-child(2) { animation-delay: 0.15s; }
        .dot:nth-child(3) { animation-delay: 0.3s; }
        @keyframes bounce { 0%, 100% { opacity: 0.3; transform: translateY(0); } 50% { opacity: 1; transform: translateY(-3px); } }

        .chat-input-row {
          display: flex; gap: 0.6rem; padding: 0.9rem 1rem;
          border-top: 1px solid #f1f5f9; background: #fff;
        }
        .chat-input-row input {
          flex: 1; padding: 0.7rem 1rem; border: 1.5px solid #e2e8f0; border-radius: 10px;
          font-size: 0.88rem; color: #0f172a; outline: none; background: #f8fafc;
        }
        .chat-input-row input:focus { border-color: #2563eb; background: #fff; }
        .send-btn {
          background: #2563eb; color: #fff; border: none; border-radius: 10px;
          padding: 0.7rem 1.4rem; font-size: 0.85rem; font-weight: 800; cursor: pointer;
        }
        .send-btn:hover:not(:disabled) { background: #1d4ed8; }
        .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
      `}</style>
    </div>
  );
}
