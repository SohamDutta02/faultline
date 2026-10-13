import type { JobEvent, JobSummary } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: any,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, detail, data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
};

/** Tail a job's event log. Returns a function that stops listening. */
export function followJob(
  jobId: string,
  onEvent: (ev: JobEvent) => void,
  onDisconnect?: () => void,
): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = (msg) => {
    const ev = JSON.parse(msg.data) as JobEvent;
    onEvent(ev);
    if (ev.type === "end") source.close();
  };
  source.onerror = () => {
    // The browser retries on its own (resuming from Last-Event-ID); give up only
    // if the job is gone, e.g. after the server restarted.
    if (source.readyState === EventSource.CLOSED) {
      onDisconnect?.();
      return;
    }
    api.get<JobSummary>(`/api/jobs/${jobId}`).catch(() => {
      source.close();
      onDisconnect?.();
    });
  };
  return () => source.close();
}
