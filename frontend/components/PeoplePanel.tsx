"use client";

import { useEffect, useState } from "react";
import type { ProjectPerson } from "@/lib/types";
import { addProjectPerson, fetchProjectPeople, removeProjectPerson } from "@/lib/api";
import { IconPlus, IconX } from "./icons";

export default function PeoplePanel({ projectId }: { projectId: string }) {
  const [people, setPeople] = useState<ProjectPerson[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");
  const [saving, setSaving] = useState(false);
  const [removingName, setRemovingName] = useState<string | null>(null);

  function load() {
    fetchProjectPeople(projectId)
      .then((r) => setPeople(r.people))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }

  useEffect(() => {
    setPeople(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !email.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const r = await addProjectPerson(projectId, { name: name.trim(), email: email.trim(), role: role.trim() || undefined });
      setPeople(r.people);
      setName("");
      setEmail("");
      setRole("");
      setAdding(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(personName: string) {
    setRemovingName(personName);
    setError(null);
    try {
      const r = await removeProjectPerson(projectId, personName);
      setPeople(r.people);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRemovingName(null);
    }
  }

  return (
    <div className="flex flex-col gap-2.5">
      {error && <p className="text-[12.5px] text-danger">{error}</p>}

      {!people && !error && <p className="text-[13px] text-text-faint">Loading…</p>}

      {people && people.length === 0 && !adding && (
        <p className="text-[13px] text-text-faint">
          No people yet - add attendees so Meeting Coordinator can resolve names from meeting minutes to real emails.
        </p>
      )}

      {people && people.length > 0 && (
        <div className="flex flex-col gap-2">
          {people.map((p) => (
            <div key={p.email} className="glass-panel flex items-center justify-between gap-3 rounded-xl p-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-accent-from to-accent-to text-[11px] font-semibold text-white">
                  {p.name.charAt(0).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-text-primary">
                    {p.name}
                    {p.role && <span className="ml-1.5 font-normal text-text-faint">· {p.role}</span>}
                  </p>
                  <p className="truncate text-[12px] text-text-faint">{p.email}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => handleRemove(p.name)}
                disabled={removingName === p.name}
                title="Remove"
                className="hover-tint shrink-0 rounded-lg p-1.5 text-text-faint transition hover:text-danger disabled:opacity-50"
              >
                <IconX className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <form onSubmit={handleAdd} className="glass-panel flex flex-col gap-2 rounded-xl p-3">
          <div className="grid grid-cols-2 gap-2">
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name"
              required
              className="rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
            />
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              placeholder="Email"
              required
              className="rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
            />
          </div>
          <input
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="Role (optional)"
            className="rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 text-[12.5px] text-text-primary outline-none focus:border-accent-to"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setAdding(false)}
              className="hover-tint rounded-lg px-3 py-1.5 text-[12.5px] text-text-muted transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim() || !email.trim()}
              className="rounded-lg bg-gradient-to-br from-accent-from to-accent-to px-3 py-1.5 text-[12.5px] font-medium text-white transition disabled:opacity-50"
            >
              {saving ? "Adding…" : "Add"}
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="hover-tint inline-flex w-fit items-center gap-1.5 rounded-lg border border-line bg-surface-solid px-3 py-1.5 text-[12.5px] font-medium text-text-muted transition hover:text-text-primary"
        >
          <IconPlus className="h-3.5 w-3.5" />
          Add person
        </button>
      )}
    </div>
  );
}
