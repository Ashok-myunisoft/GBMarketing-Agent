import type { Job, JobEvent } from "./types";

export type ExistingDataFile = { name: string; size: number; updated_at: number };

const API_BASE = "http://127.0.0.1:8040";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options?.headers || {}),
    },
  });

  if (!response.ok) {
    let message = "The request failed.";

    try {
      const payload = await response.json();
      message = payload?.detail || payload?.message || message;
    } catch {
      message = await response.text();
    }

    throw new Error(message);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function createJob(message: string) {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function getJob(id: string) {
  return request<Job>(`/jobs/${id}`);
}

export function getJobs() {
  return request<Job[]>("/jobs");
}

export function getJobEvents(id: string) {
  return request<JobEvent[]>(`/jobs/${id}/events`);
}

export function getExistingData() {
  return request<ExistingDataFile[]>("/existing-data");
}

export function uploadExistingData(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ExistingDataFile>("/existing-data", { method: "POST", body: form });
}

export function deleteExistingData(filename: string) {
  return request<void>(`/existing-data/${encodeURIComponent(filename)}`, { method: "DELETE" });
}

export const exportUrl = (jobId: string) =>
  `${API_BASE}/jobs/${jobId}/export`;
