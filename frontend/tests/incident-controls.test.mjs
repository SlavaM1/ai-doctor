import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/IncidentControls.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} })
let apiResponse
const mockedRequire = name => name === './api' ? { useApi: () => ({ data: apiResponse }), mutate: async () => ({}) }
  : name === './components' ? { ConfirmDialog: 'dialog', DataValue: 'pre', ErrorNotice: 'aside', date: value => value }
    : name === './labels' ? { label: value => value }
      : name === './deployment' ? { testsPassed: (tests, sha) => tests.passed === true && (!tests.patch_sha || tests.patch_sha === sha) } : require(name)
const module = { exports: {} }
new Function('require', 'module', 'exports', outputText)(mockedRequire, module, module.exports)
const { SessionLog, IncidentControls, ManualGit, incidentStatuses } = module.exports
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

test('session log renders error and exactly supplied before/after neighbors', () => {
  apiResponse = { trace_id: 'session-123', before: [{ timestamp: 'before-time', text: 'before event' }],
    error: { timestamp: 'error-time', text: '<script>error evidence</script>' }, after: [] }
  const html = renderToStaticMarkup(React.createElement(SessionLog, { id: 1, revision: 0 }))
  assert.match(html, /Лог|traceId: session-123/)
  assert.match(html, /До ошибки/)
  assert.match(html, /Получено|получено 0 из 5/)
  assert.match(html, /&lt;script&gt;error evidence&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<script>/)
})

test('ignored incident status is selectable and deletion has an explicit action', () => {
  assert.ok(incidentStatuses.includes('NO_ACTION'))
  const html = renderToStaticMarkup(React.createElement(IncidentControls, { id: 1, status: 'NO_ACTION', refresh() {} }))
  assert.match(html, /Удалить инцидент/)
  assert.match(html, /Автоматический анализ отключён/)
})

test('manual Git controls cannot publish unready patches', () => {
  const job = { id: 1, incident_id: 1, status: 'FAILED', patch_sha: 'a'.repeat(64), tests: { passed: false } }
  const html = renderToStaticMarkup(React.createElement(ManualGit, { job, stale: false }))
  assert.equal((html.match(/disabled=""/g) || []).length, 3)
  assert.match(html, /Создать ветку от текущей/)
  assert.match(html, /Создать коммит/)
  assert.match(html, /Отправить в GitHub/)
})

test('excluded errors offer restoration rather than status bypass or deletion', () => {
  const html = renderToStaticMarkup(React.createElement(IncidentControls, { id: 1, status: 'EXCLUDED', refresh() {} }))
  assert.match(html, /Вернуть в инциденты/)
  assert.match(html, /не анализируется агентом/)
  assert.doesNotMatch(html, /Изменить статус|Удалить инцидент/)
  const normal = renderToStaticMarkup(React.createElement(IncidentControls, { id: 1, status: 'NEW', refresh() {} }))
  assert.match(normal, /Исключить этот тип ошибок/)
})
