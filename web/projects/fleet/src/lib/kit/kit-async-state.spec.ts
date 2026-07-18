import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitAsyncState, type KitAsyncStateValue } from './kit-async-state';

@Component({
  selector: 'fleet-test-host',
  imports: [KitAsyncState],
  template: `
    <fleet-kit-async-state
      [state]="state()"
      [loadingText]="'LOADING…'"
      [loadingTestid]="'triad'"
      [errorText]="'UNAVAILABLE'"
      [errorTestid]="'triad'"
      [emptyText]="'NOTHING HERE'"
      [emptyTestid]="'triad'"
      [tone]="tone()"
    >
      <p data-testid="ready-content">populated</p>
    </fleet-kit-async-state>
  `,
})
class TestHost {
  readonly state = signal<KitAsyncStateValue>('loading');
  readonly tone = signal<'default' | 'accent'>('default');
}

describe('KitAsyncState', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the loading text and no projected content while loading', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="triad"]')?.textContent).toBe('LOADING…');
    expect(el.querySelector('[data-testid="ready-content"]')).toBeNull();
  });

  it('renders the error text in the error tone', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.state.set('error');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const status = el.querySelector('[data-testid="triad"]');
    expect(status?.textContent).toBe('UNAVAILABLE');
    expect(status?.classList.contains('error')).toBe(true);
  });

  it('renders the empty text, plain by default and accented when tone is accent', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.state.set('empty');
    await fixture.whenStable();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="triad"]')?.classList.contains('accent')).toBe(false);

    fixture.componentInstance.tone.set('accent');
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="triad"]')?.classList.contains('accent')).toBe(true);
  });

  it('projects the ready content and renders no status line when ready', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.state.set('ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="ready-content"]')?.textContent).toBe('populated');
    expect(el.querySelector('[data-testid="triad"]')).toBeNull();
  });
});
