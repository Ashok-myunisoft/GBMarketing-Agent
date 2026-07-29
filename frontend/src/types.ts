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

export type WorkflowResult = {
  user_query: string;
  industry?: string | null;
  location?: string | null;
  buyer_persona?: string | null;
  confidence?: number | null;
  companies: Company[];
  export_path?: string | null;
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
