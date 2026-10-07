import { bounceReason, parseBounceEnvelope } from './parse-bounce-envelope';

describe('parseBounceEnvelope', () => {
  it('reads cause, detail and every further key', () => {
    expect(parseBounceEnvelope('{"cause":"failure","detail":"d","waited_s":600,"node":"x"}')).toEqual({
      cause: 'failure',
      detail: 'd',
      extras: [
        ['waited_s', '600'],
        ['node', 'x'],
      ],
    });
  });

  it('returns null for text that is not a JSON object', () => {
    for (const raw of ['not json', '[1]', '"s"', 'null', '']) expect(parseBounceEnvelope(raw)).toBeNull();
  });
});

describe('bounceReason', () => {
  it('prefers detail with code ticks dropped, then cause, then the raw text', () => {
    expect(bounceReason('{"cause":"failure","detail":"node `a` routed `b`"}')).toBe('node a routed b');
    expect(bounceReason('{"cause":"poll-timeout"}')).toBe('poll-timeout');
    expect(bounceReason('{"x":1}')).toBe('{"x":1}');
    expect(bounceReason('garbled')).toBe('garbled');
  });
});
