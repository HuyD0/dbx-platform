import { ApiError, type ApiErrorPayload } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let payload: ApiErrorPayload;
    try {
      payload = (await resp.json()) as ApiErrorPayload;
    } catch {
      payload = { error: "http_error", message: `${resp.status} ${resp.statusText}` };
    }
    if (!payload.error) {
      payload = { error: "http_error", message: JSON.stringify(payload) };
    }
    throw new ApiError(resp.status, payload);
  }
  return (await resp.json()) as T;
}

export function apiGet<T>(path: string, params?: Record<string, string | number | boolean>) {
  const qs = params
    ? "?" +
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  return request<T>(`${path}${qs}`);
}

export function apiPost<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function isUnavailable(error: unknown): boolean {
  return error instanceof ApiError && [404, 405, 501].includes(error.status);
}
