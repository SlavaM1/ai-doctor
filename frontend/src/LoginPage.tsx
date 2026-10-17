import { useState } from 'react'
import { mutate } from './api'
import { ErrorNotice, Icon } from './components'

export type Account = { username: string }

export default function LoginPage({ onLogin }: { onLogin: (account: Account) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <main className="login-page"><section className="login-card">
    <div className="login-brand"><Icon name="pulse" size={32} /><strong>AI Doctor</strong></div>
    <h1>Вход в консоль</h1><p className="muted">Диагностика и контроль ошибок ваших проектов.</p>
    <form onSubmit={async event => {
      event.preventDefault()
      if (busy) return
      setBusy(true); setError('')
      try { onLogin(await mutate<Account>('/auth/login', { username, password })) }
      catch (error) { setError(error instanceof Error ? error.message : 'Не удалось войти. Попробуйте ещё раз.') }
      finally { setBusy(false) }
    }}>
      <label>Логин<input autoComplete="username" required maxLength={100} value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label>Пароль<input type="password" autoComplete="current-password" required maxLength={200} value={password} onChange={event => setPassword(event.target.value)} /></label>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <button className="button primary" disabled={busy || !username || !password}>{busy ? 'Вход…' : 'Войти'}</button>
    </form>
    <p className="small muted">Учётные записи создаёт системный администратор.</p>
  </section></main>
}
