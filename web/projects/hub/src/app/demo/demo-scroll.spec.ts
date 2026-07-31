import { scrollersIn, slowScrollToBottom } from './demo-scroll';

/**
 * The scroll half of demo mode. jsdom does no layout, so both the discovery
 * metrics (`scrollHeight`/`clientHeight`) and the mutable `scrollTop` are
 * defined onto the test's own elements — this tier asserts the *decisions*
 * (which elements get driven, where they end up, that growth is re-measured),
 * not the rendering, which is the browser tier's to prove.
 */

/** A real element the discovery pass can run `getComputedStyle` against. */
function pane(overflowY: string, scrollHeight: number, clientHeight: number): HTMLElement {
  const el = document.createElement('div');
  el.style.overflowY = overflowY;
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight });
  Object.defineProperty(el, 'clientHeight', { value: clientHeight });
  Object.defineProperty(el, 'scrollTop', { value: 0, writable: true });
  return el;
}

describe('scrollersIn', () => {
  it('finds the overflowing scroll panes inside a region, the region itself included', () => {
    const dock = pane('auto', 900, 300);
    const column = pane('scroll', 2000, 400);
    dock.append(column);

    expect(scrollersIn(dock)).toEqual([dock, column]);
  });

  it('skips elements that do not scroll and elements with nowhere to scroll', () => {
    const dock = pane('hidden', 900, 300);
    const flush = pane('auto', 302, 300);
    const real = pane('auto', 900, 300);
    dock.append(flush, real);

    expect(scrollersIn(dock)).toEqual([real]);
  });
});

describe('slowScrollToBottom', () => {
  /** The three fields the animation reads and writes — no layout needed. */
  function target(scrollHeight: number, clientHeight: number): HTMLElement {
    return { scrollTop: 0, scrollHeight, clientHeight } as unknown as HTMLElement;
  }

  it('eases every target to its bottom, together', async () => {
    const a = target(1000, 200);
    const b = target(500, 100);

    await slowScrollToBottom([a, b], 60, new AbortController().signal);

    expect(a.scrollTop).toBe(800);
    expect(b.scrollTop).toBe(400);
  });

  it('starts gently rather than jumping', async () => {
    const el = target(1000, 200);
    const seen: number[] = [];
    const watched = {
      get scrollTop() {
        return 0;
      },
      set scrollTop(value: number) {
        seen.push(value);
      },
      scrollHeight: 1000,
      clientHeight: 200,
    } as unknown as HTMLElement;

    await slowScrollToBottom([watched], 200, new AbortController().signal);

    // The first written position is a sliver of the 800px travel, not a jump-cut.
    expect(seen[0]).toBeLessThan(80);
    expect(seen[seen.length - 1]).toBe(800);
    expect(el.scrollTop).toBe(0);
  });

  it('re-measures each frame, so content arriving mid-scroll still ends at the bottom', async () => {
    let reads = 0;
    const growing = {
      scrollTop: 0,
      get scrollHeight() {
        reads += 1;
        // The live board appends a timeline row partway down.
        return reads > 2 ? 2000 : 1000;
      },
      clientHeight: 200,
    } as unknown as HTMLElement;

    await slowScrollToBottom([growing], 80, new AbortController().signal);

    expect(growing.scrollTop).toBe(1800);
  });

  it('gives up the moment the run is torn down', async () => {
    const el = target(1000, 200);
    const controller = new AbortController();
    const scrolling = slowScrollToBottom([el], 5000, controller.signal);
    controller.abort();

    await expect(scrolling).rejects.toThrow('demo aborted');
    expect(el.scrollTop).toBeLessThan(800);
  });

  it('does nothing when there is nothing to scroll', async () => {
    await expect(slowScrollToBottom([], 1000, new AbortController().signal)).resolves.toBeUndefined();
  });
});
