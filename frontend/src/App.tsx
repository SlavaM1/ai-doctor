import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApi, useLiveEvents } from './api'
import { Badge, DataValue, date, Empty, ErrorNotice, Icon, number, statusTone } from './components'
import IncidentPanel from './IncidentPanel'
import SettingsPage from './SettingsPage'
import type { Deployment, Incident, Model, Settings, Status } from './types'

type Page = 'Dashboard' | 'Incidents' | 'Deployments' | 'Settings'
const navigation: Page[] = ['Dashboard', 'Incidents', 'Deployments', 'Settings']

function Telemetry({ status }: { status?: Status }) {
  return <section className="telemetry" aria-label="Operational metrics">
    <div className="metric"><span>Active incidents</span><strong className={status?.stats?.active_incidents ? 'orange-text' : ''}>{number(status?.stats?.active_incidents)}</strong><small>Current incident queue</small></div>
    <div className="metric"><span>Errors / hour</span><strong>{number(status?.stats?.errors_hour)}</strong><small>{number(status?.stats?.errors_day)} in the last 24 hours</small></div>
    <div className="metric"><span>Completed fixes</span><strong className="cyan-text">{number(status?.stats?.completed_fixes)}</strong><small>As reported by the API</small></div>
    <div className="metric token-metric"><span>Model usage</span><strong>{number(status?.usage?.total_tokens)}<small> tokens</small></strong><small>{number(status?.usage?.calls)} calls <span className="metric-separator">/</span> {number(status?.usage?.latency_ms)} ms</small></div>
  </section>
}

