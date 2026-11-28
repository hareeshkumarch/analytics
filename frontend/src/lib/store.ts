import { create } from "zustand";
import type { RunMode } from "@/lib/types";

export const WORKSPACE_STORAGE_KEY = "ledgerline.workspace";

interface TransientUiState {
  activeWorkspaceId: string;
  sidebarCollapsed: boolean;
  draftQuestion: string;
  runMode: RunMode;
  setWorkspaceId: (workspaceId: string) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setDraftQuestion: (q: string) => void;
  setRunMode: (mode: RunMode) => void;
}

export const useUiStore = create<TransientUiState>((set) => ({
  activeWorkspaceId: process.env.NEXT_PUBLIC_WORKSPACE_ID || "default",
  sidebarCollapsed: false,
  draftQuestion: "",
  runMode: "quick",
  setWorkspaceId: (workspaceId) => {
    const clean = workspaceId.trim() || "default";
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(WORKSPACE_STORAGE_KEY, clean);
      }
    } catch {}
    set({ activeWorkspaceId: clean, draftQuestion: "" });
  },
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
  setDraftQuestion: (draftQuestion) => set({ draftQuestion }),
  setRunMode: (runMode) => set({ runMode }),
}));
