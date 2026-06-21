"use client";

import { useState } from "react";
import { apiPostSSE } from "@/lib/api/client";

export function ChatPanel({ artifactId }: { artifactId?: string }) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [busy, setBusy] = useState(false);

  async function send() {
    if (!input.trim() || !artifactId || busy) return;
    const userMsg = input.trim();
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", content: userMsg }]);
    try {
      let assistant = "";
      await apiPostSSE(
        "/api/chat",
        { document_id: artifactId, message: userMsg },
        (chunk) => {
          assistant += chunk;
          setMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            if (last?.role === "assistant") last.content = assistant;
            else copy.push({ role: "assistant", content: assistant });
            return copy;
          });
        },
      );
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: "Tutor unavailable — check the API is running and the document is indexed.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="chat-panel">
      <h3>Tutor</h3>
      <div className="chat-panel__messages">
        {messages.length === 0 && (
          <p className="muted">Ask about the current page or question.</p>
        )}
        {messages.map((m, i) => (
          <p key={i} className={`chat-msg chat-msg--${m.role}`}>
            {m.content}
          </p>
        ))}
      </div>
      <div className="chat-panel__input">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the current question…"
          disabled={!artifactId || busy}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
        />
        <button type="button" onClick={send} disabled={!artifactId || busy || !input.trim()}>
          Send
        </button>
      </div>
    </section>
  );
}
