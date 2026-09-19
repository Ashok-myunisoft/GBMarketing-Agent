import { FormEvent, useEffect, useMemo, useState } from "react";
import { createJob, deleteExistingData, exportUrl, getExistingData, getJob, getJobEvents, getJobs, uploadExistingData } from "./api";
import type { ExistingDataFile } from "./api";
import { getMauticContactActivity, getMauticDashboard, mauticConnectUrl, syncJobToMautic } from "./services/mauticService";
import type { Company, Job, JobEvent, MauticActivityEvent, MauticContact, MauticPagination, MauticSummary, MauticSyncResult, PipelineStats } from "./types";

type Page = "Dashboard" | "Lead Search" | "Search History" | "Leads" | "Companies" | "Mautic" | "Existing Data" | "Exports" | "Settings";
const pages: Page[] = ["Dashboard", "Lead Search", "Search History", "Leads", "Companies", "Mautic", "Existing Data", "Exports", "Settings"];
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
      {page === "Mautic" && <MauticPage />}
      {page === "Existing Data" && <ExistingData onError={setError} />}
      {page === "Exports" && <Exports jobs={completed} />}
      {page === "Settings" && <Settings />}
    </main>
  </div>;
}

function Dashboard({ jobs, completed, onSelect }: { jobs: Job[]; completed: number; onSelect: (job: Job) => void }) {
  const latest = jobs.slice(0, 5);
  const leadCount = jobs.reduce((total, job) => total + job.lead_count, 0);
  const [mauticTotal, setMauticTotal] = useState<number | null>(null);
  const [mauticFailed, setMauticFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMauticDashboard({ page: 1, limit: 1 })
      .then((response) => { if (!cancelled) setMauticTotal(response.summary.total_contacts); })
      .catch(() => { if (!cancelled) setMauticFailed(true); });
    return () => { cancelled = true; };
  }, []);

  const mauticValue = mauticFailed ? "—" : mauticTotal === null ? "…" : mauticTotal.toLocaleString();

  return <><section className="stats"><Stat label="Total jobs" value={jobs.length} /><Stat label="Completed runs" value={completed} /><Stat label="Leads saved" value={leadCount} /><Stat label="Running now" value={jobs.filter((job) => job.status === "running").length} /><Stat label="Mautic Contacts" value={mauticValue} /></section>
    <section className="panel"><div className="panel-title"><div><h2>Recent searches</h2><p>Open a job to review its workflow and results.</p></div></div><History jobs={latest} onSelect={onSelect} compact /></section></>;
}

function LeadSearch({ query, setQuery, onSubmit, job, events }: { query: string; setQuery: (value: string) => void; onSubmit: (event: FormEvent) => void; job: Job | null; events: JobEvent[] }) {
  return <><form className="search-panel" onSubmit={onSubmit}><label htmlFor="query">Describe the companies you need</label><div><input id="query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find pump manufacturers in Coimbatore" /><button className="primary" type="submit">Start lead search</button></div><p>Example: “Find 50 textile manufacturers in Coimbatore and identify their Managing Director.”</p></form>
    {!job && <section className="empty-state"><h2>Your workflow will appear here</h2><p>Start a search to see real-time progress, events, and lead results.</p></section>}
    {job && <section className="job-grid"><div className="panel"><div className="panel-title"><div><p className="eyebrow">Current workflow</p><h2>{job.query}</h2></div><Status status={job.status} /></div><Workflow status={job.status} currentStep={job.current_step} /><p className="job-meta">Created {formatDate(job.created_at)} · {job.lead_count} leads available</p></div><LiveLogs events={events} /></section>}
    {job?.result && <section className="panel results-preview"><div className="panel-title"><div><p className="eyebrow">Results</p><h2>{job.result.industry || "Lead"} companies {job.result.location ? `in ${job.result.location}` : ""}</h2>{job.result.validation_stats && <p className="job-meta">{job.result.validation_stats.new} new · {job.result.validation_stats.duplicates} already in export · {job.result.validation_stats.rejected} rejected</p>}</div><div className="results-actions"><MauticSyncButton job={job} /><a className="download" href={exportUrl(job.id)}>Download Excel</a></div></div>{job.result.pipeline_stats && <PipelineFunnel stats={job.result.pipeline_stats} />}<LeadTable companies={job.result.companies.slice(0, 10)} filter="all" setFilter={() => undefined} preview /></section>}
    {job?.result?.pipeline_stats && <RemovedLeads stats={job.result.pipeline_stats} />}</>;
}

