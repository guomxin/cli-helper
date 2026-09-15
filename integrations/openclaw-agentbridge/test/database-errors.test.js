import test from 'node:test';
import assert from 'node:assert/strict';
import { taskOutcomeForUnreferencedResult } from '../lib/proxy-tools.js';

test('database rejection preserves top-level code and explanation', () => {
  const result = { structuredContent: { status: 'rejected', code: 'DATABASE_SQL_UNSUPPORTED', message: 'Unsupported SQL' } };
  assert.deepEqual(taskOutcomeForUnreferencedResult(result), {
    status: 'failed', errorCode: 'DATABASE_SQL_UNSUPPORTED', message: 'Unsupported SQL',
  });
});

test('a code field in a successful payload is not an error', () => {
  assert.equal(taskOutcomeForUnreferencedResult({ structuredContent: { status: 'succeeded', code: 'item-code' } }).status, 'succeeded');
});
