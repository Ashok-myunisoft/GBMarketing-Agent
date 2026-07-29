import type { Job, JobEvent } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "The request failed.");
  }
  return response.json() as Promise<T>;
}

export function createJob(message: string) {
  return request<Job>("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

export function getJob(id: string) {
  return request<Job>(`/jobs/${id}`);
}

export function getJobs() { return request<Job[]>("/jobs"); }
export function getJobEvents(id: string) { return request<JobEvent[]>(`/jobs/${id}/events`); }
export const exportUrl = (jobId: string) => `/api/jobs/${jobId}/export`;
