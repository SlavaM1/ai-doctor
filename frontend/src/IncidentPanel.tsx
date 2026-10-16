import { useState } from 'react'
import { mutate, useApi } from './api'
import { canDeploy, testsPassed } from './deployment'
import { Badge, ConfirmDialog, DataValue, date, Empty, ErrorNotice, Icon, ModelSelect, number, statusTone, text } from './components'
import type { Diagnosis, FixJob, IncidentDetail, JsonObject, Model, Settings } from './types'

function DiagnosisResult({ diagnosis }: { diagnosis: Diagnosis }) {
  const result = diagnosis.result || {}
  return <div className="diagnosis-result">
    <div className="section-meta"><span>{diagnosis.model || 'Model not reported'}</span><time>{date(diagnosis.created_at)}</time></div>
    {diagnosis.error && <ErrorNotice>{diagnosis.error}</ErrorNotice>}
    {Object.keys(result).length === 0 && !diagnosis.error && <p className="muted">No diagnosis result was returned.</p>}
    {result.root_cause != null && <div className="root-cause"><span className="field-label">Root cause</span><DataValue value={result.root_cause} /></div>}
    {result.confidence != null && <div className="confidence">Confidence <strong>{text(result.confidence)}</strong></div>}
    {['observations', 'hypotheses', 'recommendations', 'locations'].map(key => result[key] != null && <details key={key} className="result-details" open={key === 'recommendations'}><summary>{key.charAt(0).toUpperCase() + key.slice(1)}</summary><DataValue value={result[key]} /></details>)}
    {Object.keys(result).filter(key => !['root_cause', 'confidence', 'observations', 'hypotheses', 'recommendations', 'locations'].includes(key)).length > 0 && <details className="result-details"><summary>Additional result fields</summary><DataValue value={Object.fromEntries(Object.entries(result).filter(([key]) => !['root_cause', 'confidence', 'observations', 'hypotheses', 'recommendations', 'locations'].includes(key)))} /></details>}
  </div>
}

function ChatEntry({ entry }: { entry: unknown }) {
  if (entry && typeof entry === 'object') {
    const message = entry as JsonObject
    const content = message.content ?? message.message ?? message.response ?? message.answer
    const timestamp = message.created_at ?? message.at
    return <div className={`chat-entry ${message.role === 'user' ? 'user' : ''}`}>
      <span className="field-label">{typeof message.role === 'string' ? message.role : 'Server response'}{timestamp ? ` · ${date(String(timestamp))}` : ''}</span>
      <DataValue value={content ?? entry} />
    </div>
  }
  return <div className="chat-entry"><DataValue value={entry} /></div>
}

function SourcePreview({ value }: { value: unknown }) {
  if (value && typeof value === 'object' && 'files' in value && Array.isArray(value.files)) {
    if (!value.files.length) return <p className="muted">No matching source files were returned for the captured frames.</p>
    return <div className="source-files">{value.files.map((file: unknown, index: number) => {
      if (!file || typeof file !== 'object') return <DataValue key={index} value={file} />
      const source = file as JsonObject
      return <div key={index}><div className="source-file-heading"><code>{text(source.path) || 'Path not reported'}</code>{source.line != null && <span>Line {text(source.line)}</span>}</div>{typeof source.content === 'string' ? <pre className="code-block">{source.content}</pre> : <DataValue value={source} />}</div>
    })}</div>
  }
  return <DataValue value={value} />
}

function TestResults({ tests }: { tests: JsonObject }) {
  const metadata = Object.fromEntries(Object.entries(tests).filter(([key]) => key !== 'output'))
  return <div className="test-results"><DataValue value={metadata} />{tests.output != null && <details className="result-details"><summary>Test output</summary><pre className="code-block">{text(tests.output)}</pre></details>}</div>
}

