import type { CRMImportColumn, CRMImportRow, CRMUploadResult, Job, JobEvent } from "./types";

export type ExistingDataFile = { name: string; size: number; updated_at: number };

export const API_BASE =  "http://217.217.249.121:8040"
const CRM_SESSION_KEY = "gbmarketing.crm.session";
const CRM_USER_KEY = "gbmarketing.crm.user";
const CRM_DATABASE_KEY = "gbmarketing.crm.database";

function storedSessionValue(key: string): string | null {
  try { return window.sessionStorage.getItem(key); }
  catch { return null; }
}

function storeSessionValue(key: string, value: string | null) {
  try {
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch { /* Keep the current page usable if browser storage is unavailable. */ }
}

let crmSession: string | null = storedSessionValue(CRM_SESSION_KEY);

export function hasCRMSession() { return Boolean(crmSession); }

export function getStoredCRMUser() {
  const userName = storedSessionValue(CRM_USER_KEY) || "";
  const databaseName = storedSessionValue(CRM_DATABASE_KEY);
  return databaseName ? `${userName} (${databaseName})` : userName;
}

export async function loginToCRM(userCode: string, password: string, connectionName: string) {
  const result = await request<{ session_id: string; user_name: string; database_name: string }>("/crm/login", {
    method: "POST", body: JSON.stringify({ user_code: userCode, password, connection_name: connectionName }),
  });
  crmSession = result.session_id;
  storeSessionValue(CRM_SESSION_KEY, result.session_id);
  storeSessionValue(CRM_USER_KEY, result.user_name);
  storeSessionValue(CRM_DATABASE_KEY, result.database_name);
  return result;
}

export async function logoutOfCRM() {
  if (crmSession) await request("/crm/logout", { method: "POST", headers: { "X-CRM-Session": crmSession } });
  crmSession = null;
  storeSessionValue(CRM_SESSION_KEY, null);
  storeSessionValue(CRM_USER_KEY, null);
  storeSessionValue(CRM_DATABASE_KEY, null);
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
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

export function createJob(message: string, incharge: string, allotedTo: string) {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({ message, incharge, alloted_to: allotedTo }),
  });
}

export function getJob(id: string) {
  return request<Job>(`/jobs/${id}`);
}

export function getJobs() {
  return request<Job[]>("/jobs");
}

export function getJobCounts() {
  return request<{ total_jobs: number; completed_runs: number }>("/stats/jobs");
}

export function getHourlyCompanyStats() {
  return request<unknown>("/stats/companies/hourly");
}

export function getSavedLeadCount() {
  return request<{ count: number }>("/stats/leads/saved");
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

export async function getCRMImportPreview(jobId: string) {
  return request<{ rows: CRMImportRow[]; columns: CRMImportColumn[] }>(`/jobs/${jobId}/crm-import-preview`);
}

export function uploadJobRowsToCRM(jobId: string, rows: CRMImportRow[]) {
  return request<CRMUploadResult>(`/jobs/${jobId}/crm-upload`, {
    method: "POST",
    headers: { "X-CRM-Session": crmSession || "" },
    body: JSON.stringify({ rows }),
  });
}

export function uploadFileToCRM(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<CRMUploadResult>("/crm/upload-file", { method: "POST", body: form, headers: { "X-CRM-Session": crmSession || "" } });
}

export const exportUrl = (jobId: string) =>
  `${API_BASE}/jobs/${jobId}/export`;
