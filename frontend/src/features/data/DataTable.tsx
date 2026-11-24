import React from "react";
import { HScroll } from "@/components/HScroll";
import { cx } from "@/components/ui";

export const rowCls = "border-t border-border align-top";
export const cellCls = "px-3.5 py-2.5";

export function DataTable({
  label,
  head,
  minWidth = "min-w-[560px]",
  rawHead = false,
  scrollY = false,
  children,
}: {
  label: string;
  head: string[];
  minWidth?: string;
  rawHead?: boolean;
  scrollY?: boolean;
  children: React.ReactNode;
}) {
  const table = (
    <HScroll className={scrollY ? "" : "border border-border"} label={label}>
      <table className={cx("w-full border-collapse text-left text-[13px]", minWidth)}>
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr className={cx("bg-panel text-secondary", rawHead ? "font-mono text-[11.5px]" : "text-[11px] uppercase tracking-[0.05em]")}>
            {head.map((h) => (
              <th key={h} scope="col" className="whitespace-nowrap px-3.5 py-2.5 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </HScroll>
  );
  return scrollY ? <div className="max-h-[480px] overflow-y-auto border border-border">{table}</div> : table;
}
