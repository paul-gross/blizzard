import { TestBed } from '@angular/core/testing';
import { FLEET_CLOCK } from 'fleet';
import { DEMO_CLOCK, DemoAborted, nextFrame, sleepUntil, waitFor } from './demo-timing';
import { slowScrollToBottom } from './demo-scroll';

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

  it('sleeps for the remaining time', async () => {
    const done = vi.fn();
    const sleeping = sleepUntil(1050, signal, () => now).then(done);
    await vi.advanceTimersByTimeAsync(49);
    expect(done).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await sleeping;
    expect(done).toHaveBeenCalledOnce();
  });

  it.each([3_600_000, -3_600_000])('paces scrolling through a wall-clock correction of %i ms', async (correction) => {
    TestBed.configureTestingModule({ providers: [{ provide: FLEET_CLOCK, useValue: () => now }] });
    const clock = TestBed.inject(DEMO_CLOCK);
    const target = { scrollTop: 0, scrollHeight: 1000, clientHeight: 200 } as HTMLElement;
    const scrolling = slowScrollToBottom([target], 1000, signal, clock);
    await vi.advanceTimersByTimeAsync(250);
    now += correction;
    await vi.advanceTimersByTimeAsync(250);
    expect(target.scrollTop).toBeGreaterThan(300);
    expect(target.scrollTop).toBeLessThan(500);
    await vi.advanceTimersByTimeAsync(550);
    await scrolling;
    expect(target.scrollTop).toBe(800);
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
