import type React from "react";

export type { RunMode } from "@/lib/api/types";

export interface RichOption {
  value: string;
  label: string;
  icon?: React.ReactNode;
  sublabel?: string;
  badge?: string;
  disabled?: boolean;
}
