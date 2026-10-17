import { useEffect, useState } from 'react'
import { mutate } from './api'
import { Badge, ErrorNotice, Icon, ModelSelect } from './components'
import type { Model, Settings } from './types'

const numericFields = [
  ['warn_threshold', 'Порог предупреждений', 'Число повторений для повышения важности предупреждения.', 1],
  ['warn_window_seconds', 'Окно предупреждений', 'Длительность окна объединения предупреждений в секундах.', 1],
  ['diagnosis_cooldown_seconds', 'Пауза между диагностиками', 'Число секунд до следующей автоматической диагностики.', 0],
  ['max_context_tokens', 'Лимит токенов контекста', 'Максимальный объём контекста, передаваемого модели.', 1],
  ['max_output_tokens', 'Лимит токенов ответа', 'Максимальное число токенов в одном ответе.', 1],
  ['max_agent_iterations', 'Лимит итераций агента', 'Максимальное число итераций при подготовке исправления.', 1],
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
        if (!Number.isSafeInteger(value) || value < min) throw new Error(`«${label}»: укажите целое число не меньше ${min}.`)
        values[key] = value
      }
      await mutate('/settings', values, 'PATCH')
      setMessage('Настройки сохранены.'); setDirty(false); onSaved()
    } catch (error) { setSaveError(error instanceof Error ? error.message : String(error)) }
    finally { setSaving(false) }
  }
  return <div className="settings-page">
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading && !settings && <div className="loading">Загрузка настроек…</div>}
    {draft && <form onSubmit={event => { event.preventDefault(); save() }}><section className="settings-section"><div className="settings-intro"><Icon name="pulse" size={22} /><h2>Настройка моделей</h2><p>Выберите доступную модель или оставьте поле пустым, чтобы использовать настройки сервера.</p></div><div className="settings-fields"><div className="credential-status"><span>Доступ к провайдеру</span><Badge tone={settings?.has_api_key === true ? 'cyan' : settings?.has_api_key === false ? 'warning' : ''}>{settings?.has_api_key === true ? 'Настроен на сервере' : settings?.has_api_key === false ? 'Не настроен' : 'Нет данных'}</Badge></div><ModelSelect label="Модель диагностики по умолчанию" value={draft.default_diagnosis_model} onChange={value => change('default_diagnosis_model', value)} models={models} /><ModelSelect label="Модель исправлений по умолчанию" value={draft.default_fix_model} onChange={value => change('default_fix_model', value)} models={models} /><p className="small muted">Ключи API не вводятся и не хранятся в браузере. Настройте их в окружении сервера.</p></div></section>
      <section className="settings-section"><div className="settings-intro"><Icon name="settings" size={22} /><h2>Обнаружение и лимиты</h2><p>Настройте объединение предупреждений и лимиты ресурсов для агентов диагностики и исправлений.</p></div><div className="settings-fields numeric-fields">{numericFields.map(([key, label, hint, min]) => <label key={key}><span>{label}</span><input type="number" min={min} step="1" value={draft[key]} onChange={event => change(key, event.target.value)} placeholder="Нет данных" /><small>{hint}</small></label>)}</div></section>
      {saveError && <ErrorNotice>{saveError}</ErrorNotice>}{message && <p className="feedback" role="status">{message}</p>}<div className="settings-save"><p><Icon name="shield" size={16} />Политика развёртывания управляется сервером, а не этой консолью.</p><button className="button primary" type="submit" disabled={saving || !dirty || !!error}>{saving ? 'Сохранение…' : 'Сохранить настройки'}</button></div>
    </form>}
  </div>
}