function FixBudget({ job, refresh, onError }: { job: FixJob; refresh: () => void; onError: (error: string) => void }) {
  const [busy, setBusy] = useState(false)
  const canContinue = ['FAILED', 'BUDGET_EXHAUSTED', 'INTERRUPTED'].includes(job.status) && (job.remaining_calls ?? 0) > 0
  const resume = async () => {
    setBusy(true)
    try { await mutate(`/fixes/${job.id}/continue`, {}); refresh() }
    catch (error) { onError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <div className="test-results">
    {job.usage && <p className="small muted">{number(job.calls_used)} calls used; {number(job.remaining_calls)} remain. Input {number(job.usage.input_tokens)} / output {number(job.usage.output_tokens)} tokens{job.usage.approximate ? ' (approximate)' : ''}. {number(job.usage.latency_ms)} ms.</p>}
    {canContinue && <button className="button" disabled={busy} onClick={resume}>{busy ? 'Requesting…' : 'Continue remaining budget'}</button>}
    {canContinue && <p className="small muted">Explicit continuation preserves this workspace and the original call count. It does not reset the budget or approve deployment.</p>}
  </div>
}

function FixReview({ job, refresh, onError, stale }: { job: FixJob; refresh: () => void; onError: (error: string) => void; stale: boolean }) {
  const [approval, setApproval] = useState<{ id: number; sha: string } | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState('')
  const [result, setResult] = useState<unknown>()
  const passed = testsPassed(job.tests || {}, job.patch_sha)
  const eligible = canDeploy(job) && !stale && result === undefined
  const unchanged = !!approval && approval.id === job.id && approval.sha === job.patch_sha && eligible
  const deploy = async () => {
    if (!approval || !acknowledged || !unchanged || deploying) return
    setDeploying(true)
    setDeployError('')
    onError('')
    try {
      const response = await mutate<unknown>(`/fixes/${approval.id}/deploy`, { confirmed: true, patch_sha: approval.sha })
      setResult(response)
      setApproval(null)
      refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      onError(message)
      setDeployError(message)
    }
    finally { setDeploying(false) }
  }
  return <div className="fix-review">
    <div className="fix-heading"><div><strong>Fix #{job.id}</strong><span className="muted">{date(job.created_at)}</span></div><Badge tone={statusTone(job.status)}>{job.status}</Badge></div>
    {job.goal && <p className="fix-goal">{job.goal}</p>}
    {job.summary && <p className="prose">{job.summary}</p>}
    {job.error && <ErrorNotice>{job.error}</ErrorNotice>}
    <FixBudget job={job} refresh={refresh} onError={onError} />
    <dl className="compact-facts"><div><dt>Model</dt><dd>{job.model || 'Not reported'}</dd></div>{job.base_commit && <div><dt>Base commit</dt><dd className="mono wrap">{job.base_commit}</dd></div>}</dl>
    {job.changed_files?.length > 0 && <details className="result-details"><summary>Changed files ({job.changed_files.length})</summary><DataValue value={job.changed_files} /></details>}
    <div className="code-heading"><span><Icon name="source" size={15} /> Proposed diff</span><span>Server-redacted</span></div>
    {job.patch ? <pre className="code-block diff">{job.patch.split('\n').map((line, index) => <span key={index} className={line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : line.startsWith('@@') ? 'diff-position' : ''}>{line}{'\n'}</span>)}</pre> : <div className="inline-empty">{['QUEUED', 'RUNNING'].includes(job.status) ? 'The patch will appear when the agent has prepared it.' : 'No patch was returned.'}</div>}
    {job.patch_sha && <div className="patch-sha"><span>Patch SHA</span><code>{job.patch_sha}</code></div>}
    <div className="test-heading"><h4>Test results</h4><Badge tone={passed ? 'cyan' : ''}>{passed ? 'Passed' : Object.keys(job.tests || {}).length ? 'Not passed' : 'Not reported'}</Badge></div>
    {Object.keys(job.tests || {}).length > 0 ? <TestResults tests={job.tests} /> : <p className="muted small">Deployment stays locked until the server reports passing tests.</p>}
    <details className="result-details"><summary>Agent events ({job.events?.length || 0})</summary>{job.events?.length ? <ol className="agent-events">{job.events.map((event, index) => <li key={index}><DataValue value={event} /></li>)}</ol> : <p className="muted">No agent events have been reported.</p>}</details>
    <div className="deploy-bar"><div><Icon name="shield" size={17} /><span>{eligible ? 'Review the diff and tests before approval.' : 'Requires a ready fix, patch hash, and passing tests.'}</span></div><button className="button primary" disabled={!eligible || deploying} onClick={() => { setAcknowledged(false); setDeployError(''); setApproval({ id: job.id, sha: job.patch_sha }) }}><Icon name="deployments" size={16} />Approve &amp; Deploy</button></div>
    {result !== undefined && <div className="action-result" role="status"><strong>Deployment request accepted</strong><DataValue value={result} /></div>}
    {approval && <ConfirmDialog title="Approve & deploy this patch?" onClose={() => { if (!deploying) setApproval(null) }}>
      <p>This sends the reviewed patch to the deployment runner. It changes the target application; preparing a fix alone does not.</p>
      <dl className="approval-facts"><div><dt>Fix</dt><dd>#{approval.id}</dd></div><div><dt>Tests</dt><dd>{passed ? 'Passed, as reported by the API' : 'No longer passing'}</dd></div><div><dt>Exact patch SHA</dt><dd><code>{approval.sha}</code></dd></div></dl>
      {!unchanged && <ErrorNotice>The fix changed or is no longer deployable. Close this dialog and review the latest patch.</ErrorNotice>}
      {deployError && <ErrorNotice>{deployError}</ErrorNotice>}
      <label className="acknowledgement"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />I reviewed the diff, the exact hash, and the passing test results.</label>
      <div className="dialog-actions"><button className="button" onClick={() => setApproval(null)} disabled={deploying}>Cancel</button><button className="button primary" onClick={deploy} disabled={!acknowledged || !unchanged || deploying}>{deploying ? 'Requesting deployment…' : 'Approve & Deploy'}</button></div>
    </ConfirmDialog>}
  </div>
}

export default function IncidentPanel({ id, revision, refresh, models, settings }: { id: number; revision: number; refresh: () => void; models: Model[]; settings?: Settings }) {
  const detail = useApi<IncidentDetail>(`/incidents/${id}`, revision)
  const [sourceOpen, setSourceOpen] = useState(false)
  const source = useApi<unknown>(sourceOpen ? `/incidents/${id}/source` : null, revision)
  const [diagnosisModel, setDiagnosisModel] = useState('')
  const [fixModel, setFixModel] = useState('')
  const [goal, setGoal] = useState('')
  const [intent, setIntent] = useState<'explain' | 'fix'>('explain')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [lastAction, setLastAction] = useState('')
  const [chatResponse, setChatResponse] = useState<unknown>()
  const [chosenFix, setChosenFix] = useState<number | null>(null)
  const fixes = [...(detail.data?.fix_jobs || [])].sort((a, b) => b.id - a.id)
  const fixId = chosenFix ?? fixes[0]?.id
  const fix = useApi<FixJob>(fixId != null ? `/fixes/${fixId}` : null, revision)
  const currentFix = fix.data || fixes.find(job => job.id === fixId)

  const action = async (kind: 'analyze' | 'explain' | 'fix') => {
    if (busy || (kind !== 'analyze' && !goal.trim())) return
    setBusy(kind); setError(''); setFeedback('')
    setLastAction(kind)
    try {
      const response = await mutate<unknown>(`/incidents/${id}/${kind === 'explain' ? 'chat' : kind}`, kind === 'analyze' ? { model: diagnosisModel } : kind === 'fix' ? { goal: goal.trim(), model: fixModel } : { message: goal.trim(), model: diagnosisModel })
      if (kind === 'explain') { setChatResponse(response); setFeedback('Response received.') }
      if (kind === 'analyze') setFeedback('Analysis requested. Results appear when the server reports them.')
      if (kind === 'fix') {
        setFeedback('Fix preparation requested. Nothing will deploy without approval.')
        if (response && typeof response === 'object') {
          const data = response as JsonObject
          const job = data.fix_job && typeof data.fix_job === 'object' ? data.fix_job as JsonObject : data
          if (typeof job.id === 'number') setChosenFix(job.id)
        }
      }
      if (kind !== 'analyze') setGoal('')
      refresh()
    } catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy('') }
  }

  if (!detail.data) return <div className="incident-detail">{detail.error ? <ErrorNotice>{detail.error}</ErrorNotice> : <div className="loading" role="status">Loading incident #{id}…</div>}</div>
  const { incident, diagnoses = [], agent_runs = [], chat = [] } = detail.data
  const sortedDiagnoses = [...diagnoses].sort((a, b) => b.id - a.id)
  const latestDiagnosis = sortedDiagnoses[0]
  const event = incident.event || {}
  const trace = event.stack_trace ?? event.stacktrace ?? event.traceback ?? event.stack
  const timeline = [
    { at: incident.first_seen, title: 'First detected', detail: `${incident.service}${incident.version ? ` / ${incident.version}` : ''}` },
    ...diagnoses.map(entry => ({ at: entry.created_at, title: entry.error ? 'Diagnosis failed' : 'Diagnosis recorded', detail: entry.model })),
    ...fixes.map(entry => ({ at: entry.created_at, title: `Fix #${entry.id} created`, detail: entry.status })),
    ...(incident.last_seen !== incident.first_seen ? [{ at: incident.last_seen, title: 'Most recent occurrence', detail: `${number(incident.occurrences)} total occurrences` }] : []),
  ].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())

  return <article className="incident-detail" aria-label={`Incident ${id}`}>
    {detail.error && <ErrorNotice>Refresh failed; showing the last loaded incident. {detail.error}</ErrorNotice>}
    <header className="incident-heading"><div className="incident-kicker"><span className="mono">INC-{incident.id}</span><Badge tone={statusTone(incident.severity)}>{incident.severity}</Badge><Badge tone={statusTone(incident.status)}>{incident.status}</Badge></div><h2>{incident.exception_type || 'Application incident'}</h2><p>{incident.message || 'No incident message was provided.'}</p><div className="incident-meta"><span><Icon name="source" size={14} />{incident.service || 'Service not reported'}</span>{incident.version && <span className="mono">{incident.version}</span>}<span><Icon name="clock" size={14} />{date(incident.last_seen)}</span><span>{number(incident.occurrences)} occurrences</span></div></header>

    <section className="work-section evidence-section"><div className="section-heading"><h3><Icon name="source" />Evidence &amp; source</h3></div>
      {trace ? <pre className="code-block stack-trace">{text(trace)}</pre> : <p className="inline-empty">No stack trace was included in this incident.</p>}
      <details className="result-details"><summary>Captured event</summary><DataValue value={event} /></details>
      <details className="result-details" open={sourceOpen} onToggle={event => setSourceOpen(event.currentTarget.open)}><summary>Source preview <span className="muted">Read-only context</span></summary>{source.error ? <ErrorNotice>{source.error}</ErrorNotice> : source.loading && !source.data ? <p className="muted">Loading source context…</p> : source.data != null ? <SourcePreview value={source.data} /> : <p className="muted">No source context was returned.</p>}</details>
    </section>

    <section className="work-section"><div className="section-heading"><h3><Icon name="pulse" />Diagnosis</h3><Badge tone={statusTone(incident.diagnosis_status)}>{incident.diagnosis_status}</Badge></div>
      <div className="analysis-controls"><ModelSelect value={diagnosisModel} onChange={setDiagnosisModel} models={models} configured={settings?.default_diagnosis_model} /><button className="button" disabled={!!busy} onClick={() => action('analyze')}><Icon name="pulse" size={16} />{busy === 'analyze' ? 'Requesting…' : latestDiagnosis ? 'Analyze again' : 'Analyze incident'}</button></div>
      {lastAction === 'analyze' && error && <ErrorNotice>{error}</ErrorNotice>}{lastAction === 'analyze' && feedback && <p className="feedback" role="status">{feedback}</p>}
      {latestDiagnosis ? <DiagnosisResult diagnosis={latestDiagnosis} /> : incident.root_cause ? <div className="root-cause"><span className="field-label">Recorded root cause</span><p>{incident.root_cause}</p></div> : <p className="inline-empty">No diagnosis yet. Choose a model or use the configured default to analyze the captured evidence.</p>}
      {sortedDiagnoses.length > 1 && <details className="result-details"><summary>Previous diagnoses ({sortedDiagnoses.length - 1})</summary>{sortedDiagnoses.slice(1).map(diagnosis => <DiagnosisResult key={diagnosis.id} diagnosis={diagnosis} />)}</details>}
    </section>

    <section className="work-section fix-section"><div className="section-heading"><h3><Icon name="chat" />Investigation &amp; fix</h3><span className="small muted">Human approval required</span></div>
      <div className="chat-log">{chat.map((entry, index) => <ChatEntry key={index} entry={entry} />)}{chatResponse !== undefined && <div className="latest-response"><span className="field-label">Latest action response</span><ChatEntry entry={chatResponse} /></div>}</div>
      <form className="composer" onSubmit={event => { event.preventDefault(); action(intent) }}><div className="intent-switch" role="group" aria-label="Action type"><button type="button" aria-pressed={intent === 'explain'} className={intent === 'explain' ? 'selected' : ''} onClick={() => setIntent('explain')}>Ask / explain</button><button type="button" aria-pressed={intent === 'fix'} className={intent === 'fix' ? 'selected' : ''} onClick={() => setIntent('fix')}>Prepare fix</button></div><label className="sr-only" htmlFor={`goal-${id}`}>{intent === 'fix' ? 'Fix goal' : 'Question about the incident'}</label><textarea id={`goal-${id}`} value={goal} onChange={event => setGoal(event.target.value)} placeholder={intent === 'fix' ? 'Describe the change you want the agent to prepare…' : 'Ask about the evidence, diagnosis, or a safer approach…'} rows={3} required maxLength={2000} /><div className="composer-footer"><ModelSelect value={intent === 'fix' ? fixModel : diagnosisModel} onChange={intent === 'fix' ? setFixModel : setDiagnosisModel} models={models} configured={intent === 'fix' ? settings?.default_fix_model : settings?.default_diagnosis_model} /><button className="button primary" type="submit" disabled={!!busy || !goal.trim()}>{busy === 'explain' || busy === 'fix' ? 'Requesting…' : intent === 'fix' ? 'Prepare fix' : 'Send question'}<Icon name="arrow" size={16} /></button></div></form>
      <p className="composer-note">{intent === 'fix' ? 'Creates an isolated candidate patch. Review the diff and tests before deploying.' : 'Sends your question to the server. Any resulting diagnosis or action will appear here.'}</p>
      {lastAction !== 'analyze' && error && <ErrorNotice>{error}</ErrorNotice>}{lastAction !== 'analyze' && feedback && <p className="feedback" role="status">{feedback}</p>}
      {fixes.length > 1 && <label className="fix-selector">Fix history<select value={fixId} onChange={event => setChosenFix(Number(event.target.value))}>{fixes.map(job => <option key={job.id} value={job.id}>#{job.id} — {job.status} — {date(job.created_at)}</option>)}</select></label>}
      {fix.error && <ErrorNotice>{fix.error}</ErrorNotice>}
      {currentFix ? <FixReview key={currentFix.id} job={currentFix} refresh={refresh} onError={message => { setLastAction('deploy'); setError(message) }} stale={!!fix.error || !!detail.error} /> : <div className="inline-empty">No fix has been prepared for this incident.</div>}
    </section>

    <section className="work-section timeline-section"><div className="section-heading"><h3><Icon name="clock" />Incident timeline</h3><span className="small muted">Recorded events only</span></div><ol className="timeline">{timeline.map((entry, index) => <li key={`${entry.title}-${entry.at}-${index}`}><span className="timeline-marker" /><div><strong>{entry.title}</strong><span>{entry.detail}</span></div><time>{date(entry.at)}</time></li>)}</ol>
      <details className="result-details"><summary>Model usage ({agent_runs.length} runs)</summary>{agent_runs.length ? <div className="runs">{agent_runs.map(run => <div key={run.id}><div><strong>{run.kind}</strong><span className="muted">{run.model || 'Model not reported'}</span><time>{date(run.created_at)}</time></div><p>{number(run.total_tokens)} tokens{run.approximate ? ' (approximate)' : ''} · {number(run.calls)} calls · {number(run.latency_ms)} ms</p>{run.error && <ErrorNotice>{run.error}</ErrorNotice>}</div>)}</div> : <Empty title="No model runs recorded" />}</details>
    </section>
  </article>
}