function DeploymentsPage({ revision }: { revision: number }) {
  const deployments = useApi<Deployment[]>('/deployments', revision)
  return <section className="deployments-page"><div className="page-section-heading"><h2>Deployment history</h2><span className="muted small">Explicitly approved changes</span></div>{deployments.error && <ErrorNotice>{deployments.error}</ErrorNotice>}{deployments.loading && !deployments.data ? <div className="loading">Loading deployments…</div> : deployments.data?.length ? <div className="deployment-list">{[...deployments.data].sort((a, b) => b.id - a.id).map(deployment => <details key={deployment.id} className="deployment-row"><summary><span className="deployment-id"><Icon name="deployments" /><strong>Deployment #{deployment.id}</strong></span><span>Fix #{deployment.fix_job_id}</span><time>{date(deployment.created_at)}</time><Badge tone={statusTone(deployment.status)}>{deployment.status}</Badge></summary><div className="deployment-result"><DataValue value={deployment.result} /></div></details>)}</div> : !deployments.error && <Empty title="No deployments recorded" icon="deployments">Approved deployments will appear here. Review a prepared fix, its exact patch hash, and passing tests before deploying.</Empty>}</section>
}

export default function App() {
  const [page, setPage] = useState<Page>('Dashboard')
  const [revision, setRevision] = useState(0)
  const [configRevision, setConfigRevision] = useState(0)
  const refresh = useCallback(() => setRevision(previous => previous + 1), [])
  const onLiveRefresh = useCallback((settingsChanged?: boolean) => {
    refresh()
    if (settingsChanged) setConfigRevision(previous => previous + 1)
  }, [refresh])
  const connection = useLiveEvents(onLiveRefresh)
  const status = useApi<Status>('/status', revision)
  const settings = useApi<Settings>('/settings', configRevision)
  const modelResponse = useApi<{ models: Model[] }>('/models', configRevision)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filterStatus, setFilterStatus] = useState('')
  const [severity, setSeverity] = useState('')
  const [search, setSearch] = useState('')
  const query = new URLSearchParams()
  if (filterStatus) query.set('status', filterStatus)
  if (severity) query.set('severity', severity)
  const incidents = useApi<Incident[]>(`/incidents${query.size ? `?${query}` : ''}`, revision)
  const models = useMemo(() => {
    const values = [...(settings.data?.models || []), ...(modelResponse.data?.models || [])]
    return Array.from(new Map(values.filter(model => typeof model?.id === 'string' && model.id).map(model => [model.id, model])).values())
  }, [settings.data, modelResponse.data])
  const filtered = (incidents.data || []).filter(incident => `${incident.exception_type} ${incident.message} ${incident.service} ${incident.id}`.toLowerCase().includes(search.toLowerCase()))
  const activeId = filtered.some(incident => incident.id === selectedId) ? selectedId : filtered[0]?.id ?? null
  useEffect(() => {
    if (activeId != null && activeId !== selectedId) setSelectedId(activeId)
  }, [activeId, selectedId])

  // Periodic reconciliation also covers dropped SSE messages and queued work while reconnecting.
  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 15000)
    const visible = () => { if (document.visibilityState === 'visible') refresh() }
    document.addEventListener('visibilitychange', visible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible) }
  }, [refresh])

  const target = status.data?.target
  const targetLabel = target?.running === false ? target.status || 'Target stopped' : target?.healthy === true ? 'Target healthy' : target?.healthy === false ? target.status || 'Target unhealthy' : target?.status || (target?.running === true ? 'Target running' : 'Target unknown')
  const descriptions: Record<Page, string> = {
    Dashboard: 'From first signal to a reviewed, controlled fix.',
    Incidents: 'Inspect the evidence. Understand the cause. Review the change.',
    Deployments: 'A record of approved changes and their reported outcomes.',
    Settings: 'Model defaults, detection thresholds, and agent budgets.',
  }
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="#" onClick={event => { event.preventDefault(); setPage('Dashboard') }} aria-label="AI Doctor dashboard"><span className="brand-mark"><span /><span /></span><div><strong>AI Doctor</strong><span>Incident console</span></div></a><div className="nav-caption">Workspace</div><nav aria-label="Main navigation">{navigation.map(item => <button key={item} className={`nav-item ${page === item ? 'active' : ''}`} aria-label={item} title={item} aria-current={page === item ? 'page' : undefined} onClick={() => setPage(item)}><Icon name={item.toLowerCase()} /><span>{item}</span>{item === 'Incidents' && typeof status.data?.stats?.active_incidents === 'number' && <span className="nav-count">{status.data.stats.active_incidents}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="safety-note"><Icon name="shield" /><strong>Review before release</strong><p>Agents prepare changes.<br />You approve deployment.</p></div><div className="sidebar-connection"><span className={`status-dot ${connection === 'live' ? 'live' : ''}`} /><span>{connection === 'live' ? 'Event stream connected' : connection === 'connecting' ? 'Connecting to events' : 'Events reconnecting'}</span></div></div></aside>
    <main className="main-shell"><header className="topbar"><div className="breadcrumb">Workspace <span>/</span><strong>{page}</strong></div><div className="topbar-actions"><span className={`target-status ${target?.healthy === true ? 'cyan-text' : target?.healthy === false || target?.running === false ? 'orange-text' : ''}`}><span className={`status-dot ${target?.healthy === true ? 'live' : target?.healthy === false || target?.running === false ? 'warning' : ''}`} />{targetLabel}</span><button className="icon-button" aria-label="Refresh console" title="Refresh console" onClick={() => { refresh(); setConfigRevision(previous => previous + 1) }}><Icon name="refresh" /></button></div></header>
      <div className="page-content"><div className="page-heading"><div><h1>{page === 'Dashboard' ? 'Operational overview' : page === 'Incidents' ? 'Incident workbench' : page}</h1><p>{descriptions[page]}</p></div><span className={`live-label ${connection === 'live' ? 'cyan-text' : ''}`}><Icon name="pulse" size={16} />{connection === 'live' ? 'Live telemetry' : '15-second reconciliation'}</span></div>
        {status.error && <ErrorNotice>Cannot refresh system status. {status.error}</ErrorNotice>}
        {(page === 'Dashboard' || page === 'Incidents') && <><Telemetry status={status.data} /><div className="system-strip"><div><Icon name="pulse" size={15} /><span>Target</span><strong>{targetLabel}</strong>{target?.container_id && <code title={target.container_id}>{target.container_id.slice(0, 12)}</code>}</div><details><summary>Collector &amp; usage</summary><div className="system-popover"><span className="field-label">Collector</span><DataValue value={status.data?.collector} /><span className="field-label">Token usage</span><DataValue value={status.data?.usage} />{target?.health != null && <><span className="field-label">Target health</span><DataValue value={target.health} /></>}</div></details></div>
          <div className="incident-workspace"><section className="incident-queue" aria-label="Incident queue"><div className="queue-heading"><h2>Incident queue</h2><span className="queue-count">{incidents.data ? filtered.length : '—'}</span></div><div className="queue-controls"><label className="search-input"><Icon name="search" size={16} /><input type="search" placeholder="Find an incident…" value={search} onChange={event => setSearch(event.target.value)} aria-label="Search incidents" /></label><div className="queue-filters"><label><span className="sr-only">Filter status</span><select value={filterStatus} onChange={event => setFilterStatus(event.target.value)}><option value="">All statuses</option>{Array.from(new Set(['NEW', 'OPEN', 'DIAGNOSED', 'FIXING', 'RESOLVED', ...(incidents.data || []).map(incident => incident.status)])).map(value => <option key={value} value={value}>{value}</option>)}</select></label><label><span className="sr-only">Filter severity</span><select value={severity} onChange={event => setSeverity(event.target.value)}><option value="">All severities</option>{Array.from(new Set(['ERROR', 'WARNING', 'CRITICAL', ...(incidents.data || []).map(incident => incident.severity)])).map(value => <option key={value} value={value}>{value}</option>)}</select></label></div></div>
            {incidents.error && <ErrorNotice>{incidents.error}</ErrorNotice>}
            {incidents.loading && !incidents.data ? <div className="loading">Loading incidents…</div> : filtered.length ? <div className="incident-list">{filtered.map(incident => <button key={incident.id} className={`incident-card ${activeId === incident.id ? 'selected' : ''}`} aria-pressed={activeId === incident.id} onClick={() => setSelectedId(incident.id)}><div className="incident-card-top"><span className={`severity-mark ${/ERROR|CRITICAL/i.test(incident.severity) ? 'warning' : ''}`} /> <span className="mono">INC-{incident.id}</span><span className="incident-card-status">{incident.status}</span></div><strong>{incident.exception_type || 'Application incident'}</strong><p>{incident.message || 'No message provided'}</p><div className="incident-card-bottom"><span>{incident.service}</span><span>{number(incident.occurrences)}×</span><time>{date(incident.last_seen)}</time></div></button>)}</div> : !incidents.error && <Empty title={search || filterStatus || severity ? 'No matching incidents' : 'No incidents reported'} icon="incidents">{search || filterStatus || severity ? 'Adjust the search or filters to see more incidents.' : 'Events collected from the target application will appear here.'}</Empty>}
          </section><div className="detail-workspace">{activeId != null ? <IncidentPanel key={activeId} id={activeId} revision={revision} refresh={refresh} models={models} settings={settings.data} /> : <div className="idle-workspace"><div className="idle-graphic" aria-hidden="true"><Icon name="pulse" size={74} /></div><h2>{incidents.error ? 'Incident data unavailable' : incidents.loading ? 'Loading the incident queue' : 'Ready for the next signal'}</h2><p>{incidents.error ? 'Check the API connection and refresh the console. No incident data has been invented.' : 'Select an incident to inspect its evidence, run a diagnosis, and prepare a fix for review.'}</p><div className="idle-process"><span>Evidence</span><Icon name="arrow" size={14} /><span>Diagnosis</span><Icon name="arrow" size={14} /><span>Reviewed fix</span></div></div>}</div></div>
        </>}
        {page === 'Deployments' && <DeploymentsPage revision={revision} />}
        {page === 'Settings' && <SettingsPage settings={settings.data} models={models} error={settings.error} loading={settings.loading} onSaved={() => setConfigRevision(previous => previous + 1)} />}
        {(page === 'Settings' || activeId != null && (page === 'Dashboard' || page === 'Incidents')) && modelResponse.error && <div className="model-warning"><ErrorNotice>Model discovery unavailable. {modelResponse.error} Model selectors only show IDs returned by the server; an empty selection uses its default.</ErrorNotice></div>}
        {page === 'Settings' && !modelResponse.error && !modelResponse.loading && models.length === 0 && <p className="muted small">The server returned no models. Configure the provider on the server and refresh.</p>}
        <footer className="page-footer"><span>AI Doctor <span className="footer-slash">/</span> Diagnosis with evidence. Deployment with consent.</span><span>Same-origin API <code>/api</code></span></footer>
      </div>
    </main>
  </div>
}
