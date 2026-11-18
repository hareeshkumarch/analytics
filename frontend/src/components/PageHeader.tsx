import React from "react";

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className="font-display text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-[2rem]">
          {title}
        </h1>
        <p className="mt-2 text-[14.5px] leading-relaxed text-secondary">{description}</p>
      </div>
      {action}
    </header>
  );
}
