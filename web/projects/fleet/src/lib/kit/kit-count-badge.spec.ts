import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitCountBadge } from './kit-count-badge';

@Component({
  selector: 'fleet-test-host',
  imports: [KitCountBadge],
  template: `<fleet-kit-count-badge [count]="count()" testid="badge-a" />`,
})
class TestHost {
  readonly count = signal(1);
}

describe('KitCountBadge', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the count and the testid', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('[data-testid="badge-a"]');
    expect(badge?.textContent).toBe('1');
  });

  it('updates its rendered text as the count changes, including a two-digit count', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    fixture.componentInstance.count.set(10);
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="badge-a"]')?.textContent).toBe('10');
  });

  it('renders no testid attribute when none is supplied', async () => {
    await TestBed.configureTestingModule({
      imports: [KitCountBadge],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitCountBadge);
    fixture.componentRef.setInput('count', 3);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.badge')?.hasAttribute('data-testid')).toBe(false);
  });
});
