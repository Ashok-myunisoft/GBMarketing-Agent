import { FormEvent, useEffect, useMemo, useState } from "react";
import { createJob, deleteExistingData, exportUrl, getExistingData, getJob, getJobEvents, getJobs, uploadExistingData } from "./api";
import type { ExistingDataFile } from "./api";
import type { Company, Job, JobEvent } from "./types";

type Page = "Dashboard" | "Lead Search" | "Search History" | "Leads" | "Companies" | "Existing Data" | "Exports" | "Settings";
const pages: Page[] = ["Dashboard", "Lead Search", "Search History", "Leads", "Companies", "Existing Data", "Exports", "Settings"];
const steps = ["search", "enrichment", "validation", "contact", "export"];

export default function App() {
  const [page, setPage] = useState<Page>("Dashboard");
  const [query, setQuery] = useState("Find 50 valve manufacturers in Chennai and identify their Managing Director");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "missing-gst" | "missing-contact">("all");

  async function refreshJobs() {
    try { setJobs(await getJobs()); } catch (err) { setError(messageOf(err)); }
  }

  async function refreshSelected(id: string) {
    try {
      const [job, nextEvents] = await Promise.all([getJob(id), getJobEvents(id)]);
      setSelectedJob(job); setEvents(nextEvents);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (err) { setError(messageOf(err)); }
  }

  useEffect(() => { void refreshJobs(); }, []);
  useEffect(() => {
    if (!selectedJob || ["completed", "failed"].includes(selectedJob.status)) return;
    const timer = window.setInterval(() => void refreshSelected(selectedJob.id), 2500);
    return () => window.clearInterval(timer);
  }, [selectedJob?.id, selectedJob?.status]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setError(null);
    try {
      const job = await createJob(query.trim());
      setSelectedJob(job); setEvents([]); setJobs((current) => [job, ...current]); setPage("Lead Search");
    } catch (err) { setError(messageOf(err)); }
  }

  const companies = useMemo(() => selectedJob?.result?.companies ?? [], [selectedJob]);
  const visibleCompanies = useMemo(() => companies.filter((company) => {
    if (filter === "missing-gst") return !company.gst;
    if (filter === "missing-contact") return !company.contact_person || !company.designation;
    return true;
  }), [companies, filter]);
  const completed = jobs.filter((job) => job.status === "completed");

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="logo"><span>GB</span> Marketing Agent</div>
      <p className="workspace-label">WORKSPACE</p>
      <nav>{pages.map((item) => <button key={item} className={page === item ? "nav-item active" : "nav-item"} onClick={() => setPage(item)}>{iconFor(item)}<span>{item}</span></button>)}</nav>
      <div className="sidebar-footer"><span className="online-dot" /> API workspace ready</div>
    </aside>

    <main className="main-workspace">
      <header className="main-header"><div><p className="eyebrow">{page}</p><h1>{headingFor(page)}</h1></div><button className="secondary" onClick={() => void refreshJobs()}>Refresh</button></header>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}

      {page === "Dashboard" && <Dashboard jobs={jobs} completed={completed.length} onSelect={(job) => { setSelectedJob(job); void refreshSelected(job.id); setPage("Lead Search"); }} />}
      {page === "Lead Search" && <LeadSearch query={query} setQuery={setQuery} onSubmit={submit} job={selectedJob} events={events} />}
      {page === "Search History" && <History jobs={jobs} onSelect={(job) => { setSelectedJob(job); void refreshSelected(job.id); setPage("Lead Search"); }} />}
      {page === "Leads" && <LeadTable companies={visibleCompanies} filter={filter} setFilter={setFilter} />}
      {page === "Companies" && <CompanyDirectory companies={companies} />}
      {page === "Existing Data" && <ExistingData onError={setError} />}
      {page === "Exports" && <Exports jobs={completed} />}
      {page === "Settings" && <Settings />}
    </main>
  </div>;
}

function Dashboard({ jobs, completed, onSelect }: { jobs: Job[]; completed: number; onSelect: (job: Job) => void }) {
  const latest = jobs.slice(0, 5);
  const leadCount = jobs.reduce((total, job) => total + job.lead_count, 0);
  return <><section className="stats"><Stat label="Total jobs" value={jobs.length} /><Stat label="Completed runs" value={completed} /><Stat label="Leads saved" value={leadCount} /><Stat label="Running now" value={jobs.filter((job) => job.status === "running").length} /></section>
    <section className="panel"><div className="panel-title"><div><h2>Recent searches</h2><p>Open a job to review its workflow and results.</p></div></div><History jobs={latest} onSelect={onSelect} compact /></section></>;
}

