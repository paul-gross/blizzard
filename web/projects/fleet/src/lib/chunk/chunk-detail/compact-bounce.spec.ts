import { compactBounce } from './compact-bounce';

const repo = { repo: 'org/repo', branch: 'work', commit: 'abc', pr: { number: 42, url: 'https://example.test/pull/42' } };
const action = { name: 'pre-release', choice: 'failure', log_artifact: 'artifact-1' };
const envelope = (detail: string, cause = 'failure') => JSON.stringify({ cause, detail, unlanded: [repo], action });

describe('compactBounce', () => {
  for (const recordedCause of [null, 'failure']) {
    it.each([
      '{"detail":"","error":"permission denied","code":403}',
      '{"cause":"","detail":"","diagnostic":{"message":"permission denied","code":403}}',
    ])(`keeps mixed unknown diagnostics with recorded cause ${recordedCause}: %s`, (raw) => {
      expect(compactBounce(raw, recordedCause)).toEqual({ identity: null, reason: raw, full: raw });
    });
  }

  it('keeps meaningful recognized detail ahead of unknown extra fields', () => {
    const raw = '{"cause":"","detail":"repository permission denied","diagnostic":{"code":403}}';
    expect(compactBounce(raw, 'failure')).toEqual({ identity: null, reason: 'repository permission denied', full: 'repository permission denied' });
  });

  it.each(['{}', '', '  ', '{"cause":"","detail":""}'])('uses the recorded cause when the envelope has no account: %s', (raw) => {
    expect(compactBounce(raw, 'poll-timeout')).toEqual({ identity: null, reason: 'poll-timeout', full: 'poll-timeout' });
  });

  it('does not replace a specific recovered cause or unknown diagnostic with the outer cause', () => {
    expect(compactBounce(envelope('', 'repository permission denied'), 'failure').reason).toBe('repository permission denied');
    const raw = '{"error":"permission denied","code":403}';
    expect(compactBounce(raw, 'failure')).toEqual({ identity: null, reason: raw, full: raw });
    expect(compactBounce(envelope('', ''), 'poll-timeout').reason).toBe('poll-timeout');
    expect(compactBounce(envelope('bounced'), 'poll-timeout').reason).toBe('bounced');
    expect(compactBounce(envelope('lint found 3 errors'), 'poll-timeout').full).toBe('lint found 3 errors');
  });
  it.each([
    'PR #42 in org/repo did not land: pre-release answered failure',
    'pre-release answered failure',
  ])('compacts only the known producer wrapper: %s', (detail) => {
    expect(compactBounce(envelope(detail))).toEqual({ identity: 'org/repo · PR #42', reason: 'Pre-release: failure', full: detail });
  });

  it('keeps a distinct cause when detail is empty instead of replacing it with the action choice', () => {
    expect(compactBounce(envelope('', 'repository permission denied'))).toEqual({
      identity: 'org/repo · PR #42', reason: 'repository permission denied', full: 'repository permission denied',
    });
  });

  it('uses the action only when neither detail nor cause gives an account', () => {
    expect(compactBounce(envelope('', '')).reason).toBe('Pre-release: failure');
  });

  it.each(['lint found 3 errors', 'PR #42 in org/repo did not land: pre-release answered failure: permission denied'])('keeps unknown diagnostics: %s', (detail) => {
    expect(compactBounce(envelope(detail))).toEqual({ identity: 'org/repo · PR #42', reason: detail, full: detail });
  });

  it('recognizes the legacy single-PR wrapper without swallowing a diagnostic suffix', () => {
    expect(compactBounce('PR #42 in org/repo did not land: `pre-release` answered failure')).toEqual({
      identity: 'org/repo · PR #42', reason: 'Pre-release: failure', full: 'PR #42 in org/repo did not land: `pre-release` answered failure',
    });
    const raw = 'PR #42 in org/repo did not land: pre-release answered failure: connection reset';
    expect(compactBounce(raw)).toEqual({ identity: null, reason: raw, full: raw });
  });

  it.each(['{}', ' { }\n', '', '  ', '{"cause":"","detail":""}'])('uses the generic fallback for an envelope without an account: %s', (raw) => {
    expect(compactBounce(raw)).toEqual({ identity: null, reason: 'bounced', full: 'bounced' });
  });

  it.each(['not json {', 'unknown `diagnostic` with details', '{"action":42', '{"error":"permission denied","code":403}', '{"x":1}', '["failure"]'])('preserves unrecognized raw errors: %s', (raw) => {
    expect(compactBounce(raw)).toEqual({ identity: null, reason: raw, full: raw });
  });

  describe('a record carrying diagnostic', () => {
    const fact = (over: object) => JSON.stringify({ cause: 'failure', detail: '', unlanded: [repo], action, diagnostic: null, ...over });

    it('derives identity, reason and explanation from the facts', () => {
      expect(compactBounce(fact({}))).toEqual({
        identity: 'org/repo · PR #42', reason: 'Pre-release: failure', full: 'PR #42 in org/repo did not land: Pre-release answered failure',
      });
    });

    it('never runs the wrapper match: a wrapper-shaped detail beside a known diagnostic is not unwrapped', () => {
      const detail = 'PR #42 in org/repo did not land: `pre-release` answered failure';
      expect(compactBounce(fact({ detail, diagnostic: detail })).reason).toBe('PR #42 in org/repo did not land: pre-release answered failure');
    });

    it('leads with a differing cause over the action choice', () => {
      expect(compactBounce(fact({ cause: 'repository permission denied' })).reason).toBe('repository permission denied');
    });

    it('shows a genuine diagnostic as the reason and joins it into the explanation', () => {
      const out = compactBounce(fact({ action: null, cause: 'poll-timeout', detail: 'hub node `deliver` timed out', diagnostic: 'hub node `deliver` timed out', unlanded: [] }));
      expect(out.reason).toBe('hub node deliver timed out');
      expect(out.full).toBe('delivery did not land. hub node `deliver` timed out');
    });

    it('names several repositories, with and without a pull request, and an action with no log', () => {
      const other = { repo: 'org/two', branch: 'work', commit: 'def', pr: null };
      const out = compactBounce(fact({ unlanded: [repo, other], action: { ...action, log_artifact: null } }));
      expect(out.identity).toBe('org/repo · PR #42; org/two');
      expect(out.full).toBe('PR #42 in org/repo, org/two did not land: Pre-release answered failure');
    });

    it('falls back to the cause, then the recorded cause, then bounced when there is no action', () => {
      expect(compactBounce(fact({ action: null, cause: 'conflict' })).reason).toBe('conflict');
      expect(compactBounce(fact({ action: null, cause: '' }), 'poll-timeout').reason).toBe('poll-timeout');
      expect(compactBounce(fact({ action: null, cause: '' })).reason).toBe('bounced');
    });

    it('treats a malformed diagnostic as an unknown record', () => {
      const raw = fact({ detail: 'PR #42 in org/repo did not land: pre-release answered failure', diagnostic: 42 });
      expect(compactBounce(raw).reason).toBe('Pre-release: failure');
      expect(compactBounce(raw).full).toBe('PR #42 in org/repo did not land: pre-release answered failure');
    });
  });
});
