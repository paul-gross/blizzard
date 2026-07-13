import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { LocalPanel } from './local-panel';

describe('LocalPanel', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LocalPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the local-panel shell with an empty state', async () => {
    const fixture = TestBed.createComponent(LocalPanel);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-panel"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('RUNNER IDLE');
  });

  it('reflects the connection input in the header', async () => {
    const fixture = TestBed.createComponent(LocalPanel);
    fixture.componentRef.setInput('connection', 'ok');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });
});
