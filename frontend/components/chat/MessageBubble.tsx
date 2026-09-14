"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "@/lib/types";

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

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
    </div>
  );
}