function MauticSyncButton({ job }: { job: Job }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MauticSyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const eligibleCount = (job.result?.companies ?? []).filter((company) => company.validation_status === "validated" && company.email).length;

  async function send() {
    if (!window.confirm(`This will email ${eligibleCount} validated compan${eligibleCount === 1 ? "y" : "ies"} through the Mautic outreach campaign. Continue?`)) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await syncJobToMautic(job.id));
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  return <>
    <button className="secondary" disabled={busy || eligibleCount === 0} onClick={() => void send()}>{busy ? "Sending…" : `Send to Mautic (${eligibleCount})`}</button>
    {result && <span className="muted">Sent {result.sent} · Skipped {result.skipped} · Failed {result.failed}</span>}
    {error && <span className="mautic-sync-error">{error}</span>}
  </>;
}
function PipelineFunnel({ stats }: { stats: PipelineStats }) {
  return <div className="stats pipeline-funnel">
    <Stat label="Raw fetched" value={stats.raw_fetched} />
    <Stat label="After dedup" value={stats.after_search_dedup} />
    <Stat label="After enrichment" value={stats.after_enrichment} />
    <Stat label="After validation" value={stats.after_validation} />
  </div>;
}

function RemovedLeads({ stats }: { stats: PipelineStats }) {
  const removed = stats.removed;
  if (!removed.length) return null;
  return <section className="panel removed-panel">
    <div className="panel-title"><div><h2>Removed leads <span className="muted">{removed.length}</span></h2><p>Companies found during this run that were dropped before the final results, and why.</p></div></div>
    <div className="table-wrap"><table><thead><tr><th>Company</th><th>Stage</th><th>Reason</th></tr></thead><tbody>
      {removed.map((item, index) => <tr key={`${item.company_name}-${index}`}><td><strong>{item.company_name}</strong></td><td className="mono">{item.stage === "search_dedup" ? "Search dedup" : "Validation"}</td><td>{item.reason}</td></tr>)}
    </tbody></table></div>
  </section>;
}

function History({ jobs, onSelect, compact = false }: { jobs: Job[]; onSelect: (job: Job) => void; compact?: boolean }) {
  if (!jobs.length) return <div className="empty-row">No searches have been started yet.</div>;
  return <div className={compact ? "history compact" : "panel history"}>{!compact && <div className="panel-title"><div><h2>Search history</h2><p>Every job is saved so results can be reviewed later.</p></div></div>}{jobs.map((job) => <button className="history-row" key={job.id} onClick={() => onSelect(job)}><div><strong>{job.query}</strong><span>{formatDate(job.created_at)}</span></div><div><Status status={job.status} /><span className="lead-count">{job.lead_count} leads</span></div></button>)}</div>;
}

