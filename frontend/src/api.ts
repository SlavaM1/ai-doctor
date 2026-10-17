import { useEffect, useRef, useState } from 'react'

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(options?.method && options.method !== 'GET' ? { 'Content-Type': 'application/json', 'X-Doctor-Action': '1' } : {}),
      ...options?.headers,
    },
  })
  const text = await response.text()
  let value: unknown
  try { value = text ? JSON.parse(text) : null } catch {
    throw new Error(response.ok ? 'API вернул ответ не в формате JSON. Проверьте прокси /api.' : `Ошибка запроса к API (${response.status}).`)
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('doctor-unauthorized'))
    const detail = value && typeof value === 'object' && 'detail' in value ? (value as { detail: unknown }).detail : value
    throw new Error(typeof detail === 'string' ? detail : `Ошибка запроса к API (${response.status})${detail ? `: ${JSON.stringify(detail)}` : '.'}`)
  }
  return value as T
}

export function mutate<T>(path: string, body: unknown, method = 'POST') {
  return request<T>(path, { method, body: JSON.stringify(body) })
}

export function useApi<T>(path: string | null, revision = 0) {
  const [state, setState] = useState<{ path: string | null; data?: T; error?: string; loading: boolean }>({ path, loading: !!path })
  useEffect(() => {
    if (!path) { setState({ path, loading: false }); return }
    const controller = new AbortController()
    setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, loading: true }))
    request<T>(path, { signal: controller.signal })
      .then(data => setState({ path, data, loading: false }))
      .catch(error => {
        if (!controller.signal.aborted) setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, error: String(error.message || error), loading: false }))
      })
    return () => controller.abort()
  }, [path, revision])
  return state.path === path ? state : { path, loading: !!path, data: undefined, error: undefined }
}

export function useLiveEvents(onRefresh: (settingsChanged?: boolean) => void, enabled = true) {
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting'>('connecting')
  const callback = useRef(onRefresh)
  callback.current = onRefresh
  useEffect(() => {
    if (!enabled) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let settingsChanged = false
    const events = new EventSource('/api/events')
    events.onopen = () => { setConnection('live'); callback.current() }
    events.onmessage = event => {
      try { settingsChanged ||= JSON.parse(event.data)?.type === 'settings' } catch { /* An untyped event still refreshes incident data. */ }
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => { callback.current(settingsChanged); settingsChanged = false }, 350)
    }
    events.onerror = () => {
      setConnection('reconnecting')
      request('/auth/me').catch(() => { /* Unauthorized responses return to login. */ })
    }
    return () => { events.close(); if (timer) clearTimeout(timer) }
  }, [enabled])
  return connection
}
