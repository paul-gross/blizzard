import type { runnerApi } from 'fleet';

import { selftestRows, selftestTone, startingHarness } from './selftest.model';

const health = (harness_id: string, available = true): runnerApi.HarnessHealthView => ({
  harness_id, available, admitted_range: null, cause: null, degradations: [], version: null,
});
const run = (harness: string, status: string): runnerApi.SelfTestView => ({ id: `st_${harness}`, harness, status, checks: [], error: null });

describe('selftestRows', () => {
  it('gives each harness its latest read over its started run, busy while starting or running', () => {
    const rows = selftestRows(
      [health('a'), health('b'), health('c', false)],
      new Map([['a', run('a', 'running')], ['b', run('b', 'running')]]),
      new Map([['b', { run: run('b', 'passed'), error: null }]]),
      'c',
      new Map(),
    );
    expect(rows.map((row) => [row.harness, row.run?.status ?? null, row.busy, row.available])).toEqual([
      ['a', 'running', true, true],
      ['b', 'passed', false, true],
      ['c', null, true, false],
    ]);
  });

  it('prefers the start failure over the run read failure', () => {
    const rows = selftestRows([health('a')], new Map(), new Map([['a', { run: null, error: 'read failed' }]]), null, new Map([['a', 'boom']]));
    expect(rows[0].error).toBe('boom');
    expect(selftestRows([health('a')], new Map(), new Map([['a', { run: null, error: 'read failed' }]]), null, new Map())[0].error)
      .toBe('read failed');
  });
});

describe('selftestTone', () => {
  it('tones each status, an unlisted one idle', () => {
    expect(['running', 'passed', 'failed', 'queued'].map((status) => selftestTone(status))).toEqual(['spawning', 'done', 'needs', 'idle']);
  });
});

describe('startingHarness', () => {
  it('names the harness only while its start is pending', () => {
    expect(startingHarness(true, 'mock')).toBe('mock');
    expect(startingHarness(false, 'mock')).toBeNull();
    expect(startingHarness(true, undefined)).toBeNull();
  });
});
