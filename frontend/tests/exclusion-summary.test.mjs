import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/ExclusionSummary.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} })
const module = { exports: {} }
const mockedRequire = name => name === './components' ? { number: value => value ?? '—' } : require(name)
new Function('require', 'module', 'exports', outputText)(mockedRequire, module, module.exports)
const { default: ExclusionSummary, ExcludedNotice, exclusionStatuses } = module.exports
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

test('exclusions always offer both ignored statuses and clear lifetime/window counters', () => {
  assert.deepEqual(exclusionStatuses, ['EXCLUDED', 'NO_ACTION'])
  const html = renderToStaticMarkup(React.createElement(ExclusionSummary, { stats: {
    excluded_incidents: 7, excluded_occurrences: 31, excluded_errors_hour: 2, excluded_errors_day: 9,
  } }))
  for (const value of [7, 31, 2, 9]) assert.ok(html.includes(`<strong>${value}</strong>`))
  for (const text of ['Статистика исключений', 'Не требует решения', 'Возникновений всего', 'Первое событие и все повторы', 'За последний час', 'За последние 24 часа']) assert.ok(html.includes(text))
})

test('unknown counters are not shown as zero', () => {
  const html = renderToStaticMarkup(React.createElement(ExclusionSummary))
  assert.equal((html.match(/<strong>—<\/strong>/g) || []).length, 4)
})

test('overview explains collected errors hidden by exclusions and offers navigation', () => {
  const html = renderToStaticMarkup(React.createElement(ExcludedNotice, { stats: {
    excluded_incidents: 1, excluded_errors_hour: 5,
  }, onOpen() {} }))
  assert.match(html, /Ошибки в исключениях: 1/)
  assert.match(html, /За последний час: 5 повторений/)
  assert.match(html, /учитываются в счётчиках/)
  assert.match(html, /Показать исключённые ошибки/)
})

test('overview does not invent excluded errors while loading or without exclusions', () => {
  for (const stats of [undefined, { excluded_incidents: 0 }]) {
    assert.equal(renderToStaticMarkup(React.createElement(ExcludedNotice, { stats, onOpen() {} })), '')
  }
})
