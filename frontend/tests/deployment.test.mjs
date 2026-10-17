import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// Test the production gate without adding a browser or a second test framework.
const source = readFileSync(new URL('../src/deployment.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
})
const { canContinueFix, canDeploy } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const sha = 'a'.repeat(64)
const ready = {
  status: 'READY', patch: '+reviewed change', patch_sha: sha, base_commit: 'base',
  tests: { passed: true, patch_sha: sha, base_commit: 'base' },
}

test('allows a ready, reviewed patch with an explicit matching test pass', () => {
  assert.equal(canDeploy(ready), true)
})

test('never allows queued, running, failed, exhausted, or already deployed jobs', () => {
  for (const status of ['QUEUED', 'RUNNING', 'FAILED', 'BUDGET_EXHAUSTED', 'DEPLOYED', 'INTERRUPTED', '']) {
    assert.equal(canDeploy({ ...ready, status }), false, status)
  }
})

test('requires a nonempty patch and a full SHA-256', () => {
  for (const patch of ['', '  \n']) assert.equal(canDeploy({ ...ready, patch }), false)
  for (const patch_sha of ['', 'abc123', 'z'.repeat(64)]) assert.equal(canDeploy({ ...ready, patch_sha }), false)
})

test('empty, skipped, timed out, or non-boolean passes cannot unlock deployment', () => {
  for (const tests of [{}, { passed: false }, { passed: 'true' }, { passed: true, skipped: true }, { passed: true, timed_out: true }]) {
    assert.equal(canDeploy({ ...ready, tests }), false)
  }
})

test('rejects passing tests for a different patch or base commit', () => {
  assert.equal(canDeploy({ ...ready, tests: { ...ready.tests, patch_sha: 'b'.repeat(64) } }), false)
  assert.equal(canDeploy({ ...ready, tests: { ...ready.tests, base_commit: 'another-base' } }), false)
})

test('supports an explicit pass when optional test hash metadata is not provided', () => {
  assert.equal(canDeploy({ ...ready, tests: { passed: true } }), true)
})

test('stale parent workspace must start a new job instead of endless continuation', () => {
  const stopped = { ...ready, status: 'FAILED', remaining_calls: 20 }
  assert.equal(canContinueFix(stopped), true)
  assert.equal(canContinueFix({ ...stopped, new_workspace_required: true }), false)
  assert.equal(canContinueFix({ ...stopped, superseded: true }), false)
  assert.equal(canContinueFix({ ...stopped, remaining_calls: 0 }), false)
  assert.equal(canContinueFix({ ...stopped, remaining_tool_calls: 0 }), false)
})
