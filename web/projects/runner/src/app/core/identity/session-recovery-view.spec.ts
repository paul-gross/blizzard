import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { SessionRecoveryView } from './session-recovery-view';

async function render() {
  await TestBed.configureTestingModule({
    imports: [SessionRecoveryView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(SessionRecoveryView);
  await fixture.whenStable();
  return fixture;
}

describe('SessionRecoveryView (issue #312)', () => {
  it('renders the recovery surface with a retry control', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="session-recovery"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="session-recovery-retry"]')).not.toBeNull();
  });

  it('emits retry when the control is activated', async () => {
    const fixture = await render();
    const retrySpy = vi.fn();
    fixture.componentInstance.retry.subscribe(retrySpy);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="session-recovery-retry"]')?.click();
    await fixture.whenStable();

    expect(retrySpy).toHaveBeenCalledTimes(1);
  });
});
