import { create } from "zustand";

export interface ToastItem {
  id: number;
  text: string;
  /** Set while the exit animation plays; the toast is removed once it finishes. */
  leaving?: boolean;
}

interface ToastState {
  toasts: ToastItem[];
  push: (text: string) => void;
  dismiss: (id: number) => void;
}

const VISIBLE_MS = 4500;
/** Matches the toast-out animation in tailwind.config.ts. */
const EXIT_MS = 150;
let nextId = 1;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (text) => {
    const id = nextId++;
    // Keep at most three on screen so a burst of saves doesn't cover the page.
    set((s) => ({ toasts: [...s.toasts, { id, text }].slice(-3) }));
    if (typeof window !== "undefined") window.setTimeout(() => get().dismiss(id), VISIBLE_MS);
  },
  dismiss: (id) => {
    const target = get().toasts.find((t) => t.id === id);
    if (!target || target.leaving) return;
    set((s) => ({ toasts: s.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t)) }));
    const remove = () => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    if (typeof window !== "undefined") window.setTimeout(remove, EXIT_MS);
    else remove();
  },
}));

/**
 * Brief confirmation that something worked ("Routing saved."). Errors stay inline next to the
 * control that failed, because they need to stay on screen until the user has dealt with them.
 */
export function toast(text: string): void {
  useToastStore.getState().push(text);
}
