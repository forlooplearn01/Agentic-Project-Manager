"use client";

import { useEffect, useRef, useState } from "react";
import { fetchChatHistory, sendChatMessage } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";
import { IconPlus } from "@/components/icons";
import MessageBubble from "./MessageBubble";
import TypingIndicator from "./TypingIndicator";

// One question per topic, each checked against the golden `forloop` project: the answer comes from a tool and matches the data
// (task counts, hours left and the forecast, tasks over their estimate, who is free, a dependency, and a fact from the SOW).
const SUGGESTED_PROMPTS = [
  "Give me a status summary of forloop: how many tasks are completed, in progress and not started?",
  "How many hours are left in forloop, and are we on track?",
  "Which forloop tasks have used more hours than estimated?",
  "Who is free for QA or backend work in forloop over the next 4 weeks?",
  "What does the Sales Trends view in forloop depend on, and is that finished?",
  "What does the forloop SOW say about the go-live date and the number of concurrent users?",
];

const SESSION_STORAGE_KEY = "chat-session-id";

function newId() {
  return crypto.randomUUID();
}

function getOrCreateSessionId(): string {
  let id = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_STORAGE_KEY, id);
  }
  return id;
}

export default function ChatWindow() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const id = getOrCreateSessionId();
    setSessionId(id);
    fetchChatHistory(id)
      .then((r) => {
        setMessages(r.messages.map((m) => ({ id: newId(), role: m.role, text: m.content })));
      })
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  async function submit(text: string) {
    if (!text.trim() || !sessionId || sending) return;

    setMessages((prev) => [...prev, { id: newId(), role: "user", text }]);
    setInput("");
    setSending(true);

    try {
      const res = await sendChatMessage(sessionId, text);
      setMessages((prev) => [...prev, { id: newId(), role: "assistant", text: res.text, trace: res.trace }]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "assistant", text: "Something went wrong reaching the chat backend. Check that the API is running and try again." },
      ]);
    } finally {
      setSending(false);
    }
  }

  function handleSubmitForm(e: React.FormEvent) {
    e.preventDefault();
    submit(input);
  }

  function handleNewChat() {
    // The old session's messages stay in the database under their old session id -
    // this just stops pointing at them, it doesn't delete anything.
    const id = crypto.randomUUID();
    localStorage.setItem(SESSION_STORAGE_KEY, id);
    setSessionId(id);
    setMessages([]);
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-32px)] w-full max-w-3xl flex-col px-6">
      <div className="pt-4" />

      <div className="flex-1 overflow-y-auto py-8">
        {loadingHistory ? null : messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
            <div>
              <div className="mb-3 flex justify-center">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-accent-from to-accent-to text-lg font-semibold text-white shadow-[0_0_32px_-8px_var(--color-accent-to)]">
                  A
                </div>
              </div>
              <h1 className="text-[17px] font-medium text-text-primary">What do you need to know?</h1>
              <p className="mt-1 text-[13px] text-text-muted">Ask about tasks, hours, who is free, dependencies, or what a document says.</p>
            </div>
            <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => submit(prompt)}
                  className="hover-tint rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left text-[12.5px] leading-snug text-text-muted transition hover:border-accent-to/40 hover:text-text-primary"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
            {sending && <TypingIndicator />}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmitForm} className="flex items-end gap-2 pb-6">
        <button
          type="button"
          onClick={handleNewChat}
          className="hover-tint inline-flex h-11 shrink-0 items-center gap-1.5 rounded-xl border border-line bg-surface-solid px-3 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
        >
          <IconPlus className="h-3.5 w-3.5" />
          New Chat
        </button>
        <div className="flex min-w-0 flex-1 items-end gap-2 rounded-2xl border border-line bg-surface px-3.5 py-2.5 shadow-lg focus-within:border-accent-to/50 focus-within:shadow-[0_0_0_1px_var(--color-accent-to)]">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(input);
              }
            }}
            placeholder="Ask about your projects and tasks…"
            rows={1}
            disabled={!sessionId}
            className="max-h-40 flex-1 resize-none overflow-y-auto bg-transparent text-[14.5px] text-text-primary outline-none placeholder:text-text-faint disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending || !sessionId}
            className="approve-glow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-accent-from to-accent-to text-white transition disabled:cursor-not-allowed disabled:opacity-40"
          >
            <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor">
              <path d="M14 8L3 3l4 5-4 5 11-5z" />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
}
