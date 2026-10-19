import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { SubscriptionBlocks } from './subscription-blocks';
import { paceLine, type SubscriptionLine } from './subscription-blocks.model';

const PACED: SubscriptionLine = {
  slug: 'claude-max',
  name: 'Claude Max',
  state: 'paced',
  windows: [paceLine('5h', 62, 70), paceLine('7d', 48, 30)],
  stale: false,
  age: '[5m]',
};

async function render(line: SubscriptionLine): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(SubscriptionBlocks);
  fixture.componentRef.setInput('line', line);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('SubscriptionBlocks', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SubscriptionBlocks],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('draws a line of blocks per window, filled to its usage, red where usage runs ahead of the clock', async () => {
    const el = await render(PACED);

    const windows = [...el.querySelectorAll<HTMLElement>('[data-testid="subscription-blocks-window"]')];
    expect(windows.map((w) => w.getAttribute('data-window'))).toEqual(['5h', '7d']);
    expect(windows[0].querySelectorAll('.cell.on')).toHaveLength(6);
    expect(windows[0].querySelector('.cell.clock')).not.toBeNull();
    expect(windows.map((w) => w.classList.contains('ahead'))).toEqual([false, true]);
    expect(windows[0].getAttribute('title')).toBe('5h · 62% used · 70% of window');
    expect(el.querySelector('[data-testid="subscription-blocks-name"]')?.textContent).toBe('Claude Max');
    expect(el.querySelector('[data-testid="subscription-blocks-age"]')?.textContent).toBe('[5m]');
  });

  it('draws a cross for a lapsed credential, in place of the blocks', async () => {
    const el = await render({ ...PACED, state: 'lapsed', windows: [] });

    expect(el.querySelector('[data-testid="subscription-blocks-lapsed"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="subscription-blocks-window"]')).toBeNull();
  });

  it("says an unsampled line's note where it has one, and a dash where it has none", async () => {
    const noted = await render({ ...PACED, state: 'unsampled', windows: [], note: 'miss: endpoint unreachable' });
    expect(noted.querySelector('[data-testid="subscription-blocks-note"]')?.textContent).toBe('miss: endpoint unreachable');
    expect(noted.querySelector('[data-testid="subscription-blocks-unsampled"]')).toBeNull();
  });

  it('draws a dash for a subscription with no reading at all', async () => {
    const el = await render({ ...PACED, state: 'unsampled', windows: [], age: null });

    expect(el.querySelector('[data-testid="subscription-blocks-unsampled"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="subscription-blocks-age"]')).toBeNull();
  });

  it('dims a stale line and carries its slug on the host', async () => {
    const el = await render({ ...PACED, stale: true });

    expect(el.classList.contains('stale')).toBe(true);
    expect(el.getAttribute('data-subscription-slug')).toBe('claude-max');
  });
});
