import { API_BASE, request } from "../api";
import type { MauticActivityResponse, MauticDashboardResponse, MauticSyncResult } from "../types";

export type MauticDashboardParams = {
  page?: number;
  limit?: number;
  search?: string;
};

export function getMauticDashboard(params: MauticDashboardParams = {}) {
  const query = new URLSearchParams();

  if (params.page) query.set("page", String(params.page));
  if (params.limit) query.set("limit", String(params.limit));
  if (params.search) query.set("search", params.search);

  const queryString = query.toString();

  return request<MauticDashboardResponse>(
    `/mautic/dashboard${queryString ? `?${queryString}` : ""}`
  );
}

export const mauticConnectUrl = `${API_BASE}/mautic/connect`;

export function getMauticContactActivity(contactId: number) {
  return request<MauticActivityResponse>(
    `/mautic/contacts/${contactId}/activity`
  );
}

export function syncJobToMautic(jobId: string) {
  return request<MauticSyncResult>(`/jobs/${jobId}/mautic-sync`, {
    method: "POST",
  });
}
