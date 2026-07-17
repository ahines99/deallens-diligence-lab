import "server-only";

import {
  api,
  ApiError,
  configureServerAuthorizationProvider,
} from "./api";
import { getServerAuthorization } from "./serverAuth";

// getServerAuthorization reads Next's request-local cookie store on every call;
// no credential is captured in module state or shared between requests.
configureServerAuthorizationProvider(getServerAuthorization);

export { api, ApiError };

export interface Loaded<T> {
  data: T;
  unavailable: boolean;
}

/**
 * Await a server-side fetch, degrading to `fallback` WITH an explicit unavailable flag.
 * Pages must render that flag — a bare `.catch(() => [])` presents an API outage as clean
 * empty data, the exact "risk absence inferred from an unavailable source" the app forbids.
 */
export async function loadOrUnavailable<T>(promise: Promise<T>, fallback: T): Promise<Loaded<T>> {
  try {
    return { data: await promise, unavailable: false };
  } catch {
    return { data: fallback, unavailable: true };
  }
}
