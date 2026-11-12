import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ScopeView, UrgencyView } from 'fleet';
import { GardeningRunDialogView, type RunSubmission } from './gardening-run-dialog-view';

describe('routine run urgency', () => {
  it('labels the inherited routine urgency and emits only an explicit choice', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningRunDialogView], providers: [provideZonelessChangeDetection(), provideRouter([])] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDialogView);
    fixture.componentRef.setInput('routineName', 'nightly');
    fixture.componentRef.setInput('scopes', [{ slug: 'web', description: 'web' } as ScopeView]);
    fixture.componentRef.setInput('sweptSlugs', new Set());
    fixture.componentRef.setInput('baselines', []);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('routineUrgency', 'urgent');
    fixture.componentRef.setInput('urgencies', [
      { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const values: RunSubmission[] = [];
    fixture.componentInstance.runSubmitted.subscribe((value) => values.push(value));
    const trigger = root.querySelector<HTMLElement>('[data-testid="run-urgency"]')!;
    expect(trigger.textContent).toContain('Routine default (Urgent)');
    root.querySelector<HTMLElement>('[data-testid="run-dialog-submit"]')!.click();
    expect(values[0]).not.toHaveProperty('urgency');
    trigger.click();
    await fixture.whenStable();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Normal')!.click();
    await fixture.whenStable();
    root.querySelector<HTMLElement>('[data-testid="run-dialog-submit"]')!.click();
    expect(values[1].urgency).toBe('normal');
    fixture.destroy();
  });
});
