import type { HarnessHealth } from '../core/runner-status.domain';
import { selftestRun } from './selftest.builders';
import type { SelftestRun, SelftestStatus } from './selftest.domain';
import { checkTone, pendingHarnesses, selftestPhase, selftestRows, selftestRunRefs, selftestTone } from './selftest.model';

const health = (harness_id: string, available = true): HarnessHealth => ({
  harness_id, available, admitted_range: null, cause: null, degradations: [], version: null,
});
const status = (raw: string): SelftestStatus =>
  raw === 'running' || raw === 'passed' || raw === 'failed' ? raw : { kind: 'unknown', raw };
const run = (harness: string, raw: string, settled?: boolean): SelftestRun =>
  selftestRun({ id: `st_${harness}`, harness, status: status(raw), settled: settled ?? null });
const none = new Set<string>();

describe('selftestPhase', () => {
  it('reads running, settled, or unclassified from settled alone', () => {
    expect(selftestPhase(run('a', 'running', false))).toBe('running');
    expect(selftestPhase(run('a', 'passed', true))).toBe('settled');
    expect(selftestPhase(run('a', 'failed', true))).toBe('settled');
    expect(selftestPhase(run('a', 'running'))).toBe('unclassified');
  });

  it('classifies an unknown status by settled, never by the status', () => {
    expect(selftestPhase(run('a', 'queued', false))).toBe('running');
    expect(selftestPhase(run('a', 'queued', true))).toBe('settled');
    expect(selftestPhase(run('a', 'queued'))).toBe('unclassified');
    expect(selftestPhase(run('a', 'passed'))).toBe('unclassified');
  });
});

describe('selftestRows', () => {
  it('gives each harness its latest read over its started run, busy while pending or running', () => {
    const rows = selftestRows(
      [health('a'), health('b'), health('c', false)],
      new Map([['a', run('a', 'running', false)], ['b', run('b', 'running', false)]]),
      new Map([['b', { run: run('b', 'passed', true), error: null }]]),
      new Set(['c']),
      new Map(),
    );
    expect(rows.map((row) => [row.harness, row.run?.status ?? null, row.busy, row.available])).toEqual([
      ['a', 'running', true, true],
      ['b', 'passed', false, true],
      ['c', null, true, false],
    ]);
  });

  it('holds every pending harness busy at once', () => {
    const rows = selftestRows([health('a'), health('b'), health('c')], new Map(), new Map(), new Set(['a', 'c']), new Map());
    expect(rows.map((row) => row.busy)).toEqual([true, false, true]);
  });

  it('leaves an unclassified run not busy', () => {
    const rows = selftestRows([health('a')], new Map([['a', run('a', 'running')]]), new Map(), none, new Map());
    expect(rows[0].busy).toBe(false);
  });

  it('is not busy when the first read of a started run failed', () => {
    const started = new Map([['a', run('a', 'running', false)]]);
    const rows = selftestRows([health('a')], started, new Map([['a', { run: null, error: 'read failed' }]]), none, new Map());
    expect(rows[0].busy).toBe(false);
  });

  it('prefers the start failure over the run read failure', () => {
    const reads = new Map([['a', { run: null, error: 'read failed' }]]);
    expect(selftestRows([health('a')], new Map(), reads, none, new Map([['a', 'boom']]))[0].error).toBe('boom');
    expect(selftestRows([health('a')], new Map(), reads, none, new Map())[0].error).toBe('read failed');
  });
});

describe('pendingHarnesses', () => {
  it('collects the harness of every pending start', () => {
    expect(pendingHarnesses([{ harness: 'a' }, { harness: 'b' }])).toEqual(new Set(['a', 'b']));
  });
});

describe('selftestRunRefs', () => {
  it('refs each started run by harness and id', () => {
    expect(selftestRunRefs(new Map([['a', run('a', 'running', false)], ['b', run('b', 'passed', true)]])))
      .toEqual([{ harness: 'a', id: 'st_a' }, { harness: 'b', id: 'st_b' }]);
  });
});

describe('selftestTone', () => {
  it('tones each status, an unlisted one idle', () => {
    expect(['running', 'passed', 'failed', 'queued'].map((raw) => selftestTone(status(raw)))).toEqual(['spawning', 'done', 'needs', 'idle']);
  });
});

describe('checkTone', () => {
  it('tones a passed check as a passed run, a failed one as a failed run', () => {
    expect(checkTone(true)).toBe(selftestTone('passed'));
    expect(checkTone(false)).toBe(selftestTone('failed'));
  });
});
