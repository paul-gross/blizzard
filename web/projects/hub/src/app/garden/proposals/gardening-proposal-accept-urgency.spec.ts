import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { UrgencyView } from 'fleet';
import { GardeningProposalAcceptDialogView, type AcceptSubmission } from './gardening-proposal-accept-dialog-view';

describe('proposal accept urgency', () => {
  it('defaults to tenant, sends a chosen urgency only for minting', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningProposalAcceptDialogView], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningProposalAcceptDialogView);
    fixture.componentRef.setInput('proposalTitle', 'Proposal');
    fixture.componentRef.setInput('proposalBody', 'Body');
    fixture.componentRef.setInput('urgencies', [
      { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[data-testid="proposal-accept-body-input"]')?.getAttribute('aria-label')).toBe('Work item body');
    expect(root.querySelector('[data-testid="proposal-accept-reason-input"]')?.getAttribute('aria-label')).toBe('Reason (optional)');
    const values: AcceptSubmission[] = [];
    fixture.componentInstance.submitted.subscribe((value) => values.push(value));
    const trigger = root.querySelector<HTMLElement>('[data-testid="proposal-accept-urgency"]')!;
    expect(trigger.textContent).toContain('Tenant default (Normal)');
    root.querySelector<HTMLElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    expect(values[0]).not.toHaveProperty('urgency');
    trigger.click();
    await fixture.whenStable();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Urgent')!.click();
    await fixture.whenStable();
    root.querySelector<HTMLElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    expect(values[1].urgency).toBe('urgent');
    root.querySelector<HTMLElement>('[data-testid="proposal-accept-mode-decline"]')!.click();
    await fixture.whenStable();
    expect(root.querySelector('[data-testid="proposal-accept-urgency"]')).toBeNull();
    const reason = root.querySelector<HTMLTextAreaElement>('[data-testid="proposal-accept-decline-reason-input"]')!;
    expect(reason.getAttribute('aria-label')).toBe('Reason — required');
    reason.value = 'Already tracked';
    reason.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    root.querySelector<HTMLElement>('[data-testid="proposal-accept-dialog-submit"]')!.click();
    expect(values[2]).toEqual({ mintWorkItem: false, reason: 'Already tracked' });
    fixture.destroy();
  });
});
