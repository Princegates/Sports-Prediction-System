import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { clearChatHistory, fetchBranding, fetchChatHistory, streamChatMessage } from "../api";
import { Mascot } from "../components/Mascot";
import { useAuth } from "../lib/AuthContext";
import { formatWhatsapp, whatsappLink } from "../lib/whatsapp";
import type { ChatAnswer, ChatPick, ChatSource } from "../types";

/**
 * The live AI chat dock: a floating assistant available on every signed-in
 * page.
 *
 * Three things it does that a plain chat box doesn't:
 *
 * - **Context awareness.** If the user is on /match/123, that id rides along
 *   with the message, so "why is this favored?" resolves without them having
 *   to re-name the teams.
 * - **Citations.** Answers come back with the match, team or page they were
 *   derived from, rendered as links. A claim you can click through to check is
 *   worth more than a confident tone.
 * - **Streaming.** Replies arrive progressively over SSE rather than landing
 *   as a wall of text.
 *
 * History is server-side and per-account, so a conversation survives a reload
 * and follows the user across devices.
 */

interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: ChatSource[];
  suggestions?: string[];
  picks?: ChatPick[];
  rewritten?: boolean;
  general_chat?: boolean;
  caveat?: string | null;
  streaming?: boolean;
  failed?: boolean;
  /** Set on the synthetic "redeem a code" reply shown to accounts without
   * access -- renders a WhatsApp button rather than a real network answer. */
  whatsapp?: string;
}

const OPENING_SUGGESTIONS = [
  "What are today's best picks?",
  "How accurate is the model?",
  "Compare Arsenal and Chelsea",
  "How does the model work?",
  "What's on this weekend?",
];

const STORAGE_KEY = "chat_dock_open";

function readStoredOpen(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Minimal inline markdown renderer for the assistant's output.
 *
 * The assistant emits a known, small subset -- `**bold**`, `- ` bullets,
 * blank-line paragraphs -- so a full markdown dependency would be several
 * hundred kilobytes to render four constructs. Text is split on the bold
 * delimiter and joined as React nodes rather than being passed to
 * dangerouslySetInnerHTML, so assistant output can never inject markup.
 */
function renderInline(text: string, keyPrefix: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`${keyPrefix}-${i}`}>{part}</span>;
  });
}

