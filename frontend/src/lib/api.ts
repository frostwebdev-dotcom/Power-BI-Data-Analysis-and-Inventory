/**
 * Typed API client.
 *
 * One place knows the base URL, the bearer token and the error envelope
 * (`{"error": {"code", "message", "details"}}`). Every call site gets a typed
 * result or an `ApiError` carrying the server's code, so screens can react to
 * `mapping_supersession_required` or `import_file_belongs_to_another_vendor`
 * by name instead of by guessing at status numbers.
 */

import { apiUrl } from "./config";
import { clearToken, readToken } from "./auth-storage";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
    readonly code: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

async function toError(response: Response): Promise<ApiError> {
  let body: ErrorEnvelope = {};
  try {
    body = (await response.json()) as ErrorEnvelope;
  } catch {
    // no JSON body; keep the status text
  }
  return new ApiError(
    body.error?.message ?? `${response.status} ${response.statusText}`,
    response.status,
    body.error?.code ?? `http_${response.status}`,
    body.error?.details ?? {},
  );
}

function authHeaders(): Record<string, string> {
  const token = readToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

async function request<T>(method: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    method,
    cache: "no-store",
    headers: { Accept: "application/json", ...authHeaders(), ...(init.headers ?? {}) },
  });
  if (response.status === 401) {
    clearToken();
  }
  if (!response.ok) {
    throw await toError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get<T>(path: string, query?: Query): Promise<T> {
    return request<T>("GET", withQuery(path, query));
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>("POST", path, {
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  },
  patch<T>(path: string, body: unknown): Promise<T> {
    return request<T>("PATCH", path, {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  /** Multipart: the browser sets the boundary, so no Content-Type here. */
  upload<T>(path: string, form: FormData): Promise<T> {
    return request<T>("POST", path, { body: form });
  },
  /** Raw bytes, for downloads; the caller decides what to do with the blob. */
  async blob(path: string): Promise<{ blob: Blob; filename: string | null }> {
    const response = await fetch(apiUrl(path), { headers: authHeaders(), cache: "no-store" });
    if (!response.ok) throw await toError(response);
    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename\*=UTF-8''([^;]+)|filename="([^"]+)"/.exec(disposition);
    const filename = match ? decodeURIComponent(match[1] ?? match[2] ?? "") : null;
    return { blob: await response.blob(), filename };
  },
};

/** Standard paged list envelope used by every list endpoint. */
export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}
