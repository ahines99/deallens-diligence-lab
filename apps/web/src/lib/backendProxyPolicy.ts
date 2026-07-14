export const DEFAULT_PROXY_BODY_LIMIT = 1024 * 1024;
export const LARGE_UPLOAD_BODY_LIMIT = 20 * 1024 * 1024;
export const SESSION_BRIDGE_BODY_LIMIT = 4 * 1024;

const LARGE_UPLOAD_PATHS = [
  /^api\/workspaces\/[^/]+\/underwriting\/financial-imports\/(?:csv|xlsx)$/,
  /^api\/deals\/[^/]+\/intelligence\/documents\/upload$/,
];

export interface ProxyPolicy {
  upstreamPath: string;
  bodyLimit: number;
}

export class BodyLimitError extends Error {
  constructor(readonly limit: number) {
    super(`Request body exceeds the ${limit}-byte limit`);
    this.name = "BodyLimitError";
  }
}

export function resolveProxyPolicy(path: readonly string[]): ProxyPolicy | null {
  if (path.length < 2 || path[0] !== "api") return null;
  if (path.some((segment) => (
    !segment
    || segment === "."
    || segment === ".."
    || /[\\/\u0000-\u001f\u007f]/.test(segment)
  ))) return null;

  const upstreamPath = path.map(encodeURIComponent).join("/");
  return {
    upstreamPath,
    bodyLimit: LARGE_UPLOAD_PATHS.some((pattern) => pattern.test(upstreamPath))
      ? LARGE_UPLOAD_BODY_LIMIT
      : DEFAULT_PROXY_BODY_LIMIT,
  };
}

export function declaredBodyExceedsLimit(value: string | null, limit: number) {
  if (value === null) return false;
  if (!/^\d+$/.test(value.trim())) return true;
  const length = Number(value);
  return !Number.isSafeInteger(length) || length > limit;
}

export async function readBodyWithLimit(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
): Promise<Uint8Array | undefined> {
  if (!body) return undefined;
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel("Request body limit exceeded");
        throw new BodyLimitError(limit);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}