function AssistantText({ text }: { text: string }) {
  const blocks: JSX.Element[] = [];
  let bullets: string[] = [];

  const flushBullets = () => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="chat-bullets">
        {bullets.map((b, i) => (
          <li key={i}>{renderInline(b, `b-${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trimEnd();
    if (line.trimStart().startsWith("- ")) {
      bullets.push(line.trimStart().slice(2));
      continue;
    }
    flushBullets();
    if (!line.trim()) continue;
    blocks.push(
      <p key={`p-${blocks.length}`}>{renderInline(line, `p-${blocks.length}`)}</p>,
    );
  }
  flushBullets();

  return <>{blocks}</>;
}

function SourceChips({ sources }: { sources: ChatSource[] }) {
  const navigate = useNavigate();
  if (sources.length === 0) return null;

  function go(source: ChatSource) {
    if (source.ref == null) return;
    if (source.kind === "match") navigate(`/app/match/${source.ref}`);
    else if (source.kind === "team") navigate(`/app/teams/${source.ref}`);
    else if (source.kind === "page") navigate(String(source.ref));
  }

  return (
    <div className="chat-sources">
      <span className="chat-sources-label">From:</span>
      {sources.map((s, i) => (
        <button key={i} className="chat-source-chip" onClick={() => go(s)} title={`Open ${s.label}`}>
          {s.label}
        </button>
      ))}
    </div>
  );
}

/** Hands an answer's picks to AI Generation for real pricing -- Guda only
 * ever ranks by model probability, never a bookmaker price (see the
 * responder's own caveat text), so this is how a picks list actually gets
 * priced rather than staying an estimate. */
function SendToGenerationButton({ picks }: { picks: ChatPick[] }) {
  const navigate = useNavigate();
  if (picks.length === 0) return null;

  return (
    <button
      className="btn ghost"
      style={{ marginTop: 8 }}
      onClick={() => navigate("/app/betcodes", { state: { picks } })}
    >
      Send {picks.length} pick{picks.length === 1 ? "" : "s"} to AI Generation for odds
    </button>
  );
}

export function ChatDock() {
  const { accessStatus } = useAuth();
  const hasAccess = accessStatus?.has_access ?? false;

  const [open, setOpen] = useState(readStoredOpen);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadedHistory, setLoadedHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [whatsapp, setWhatsapp] = useState<string | null>(null);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const location = useLocation();

  useEffect(() => {
    if (hasAccess) return;
    fetchBranding()
      .then((b) => setWhatsapp(b.contact_whatsapp || null))
      .catch(() => {});
  }, [hasAccess]);

  // The match the user is looking at, so follow-up questions have a referent.
  const matchIdMatch = location.pathname.match(/\/match\/(\d+)/);
  const contextMatchId = matchIdMatch ? Number(matchIdMatch[1]) : null;

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
    } catch {
      // storage disabled -- the dock just won't remember being open
    }
  }, [open]);

  // Load the stored conversation the first time the dock is opened, not on
  // mount: an unopened dock shouldn't cost a request on every page load.
  // Skipped entirely without access -- chat history is as gated as chat
  // itself, and there is never anything real to load.
  useEffect(() => {
    if (!open || loadedHistory || !hasAccess) return;
    setLoadedHistory(true);
    fetchChatHistory(40)
      .then((rows) => {
        setTurns(
          rows.map((r) => ({
            id: `stored-${r.id}`,
            role: r.role,
            text: r.content,
            sources: r.sources,
            suggestions: r.suggestions,
            picks: r.picks,
            rewritten: r.rewritten,
            general_chat: r.general_chat,
          })),
        );
      })
      .catch(() => {
        // An empty dock is a fine fallback; the user can still ask.
      });
  }, [open, loadedHistory]);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Cmd/Ctrl-J toggles the dock. Cmd-K is already the team search.
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === "Escape" && open) setOpen(false);
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);

  // Abort any in-flight stream when the dock unmounts, so a navigation away
  // doesn't leave a reader running against a dead component.
  useEffect(() => () => abortRef.current?.(), []);

  const ask = useCallback(
    (question: string) => {
      const text = question.trim();
      if (!text || busy) return;

      setError(null);
      setInput("");

      // Chat is a premium feature (require_active_access on the backend) --
      // this answers locally rather than sending a request that would just
      // 403, so it reads as Guda itself gating the conversation instead of
      // a network error.
      if (!hasAccess) {
        const body = whatsapp
          ? `Guda's full conversational assistant -- match predictions, team comparisons, live analysis, and everything else it can do -- is a premium feature. Redeem an access code to unlock it.\n\nMessage **${formatWhatsapp(whatsapp)}** on WhatsApp to arrange one -- WhatsApp only, no calls or texts.`
          : "Guda's full conversational assistant is a premium feature. Redeem an access code from the Access page to unlock it.";
        setTurns((prev) => [
          ...prev,
          { id: `user-${Date.now()}`, role: "user", text },
          { id: `locked-${Date.now()}`, role: "assistant", text: body, whatsapp: whatsapp ?? undefined },
        ]);
        return;
      }

      setBusy(true);

      const streamId = `stream-${Date.now()}`;
      setTurns((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: "user", text },
        { id: streamId, role: "assistant", text: "", streaming: true },
      ]);

      const patch = (updater: (turn: Turn) => Turn) =>
        setTurns((prev) => prev.map((t) => (t.id === streamId ? updater(t) : t)));

      abortRef.current = streamChatMessage(text, contextMatchId, {
        onChunk: (chunk) => patch((t) => ({ ...t, text: t.text + chunk })),
        onDone: (answer: ChatAnswer) =>
          patch((t) => ({
            ...t,
            text: answer.text,
            sources: answer.sources,
            suggestions: answer.suggestions,
            picks: answer.picks,
            rewritten: answer.rewritten,
            general_chat: answer.general_chat,
            caveat: answer.caveat,
            streaming: false,
          })),
        onError: (message) => {
          patch((t) => ({
            ...t,
            text: t.text || `Couldn't reach the assistant: ${message}`,
            streaming: false,
            failed: !t.text,
          }));
          setError(message);
        },
      });

      // The stream's own completion is what clears `busy`; poll the turn
      // state rather than guessing a duration.
      const release = setInterval(() => {
        setTurns((prev) => {
          const turn = prev.find((t) => t.id === streamId);
          if (turn && !turn.streaming) {
            clearInterval(release);
            setBusy(false);
            abortRef.current = null;
          }
          return prev;
        });
      }, 120);
    },
    [busy, contextMatchId, hasAccess, whatsapp],
  );

  async function handleClear() {
    try {
      await clearChatHistory();
    } catch {
      // Clearing the view is still the right response to the user's intent.
    }
    setTurns([]);
    setError(null);
  }

  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant" && !t.streaming);
  const suggestions = !hasAccess
    ? []
    : turns.length === 0
      ? OPENING_SUGGESTIONS
      : (lastAssistant?.suggestions ?? []).slice(0, 3);

  if (!open) {
    return (
      <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Open Guda">
        <Mascot pose="idle" size={30} />
        <span className="chat-fab-label">Ask Guda</span>
        <kbd>&#8984;J</kbd>
      </button>
    );
  }

  return (
    <div className="chat-dock" role="dialog" aria-label="Guda">
      <header className="chat-dock-head">
        <div className="chat-dock-title">
          <Mascot pose={busy ? "thinking" : "idle"} size={28} />
          <div>
            <strong>Guda</strong>
            <span>
              {!hasAccess
                ? "Premium feature"
                : contextMatchId
                  ? "Reading this match's data"
                  : "Grounded in this system's database"}
            </span>
          </div>
        </div>
        <div className="chat-dock-actions">
          {turns.length > 0 && (
            <button className="btn ghost chat-icon-btn" onClick={handleClear} title="Clear conversation">
              Clear
            </button>
          )}
          <button
            className="btn ghost chat-icon-btn"
            onClick={() => setOpen(false)}
            aria-label="Close assistant"
          >
            ✕
          </button>
        </div>
      </header>

      <div className="chat-thread" ref={threadRef}>
        {turns.length === 0 && !hasAccess && (
          <div className="chat-empty">
            <Mascot pose="sad" size={56} />
            <h4>Guda is a premium feature</h4>
            <p>
              Match predictions, team comparisons, live analysis and everything else Guda can do
              unlocks with a redeemed access code.
            </p>
            {whatsapp && (
              <a
                className="btn"
                href={whatsappLink(whatsapp, "Hi, I'd like an access code for Socca Intelligence.")}
                target="_blank"
                rel="noreferrer noopener"
              >
                Message {formatWhatsapp(whatsapp)} on WhatsApp
              </a>
            )}
          </div>
        )}

        {turns.length === 0 && hasAccess && (
          <div className="chat-empty">
            <Mascot pose="thinking" size={56} />
            <h4>Ask about any fixture</h4>
            <p>
              I'm Guda. I answer from this system's own database — stored predictions, team form,
              backtest results. I report what's there and cite it, so I can't invent a statistic. If I
              don't know something, I'll say so.
            </p>
          </div>
        )}

        {turns.map((turn) => (
          <div key={turn.id} className={`chat-turn ${turn.role}`}>
            {turn.role === "assistant" && (
              <span className="chat-turn-badge">
                {turn.failed ? "Unavailable" : "Guda"}
                {turn.rewritten && (
                  <span
                    className="chat-rewritten-badge"
                    title="Phrasing polished by the configured AI model -- every fact still comes from this system's own data, unchanged"
                  >
                    ✨ phrased by AI
                  </span>
                )}
                {turn.general_chat && (
                  <span
                    className="chat-rewritten-badge"
                    title="Answered directly by the configured AI model, not from this platform's own match data -- ask about a specific team or fixture for a grounded answer"
                  >
                    💬 general AI answer
                  </span>
                )}
              </span>
            )}
            <div className={`chat-bubble ${turn.role}${turn.failed ? " failed" : ""}`}>
              {turn.role === "assistant" ? (
                <>
                  <AssistantText text={turn.text} />
                  {turn.streaming && <span className="chat-caret" aria-hidden />}
                  {!turn.streaming && turn.caveat && (
                    <p className="chat-caveat">{turn.caveat}</p>
                  )}
                  {!turn.streaming && turn.sources && <SourceChips sources={turn.sources} />}
                  {!turn.streaming && turn.picks && <SendToGenerationButton picks={turn.picks} />}
                  {turn.whatsapp && (
                    <a
                      className="btn"
                      style={{ marginTop: 8, display: "inline-block" }}
                      href={whatsappLink(turn.whatsapp, "Hi, I'd like an access code for Socca Intelligence.")}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      Message on WhatsApp
                    </a>
                  )}
                </>
              ) : (
                turn.text
              )}
            </div>
          </div>
        ))}

        {busy && turns[turns.length - 1]?.text === "" && (
          <div className="chat-typing" aria-live="polite">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>

      {suggestions.length > 0 && !busy && (
        <div className="chat-suggestions">
          {suggestions.map((s) => (
            <button key={s} className="prompt-chip" onClick={() => ask(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {error && <p className="chat-error">{error}</p>}

      <form
        className="chat-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift-Enter makes a newline.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask(input);
            }
          }}
          placeholder={contextMatchId ? "Ask about this match..." : "Ask about a fixture, team or the model..."}
          rows={1}
          maxLength={1000}
          aria-label="Message Guda"
          disabled={busy}
        />
        <button className="btn chat-send" type="submit" disabled={busy || !input.trim()}>
          {busy ? "…" : "Send"}
        </button>
      </form>

      <p className="chat-disclaimer">
        Model probabilities, never guarantees. Figures come from stored predictions and backtests.
      </p>
    </div>
  );
}
