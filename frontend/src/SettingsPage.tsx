import { useEffect, useState } from 'react'
import { mutate } from './api'
import { Badge, ErrorNotice, Icon, ModelSelect } from './components'
import type { Model, Settings } from './types'

const numericFields = [
  ['warn_threshold', 'Warning threshold', 'Occurrences needed to promote a warning.', 1],
  ['warn_window_seconds', 'Warning window', 'Seconds in the warning aggregation window.', 1],
  ['diagnosis_cooldown_seconds', 'Diagnosis cooldown', 'Seconds before another automatic diagnosis.', 0],
  ['max_context_tokens', 'Context token limit', 'Maximum context supplied to the model.', 1],
  ['max_output_tokens', 'Output token limit', 'Maximum tokens generated per response.', 1],
  ['max_agent_iterations', 'Agent iteration limit', 'Maximum iterations while preparing a fix.', 1],
] as const
type Editable = { default_diagnosis_model: string; default_fix_model: string } & Record<(typeof numericFields)[number][0], string>

export default function SettingsPage({ settings, models, error, loading, onSaved }: { settings?: Settings; models: Model[]; error?: string; loading: boolean; onSaved: () => void }) {
  const [draft, setDraft] = useState<Editable | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => {
    if (settings && !dirty) setDraft({
      default_diagnosis_model: settings.default_diagnosis_model || '',
      default_fix_model: settings.default_fix_model || '',
      ...Object.fromEntries(numericFields.map(([key]) => [key, settings[key] == null ? '' : String(settings[key])])),
    } as Editable)
  }, [settings, dirty])
  const change = (key: keyof Editable, value: string) => { setDraft(previous => previous ? { ...previous, [key]: value } : previous); setDirty(true); setMessage('') }
  const save = async () => {
    if (!draft || saving) return
    setSaving(true); setSaveError(''); setMessage('')
    try {
      const values: Record<string, string | number> = { default_diagnosis_model: draft.default_diagnosis_model, default_fix_model: draft.default_fix_model }
      for (const [key, label, , min] of numericFields) {
        if (draft[key] === '') continue
        const value = Number(draft[key])
        if (!Number.isSafeInteger(value) || value < min) throw new Error(`${label} must be an integer of at least ${min}.`)
        values[key] = value
      }
      await mutate('/settings', values, 'PATCH')
      setMessage('Settings saved.'); setDirty(false); onSaved()
    } catch (error) { setSaveError(error instanceof Error ? error.message : String(error)) }
    finally { setSaving(false) }
  }
  return <div className="settings-page">
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading && !settings && <div className="loading">Loading settings…</div>}
    {draft && <form onSubmit={event => { event.preventDefault(); save() }}><section className="settings-section"><div className="settings-intro"><Icon name="pulse" size={22} /><h2>Model configuration</h2><p>Use a discovered model, or leave a default empty to preserve server-side behavior.</p></div><div className="settings-fields"><div className="credential-status"><span>Provider credentials</span><Badge tone={settings?.has_api_key === true ? 'cyan' : settings?.has_api_key === false ? 'warning' : ''}>{settings?.has_api_key === true ? 'Configured on server' : settings?.has_api_key === false ? 'Not configured' : 'Not reported'}</Badge></div><ModelSelect label="Default diagnosis model" value={draft.default_diagnosis_model} onChange={value => change('default_diagnosis_model', value)} models={models} /><ModelSelect label="Default fix model" value={draft.default_fix_model} onChange={value => change('default_fix_model', value)} models={models} /><p className="small muted">API keys are never entered or stored in this browser. Configure credentials in the server environment.</p></div></section>
      <section className="settings-section"><div className="settings-intro"><Icon name="settings" size={22} /><h2>Detection &amp; budgets</h2><p>Control warning aggregation and the resources available to diagnosis and fix agents.</p></div><div className="settings-fields numeric-fields">{numericFields.map(([key, label, hint, min]) => <label key={key}><span>{label}</span><input type="number" min={min} step="1" value={draft[key]} onChange={event => change(key, event.target.value)} placeholder="Not reported" /><small>{hint}</small></label>)}</div></section>
      {saveError && <ErrorNotice>{saveError}</ErrorNotice>}{message && <p className="feedback" role="status">{message}</p>}<div className="settings-save"><p><Icon name="shield" size={16} />Deployment policy is managed by the server, not this console.</p><button className="button primary" type="submit" disabled={saving || !dirty || !!error}>{saving ? 'Saving…' : 'Save settings'}</button></div>
    </form>}
  </div>
}
