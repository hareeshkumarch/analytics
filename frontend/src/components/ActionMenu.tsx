"use client";

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { btn } from "@/components/ui";

const MENU_WIDTH_PX = 220;
const ITEM_CLS =
  "block w-full px-3.5 py-2.5 text-left text-[13px] text-body hover:bg-sidebar hover:text-ink focus:bg-sidebar focus:text-ink focus:outline-none";

export interface MenuAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

export function ActionMenu({
  menuLabel,
  triggerText = "More",
  actions,
  disabled = false,
  notice,
}: {
  menuLabel: string;
  triggerText?: React.ReactNode;
  actions: MenuAction[];
  disabled?: boolean;
  notice?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [alignRight, setAlignRight] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const safe = actions.filter((a) => !a.danger);
  const danger = actions.filter((a) => a.danger);

  useLayoutEffect(() => {
    if ((!open && !notice) || !trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    setAlignRight(rect.right >= MENU_WIDTH_PX && rect.left + MENU_WIDTH_PX > window.innerWidth - 16);
  }, [open, notice]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    root.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function onMenuKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitem]"));
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }

  const item = (action: MenuAction) => (
    <button
      key={action.label}
      type="button"
      role="menuitem"
      onClick={() => {
        setOpen(false);
        action.onSelect();
      }}
      className={action.danger ? `${ITEM_CLS} text-err hover:text-err focus:text-err` : ITEM_CLS}
    >
      {action.label}
    </button>
  );

  if (actions.length === 0 && !notice) return null;

  return (
    <div ref={root} className="relative">
      {actions.length > 0 && (
        <button
          ref={trigger}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((v) => !v)}
          aria-label="More actions"
          className={btn.ghost}
        >
          {triggerText}
          <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M4 6l4 4 4-4" />
          </svg>
        </button>
      )}
      {open && (
        <div
          role="menu"
          tabIndex={-1}
          aria-label={menuLabel}
          onKeyDown={onMenuKey}
          className={`absolute top-full z-30 mt-1 min-w-[220px] border border-border-strong bg-surface py-1 shadow-pop ${
            alignRight ? "right-0" : "left-0"
          }`}
        >
          {safe.map(item)}
          {danger.length > 0 && safe.length > 0 && <div role="separator" className="my-1 border-t border-border" />}
          {danger.map(item)}
        </div>
      )}
      {notice && (
        <div
          role="alert"
          className={`absolute top-full z-20 mt-1 flex w-max max-w-[min(280px,calc(100vw-2rem))] items-start gap-2 border border-border-strong bg-surface px-3 py-2 text-[12px] text-err shadow-pop ${
            alignRight ? "right-0" : "left-0"
          }`}
        >
          {notice}
        </div>
      )}
    </div>
  );
}
