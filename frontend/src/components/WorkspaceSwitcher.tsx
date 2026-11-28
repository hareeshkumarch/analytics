"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { btn, cx, inputCls } from "@/components/ui";
import { apiFetch } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { WORKSPACE_STORAGE_KEY, useUiStore } from "@/lib/store";
import { toast } from "@/lib/toast";

type Workspace = components["schemas"]["WorkspaceOut"];
type WorkspaceList = components["schemas"]["WorkspaceListOut"];

export function WorkspaceSwitcher({ collapsed }: { collapsed: boolean }) {
  const activeId = useUiStore((s) => s.activeWorkspaceId);
  const setWorkspaceId = useUiStore((s) => s.setWorkspaceId);
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();

  const [menuOpen, setMenuOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [customId, setCustomId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (createOpen) {
      const id = window.requestAnimationFrame(() => nameInputRef.current?.focus());
      return () => window.cancelAnimationFrame(id);
    }
  }, [createOpen]);

  // Hydrate persisted workspace selection after mount so SSR and initial HTML always match.
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
      if (saved && saved !== useUiStore.getState().activeWorkspaceId) {
        useUiStore.setState({ activeWorkspaceId: saved });
      }
    } catch {}
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  const workspacesQuery = useQuery<WorkspaceList>({
    queryKey: ["workspaces"],
    queryFn: () => apiFetch<WorkspaceList>("/workspaces"),
    retry: false,
    staleTime: 60_000,
  });

  const list: Workspace[] = workspacesQuery.data?.workspaces?.length
    ? workspacesQuery.data.workspaces
    : [{ workspace_id: activeId, name: activeId === "default" ? "Default Workspace" : activeId, business_timezone: "UTC" }];

  const current = list.find((w) => w.workspace_id === activeId) ?? {
    workspace_id: activeId,
    name: activeId === "default" ? "Default Workspace" : activeId,
    business_timezone: "UTC",
  };

  function selectWorkspace(ws: Workspace) {
    setMenuOpen(false);
    if (ws.workspace_id === activeId) return;
    setWorkspaceId(ws.workspace_id);
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "workspaces" });
    if (typeof window !== "undefined" && window.location.search) {
      router.replace(pathname);
    }
    toast(`Switched to ${ws.name}`);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<Workspace>("/workspaces", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          ...(customId.trim() ? { workspace_id: customId.trim() } : {}),
        }),
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<WorkspaceList>(["workspaces"], (prev) => ({
        workspaces: [...(prev?.workspaces ?? []), created],
      }));
      setCreateOpen(false);
      setName("");
      setCustomId("");
      setFormError(null);
      selectWorkspace(created);
    },
    onError: (err) => {
      setFormError(err instanceof Error ? err.message : "Could not create workspace");
    },
  });

  const initial = (current.name.trim()[0] || "W").toUpperCase();

  return (
    <div ref={rootRef} className={cx("relative", collapsed && "flex justify-center")}>
      <button
        type="button"
        onClick={() => setMenuOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-label={`Workspace: ${current.name}. Switch workspace`}
        title={collapsed ? `Workspace: ${current.name}` : undefined}
        className={cx(
          "flex items-center rounded-lg border border-border bg-surface text-left transition-colors hover:border-border-strong hover:bg-ink/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus dark:hover:bg-ink/[0.08]",
          collapsed ? "h-8 w-8 justify-center" : "min-h-[32px] w-full justify-between gap-1.5 px-2 py-1",
        )}
      >
        {collapsed ? (
          <span className="text-xs font-semibold text-ink">{initial}</span>
        ) : (
          <>
            <span className="flex min-w-0 items-center gap-2">
              <span className="grid h-4 w-4 shrink-0 place-items-center rounded bg-accent/15 text-[10px] font-semibold text-accent">
                {initial}
              </span>
              <span className="truncate text-[12.5px] font-medium text-ink">{current.name}</span>
            </span>
            <Icon name="chevron-down" className="h-3.5 w-3.5 shrink-0 text-secondary" />
          </>
        )}
      </button>

      {menuOpen && (
        <div
          role="menu"
          aria-label="Workspaces"
          className={cx(
            "absolute z-40 mt-1.5 origin-top-left animate-pop-in rounded-xl border border-ink/[0.08] bg-popover p-1.5 shadow-pop dark:border-border-strong",
            collapsed ? "left-0 w-56" : "left-0 right-0 min-w-[200px]",
          )}
        >
          <p className="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-secondary">Workspaces</p>
          <div className="max-h-52 space-y-0.5 overflow-y-auto">
            {list.map((ws) => {
              const selected = ws.workspace_id === activeId;
              return (
                <button
                  key={ws.workspace_id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => selectWorkspace(ws)}
                  className={cx(
                    "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] transition-colors",
                    selected ? "bg-ink/[0.08] font-medium text-ink dark:bg-ink/[0.14]" : "text-body hover:bg-ink/[0.05] hover:text-ink dark:hover:bg-ink/[0.08]",
                  )}
                >
                  <span className="min-w-0 truncate">{ws.name}</span>
                  {selected && <Icon name="check" className="h-3.5 w-3.5 shrink-0 text-accent" />}
                </button>
              );
            })}
          </div>
          <div className="mt-1 border-t border-border pt-1">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setFormError(null);
                setCreateOpen(true);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] font-medium text-accent transition-colors hover:bg-ink/[0.05] dark:hover:bg-ink/[0.08]"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-3.5 w-3.5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
              New workspace
            </button>
          </div>
        </div>
      )}

      <Dialog.Root open={createOpen} onOpenChange={setCreateOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[1px] data-[state=closed]:animate-fade-out data-[state=open]:animate-fade-in dark:bg-black/65" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,400px)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-ink/[0.08] bg-popover p-5 shadow-pop focus:outline-none data-[state=closed]:animate-dialog-out data-[state=open]:animate-dialog-in dark:border-border-strong">
            <Dialog.Title className="text-base font-semibold text-ink">Create workspace</Dialog.Title>
            <Dialog.Description className="mt-1 text-sm text-secondary">
              Each workspace isolates its own datasets, runs, pinned answers, and model keys.
            </Dialog.Description>
            <form
              className="mt-4 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!name.trim() || createMutation.isPending) return;
                createMutation.mutate();
              }}
            >
              <label className="block space-y-1">
                <span className="text-xs font-medium text-ink">Workspace name</span>
                <input
                  ref={nameInputRef}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Finance Ops"
                  required
                  maxLength={120}
                  className={inputCls}
                />
              </label>
              <label className="block space-y-1">
                <span className="text-xs font-medium text-secondary">Slug (optional)</span>
                <input
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value)}
                  placeholder="finance_ops"
                  pattern="^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
                  maxLength={64}
                  className={cx(inputCls, "font-mono text-[16px] sm:text-xs")}
                />
              </label>
              {formError && <p className="text-xs text-err">{formError}</p>}
              <div className="flex items-center justify-end gap-2 pt-2">
                <Dialog.Close asChild>
                  <button type="button" className={btn.ghost}>
                    Cancel
                  </button>
                </Dialog.Close>
                <button type="submit" disabled={!name.trim() || createMutation.isPending} className={btn.primary}>
                  {createMutation.isPending ? "Creating…" : "Create workspace"}
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