function LeadSearch({ query, setQuery, onSubmit, job, events }: { query: string; setQuery: (value: string) => void; onSubmit: (event: FormEvent) => void; job: Job | null; events: JobEvent[] }) {
  return <><form className="search-panel" onSubmit={onSubmit}><label htmlFor="query">Describe the companies you need</label><div><input id="query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find pump manufacturers in Coimbatore" /><button className="primary" type="submit">Start lead search</button></div><p>Example: “Find 50 textile manufacturers in Coimbatore and identify their Managing Director.”</p></form>
    {!job && <section className="empty-state"><h2>Your workflow will appear here</h2><p>Start a search to see real-time progress, events, and lead results.</p></section>}
    {job && <section className="job-grid"><div className="panel"><div className="panel-title"><div><p className="eyebrow">Current workflow</p><h2>{job.query}</h2></div><Status status={job.status} /></div><Workflow status={job.status} currentStep={job.current_step} /><p className="job-meta">Created {formatDate(job.created_at)} · {job.lead_count} leads available</p></div><LiveLogs events={events} /></section>}
    {job?.result && <section className="panel results-preview"><div className="panel-title"><div><p className="eyebrow">Results</p><h2>{job.result.industry || "Lead"} companies {job.result.location ? `in ${job.result.location}` : ""}</h2></div><a className="download" href={exportUrl(job.id)}>Download Excel</a></div><LeadTable companies={job.result.companies.slice(0, 10)} filter="all" setFilter={() => undefined} preview /></section>}</>;
}

function History({ jobs, onSelect, compact = false }: { jobs: Job[]; onSelect: (job: Job) => void; compact?: boolean }) {
  if (!jobs.length) return <div className="empty-row">No searches have been started yet.</div>;
  return <div className={compact ? "history compact" : "panel history"}>{!compact && <div className="panel-title"><div><h2>Search history</h2><p>Every job is saved so results can be reviewed later.</p></div></div>}{jobs.map((job) => <button className="history-row" key={job.id} onClick={() => onSelect(job)}><div><strong>{job.query}</strong><span>{formatDate(job.created_at)}</span></div><div><Status status={job.status} /><span className="lead-count">{job.lead_count} leads</span></div></button>)}</div>;
}

function LeadTable({ companies, filter, setFilter, preview = false }: { companies: Company[]; filter: string; setFilter: (value: "all" | "missing-gst" | "missing-contact") => void; preview?: boolean }) {
  return <section className={preview ? "" : "panel"}><div className="panel-title"><div><h2>{preview ? "Latest leads" : "Leads"} <span className="muted">{companies.length}</span></h2></div>{!preview && <div className="filters"><button className={filter === "all" ? "selected" : ""} onClick={() => setFilter("all")}>All</button><button className={filter === "missing-gst" ? "selected" : ""} onClick={() => setFilter("missing-gst")}>Missing GST</button><button className={filter === "missing-contact" ? "selected" : ""} onClick={() => setFilter("missing-contact")}>Missing contact</button></div>}</div><div className="table-wrap"><table><thead><tr><th>Company</th><th>Location</th><th>GSTIN</th><th>Contact</th><th>Designation</th><th>Email</th><th>Status</th></tr></thead><tbody>{companies.map((company, index) => <tr key={`${company.company_name}-${index}`}><td><strong>{company.company_name}</strong>{company.website && <a href={company.website} target="_blank" rel="noreferrer">Website ↗</a>}</td><td>{company.city || company.address || "—"}</td><td className="mono">{company.gst || "—"}</td><td>{company.contact_person || "—"}</td><td>{company.designation || "—"}</td><td>{company.email || "—"}</td><td><Status status={company.validation_status} /></td></tr>)}{!companies.length && <tr><td colSpan={7} className="empty-row">No leads are available for this view.</td></tr>}</tbody></table></div></section>;
}

