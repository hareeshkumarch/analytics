import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-lg flex-col items-start gap-4 py-16">
      <h1 className="font-display text-[1.6rem] font-semibold tracking-[-0.02em] text-ink">Page not found</h1>
      <p className="text-[14.5px] leading-relaxed text-secondary">That address doesn&apos;t match a page in Ledgerline.</p>
      <Link
        href="/ask"
        className="inline-flex min-h-[40px] items-center bg-plum px-[18px] text-[13.5px] font-medium text-on-accent hover:bg-plum-deep"
      >
        Go to Ask
      </Link>
    </div>
  );
}
