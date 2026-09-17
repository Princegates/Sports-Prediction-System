import { useState } from "react";
import { answerQuestion, SUGGESTED_QUESTIONS, type MatchContext } from "../lib/answers";

interface Message {
  role: "user" | "ai";
  text: string;
}

export function AskAboutMatch({ context }: { context: MatchContext }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");

  function ask(question: string) {
    if (!question.trim()) return;
    const answer = answerQuestion(question, context);
    setMessages((prev) => [...prev, { role: "user", text: question }, { role: "ai", text: answer }]);
    setInput("");
  }

  return (
    <div className="ask-panel">
      <p className="badge-neutral" style={{ display: "inline-block" }}>
        Grounded in this match's real numbers -- not a live language model
      </p>

      {messages.length > 0 && (
        <div className="ask-thread">
          {messages.map((m, i) => (
            <div key={i} className={`ask-bubble ${m.role}`}>
              {m.role === "ai" && <span className="tag">AI Assessment</span>}
              {m.text}
            </div>
          ))}
        </div>
      )}

      <div className="ask-suggestions">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button key={q} className="prompt-chip" onClick={() => ask(q)}>
            {q}
          </button>
        ))}
      </div>

      <form
        className="ask-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this match..."
          aria-label="Ask about this match"
        />
        <button type="submit" className="btn">
          Ask
        </button>
      </form>
    </div>
  );
}
