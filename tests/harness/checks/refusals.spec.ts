import { expect, test } from '@playwright/test';
import { parseLines, findBackgroundRefusals, UNHANDLED_REQUEST_ERROR } from '../logs';
import { readContract } from '../env';

const requestRefusal = JSON.stringify({
  event: UNHANDLED_REQUEST_ERROR,
  tenant_id: 'ten_1',
  method: 'GET',
  path: '/api/chunks',
  exception_class: 'UnscopedTenantStatement',
  exception: 'Traceback ... UnscopedTenantStatement: SELECT ...',
});
const backgroundRefusal = JSON.stringify({
  event: 'sweep pass failed',
  logger: 'blizzard.hub.sweeps',
  error: 'UnscopedTenantStatement: SELECT * FROM chunks',
});

test('the refusals are read from the environment contract', () => {
  expect(readContract().refusals).toEqual(['UnscopedTenantStatement', 'UnscopedRunnerStatement']);
});

test('a refusal inside a request, and non-JSON lines, pass', () => {
  const log = [
    'INFO:     Started server process [1]',
    requestRefusal,
    'Traceback (most recent call last):',
    'blizzard.foundation.store.key_scoping.UnscopedTenantStatement: SELECT ...',
  ].join('\n');
  expect(findBackgroundRefusals(parseLines(log))).toEqual([]);
});

test('a refusal outside a request fails, printing its line', () => {
  const log = ['{"event": "sweep pass completed"}', backgroundRefusal].join('\n');
  expect(findBackgroundRefusals(parseLines(log)).map((line) => line.raw)).toEqual([backgroundRefusal]);
});

test('a runner statement refusal fails the same way', () => {
  const line = JSON.stringify({ event: 'tick failed', error: 'UnscopedRunnerStatement' });
  expect(findBackgroundRefusals(parseLines(line)).map((found) => found.raw)).toEqual([line]);
});