function LeadTable({ companies, filter, setFilter, preview = false }: { companies: Company[]; filter: string; setFilter: (value: "all" | "missing-gst" | "missing-contact") => void; preview?: boolean }) {
  return <section className={preview ? "" : "panel"}><div className="panel-title"><div><h2>{preview ? "Latest leads" : "Leads"} <span className="muted">{companies.length}</span></h2></div>{!preview && <div className="filters"><button className={filter === "all" ? "selected" : ""} onClick={() => setFilter("all")}>All</button><button className={filter === "missing-gst" ? "selected" : ""} onClick={() => setFilter("missing-gst")}>Missing GST</button><button className={filter === "missing-contact" ? "selected" : ""} onClick={() => setFilter("missing-contact")}>Missing contact</button></div>}</div><div className="table-wrap"><table><thead><tr><th>Company</th><th>Location</th><th>GSTIN</th><th>Contact</th><th>Designation</th><th>Email</th><th>Status</th></tr></thead><tbody>{companies.map((company, index) => <tr key={`${company.company_name}-${index}`}><td><strong>{company.company_name}</strong>{company.website && <a href={company.website} target="_blank" rel="noreferrer">Website ↗</a>}</td><td>{company.city || company.address || "—"}</td><td className="mono">{company.gst || "—"}</td><td>{company.contact_person || "—"}</td><td>{company.designation || "—"}</td><td>{company.email || "—"}</td><td><Status status={company.validation_status} /></td></tr>)}{!companies.length && <tr><td colSpan={7} className="empty-row">No leads are available for this view.</td></tr>}</tbody></table></div></section>;
}

function CompanyDirectory({ companies }: { companies: Company[] }) { return <section className="panel"><div className="panel-title"><div><h2>Companies</h2><p>All companies from the selected lead-generation job.</p></div></div>{companies.length ? <div className="company-grid">{companies.map((company, index) => <article key={`${company.company_name}-${index}`} className="company-card"><h3>{company.company_name}</h3><p>{company.city || company.address || "Location not available"}</p><dl><dt>GSTIN</dt><dd>{company.gst || "Not found"}</dd><dt>Contact</dt><dd>{company.contact_person || "Not found"}</dd><dt>Designation</dt><dd>{company.designation || "Not found"}</dd></dl></article>)}</div> : <div className="empty-row">Select a completed job from Search History first.</div>}</section>; }
function MauticPage() {
  const [contacts, setContacts] = useState<MauticContact[]>([]);
  const [summary, setSummary] = useState<MauticSummary | null>(null);
  const [pagination, setPagination] = useState<MauticPagination | null>(null);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activityContact, setActivityContact] = useState<MauticContact | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getMauticDashboard({ page, limit: 25, search: appliedSearch || undefined })
      .then((response) => {
        if (cancelled) return;
        setContacts(response.contacts);
        setSummary(response.summary);
        setPagination(response.pagination);
      })
      .catch((err) => {
        if (cancelled) return;
        setContacts([]); setSummary(null); setPagination(null);
        setError(messageOf(err));
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [page, appliedSearch]);

  const disconnected = !!error && error.toLowerCase().includes("not connected");

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setPage(1);
    setAppliedSearch(searchInput.trim());
  }

  return <>
    <section className="stats">
      <Stat label="Total Mautic Contacts" value={summary ? (summary.total_contacts ?? "—").toLocaleString() : "—"} />
      <Stat label="Contacts on this page" value={summary ? summary.contacts_on_page : "—"} />
      <Stat label="Connection" value={loading ? "Checking…" : disconnected ? "Not connected" : error ? "Error" : "Connected"} />
    </section>

    <section className="panel">
      <div className="panel-title">
        <div><h2>Contacts</h2><p>Live contacts loaded directly from Mautic, one page at a time.</p></div>
        <div className="filters"><button className="selected">Contacts</button></div>
      </div>

      <form className="search-panel" onSubmit={submitSearch}>
        <label htmlFor="mautic-search">Search contacts</label>
        <div>
          <input id="mautic-search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="Search by name, email, or company" />
          <button className="primary" type="submit">Search</button>
        </div>
      </form>

      {disconnected && <div className="empty-state"><h2>Mautic is not connected</h2><p>Connect your Mautic account to load contacts.</p><a className="primary" href={mauticConnectUrl}>Connect Mautic</a></div>}

      {!disconnected && error && <div className="error-banner" role="alert">{error}</div>}

      {!disconnected && !error && loading && <div className="empty-row">Loading contacts…</div>}

      {!disconnected && !error && !loading && !contacts.length && <div className="empty-row">No contacts found</div>}

      {!disconnected && !error && !loading && contacts.length > 0 && <>
        <div className="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th><th>Company</th><th>City</th><th>State</th><th>Country</th><th>Position</th><th>Date added</th><th>Activity</th></tr></thead>
            <tbody>
              {contacts.map((contact) => <tr key={contact.id}>
                <td className="mono">{contact.id}</td>
                <td><strong>{[contact.firstname, contact.lastname].filter(Boolean).join(" ") || "—"}</strong></td>
                <td>{contact.email || "—"}</td>
                <td>{contact.phone || "—"}</td>
                <td>{contact.company || "—"}</td>
                <td>{contact.city || "—"}</td>
                <td>{contact.state || "—"}</td>
                <td>{contact.country || "—"}</td>
                <td>{contact.position || "—"}</td>
                <td>{contact.date_added ? formatDate(contact.date_added) : "—"}</td>
                <td><button className="secondary" onClick={() => setActivityContact(contact)}>View</button></td>
              </tr>)}
            </tbody>
          </table>
        </div>

        {pagination && <div className="pagination">
          <button className="secondary" disabled={!pagination.has_previous} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
          <span className="muted">Page {pagination.page} of {pagination.total_pages ?? "—"}</span>
          <button className="secondary" disabled={!pagination.has_next} onClick={() => setPage((current) => current + 1)}>Next</button>
        </div>}
      </>}
    </section>

    {activityContact && <ContactActivityModal
      contactId={activityContact.id}
      contactName={[activityContact.firstname, activityContact.lastname].filter(Boolean).join(" ") || activityContact.email || `Contact #${activityContact.id}`}
      onClose={() => setActivityContact(null)}
    />}
  </>;
}

