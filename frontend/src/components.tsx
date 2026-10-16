import { useEffect, useRef, type ReactNode } from 'react'
import type { Model } from './types'

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    incidents: <><path d="m12 3 10 18H2L12 3Z" /><path d="M12 9v5m0 3h.01" /></>,
    deployments: <><path d="m12 3 8 5-8 5-8-5 8-5Zm-8 9 8 5 8-5M4 16l8 5 8-5" /></>,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
    pulse: <path d="M2 12h5l3-8 4 16 3-8h5" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>,
    source: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 20" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6l8-3Z" /><path d="m8 12 3 3 5-6" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    chat: <path d="M21 12a8 8 0 0 1-8 8H7l-5 2 2-6v-4a8 8 0 0 1 8-8h1a8 8 0 0 1 8 8Z" />,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.pulse}</svg>
}

export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export function statusTone(status?: string) {
  if (!status) return ''
  if (/FAIL|ERROR|EXHAUSTED|CRITICAL|UNHEALTHY/i.test(status)) return 'warning'
  if (/READY|HEALTHY|SUCCESS|COMPLETED|RESOLVED|DEPLOYED|PASSED/i.test(status)) return 'cyan'
  return ''
}

export function ErrorNotice({ children }: { children: ReactNode }) {
  return <div className="error-notice" role="alert"><Icon name="incidents" size={16} /><span>{children}</span></div>
}

export function Empty({ title, children, icon = 'pulse' }: { title: string; children?: ReactNode; icon?: string }) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={26} /></span><h3>{title}</h3>{children && <p>{children}</p>}</div>
}

export function ModelSelect({ value, onChange, models, label = 'Model', configured }: { value: string; onChange: (value: string) => void; models: Model[]; label?: string; configured?: string }) {
  return <label className="model-select"><span>{label}</span><select value={value} onChange={event => onChange(event.target.value)}>
    <option value="">{configured ? `Default: ${configured}` : 'Use server default'}</option>
    {models.map(model => <option key={model.id} value={model.id}>{model.id}</option>)}
    {value && !models.some(model => model.id === value) && <option value={value}>{value} (configured)</option>}
  </select></label>
}

export function date(value?: string) {
  if (!value) return 'Not reported'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function number(value?: number) {
  return typeof value === 'number' ? new Intl.NumberFormat().format(value) : '—'
}

export function text(value: unknown): string {
  if (value == null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

export function DataValue({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="muted">Not reported</span>
  if (Array.isArray(value)) return value.length ? <ul className="data-list">{value.map((entry, index) => <li key={index}><DataValue value={entry} /></li>)}</ul> : <span className="muted">None reported</span>
  if (typeof value === 'object') return <pre className="structured-data">{text(value)}</pre>
  return <span className="prose">{String(value)}</span>
}

export function ConfirmDialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return <dialog ref={dialog} className="confirm-dialog" aria-labelledby="dialog-title" onCancel={event => { event.preventDefault(); close.current() }}>
    <div className="dialog-heading"><span className="shield-icon"><Icon name="shield" size={24} /></span><button className="icon-button" aria-label="Close confirmation" onClick={onClose}><Icon name="close" /></button></div>
    <h2 id="dialog-title">{title}</h2>{children}
  </dialog>
}
