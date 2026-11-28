import React from "react";
import { HScroll } from "@/components/HScroll";
import { Icon } from "@/components/Icon";
import { cx, tableHeadCls } from "@/components/ui";

export const rowCls = "border-t border-border align-top";
export const cellCls = "px-3.5 py-2.5";

export type SortDir = "asc" | "desc";

/** A column header: plain text, or an object when it is right-aligned, hidden, or sortable. */
export type HeadCell =
  | string
  | {
      label: string;
      align?: "right";
      srOnly?: boolean;
      className?: string;
      /** `dir` is null when another column is the active sort. */
      sort?: { dir: SortDir | null; onToggle: () => void };
    };

export function DataTable({
  label,
  head,
  minWidth = "min-w-[560px]",
  rawHead = false,
  scrollY = false,
  children,
}: {
  label: string;
  head: HeadCell[];
  minWidth?: string;
  rawHead?: boolean;
  scrollY?: boolean;
  children: React.ReactNode;
}) {
  const table = (
    <table className={cx("w-full border-collapse text-left text-sm", minWidth)}>
      <caption className="sr-only">{label}</caption>
      <thead className={scrollY ? "sticky top-0 z-10" : undefined}>
        <tr className={rawHead ? "bg-panel font-mono text-xs text-secondary" : tableHeadCls}>
          {head.map((h) => {
            const cell = typeof h === "string" ? { label: h } : h;
            const right = cell.align === "right";
            return (
              <th
                key={cell.label}
                scope="col"
                aria-sort={cell.sort ? (cell.sort.dir === "asc" ? "ascending" : cell.sort.dir === "desc" ? "descending" : "none") : undefined}
                className={cx("whitespace-nowrap px-3.5", cell.sort ? "py-1.5" : "py-2.5", "font-medium", right && "text-right", cell.className)}
              >
                {cell.srOnly ? (
                  <span className="sr-only">{cell.label}</span>
                ) : cell.sort ? (
                  <button
                    type="button"
                    onClick={cell.sort.onToggle}
                    className={cx(
                      "-mx-2 inline-flex min-h-[28px] items-center gap-1 rounded-md px-2 font-medium transition-colors hover:text-ink",
                      cell.sort.dir && "text-ink"
                    )}
                  >
                    {cell.label}
                    <Icon
                      name={cell.sort.dir === "asc" ? "arrow-up" : "arrow-down"}
                      className={cx("h-3 w-3", !cell.sort.dir && "opacity-30")}
                      strokeWidth={2.25}
                    />
                  </button>
                ) : (
                  cell.label
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
  if (scrollY) {
    return (
      <div
        role="region"
        aria-label={label}
        className="max-h-[480px] overflow-auto overscroll-contain rounded-lg border border-border"
      >
        {table}
      </div>
    );
  }
  return (
    <HScroll className="rounded-lg border border-border" label={label}>
      {table}
    </HScroll>
  );
}
