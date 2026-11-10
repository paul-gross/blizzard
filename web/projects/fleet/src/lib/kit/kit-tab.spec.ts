import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTab, KitTabStrip } from './kit-tab';

@Component({
  selector: 'fleet-test-host',
  imports: [KitTab, KitTabStrip],
  template: `<nav fleetKitTabStrip>
    <a fleetKitTab [count]="count()" [countHot]="hot()" countTestid="tab-count"
      >Proposals</a
    >
  </nav>`,
})
class TestHost {
  readonly count = signal<number | null>(null);
  readonly hot = signal(false);
}

describe('KitTab count', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function render() {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    return fixture;
  }

  it('renders no count span while the count is null', async () => {
    const fixture = await render();
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.cnt'),
    ).toBeNull();
  });

  it('renders a count, zero included, with the testid, after the projected label', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    for (const n of [3, 0]) {
      fixture.componentInstance.count.set(n);
      await fixture.whenStable();
      const cnt = el.querySelector('[data-testid="tab-count"]') as HTMLElement;
      expect(cnt.classList.contains('cnt')).toBe(true);
      expect(cnt.textContent).toBe(String(n));
      expect(el.querySelector('a')?.textContent?.trim()).toBe(`Proposals${n}`);
    }
  });

  it('marks the count hot only when asked', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.count.set(2);
    await fixture.whenStable();
    expect(el.querySelector('.cnt')?.classList.contains('hot')).toBe(false);

    fixture.componentInstance.hot.set(true);
    await fixture.whenStable();
    expect(el.querySelector('.cnt')?.classList.contains('hot')).toBe(true);
  });
});

@Component({
  selector: 'fleet-test-bottom-host',
  imports: [KitTab, KitTabStrip],
  template: `<nav fleetKitTabStrip variant="bottom">
    <a
      fleetKitTab
      variant="bottom"
      role="link"
      [unavailable]="unavailable()"
      [count]="count()"
      countTestid="tab-count"
      >Asks</a
    >
  </nav>`,
})
class BottomHost {
  readonly unavailable = signal(false);
  readonly count = signal<number | null>(null);
}

describe('KitTab bottom variant', () => {
  async function render() {
    await TestBed.configureTestingModule({
      imports: [BottomHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BottomHost);
    await fixture.whenStable();
    return fixture;
  }

  it('marks the strip and the tab bottom', async () => {
    const el = (await render()).nativeElement as HTMLElement;
    expect(el.querySelector('nav')?.classList.contains('bottom')).toBe(true);
    expect(el.querySelector('a')?.classList.contains('bottom')).toBe(true);
  });

  it('renders the count as the kit count badge carrying the testid', async () => {
    const fixture = await render();
    fixture.componentInstance.count.set(2);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.cnt')).toBeNull();
    const badge = el.querySelector(
      'fleet-kit-count-badge [data-testid="tab-count"]',
    );
    expect(badge?.textContent?.trim()).toBe('2');
  });

  it('sets aria-disabled only while unavailable', async () => {
    const fixture = await render();
    const tab = (fixture.nativeElement as HTMLElement).querySelector('a')!;
    expect(tab.hasAttribute('aria-disabled')).toBe(false);
    fixture.componentInstance.unavailable.set(true);
    await fixture.whenStable();
    expect(tab.getAttribute('aria-disabled')).toBe('true');
    expect(tab.classList.contains('unavailable')).toBe(true);
  });
});
