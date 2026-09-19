export type Company = {
  company_name: string;
  website?: string | null;
  phone?: string | null;
  phone_alt?: string | null;
  email?: string | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  industry?: string | null;
  turnover?: string | null;
  gst?: string | null;
  region?: string | null;
  contact_person?: string | null;
  designation?: string | null;
  linkedin_url?: string | null;
  validation_status: "pending" | "validated" | "unverified" | "rejected";
  validation_notes: string[];
};

export type ValidationStats = {
  scanned: number;
  new: number;
  duplicates: number;
  rejected: number;
  out_of_area: number;
  no_identifiers: number;
};

export type RemovedCompany = {
  company_name: string;
  stage: "search_dedup" | "validation";
  reason: string;
};

export type PipelineStats = {
  raw_fetched: number;
  after_search_dedup: number;
  after_enrichment: number;
  after_validation: number;
  removed: RemovedCompany[];
};

export type WorkflowResult = {
  user_query: string;
  industry?: string | null;
  location?: string | null;
  buyer_persona?: string | null;
  confidence?: number | null;
  companies: Company[];
  export_path?: string | null;
  validation_stats?: ValidationStats | null;
  pipeline_stats?: PipelineStats | null;
};

export type JobEvent = {
  id: number;
  created_at: string;
  step: string | null;
  status: string;
  message: string;
};

export type Job = {
  id: string;
  query: string;
  status: "queued" | "running" | "completed" | "failed";
  current_step: string | null;
  created_at: string;
  completed_at: string | null;
  error: string | null;
  export_path: string | null;
  lead_count: number;
  result: WorkflowResult | null;
};

export type MauticContact = {
  id: number;
  firstname: string | null;
  lastname: string | null;
  email: string | null;
  phone: string | null;
  company: string | null;
  city: string | null;
  country: string | null;
  address1: string | null;
  address2: string | null;
  state: string | null;
  zipcode: string | null;
  position: string | null;
  website: string | null;
  date_added: string | null;
  date_modified: string | null;
};

export type MauticSummary = {
  total_contacts: number | null;
  contacts_on_page: number;
};

export type MauticPagination = {
  page: number;
  limit: number;
  count: number;
  total_pages: number | null;
  has_previous: boolean;
  has_next: boolean;
};

export type MauticDashboardResponse = {
  connected: boolean;
  summary: MauticSummary;
  contacts: MauticContact[];
  pagination: MauticPagination;
};

export type MauticActivityEvent = {
  type: string | null;
  category: "opened" | "clicked" | "sent" | "other";
  label: string | null;
  detail: string | null;
  url: string | null;
  timestamp: string | null;
};

export type MauticActivityResponse = {
  contact_id: number;
  total: number | null;
  events: MauticActivityEvent[];
};

export type MauticSyncError = {
  company: string | null;
  email: string;
  error: string;
};

export type MauticSyncResult = {
  job_id: string;
  campaign_id: number;
  sent: number;
  skipped: number;
  failed: number;
  errors: MauticSyncError[];
};
