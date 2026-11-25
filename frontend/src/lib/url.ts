"use client";

import { useCallback, useEffect, useState } from "react";

export function setUrlParam(key: string, value: string | null) {
  const url = new URL(window.location.href);
  if (value) url.searchParams.set(key, value);
  else url.searchParams.delete(key);
  window.history.replaceState(null, "", url.toString());
}

export function useUrlState<T extends string>(key: string, fallback: T, isValid: (value: string) => value is T) {
  const [value, setValue] = useState<T>(fallback);

  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get(key);
    if (linked !== null && isValid(linked)) setValue(linked);
  }, [key, isValid]);

  const update = useCallback(
    (next: T) => {
      setValue(next);
      setUrlParam(key, next === fallback ? null : next);
    },
    [key, fallback]
  );

  return [value, update] as const;
}

export const anyText = (value: string): value is string => value.length <= 200;
