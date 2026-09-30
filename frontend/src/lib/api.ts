// Browser API client. Calls go through the same-origin proxy (/api/backend/*),
// which attaches the httpOnly session JWT, so tokens never touch client JS.

export type ApiErrorBody = {
  error: { code: string; message: string; details?: Record<string, unknown>; request_id?: string };
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
    public requestId: string | null = null,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/backend/${path.replace(/^\//, "")}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, err?.code ?? "HTTP_ERROR", err?.message ?? res.statusText, err?.details ?? {}, err?.request_id ?? null);
  }
  return data as T;
}

async function throwApiError(res: Response): Promise<never> {
  const data = await res.json().catch(() => null);
  const err = (data as ApiErrorBody | null)?.error;
  throw new ApiError(res.status, err?.code ?? "HTTP_ERROR", err?.message ?? res.statusText, err?.details ?? {}, err?.request_id ?? null);
}

// Raw audio upload (the backend accepts only a raw audio/wav body — no multipart form).
async function postAudio<T>(path: string, wav: Blob): Promise<T> {
  const res = await fetch(`/api/backend/${path.replace(/^\//, "")}`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: wav,
    cache: "no-store",
  });
  if (!res.ok) return throwApiError(res);
  return (await res.json()) as T;
}

// POST that returns binary (spoken read-back audio).
async function postForBlob(path: string): Promise<Blob> {
  const res = await fetch(`/api/backend/${path.replace(/^\//, "")}`, { method: "POST", cache: "no-store" });
  if (!res.ok) return throwApiError(res);
  return res.blob();
}

export const api = {
  postAudio,
  postForBlob,
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

export async function login(username: string, role: string): Promise<{ username: string; role: string; home: string }> {
  const res = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, role }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, err?.code ?? "HTTP_ERROR", err?.message ?? "Login failed");
  }
  return data;
}

export async function logout(): Promise<void> {
  await fetch("/api/session", { method: "DELETE" });
}
