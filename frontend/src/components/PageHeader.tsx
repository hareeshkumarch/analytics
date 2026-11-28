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
        <h1 className="font-display text-[28px] font-[360] leading-[1.2] text-ink sm:text-[32px]">
          {title}
        </h1>
        <p className="mt-1.5 text-base leading-relaxed text-secondary">{description}</p>
      </div>
      {action}
    </header>
  );
}
