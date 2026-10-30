"use client";

import { useState } from "react";
import InlineEditShell, { focusWithoutScrolling } from "./InlineEditShell";

// Click-to-edit text: shows the value (or a faint placeholder), turns into an input on click,
// saves on Enter/blur, cancels on Escape. onSave gets null when the field was cleared.
// The input floats over the text (InlineEditShell), so editing never resizes the cell or the table.
export default function TextCell({
  value,
  placeholder,
  onSave,
}: {
  value: string | null;
  placeholder: string;
  onSave: (v: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  function commit(next: string) {
    setEditing(false);
    const cleaned = next.trim();
    if (cleaned !== (value ?? "")) onSave(cleaned || null);
  }
  return (
    <InlineEditShell
      editing={editing}
      minWidth="12rem"
      display={
        <button type="button" onClick={() => setEditing(true)} className="hover-tint -mx-1.5 rounded px-1.5 py-0.5 text-left">
          {value ? <span className="text-text-primary">{value}</span> : <span className="text-text-faint">{placeholder}</span>}
        </button>
      }
      editor={
        <input
          ref={focusWithoutScrolling}
          defaultValue={value ?? ""}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit((e.target as HTMLInputElement).value);
            if (e.key === "Escape") setEditing(false);
          }}
          className="w-full rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary shadow-lg outline-none focus:border-accent-to"
        />
      }
    />
  );
}
