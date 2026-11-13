import { probeOrigin, probeOrigins } from './host-origin.query';

function respond(body: unknown, ok = true): Promise<Response> {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) } as Response);
}

describe('probeOrigins', () => {
  it('asks each origin for /api/health with credentials omitted, in order', async () => {
    const calls: [string, RequestInit][] = [];
    const fetchFn = (input: string, init: RequestInit) => {
      calls.push([input, init]);
      return respond({ status: 'ok', service: 'r', version: '1', host_id: input.includes('b.') ? 'hs_1' : null });
    };
    const probes = await probeOrigins(['https://a.example', 'https://b.example'], fetchFn, 1000);
    expect(probes).toEqual([
      { origin: 'https://a.example', kind: 'host', hostId: null },
      { origin: 'https://b.example', kind: 'host', hostId: 'hs_1' },
    ]);
    expect(calls.map(([url]) => url)).toEqual(['https://a.example/api/health', 'https://b.example/api/health']);
    expect(calls[0][1]).toMatchObject({ mode: 'cors', credentials: 'omit' });
  });
  it('treats a rejection and a non-OK status as unreachable', async () => {
    expect(await probeOrigin('https://a.example', () => Promise.reject(new TypeError('blocked')), 1000)).toEqual({ origin: 'https://a.example', kind: 'unreachable' });
    expect(await probeOrigin('https://a.example', () => respond({}, false), 1000)).toEqual({ origin: 'https://a.example', kind: 'unreachable' });
  });
  it('reads a body without host_id as a legacy answer', async () => {
    expect(await probeOrigin('https://a.example', () => respond({ status: 'ok', service: 'r', version: '1' }), 1000)).toEqual({ origin: 'https://a.example', kind: 'legacy' });
  });
  it('settles a hung probe at the timeout', async () => {
    const hung = () => new Promise<Response>(() => undefined);
    const started = Date.now();
    expect(await probeOrigin('https://a.example', hung, 50)).toEqual({ origin: 'https://a.example', kind: 'unreachable' });
    expect(Date.now() - started).toBeLessThan(1000);
  });
});
