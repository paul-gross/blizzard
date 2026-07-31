import { demoQueryParams, parseDuration, readDemoConfig } from './demo-config';

/**
 * Demo mode's switch and its dials. The config is what an operator types into
 * the address bar of a wall screen and then walks away from, so the two things
 * under test are that a sloppy value degrades to the default rather than to a
 * zero-length cycle, and that whatever came in is re-emitted verbatim — that
 * round trip is the whole reason a kiosk reload resumes the demo.
 */
describe('readDemoConfig', () => {
  const MINUTE = 60_000;

  it('is off for an ordinary URL', () => {
    expect(readDemoConfig('?chunk=ch_1').enabled).toBe(false);
    expect(readDemoConfig('').enabled).toBe(false);
  });

  it('reads a bare ?demo and the usual affirmatives as on', () => {
    for (const search of ['?demo', '?demo=true', '?demo=1', '?demo=YES', '?demo=on']) {
      expect(readDemoConfig(search).enabled).toBe(true);
    }
  });

  it('treats an explicit falsey value as off', () => {
    expect(readDemoConfig('?demo=false').enabled).toBe(false);
  });

  it('defaults the cycle to two minutes, the board scroll to a minute, an artifact to twenty seconds', () => {
    const config = readDemoConfig('?demo=true');

    expect(config.swapChunkMs).toBe(2 * MINUTE);
    expect(config.boardScrollMs).toBe(60_000);
    expect(config.artifactMs).toBe(20_000);
    expect(config.maxUptimeMs).toBe(60 * MINUTE);
  });

  it('takes the swap interval as bare seconds or with a unit suffix', () => {
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=900').swapChunkMs).toBe(15 * MINUTE);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=15m').swapChunkMs).toBe(15 * MINUTE);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=0.25h').swapChunkMs).toBe(15 * MINUTE);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=90s').swapChunkMs).toBe(90_000);
  });

  it('overrides the artifact dwell, the board scroll, and the reload backstop too', () => {
    const config = readDemoConfig('?demo&demo_artifact_interval=30&demo_board_scroll=10&demo_reload_after=0');

    expect(config.artifactMs).toBe(30_000);
    expect(config.boardScrollMs).toBe(10_000);
    expect(config.maxUptimeMs).toBe(0);
  });

  it('falls back on a value it cannot read, rather than to a cycle of no length', () => {
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=soon').swapChunkMs).toBe(2 * MINUTE);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=-5').swapChunkMs).toBe(2 * MINUTE);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=').swapChunkMs).toBe(2 * MINUTE);
  });

  /**
   * The dials are sane alone but not in combination: the cycle deadline is
   * struck before the board dwell, so a board scroll at or past the swap
   * interval leaves the artifact tour nothing — the director would descend and
   * swap away in the same breath.
   */
  it('clamps the board dwell to half the cycle, so the artifact tour always gets a share', () => {
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=120&demo_board_scroll=600').boardScrollMs).toBe(60_000);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=120&demo_board_scroll=120').boardScrollMs).toBe(60_000);
  });

  it('leaves a board dwell already within its share alone', () => {
    // The shipped defaults sit exactly at the ceiling — the clamp is a bound on
    // misconfiguration, not a tax on the normal case.
    expect(readDemoConfig('?demo=true').boardScrollMs).toBe(60_000);
    expect(readDemoConfig('?demo&demo_swap_chunk_interval=300&demo_board_scroll=30').boardScrollMs).toBe(30_000);
  });

  it('keeps every demo param it saw, and nothing else', () => {
    const config = readDemoConfig('?demo=1&demo_swap_chunk_interval=15m&chunk=ch_1&tab=artifacts');

    expect(config.raw).toEqual({ demo: '1', demo_swap_chunk_interval: '15m' });
  });
});

describe('demoQueryParams', () => {
  it('re-emits the params a reload needs, with demo forced explicit', () => {
    const params = demoQueryParams(readDemoConfig('?demo&demo_artifact_interval=45'));

    // `?demo` arrived bare; the URL the kiosk reloads says so out loud.
    expect(params).toEqual({ demo: 'true', demo_artifact_interval: '45' });
  });
});

describe('parseDuration', () => {
  it('answers the fallback for an absent value', () => {
    expect(parseDuration(null, 1234)).toBe(1234);
  });

  it('accepts zero', () => {
    expect(parseDuration('0', 1234)).toBe(0);
  });
});
