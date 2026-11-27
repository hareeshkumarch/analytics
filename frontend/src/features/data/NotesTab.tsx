"use client";

import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { EmptyState, Notice, Skeleton, Spinner, btn, cx, inputCls } from "@/components/ui";
import { toast } from "@/lib/toast";
import { SnapshotSummary, relativeTime, useWorkspace } from "@/lib/workspace";

interface DatasetNote {
  note_id: string;
  text: string;
  created_at: string;
}

const MAX_NOTE_CHARS = 300;

/** Things the user has confirmed about this data source. The agents read them with the schema on every question. */
export function NotesTab({ snapshot }: { snapshot: SnapshotSummary }) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const base = ws.path(`/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/notes`);
  // Notes belong to the source, so every version of it shares one list.
  const key = ["dataset-notes", ws.id, snapshot.source_id];
  const notes = useQuery({ queryKey: key, queryFn: () => apiFetch<{ notes: DatasetNote[]; limit: number }>(base) });

  const add = useMutation({
    mutationFn: (text: string) => apiFetch<DatasetNote>(base, { method: "POST", body: JSON.stringify({ text }) }),
    onSuccess: async () => {
      setDraft("");
      toast("Note saved. New questions will use it.");
      await qc.invalidateQueries({ queryKey: key });
    },
  });
  const remove = useMutation({
    mutationFn: (noteId: string) => apiFetch(`${base}/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast("Note deleted.");
      await qc.invalidateQueries({ queryKey: key });
    },
  });

  if (notes.isLoading) return <Skeleton className="h-40" />;
  if (notes.error) {
    return (
      <Notice
        tone="err"
        title="Couldn't load the notes"
        action={
          <button type="button" className={btn.secondary} onClick={() => void notes.refetch()}>
            Try again
          </button>
        }
      >
        {errorMessage(notes.error)}
      </Notice>
    );
  }

  const list = notes.data?.notes ?? [];
  const limit = notes.data?.limit ?? 20;
  const text = draft.trim();
  const full = list.length >= limit;

  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-body">
        Tell the assistant what it should know about this data: what a column means, which rows to leave out, how a metric is defined. Notes
        apply to every version of this source and are read on every new question.
      </p>

      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.length >= 3 && !full) add.mutate(text);
        }}
      >
        <label htmlFor="dataset-note" className="sr-only">
          New note
        </label>
        <input
          id="dataset-note"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={MAX_NOTE_CHARS}
          disabled={full}
          placeholder={full ? `This source has ${limit} notes; delete one to add another` : 'For example: "Revenue means paid orders only"'}
          className={cx(inputCls, "min-w-0 flex-1")}
        />
        <button type="submit" className={btn.primary} disabled={text.length < 3 || full || add.isPending}>
          {add.isPending && <Spinner />}
          Save note
        </button>
      </form>
      {add.error && <Notice tone="err" title={errorMessage(add.error, "Couldn't save the note.")} />}
      {remove.error && <Notice tone="err" title={errorMessage(remove.error, "Couldn't delete the note.")} />}

      {list.length === 0 ? (
        <EmptyState title="No notes yet" body="Without notes the assistant relies on column names and the approved definitions alone." />
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border" aria-label="Notes for this source">
          {list.map((note) => (
            <li key={note.note_id} className="flex items-start justify-between gap-3 px-3.5 py-2.5">
              <span className="min-w-0">
                <span className="block break-words text-base text-ink">{note.text}</span>
                <span className="block text-xs text-secondary">Added {relativeTime(note.created_at)}</span>
              </span>
              <button
                type="button"
                className={btn.ghost}
                disabled={remove.isPending}
                aria-label={`Delete note: ${note.text}`}
                onClick={() => remove.mutate(note.note_id)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-secondary">
        {list.length} of {limit} notes used.
      </p>
    </div>
  );
}
