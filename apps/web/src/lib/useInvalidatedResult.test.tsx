import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useInvalidatedResult } from "./useInvalidatedResult";

describe("useInvalidatedResult", () => {
  it("removes a calculated value as soon as its inputs change", () => {
    const { result } = renderHook(() => useInvalidatedResult<{ irr: number }>());

    act(() => result.current.setFreshResult({ irr: 0.24 }));
    expect(result.current.result).toEqual({ irr: 0.24 });
    expect(result.current.resultWasInvalidated).toBe(false);

    act(() => result.current.invalidateResult());
    expect(result.current.result).toBeNull();
    expect(result.current.resultWasInvalidated).toBe(true);
  });

  it("clears the stale marker after a fresh calculation", () => {
    const { result } = renderHook(() => useInvalidatedResult<number>());
    act(() => result.current.setFreshResult(1));
    act(() => result.current.invalidateResult());
    act(() => result.current.setFreshResult(2));
    expect(result.current.result).toBe(2);
    expect(result.current.resultWasInvalidated).toBe(false);
  });
});
