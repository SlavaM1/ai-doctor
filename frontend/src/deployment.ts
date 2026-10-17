import type { FixJob, JsonObject } from './types'

export function canContinueFix(job: FixJob) {
  return !job.new_workspace_required && !job.superseded && (job.remaining_tool_calls ?? 1) > 0 &&
    ['FAILED', 'BUDGET_EXHAUSTED', 'INTERRUPTED'].includes(job.status) && (job.remaining_calls ?? 0) > 0
}

export function testsPassed(tests: JsonObject, sha: string) {
  return tests.passed === true && tests.skipped !== true && tests.timed_out !== true &&
    (tests.patch_sha == null || tests.patch_sha === sha)
}

export function canDeploy(job: FixJob) {
  return job.status === 'READY' && !!job.patch?.trim() && /^[a-f0-9]{64}$/.test(job.patch_sha || '') &&
    testsPassed(job.tests || {}, job.patch_sha) &&
    (job.tests?.base_commit == null || job.tests.base_commit === job.base_commit)
}
