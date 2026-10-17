import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/labels.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
})
const { label } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('navigation, statuses, severity, roles and result headings have Russian display labels', () => {
  for (const value of [
    'Dashboard', 'Incidents', 'Exclusions', 'Alerts', 'Deployments', 'Settings', 'EXCLUDED',
    'NEW', 'OPEN', 'DIAGNOSED', 'FIXING', 'RESOLVED', 'NO_ACTION', 'SKIPPED',
    'PENDING', 'ANALYZING', 'COOLDOWN', 'QUEUED', 'RUNNING', 'PREPARING', 'TESTING',
    'READY', 'DONE', 'FAILED', 'BUDGET_EXHAUSTED', 'INTERRUPTED', 'DEPLOYING',
    'DEPLOYED', 'SUCCEEDED', 'ROLLED_BACK', 'ROLLBACK_FAILED', 'UNKNOWN', 'ABORTED',
    'ERROR', 'WARNING', 'CRITICAL', 'healthy', 'unhealthy',
    'runner unavailable', 'Configured service unavailable',
    'observations', 'hypotheses', 'recommendations', 'locations',
    'user', 'assistant', 'system', 'tool', 'diagnosis', 'chat', 'fix', 'fix_patch',
  ]) assert.match(label(value), /[А-Яа-яЁё]/u, value)
})

test('unknown technical identifiers are preserved and missing labels stay empty', () => {
  for (const value of ['deepseek-model-id', 'NullPointerException', 'future_status']) {
    assert.equal(label(value), value)
  }
  assert.equal(label(), '')
  assert.equal(label(''), '')
})

test('the document declares Russian for browser and accessibility features', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  assert.match(html, /<html lang="ru">/)
  assert.match(html, /<title>AI Doctor · Консоль инцидентов<\/title>/)
})
