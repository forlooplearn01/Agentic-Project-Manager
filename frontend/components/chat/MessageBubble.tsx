"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "@/lib/types";
import { getShowChatSources } from "@/lib/preferences";
import { sourcesOf } from "@/lib/toolLabels";

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  // Settings > "Show how the assistant got each answer". Only answers received in this session carry it (saved history does not).
  const sources = !isUser && getShowChatSources() ? sourcesOf(message.trace) : [];

  return (
    <div className={`flex w-full animate-message-in flex-col ${isUser ? "items-end" : "items-start"} gap-2`}>
      <div
        className={
          isUser
            ? "max-w-[75ch] rounded-2xl bg-gradient-to-br from-accent-from to-accent-to px-5 py-3.5 text-[14.5px] leading-relaxed text-white shadow-sm"
            : "max-w-[75ch] rounded-2xl border border-line bg-surface px-5 py-3.5 shadow-sm"
        }
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.text}</p>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
          </div>
        )}
      </div>
      {sources.length > 0 && <p className="px-1 text-[11.5px] text-text-faint">Looked at: {sources.join(" · ")}</p>}
    </div>
  );
}
