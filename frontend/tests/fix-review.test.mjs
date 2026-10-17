import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import ts from 'typescript'
const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const source = readFileSync(new URL('../src/IncidentPanel.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} })
const mockedRequire = name => name === './api' ? {}
  : name === './deployment' ? { canContinueFix: () => false, canDeploy: () => false, testsPassed: () => false }
    : name === './components' ? { Badge: 'span', Icon: () => null, ErrorNotice: 'aside',
      DataValue: ({ value }) => React.createElement('pre', null, JSON.stringify(value)),
      date: String, number: String, text: String, statusTone: () => '' }
      : name === './labels' ? { label: value => value }
        : name === './IncidentControls' ? { ManualGit: () => null } : require(name)
const module = { exports: {} }
new Function('require', 'module', 'exports', outputText)(mockedRequire, module, module.exports)

test('failed fix shows saved deployment reason and a new workspace instruction', () => {
  const job = { id: 35, status: 'FAILED', tests: {}, events: [], new_workspace_required: true,
    deployment: { id: 2, status: 'FAILED', result: { error: 'Runner HTTP 409: <safe reason>', recovery_required: false } } }
  const html = renderToStaticMarkup(React.createElement(module.exports.FixReview, { job, refresh() {}, onError() {}, stale: false }))
  assert.match(html, /Развёртывание #2: FAILED/)
  assert.match(html, /Runner HTTP 409: &lt;safe reason&gt;/)
  assert.match(html, /будет создана новая рабочая область/)
  assert.doesNotMatch(html, /Продолжить в пределах лимита/)
})
