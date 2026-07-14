"use client";

import { useCallback, useState } from "react";

/**
 * Keeps calculated output coupled to the exact inputs that produced it.
 * Once any input changes, the old value is removed rather than left on screen
 * where it could be mistaken for a current result.
 */
export function useInvalidatedResult<T>() {
  const [result, setResult] = useState<T | null>(null);
  const [resultWasInvalidated, setResultWasInvalidated] = useState(false);

  const setFreshResult = useCallback((next: T) => {
    setResult(next);
    setResultWasInvalidated(false);
  }, []);

  const invalidateResult = useCallback(() => {
    if (result !== null) setResultWasInvalidated(true);
    setResult(null);
  }, [result]);

  return { result, setFreshResult, invalidateResult, resultWasInvalidated };
}
