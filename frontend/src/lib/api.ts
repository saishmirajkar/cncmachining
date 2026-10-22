/** Thin API client: bearer token + organisation header, structured errors. */
import { PREVIEW, previewMe, previewResponse, READ_ONLY_MESSAGE } from "./preview";

const TOKEN_KEY = "cnc.token";
const ORG_KEY = "cnc.org";

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: session-only */
  }
}

export const session = {
  get token() {
    return PREVIEW ? previewMe().token : safeGet(TOKEN_KEY);
  },
  set token(v: string | null) {
    if (!PREVIEW) safeSet(TOKEN_KEY, v);
  },
  get org() {
    return PREVIEW ? previewMe().org : safeGet(ORG_KEY);
  },
  set org(v: string | null) {
    if (!PREVIEW) safeSet(ORG_KEY, v);
  },
};

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : describe(detail));
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail: unknown): string {
  if (!detail) return "Request failed";
  if (Array.isArray(detail)) return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
  if (typeof detail === "object") {
    const o = detail as Record<string, unknown>;
    if (typeof o.message === "string") return o.message;
  }
  return JSON.stringify(detail);
}

export function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (session.token) h.Authorization = `Bearer ${session.token}`;
  if (session.org) h["X-Organization-Id"] = session.org;
  return h;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  if (PREVIEW) return previewResponse(path, (rest.method ?? "GET").toUpperCase()) as T;
  const res = await fetch(path, {
    ...rest,
    headers: headers(json !== undefined ? { "Content-Type": "application/json" } : {}),
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    session.token = null;
    window.dispatchEvent(new Event("cnc:logout"));
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** Authenticated download that saves the response as a file. */
export async function download(path: string, fallbackName: string) {
  if (PREVIEW) throw new ApiError(403, "Downloads are disabled in the read-only preview.");
  const res = await fetch(path, { headers: headers() });
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail ?? res.statusText);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = /filename="?([^"]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Upload with progress and cancellation (XMLHttpRequest exposes upload progress events). */
export function uploadWithProgress(
  path: string,
  form: FormData,
  onProgress: (fraction: number) => void,
): { promise: Promise<unknown>; cancel: () => void } {
  if (PREVIEW) return { promise: Promise.reject(new ApiError(403, READ_ONLY_MESSAGE)), cancel: () => undefined };
  const xhr = new XMLHttpRequest();
  const promise = new Promise<unknown>((resolve, reject) => {
    xhr.open("POST", path);
    for (const [k, v] of Object.entries(headers())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, body.detail ?? xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled"));
    xhr.send(form);
  });
  return { promise, cancel: () => xhr.abort() };
}
