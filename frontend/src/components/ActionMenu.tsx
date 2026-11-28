"use client";

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { btn } from "@/components/ui";

/** Widest the menu can grow (it sizes to its longest item); used to decide which edge it opens from. */
const MENU_WIDTH_PX = 300;
const ITEM_CLS =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-base text-ink hover:bg-ink/[0.05] focus:bg-ink/[0.05] focus:outline-none dark:hover:bg-ink/[0.08] dark:focus:bg-ink/[0.08]";
/** Both plain items and on/off items take part in focus and arrow-key movement. */
const ITEM_SELECTOR = "[role=menuitem],[role=menuitemcheckbox]";

export interface MenuAction {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  /** Draws a rule above this item to start a new group (ignored on the first item). */
  separatorAbove?: boolean;
  /** Makes the item an on/off setting: shown with a check mark and announced as checked or not. */
  checked?: boolean;
  /** One short line under the label for what the item does, readable on touch screens (unlike a tooltip). */
  hint?: string;
}

export function ActionMenu({
  menuLabel,
  triggerText = "More",
  triggerLabel,
  actions,
  disabled = false,
  notice,
}: {
  menuLabel: string;
  triggerText?: React.ReactNode;
  triggerLabel?: string;
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
    root.current?.querySelector<HTMLButtonElement>(ITEM_SELECTOR)?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function onMenuKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>(ITEM_SELECTOR));
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }

  const item = (action: MenuAction, index: number) => {
    const toggle = action.checked !== undefined;
    return (
      <React.Fragment key={action.label}>
        {action.separatorAbove && index > 0 && <div role="separator" className="-mx-1 my-1 border-t border-ink/[0.08]" />}
        <button
          type="button"
          role={toggle ? "menuitemcheckbox" : "menuitem"}
          aria-checked={toggle ? action.checked : undefined}
          onClick={() => {
            setOpen(false);
            action.onSelect();
          }}
          className={action.danger ? `${ITEM_CLS} text-err hover:text-err focus:text-err` : ITEM_CLS}
        >
          {toggle && (
            <span aria-hidden="true" className="flex h-4 w-4 shrink-0 items-center justify-center self-start pt-[3px]">
              {action.checked && <Icon name="check" className="h-3.5 w-3.5" strokeWidth={2.25} />}
            </span>
          )}
          <span className="min-w-0 flex-1">
            {action.label}
            {action.hint && <span className="block text-xs leading-snug text-secondary">{action.hint}</span>}
          </span>
        </button>
      </React.Fragment>
    );
  };

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
          aria-label={triggerLabel ?? (triggerText === "More" ? "More actions" : undefined)}
          className={btn.ghost}
        >
          {triggerText}
          <Icon name="chevron-down" className="h-3.5 w-3.5" />
        </button>
      )}
      {open && (
        <div
          role="menu"
          tabIndex={-1}
          aria-label={menuLabel}
          onKeyDown={onMenuKey}
          className={`absolute top-full z-30 mt-1 w-max min-w-[220px] max-w-[min(300px,calc(100vw-2rem))] animate-pop-in rounded-xl border border-ink/[0.08] bg-popover p-1 shadow-pop ${
            alignRight ? "right-0 origin-top-right" : "left-0 origin-top-left"
          }`}
        >
          {safe.map(item)}
          {danger.length > 0 && safe.length > 0 && <div role="separator" className="-mx-1 my-1 border-t border-ink/[0.08]" />}
          {danger.map(item)}
        </div>
      )}
      {notice && (
        <div
          role="alert"
          className={`absolute top-full z-20 mt-1 flex w-max max-w-[min(280px,calc(100vw-2rem))] items-start gap-2 rounded-lg border border-border-strong bg-surface px-3 py-2 text-xs text-err shadow-pop ${
            alignRight ? "right-0" : "left-0"
          }`}
        >
          {notice}
        </div>
      )}
    </div>
  );
}
