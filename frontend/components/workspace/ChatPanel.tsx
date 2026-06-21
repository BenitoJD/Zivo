"use client";

import { useState } from "react";
import { apiPostSSE } from "@/lib/api/client";

export function ChatPanel({ artifactId }: { artifactId?: string }) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);

  async function send() {
    if (!input.trim() || !artifactId) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: userMsg }]);
    try {
      let assistant = "";
      await apiPostSSE(`/api/chat/threads/${artifactId}/messages`, { message: userMsg }, (chunk) => {
        assistant += chunk;
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") last.content = assistant;
          else copy.push({ role: "assistant", content: assistant });
          return copy;
        });
      });
    } catch {
      setMessages((m) => [...m, { role: "assistant", content: "Chat unavailable (sign in or start API)." }]);
    }
  }

  return (
    <section className="chat-panel">
      <h3>Tutor</h3>
      <div className="chat-panel__messages">
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
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button type="button" onClick={send}>
          Send
        </button>
      </div>
    </section>
  );
}
