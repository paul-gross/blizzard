import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { hubLink } from '../../core/runner-health.model';
import { HubPanelView } from './hub-panel-view';

describe('HubPanelView', () => {
  it('renders supplied hub data without queries and updates when its inputs change', async () => {
    await TestBed.configureTestingModule({
      imports: [HubPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(HubPanelView);
    fixture.componentRef.setInput('summary', {
      endpoint: 'https://example.test', reachable: 1, total: 2, lastContactAt: null, buffered: 7,
      issues: [{ key: 'r', runner: 'runner', text: 'cannot reach the hub', title: 'offline', tone: 'stale' }],
    });
    fixture.componentRef.setInput('link', hubLink(1, 2));
    fixture.componentRef.setInput('fleet', { ready: 3, running: 5, waiting: 1, needs: 2 });
    fixture.componentRef.setInput('fleetStale', false);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="hub-link"]')?.textContent?.trim()).toBe('1 / 2 CONNECTED');
    expect(el.querySelector('[data-testid="hub-buffered"]')?.textContent?.trim()).toBe('· 7 buffered');
    expect(el.querySelector('[data-testid="board-link"]')?.getAttribute('href')).toBe('https://example.test');
    expect(el.querySelector('[data-testid="hub-issue"]')?.textContent).toContain('cannot reach the hub');
    expect(el.querySelector('[data-testid="fleet-running"]')?.textContent).toContain('5');
    fixture.componentRef.setInput('fleet', null);
    fixture.componentRef.setInput('fleetStale', true);
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList).toContain('stale');
    expect(el.querySelector('[data-testid="fleet-running"]')?.textContent).toContain('—');
  });
});
