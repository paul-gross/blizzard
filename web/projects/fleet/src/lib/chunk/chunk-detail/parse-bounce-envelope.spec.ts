import { bounceReason, parseBounceEnvelope } from './parse-bounce-envelope';

describe('parseBounceEnvelope', () => {
  it('reads cause, detail and the unlanded rows of a stored step envelope, ignoring its step', () => {
    const raw = JSON.stringify({
      cause: 'failure',
      detail: 'd',
      unlanded: [
        { repo: 'acme/one', branch: 'feat/x', commit: 'a1', pr: { number: 7, url: 'https://forge/pull/7' } },
        { repo: 'acme/two', branch: 'feat/x', commit: 'b2', pr: null },
      ],
      step: { name: 'tests', exit_code: 2, output_tail: 'boom', log_artifact: 'hub-log.tests' },
    });
    expect(parseBounceEnvelope(raw)).toEqual({
      cause: 'failure',
      detail: 'd',
      unlanded: [
        { repo: 'acme/one', branch: 'feat/x', commit: 'a1', pr: { number: 7, url: 'https://forge/pull/7' } },
        { repo: 'acme/two', branch: 'feat/x', commit: 'b2', pr: null },
      ],
      action: null,
    });
  });

  it('reads the action a pass answered', () => {
    const raw = JSON.stringify({
      cause: 'failure',
      detail: 'acme/one did not land: `land` answered failure',
      unlanded: [],
      action: { name: 'land', choice: 'failure', log_artifact: null },
    });
    expect(parseBounceEnvelope(raw)).toEqual({
      cause: 'failure',
      detail: 'acme/one did not land: `land` answered failure',
      unlanded: [],
      action: { name: 'land', choice: 'failure', log_artifact: null },
    });
  });

  it('defaults an absent unlanded and action, and ignores a key it does not know', () => {
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","waited_s":600}')).toEqual({
      cause: 'c',
      detail: 'd',
      unlanded: [],
      action: null,
    });
  });

  it('returns null for text that is not a JSON object, or an object that does not validate', () => {
    for (const raw of ['not json', '[1]', '"s"', 'null', '', '{}', '{"cause":"c"}', '{"cause":"c","detail":"d","unlanded":{}}']) {
      expect(parseBounceEnvelope(raw)).toBeNull();
    }
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","unlanded":[{"repo":"r"}]}')).toBeNull();
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","action":{"name":"land"}}')).toBeNull();
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","action":{"name":"land","choice":"failure","log_artifact":3}}')).toBeNull();
  });
});

describe('bounceReason', () => {
  it('prefers detail with code ticks dropped, then cause, then a fixed word for a field-less envelope, and unparseable text as it is', () => {
    expect(bounceReason('{"cause":"failure","detail":"node `a` routed `b`"}')).toBe('node a routed b');
    expect(bounceReason('{"cause":"poll-timeout"}')).toBe('poll-timeout');
    expect(bounceReason('{}')).toBe('bounced');
    expect(bounceReason('{"x":1}')).toBe('bounced');
    expect(bounceReason('garbled')).toBe('garbled');
  });
});
