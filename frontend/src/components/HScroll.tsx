"use client";

import React, { useEffect, useRef, useState } from "react";

export function HScroll({
  children,
  className = "",
  fadeFrom = "from-surface",
  label,
}: {
  children: React.ReactNode;
  className?: string;
  fadeFrom?: string;
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () =>
      setEdges({
        left: el.scrollLeft > 2,
        right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2,
      });
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", update);
      ro?.disconnect();
    };
  }, []);

  const scrollable = edges.left || edges.right;

  return (
    <div className={`relative ${className}`}>
      <div
        ref={ref}
        className="scrollbar-none overflow-x-auto overscroll-x-contain"
        tabIndex={scrollable ? 0 : undefined}
        role={scrollable ? "region" : undefined}
        aria-label={scrollable ? label ?? "Scrollable content" : undefined}
      >
        {children}
      </div>
      <div
        aria-hidden="true"
        className={`pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r ${fadeFrom} to-transparent transition-opacity duration-200 ${
          edges.left ? "opacity-100" : "opacity-0"
        }`}
      />
      <div
        aria-hidden="true"
        className={`pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l ${fadeFrom} to-transparent transition-opacity duration-200 ${
          edges.right ? "opacity-100" : "opacity-0"
        }`}
      />
    </div>
  );
}