function CompanyDirectory({ companies }: { companies: Company[] }) { return <section className="panel"><div className="panel-title"><div><h2>Companies</h2><p>All companies from the selected lead-generation job.</p></div></div>{companies.length ? <div className="company-grid">{companies.map((company, index) => <article key={`${company.company_name}-${index}`} className="company-card"><h3>{company.company_name}</h3><p>{company.city || company.address || "Location not available"}</p><dl><dt>GSTIN</dt><dd>{company.gst || "Not found"}</dd><dt>Contact</dt><dd>{company.contact_person || "Not found"}</dd><dt>Designation</dt><dd>{company.designation || "Not found"}</dd></dl></article>)}</div> : <div className="empty-row">Select a completed job from Search History first.</div>}</section>; }
function Exports({ jobs }: { jobs: Job[] }) { return <section className="panel"><div className="panel-title"><div><h2>Exports</h2><p>Download the Excel file generated by a completed workflow.</p></div></div>{jobs.length ? <div className="export-list">{jobs.map((job) => <div key={job.id}><span><strong>{job.query}</strong><small>{job.lead_count} leads · {formatDate(job.completed_at || job.created_at)}</small></span><a className="download" href={exportUrl(job.id)}>Download XLSX</a></div>)}</div> : <div className="empty-row">Exports appear when a workflow completes.</div>}</section>; }
function ExistingData({ onError }: { onError: (message: string | null) => void }) {
  const [files, setFiles] = useState<ExistingDataFile[]>([]);
  const [busy, setBusy] = useState(false);
  const refresh = async () => { try { setFiles(await getExistingData()); } catch (err) { onError(messageOf(err)); } };
  useEffect(() => { void refresh(); }, []);
  async function upload(file: File | undefined) {
    if (!file) return;
    if (!/\.(csv|xlsx)$/i.test(file.name)) { onError("Choose a CSV or XLSX file."); return; }
    setBusy(true); onError(null);
    try { await uploadExistingData(file); await refresh(); } catch (err) { onError(messageOf(err)); } finally { setBusy(false); }
  }
  async function remove(file: ExistingDataFile) {
    if (!window.confirm(`Delete ${file.name}? It will no longer be used for deduplication.`)) return;
    setBusy(true); onError(null);
    try { await deleteExistingData(file.name); await refresh(); } catch (err) { onError(messageOf(err)); } finally { setBusy(false); }
  }
  return <section className="panel existing-data"><div className="panel-title"><div><p className="eyebrow">Deduplication baseline</p><h2>Existing Data</h2><p>These CSV and XLSX files are used by the current validation step to exclude existing companies. Changes apply to new searches only.</p></div><label className={`primary upload-button ${busy ? "disabled" : ""}`}>Upload file<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={(event) => { void upload(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label></div><div className="existing-file-list">{files.length ? files.map((file) => <div className="existing-file" key={file.name}><div><strong>{file.name}</strong><small>{formatFileSize(file.size)} · Updated {new Date(file.updated_at * 1000).toLocaleDateString()}</small></div><button className="delete-button" disabled={busy} onClick={() => void remove(file)}>Delete</button></div>) : <div className="empty-row">No existing-data files are loaded. Upload a CSV or XLSX file to enable baseline deduplication.</div>}</div></section>;
}
function Settings() { return <section className="panel settings"><h2>Settings</h2><p>The frontend uses the local API proxy at <code>/api</code>. The existing FastAPI server remains at <code>http://127.0.0.1:8040</code>.</p><div className="setting"><span className="online-dot" /> <strong>Backend integration</strong><small>Lead jobs, history, events, and exports are enabled.</small></div><p className="security-note">LinkedIn credentials remain server-side environment variables and are never sent to this frontend.</p></section>; }
function Workflow({ status, currentStep }: { status: string; currentStep: string | null }) { const active = steps.indexOf(currentStep || ""); return <ol className="workflow">{steps.map((step, index) => <li key={step} className={status === "completed" || index < active ? "complete" : index === active ? "active" : ""}><span>{index < active || status === "completed" ? "✓" : index + 1}</span><div><strong>{step}</strong><small>{index < active || status === "completed" ? "Complete" : index === active ? "Running" : "Waiting"}</small></div></li>)}</ol>; }
function LiveLogs({ events }: { events: JobEvent[] }) { return <section className="panel log-panel"><div className="panel-title"><div><p className="eyebrow">Live logs</p><h2>Workflow events</h2></div></div><div className="logs">{events.length ? events.map((event) => <div key={event.id}><time>{new Date(event.created_at).toLocaleTimeString()}</time><span className={`event-dot ${event.status}`} /><p>{event.message}</p></div>) : <p className="muted">Waiting for workflow events…</p>}</div></section>; }
function Stat({ label, value }: { label: string; value: number }) { return <article className="stat"><span>{label}</span><strong>{value}</strong></article>; }
function Status({ status }: { status: string }) { return <span className={`status ${status}`}>{status.replace("_", " ")}</span>; }
function headingFor(page: Page) { return page === "Lead Search" ? "Generate qualified leads" : page; }
function iconFor(page: Page) { return ({ Dashboard: "⌂", "Lead Search": "⌕", "Search History": "◷", Leads: "◫", Companies: "▦", "Existing Data": "▤", Exports: "⇩", Settings: "⚙" } as Record<Page, string>)[page]; }
function formatDate(value: string) { return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
function formatFileSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.ceil(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`; }
function messageOf(error: unknown) { return error instanceof Error ? error.message : "Something went wrong."; }
