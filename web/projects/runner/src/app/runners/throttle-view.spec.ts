import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { vi } from 'vitest';

import { ThrottleView } from './throttle-view';

const THROTTLE: runnerApi.ThrottleStatusView = {
  state: 'declining', declining: ['floor'],
  floor: { subscription: 'anthropic', admits: false, clear: null, hold: {
    kind: 'usage limit', harness_id: 'claude_code', window: null, expected_reset: '2026-10-22T14:00:00Z',
    awaits_sample: false, grace_opens_at: null,
  } },
  levers: [], on_decline: 'hold', on_usage_limit: 'release',
};

describe('ThrottleView', () => {
  it('confirms the subscription-wide scope before emitting, and disables Clear while pending', async () => {
    await TestBed.configureTestingModule({ imports: [ThrottleView], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(ThrottleView);
    fixture.componentRef.setInput('throttle', THROTTLE);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('scope', 'r-claude, r-chatgpt');
    fixture.componentRef.setInput('scopeReady', true);
    fixture.detectChanges();
    const emitted = vi.fn();
    fixture.componentInstance.clearFloor.subscribe(emitted);
    const button = fixture.nativeElement.querySelector('[data-testid="clear-floor"]') as HTMLButtonElement;
    button.click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('r-claude, r-chatgpt');
    expect(emitted).not.toHaveBeenCalled();
    (document.querySelector('[data-testid="confirm-clear-floor-confirm"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(emitted).toHaveBeenCalledOnce();
    fixture.componentRef.setInput('pending', true);
    fixture.detectChanges();
    expect(button.disabled).toBe(true);
  });

  it('names a runner-only floor in its confirmation', async () => {
    await TestBed.configureTestingModule({ imports: [ThrottleView], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(ThrottleView);
    fixture.componentRef.setInput('throttle', { ...THROTTLE, floor: { ...THROTTLE.floor, subscription: null } });
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('scope', 'r-claude');
    fixture.componentRef.setInput('scopeReady', true);
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('[data-testid="clear-floor"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('r-claude’s floor');
  });
});
