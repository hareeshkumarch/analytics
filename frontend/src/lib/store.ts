import { create } from "zustand";
import type { RunMode } from "@/lib/types";

interface TransientUiState {
  activeWorkspaceId: string;
  sidebarCollapsed: boolean;
  draftQuestion: string;
  runMode: RunMode;
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
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
  setDraftQuestion: (draftQuestion) => set({ draftQuestion }),
  setRunMode: (runMode) => set({ runMode }),
}));
