"use client";

import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { RichOption } from "@/lib/types";

export type { RichOption };

interface RichSelectProps {
  label?: string;
  value: string;
  options: RichOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  compact?: boolean;
  disabled?: boolean;
  hint?: string;
  ariaLabel?: string;
}

export function RichSelect({
  label,
  value,
  options,
  onChange,
  placeholder = "Select…",
  compact = false,
  disabled = false,
  hint,
  ariaLabel,
}: RichSelectProps) {
  const id = useId();
  const nameText = label ?? ariaLabel;
  const labelId = `${id}-label`;
  const listId = `${id}-list`;
  const optionId = (idx: number) => `${id}-opt-${idx}`;

  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [dropUp, setDropUp] = useState(false);
  const [alignRight, setAlignRight] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const typeahead = useRef({ text: "", timer: 0 });

  const selectedIdx = options.findIndex((o) => o.value === value);
  const selected = selectedIdx >= 0 ? options[selectedIdx] : null;

  const enabledIdx = (from: number, step: 1 | -1) => {
    for (let i = 1; i <= options.length; i++) {
      const idx = (from + step * i + options.length) % options.length;
      if (!options[idx].disabled) return idx;
    }
    return from;
  };

  function openList() {
    if (disabled || options.length === 0) return;
    setActiveIdx(selectedIdx >= 0 ? selectedIdx : enabledIdx(-1, 1));
    setOpen(true);
  }

  function choose(idx: number) {
    const opt = options[idx];
    if (!opt || opt.disabled) return;
    onChange(opt.value);
    setOpen(false);
    triggerRef.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    const handleOutside = (e: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", handleOutside);
    return () => document.removeEventListener("pointerdown", handleOutside);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const panelHeight = Math.min(listRef.current?.scrollHeight ?? 280, 288);
    const spaceBelow = window.innerHeight - rect.bottom;
    setDropUp(spaceBelow < panelHeight + 16 && rect.top > spaceBelow);
    setAlignRight(rect.left + 260 > window.innerWidth - 16);
  }, [open]);

  useEffect(() => {
    if (!open || activeIdx < 0) return;
    document.getElementById(`${id}-opt-${activeIdx}`)?.scrollIntoView({ block: "nearest" });
  }, [open, activeIdx, id]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault();
        if (!open) return openList();
        setActiveIdx((i) => enabledIdx(i < 0 ? -1 : i, e.key === "ArrowDown" ? 1 : -1));
        return;
      }
      case "Home":
      case "End":
        if (!open) return;
        e.preventDefault();
        setActiveIdx(e.key === "Home" ? enabledIdx(-1, 1) : enabledIdx(options.length, -1));
        return;
      case "Enter":
      case " ":
        e.preventDefault();
        if (open && activeIdx >= 0) choose(activeIdx);
        else openList();
        return;
      case "Escape":
        if (open) {
          e.preventDefault();
          setOpen(false);
        }
        return;
      case "Tab":
        setOpen(false);
        return;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const t = typeahead.current;
          window.clearTimeout(t.timer);
          t.text += e.key.toLowerCase();
          t.timer = window.setTimeout(() => (t.text = ""), 600);
          const match = options.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(t.text));
          if (match >= 0) {
            if (!open) setOpen(true);
            setActiveIdx(match);
          }
        }
    }
  }

  return (
    <div ref={containerRef} className="relative min-w-0">
      {nameText && (
        <span
          id={labelId}
          className={
            label ? "mb-1.5 block text-[12.5px] font-medium text-body" : "sr-only"
          }
        >
          {nameText}
        </span>
      )}
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={nameText ? `${labelId} ${id}-value` : undefined}
        aria-label={nameText ? undefined : selected?.label ?? placeholder}
        aria-activedescendant={open && activeIdx >= 0 ? optionId(activeIdx) : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={handleKeyDown}
        className={`flex w-full items-center justify-between gap-2 border bg-surface text-left text-[13.5px] text-ink transition-colors hover:border-plum disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-border ${
          open ? "border-plum ring-[3px] ring-plum-soft" : "border-border-strong"
        } ${compact ? "min-h-[40px] px-3 sm:min-w-[180px]" : "min-h-[44px] px-3.5"}`}
      >
        <span id={`${id}-value`} className={`flex min-w-0 items-center gap-2 ${selected ? "" : "text-secondary"}`}>
          {selected?.icon}
          <span className="truncate">{selected ? selected.label : placeholder}</span>
          {selected?.badge && !compact && (
            <span className="hidden shrink-0 bg-canvas px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-secondary sm:inline">
              {selected.badge}
            </span>
          )}
        </span>
        <svg
          aria-hidden="true"
          className={`h-3.5 w-3.5 shrink-0 text-secondary transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {hint && <p className="mt-1.5 text-[11px] leading-snug text-secondary">{hint}</p>}

      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-labelledby={nameText ? labelId : undefined}
          aria-label={nameText ? undefined : placeholder}
          tabIndex={-1}
          className={`absolute z-50 max-h-72 w-full min-w-[min(260px,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] space-y-0.5 overflow-y-auto overscroll-contain border border-border bg-surface p-1.5 shadow-pop ${
            dropUp ? "bottom-full mb-1.5" : "top-full mt-1.5"
          } ${alignRight ? "right-0" : "left-0"}`}
        >
          {options.map((opt, idx) => {
            const isSelected = opt.value === value;
            const isActive = idx === activeIdx;
            return (
              // eslint-disable-next-line jsx-a11y/click-events-have-key-events -- the combobox handles keys via aria-activedescendant
              <li
                key={opt.value}
                id={optionId(idx)}
                role="option"
                aria-selected={isSelected}
                aria-disabled={opt.disabled || undefined}
                onPointerEnter={() => !opt.disabled && setActiveIdx(idx)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(idx)}
                className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-2.5 text-[13.5px] transition-colors ${
                  opt.disabled ? "cursor-not-allowed opacity-50" : ""
                } ${isActive ? "bg-sidebar" : ""} text-ink`}
              >
                {opt.icon}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className={`truncate ${isSelected ? "font-medium" : ""}`}>{opt.label}</span>
                    {opt.badge && (
                      <span className="shrink-0 bg-surface px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-secondary ring-1 ring-border">
                        {opt.badge}
                      </span>
                    )}
                  </div>
                  {opt.sublabel && <div className="mt-0.5 truncate text-xs text-secondary">{opt.sublabel}</div>}
                </div>
                {isSelected && (
                  <svg
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0 text-plum"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
