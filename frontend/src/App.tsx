import { useCallback, useEffect, useMemo, useState } from 'react'
import { mutate, request, useApi, useLiveEvents } from './api'
import { Badge, DataValue, date, Empty, ErrorNotice, Icon, number, statusTone } from './components'
import IncidentPanel from './IncidentPanel'
import ExclusionSummary, { ExcludedNotice, exclusionStatuses } from './ExclusionSummary'
import { incidentStatuses } from './IncidentControls'
import SettingsPage from './SettingsPage'
import AlertsPage from './AlertsPage'
import LoginPage, { type Account } from './LoginPage'
import { label } from './labels'
import type { Deployment, Incident, Model, Settings, Status } from './types'

type Page = 'Dashboard' | 'Incidents' | 'Exclusions' | 'Deployments' | 'Alerts' | 'Settings'
const navigation: Page[] = ['Dashboard', 'Incidents', 'Exclusions', 'Deployments', 'Alerts', 'Settings']

function Telemetry({ status }: { status?: Status }) {
  return <section className="telemetry" aria-label="Операционные показатели">
    <div className="metric"><span>Активные инциденты</span><strong className={status?.stats?.active_incidents ? 'orange-text' : ''}>{number(status?.stats?.active_incidents)}</strong><small>Текущая очередь инцидентов</small></div>
    <div className="metric"><span>Ошибок в час</span><strong>{number(status?.stats?.errors_hour)}</strong><small>{number(status?.stats?.errors_day)} за последние 24 часа, включая исключения</small></div>
    <div className="metric"><span>Готовые исправления</span><strong className="cyan-text">{number(status?.stats?.completed_fixes)}</strong><small>По данным API</small></div>
    <div className="metric token-metric"><span>Использование моделей</span><strong>{number(status?.usage?.total_tokens)}<small> токенов</small></strong><small>Вызовов: {number(status?.usage?.calls)} <span className="metric-separator">/</span> {number(status?.usage?.latency_ms)} мс</small></div>
  </section>
}

