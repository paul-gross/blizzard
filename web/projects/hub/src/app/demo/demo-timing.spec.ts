import { DemoAborted, nextFrame, sleepUntil, waitFor } from './demo-timing';

describe('demo timing', () => {
  let now: number;
  let signal: AbortSignal;

  beforeEach(() => {
    vi.useFakeTimers();
    now = 1000;
    signal = new AbortController().signal;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('sleeps for the remaining wall-clock time', async () => {
    const done = vi.fn();
    const sleeping = sleepUntil(1050, signal, () => now).then(done);
    await vi.advanceTimersByTimeAsync(49);
    expect(done).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await sleeping;
    expect(done).toHaveBeenCalledOnce();
  });

  it('times out against the injected clock even when frames are throttled', async () => {
    vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(1);
    vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(vi.fn());
    const read = vi.fn(() => null);
    const result = waitFor(read, 500, signal, () => now);
    now += 250;
    await vi.advanceTimersByTimeAsync(250);
    expect(read).toHaveBeenCalledTimes(2);
    now += 250;
    await vi.advanceTimersByTimeAsync(250);
    await expect(result).resolves.toBeNull();
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('cancels the pending fallback and frame on abort', async () => {
    vi.spyOn(globalThis, 'requestAnimationFrame').mockReturnValue(7);
    const cancel = vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(vi.fn());
    const controller = new AbortController();
    const frame = nextFrame(controller.signal);
    controller.abort();
    await expect(frame).rejects.toBeInstanceOf(DemoAborted);
    expect(cancel).toHaveBeenCalledWith(7);
    expect(vi.getTimerCount()).toBe(0);
  });
});
