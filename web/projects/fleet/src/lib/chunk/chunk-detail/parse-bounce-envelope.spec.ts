import { bounceReason, parseBounceEnvelope } from './parse-bounce-envelope';

describe('parseBounceEnvelope', () => {
  it('reads cause, detail, the unlanded rows and the failed step', () => {
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
      step: { name: 'tests', exit_code: 2, output_tail: 'boom', log_artifact: 'hub-log.tests' },
    });
  });

  it('defaults an absent unlanded and step, and ignores a key it does not know', () => {
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","waited_s":600}')).toEqual({
      cause: 'c',
      detail: 'd',
      unlanded: [],
      step: null,
    });
  });

  it('returns null for text that is not a JSON object, or an object that does not validate', () => {
    for (const raw of ['not json', '[1]', '"s"', 'null', '', '{}', '{"cause":"c"}', '{"cause":"c","detail":"d","unlanded":{}}']) {
      expect(parseBounceEnvelope(raw)).toBeNull();
    }
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","unlanded":[{"repo":"r"}]}')).toBeNull();
    expect(parseBounceEnvelope('{"cause":"c","detail":"d","step":{"name":"t"}}')).toBeNull();
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
