import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTab, KitTabStrip } from './kit-tab';

@Component({
  selector: 'fleet-test-host',
  imports: [KitTab, KitTabStrip],
  template: `<nav fleetKitTabStrip>
    <a fleetKitTab [count]="count()" [countHot]="hot()" countTestid="tab-count">Proposals</a>
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
    expect((fixture.nativeElement as HTMLElement).querySelector('.cnt')).toBeNull();
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