function DeploymentsPage({ revision }: { revision: number }) {
  const deployments = useApi<Deployment[]>('/deployments', revision)
  return <section className="deployments-page"><div className="page-section-heading"><h2>История развёртываний</h2><span className="muted small">Явно одобренные изменения</span></div>{deployments.error && <ErrorNotice>{deployments.error}</ErrorNotice>}{deployments.loading && !deployments.data ? <div className="loading">Загрузка развёртываний…</div> : deployments.data?.length ? <div className="deployment-list">{[...deployments.data].sort((a, b) => b.id - a.id).map(deployment => <details key={deployment.id} className="deployment-row"><summary><span className="deployment-id"><Icon name="deployments" /><strong>Развёртывание #{deployment.id}</strong></span><span>Исправление #{deployment.fix_job_id}</span><time>{date(deployment.created_at)}</time><Badge tone={statusTone(deployment.status)}>{label(deployment.status)}</Badge></summary><div className="deployment-result"><DataValue value={deployment.result} /></div></details>)}</div> : !deployments.error && <Empty title="Развёртываний пока нет" icon="deployments">Здесь появятся одобренные развёртывания. Перед развёртыванием проверьте подготовленное исправление, точный хеш изменений и успешные результаты тестов.</Empty>}</section>
}

export default function App() {
  const [account, setAccount] = useState<Account | null>()
  const [error, setError] = useState('')
  const checkSession = useCallback(() => {
    setError('')
    request<Account>('/auth/me').then(setAccount).catch(error => {
      setAccount(null)
      if (error.message !== 'Войдите в аккаунт') setError(error.message)
    })
  }, [])
  useEffect(() => {
    checkSession()
    const unauthorized = () => setAccount(null)
    window.addEventListener('doctor-unauthorized', unauthorized)
    return () => window.removeEventListener('doctor-unauthorized', unauthorized)
  }, [checkSession])
  if (account === undefined) return <div className="loading" role="status">Проверка входа…</div>
  if (!account) return <>{error && <ErrorNotice>{error}</ErrorNotice>}<LoginPage onLogin={setAccount} /></>
  return <Console account={account} onLogout={() => setAccount(null)} />
}

function Console({ account, onLogout }: { account: Account; onLogout: () => void }) {
  const [page, setPage] = useState<Page>('Dashboard')
  const [project, setProject] = useState('aurelSpa')
  const configured = project === 'aurelSpa'
  const [logoutError, setLogoutError] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)
  const logout = async () => {
    setLoggingOut(true); setLogoutError('')
    try { await mutate('/auth/logout', {}); onLogout() }
    catch (error) { setLogoutError(error instanceof Error ? error.message : 'Не удалось выйти из аккаунта') }
    finally { setLoggingOut(false) }
  }
  const [revision, setRevision] = useState(0)
  const [configRevision, setConfigRevision] = useState(0)
  const refresh = useCallback(() => setRevision(previous => previous + 1), [])
  const onLiveRefresh = useCallback((settingsChanged?: boolean) => {
    refresh()
    if (settingsChanged) setConfigRevision(previous => previous + 1)
  }, [refresh])
  const connection = useLiveEvents(onLiveRefresh, configured)
  const status = useApi<Status>(configured ? '/status' : null, revision)
  const settings = useApi<Settings>(configured ? '/settings' : null, configRevision)
  const modelResponse = useApi<{ models: Model[] }>(configured ? '/models' : null, configRevision)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filterStatus, setFilterStatus] = useState('')
  const [severity, setSeverity] = useState('')
  const [search, setSearch] = useState('')
  const query = new URLSearchParams()
  if (page === 'Exclusions') query.set('excluded', 'true')
  if (filterStatus) query.set('status', filterStatus)
  if (severity) query.set('severity', severity)
  const incidents = useApi<Incident[]>(configured ? `/incidents${query.size ? `?${query}` : ''}` : null, revision)
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
  const targetLabel = target?.running === false ? label(target.status) || 'Приложение остановлено' : target?.healthy === true ? 'Приложение исправно' : target?.healthy === false ? label(target.status) || 'Приложение неисправно' : label(target?.status) || (target?.running === true ? 'Приложение работает' : 'Состояние неизвестно')
  const descriptions: Record<Page, string> = {
    Dashboard: 'От первого сигнала до проверенного и контролируемого исправления.',
    Incidents: 'Изучите данные. Выясните причину. Проверьте изменения.',
    Exclusions: 'Исключённые ошибки и инциденты «Не требует решения». Все повторения сохраняются без анализа.',
    Deployments: 'История одобренных изменений и результатов их применения.',
    Alerts: 'Уведомления об ошибках проекта в Telegram. Раздел в разработке.',
    Settings: 'Модели по умолчанию, пороги обнаружения и лимиты агентов.',
  }
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="#" onClick={event => { event.preventDefault(); setPage('Dashboard') }} aria-label="Обзор AI Doctor"><span className="brand-mark"><span /><span /></span><div><strong>AI Doctor</strong><span>Консоль инцидентов</span></div></a>
      <label className="project-picker">Проект<select aria-label="Выбор проекта" value={project} onChange={event => { setProject(event.target.value); setSelectedId(null); setSearch(''); setFilterStatus(''); setSeverity(''); setPage('Dashboard') }}><option value="aurelSpa">aurelSpa</option><option value="aurelShop">aurelShop</option></select></label>
      <div className="nav-caption">Рабочая область</div><nav aria-label="Основная навигация">{navigation.map(item => <button key={item} className={`nav-item ${page === item ? 'active' : ''}`} aria-label={label(item)} title={label(item)} aria-current={page === item ? 'page' : undefined} onClick={() => { setPage(item); setFilterStatus(''); setSelectedId(null) }}><Icon name={item.toLowerCase()} /><span>{label(item)}</span>{item === 'Incidents' && typeof status.data?.stats?.active_incidents === 'number' && <span className="nav-count">{status.data.stats.active_incidents}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="safety-note"><Icon name="shield" /><strong>Проверка перед выпуском</strong><p>Агенты готовят изменения.<br />Вы одобряете развёртывание.</p></div><div className="sidebar-connection"><span className={`status-dot ${configured && connection === 'live' ? 'live' : ''}`} /><span>{!configured ? 'Проект не подключён' : connection === 'live' ? 'Поток событий подключён' : connection === 'connecting' ? 'Подключение к событиям' : 'Переподключение к событиям'}</span></div></div>
      <div className="account-menu"><strong title={account.username}>{account.username}</strong><button className="button" disabled={loggingOut} onClick={logout}>{loggingOut ? 'Выход…' : 'Выйти'}</button>{logoutError && <ErrorNotice>{logoutError}</ErrorNotice>}</div>
    </aside>
    <main className="main-shell"><header className="topbar"><div className="breadcrumb">{project}<span>/</span><strong>{label(page)}</strong></div><div className="topbar-actions"><span className={`target-status ${target?.healthy === true ? 'cyan-text' : target?.healthy === false || target?.running === false ? 'orange-text' : ''}`}><span className={`status-dot ${target?.healthy === true ? 'live' : target?.healthy === false || target?.running === false ? 'warning' : ''}`} />{configured ? targetLabel : 'Проект не подключён'}</span><button className="icon-button" aria-label="Обновить консоль" title="Обновить консоль" onClick={() => { refresh(); setConfigRevision(previous => previous + 1) }}><Icon name="refresh" /></button></div></header>
      <div className="page-content"><div className="page-heading"><div><h1>{!configured ? 'Подключение проекта' : page === 'Dashboard' ? 'Операционный обзор' : page === 'Incidents' ? 'Работа с инцидентами' : label(page)}</h1><p>{configured ? descriptions[page] : `Для проекта ${project} мониторинг ещё не настроен.`}</p></div>{configured && <span className={`live-label ${connection === 'live' ? 'cyan-text' : ''}`}><Icon name="pulse" size={16} />{connection === 'live' ? 'Телеметрия в реальном времени' : 'Сверка каждые 15 секунд'}</span>}</div>
        {!configured ? <section className="settings-page"><Empty title={`Проект ${project} ещё не подключён`} icon="settings">Для настройки доктора для ещё одного проекта попросите вашего системного администратора его сюда добавить.</Empty></section> : <>
        {status.error && <ErrorNotice>Не удалось обновить состояние системы. {status.error}</ErrorNotice>}
        {(page === 'Dashboard' || page === 'Incidents' || page === 'Exclusions') && <>{page === 'Exclusions' ? <ExclusionSummary stats={status.data?.stats} /> : <><Telemetry status={status.data} /><div className="system-strip"><div><Icon name="pulse" size={15} /><span>Приложение</span><strong>{targetLabel}</strong>{target?.container_id && <code title={target.container_id}>{target.container_id.slice(0, 12)}</code>}</div><details><summary>Сборщик и использование</summary><div className="system-popover"><span className="field-label">Сборщик</span><DataValue value={status.data?.collector} /><span className="field-label">Использование токенов</span><DataValue value={status.data?.usage} />{target?.health != null && <><span className="field-label">Состояние приложения</span><DataValue value={target.health} /></>}</div></details></div></>}
          {page !== 'Exclusions' && <ExcludedNotice stats={status.data?.stats} onOpen={() => { setPage('Exclusions'); setFilterStatus(''); setSeverity(''); setSearch(''); setSelectedId(null) }} />}
          <div className="incident-workspace"><section className="incident-queue" aria-label={page === 'Exclusions' ? 'Исключённые ошибки' : 'Очередь инцидентов'}><div className="queue-heading"><h2>{page === 'Exclusions' ? 'Исключённые ошибки' : 'Очередь инцидентов'}</h2><span className="queue-count" title="Показано инцидентов с учётом фильтров (до 300)">{incidents.data ? filtered.length : '—'}</span></div><div className="queue-controls"><label className="search-input"><Icon name="search" size={16} /><input type="search" placeholder="Найти инцидент…" value={search} onChange={event => setSearch(event.target.value)} aria-label="Поиск инцидентов" /></label><div className="queue-filters"><label><span className="sr-only">Фильтр по статусу</span><select value={filterStatus} onChange={event => setFilterStatus(event.target.value)}><option value="">Все статусы</option>{Array.from(new Set([...(page === 'Exclusions' ? exclusionStatuses : incidentStatuses), ...(incidents.data || []).map(incident => incident.status)])).map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label><label><span className="sr-only">Фильтр по важности</span><select value={severity} onChange={event => setSeverity(event.target.value)}><option value="">Любая важность</option>{Array.from(new Set(['ERROR', 'WARNING', 'CRITICAL', ...(incidents.data || []).map(incident => incident.severity)])).map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label></div></div>
            {incidents.error && <ErrorNotice>{incidents.error}</ErrorNotice>}
            {incidents.loading && !incidents.data ? <div className="loading">Загрузка инцидентов…</div> : filtered.length ? <div className="incident-list">{filtered.map(incident => <button key={incident.id} className={`incident-card ${activeId === incident.id ? 'selected' : ''}`} aria-pressed={activeId === incident.id} onClick={() => setSelectedId(incident.id)}><div className="incident-card-top"><span className={`severity-mark ${/ERROR|CRITICAL/i.test(incident.severity) ? 'warning' : ''}`} /> <span className="mono">INC-{incident.id}</span><span className="incident-card-status">{label(incident.status)}</span></div><strong>{incident.exception_type || 'Инцидент приложения'}</strong><p>{incident.message || 'Сообщение отсутствует'}</p><div className="incident-card-bottom"><span>{incident.service}</span><span>{number(incident.occurrences)}×</span><time>{date(incident.last_seen)}</time></div></button>)}</div> : !incidents.error && <Empty title={search || filterStatus || severity ? 'Подходящих инцидентов нет' : 'Инцидентов пока нет'} icon="incidents">{search || filterStatus || severity ? 'Измените запрос или фильтры, чтобы увидеть другие инциденты.' : 'Здесь появятся события, собранные из целевого приложения.'}</Empty>}
          </section><div className="detail-workspace">{activeId != null ? <IncidentPanel key={activeId} id={activeId} revision={revision} refresh={refresh} models={models} settings={settings.data} /> : <div className="idle-workspace"><div className="idle-graphic" aria-hidden="true"><Icon name="pulse" size={74} /></div><h2>{incidents.error ? 'Данные инцидентов недоступны' : incidents.loading ? 'Загрузка очереди инцидентов' : 'Ожидание следующего сигнала'}</h2><p>{incidents.error ? 'Проверьте подключение к API и обновите консоль. Вымышленные данные не используются.' : 'Выберите инцидент, чтобы изучить данные, выполнить диагностику и подготовить исправление к проверке.'}</p><div className="idle-process"><span>Данные</span><Icon name="arrow" size={14} /><span>Диагностика</span><Icon name="arrow" size={14} /><span>Проверенное исправление</span></div></div>}</div></div>
        </>}
        {page === 'Deployments' && <DeploymentsPage revision={revision} />}
        {page === 'Alerts' && <AlertsPage />}
        {page === 'Settings' && <SettingsPage settings={settings.data} models={models} error={settings.error} loading={settings.loading} onSaved={() => setConfigRevision(previous => previous + 1)} />}
        {(page === 'Settings' || activeId != null && (page === 'Dashboard' || page === 'Incidents')) && modelResponse.error && <div className="model-warning"><ErrorNotice>Список моделей недоступен. {modelResponse.error} В списках показаны только идентификаторы, полученные от сервера; пустой выбор использует модель по умолчанию.</ErrorNotice></div>}
        {page === 'Settings' && !modelResponse.error && !modelResponse.loading && models.length === 0 && <p className="muted small">Сервер не вернул модели. Настройте провайдера на сервере и обновите страницу.</p>}
        </>}
        <footer className="page-footer"><span>AI Doctor <span className="footer-slash">/</span> Диагностика на основе данных. Развёртывание с согласия.</span><span>API на том же источнике <code>/api</code></span></footer>
      </div>
    </main>
  </div>
}