function ContactActivityModal({ contactId, contactName, onClose }: { contactId: number; contactName: string; onClose: () => void }) {
  const [events, setEvents] = useState<MauticActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getMauticContactActivity(contactId)
      .then((response) => { if (!cancelled) setEvents(response.events); })
      .catch((err) => { if (!cancelled) setError(messageOf(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [contactId]);

  return <div className="modal-overlay" onClick={onClose}>
    <div className="panel modal" onClick={(event) => event.stopPropagation()}>
      <div className="panel-title">
        <div><p className="eyebrow">Contact activity</p><h2>{contactName}</h2></div>
        <button className="secondary" onClick={onClose}>Close</button>
      </div>

      {loading && <div className="empty-row">Loading activity…</div>}
      {!loading && error && <div className="error-banner" role="alert">{error}</div>}
      {!loading && !error && !events.length && <div className="empty-row">No activity recorded for this contact.</div>}

      {!loading && !error && events.length > 0 && <div className="logs">
        {events.map((event, index) => <div key={`${event.type}-${index}`}>
          <time>{event.timestamp ? new Date(event.timestamp).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—"}</time>
          <span className={`event-dot ${event.category}`} />
          <p>
            <strong>{event.label || event.type}</strong>
            {event.detail && (event.url
              ? <> — <a href={event.url} target="_blank" rel="noreferrer">{event.detail}</a></>
              : <> — {event.detail}</>)}
          </p>
        </div>)}
      </div>}
    </div>
  </div>;
}
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
function Stat({ label, value }: { label: string; value: number | string }) { return <article className="stat"><span>{label}</span><strong>{value}</strong></article>; }
function Status({ status }: { status: string }) { return <span className={`status ${status}`}>{status.replace("_", " ")}</span>; }
function headingFor(page: Page) { return page === "Lead Search" ? "Generate qualified leads" : page; }
function iconFor(page: Page) { return ({ Dashboard: "⌂", "Lead Search": "⌕", "Search History": "◷", Leads: "◫", Companies: "▦", Mautic: "✉", "Existing Data": "▤", Exports: "⇩", Settings: "⚙" } as Record<Page, string>)[page]; }
function formatDate(value: string) { return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
function formatFileSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.ceil(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`; }
function messageOf(error: unknown) { return error instanceof Error ? error.message : "Something went wrong."; }
