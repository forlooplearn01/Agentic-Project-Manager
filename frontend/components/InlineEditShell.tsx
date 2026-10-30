"use client";

import type { ReactNode } from "react";

// Focus an editor as it appears WITHOUT letting the browser scroll anything to reveal it (autoFocus scrolls the page or the table's
// scroll area, which is a second kind of "everything moved"). Use as ref={focusWithoutScrolling} instead of autoFocus.
export const focusWithoutScrolling = (el: HTMLElement | null) => el?.focus({ preventScroll: true });

const ANCHOR = { left: "left-0", right: "right-0", center: "left-1/2 -translate-x-1/2" } as const;

// Click-to-edit without moving anything. The read-only content stays in the layout (just hidden while editing), so the cell, the row
// and the column keep exactly the size they had; the editor floats over it instead of taking its place. An editor that replaced the
// text (an input is wider and taller than the words it edits) used to make the column grow, the row grow and, once the page got
// taller, a scrollbar appear and push the whole table sideways.
//   minWidth: the least width the editor needs (it is never narrower than the cell itself)
//   align:    which edge of the cell the editor lines up with ("right" for numbers, "center" for the RACI roles)
export default function InlineEditShell({
  editing,
  display,
  editor,
  minWidth = "10rem",
  align = "left",
}: {
  editing: boolean;
  display: ReactNode;
  editor: ReactNode;
  minWidth?: string;
  align?: keyof typeof ANCHOR;
}) {
  return (
    <span className="relative inline-block max-w-full align-middle">
      <span className={editing ? "invisible" : undefined} aria-hidden={editing || undefined}>
        {display}
      </span>
      {editing && (
        <span className={`absolute top-1/2 z-30 -translate-y-1/2 ${ANCHOR[align]}`} style={{ minWidth: `max(100%, ${minWidth})` }}>
          {editor}
        </span>
      )}
    </span>
  );
}
