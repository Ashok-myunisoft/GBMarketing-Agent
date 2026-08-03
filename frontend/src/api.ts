import type { Job, JobEvent } from "./types";

const API_BASE = "http://217.217.249.121:8040";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
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

export const exportUrl = (jobId: string) =>
  `${API_BASE}/jobs/${jobId}/export`;
