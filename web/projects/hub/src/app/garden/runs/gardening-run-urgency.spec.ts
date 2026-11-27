import { provideZonelessChangeDetection, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { Urgency } from 'fleet/core';
import { gardenScope } from '../core/scope.builders';
import { EMPTY_RUN_DRAFT, type RunDraft } from '../core/run.domain';
import { runRules } from './run.form';
import { GardeningRunDialogView } from './gardening-run-dialog-view';

/** The field tree the container hands the view, over `draft`. */
function runForm(draft: Partial<RunDraft> = {}) {
  return TestBed.runInInjectionContext(() =>
    form(signal<RunDraft>({ ...EMPTY_RUN_DRAFT, scope: 'web', ...draft }), runRules(() => new Set(['web']), () => new Set())),
  );
}

describe('routine run urgency', () => {
  it('labels the inherited routine urgency and emits only an explicit choice', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningRunDialogView], providers: [provideZonelessChangeDetection(), provideRouter([])] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDialogView);
    fixture.componentRef.setInput('routineName', 'nightly');
    fixture.componentRef.setInput('scopes', [gardenScope({ slug: 'web' })]);
    fixture.componentRef.setInput('sweptSlugs', new Set());
    const tree = runForm();
    fixture.componentRef.setInput('form', tree);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('effectiveUrgency', { id: 'u', slug: 'urgent' });
    fixture.componentRef.setInput('urgencies', [
      { id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency,
      { id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const trigger = root.querySelector<HTMLElement>('[data-testid="run-urgency"]')!;
    expect(trigger.textContent).toContain('Routine default (Urgent)');
    expect(tree.urgency().value()).toBe('');
    trigger.click();
    await fixture.whenStable();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Normal')!.click();
    await fixture.whenStable();
    expect(tree.urgency().value()).toBe('normal');
    fixture.destroy();
  });

  it('names the effective urgency by its slug while the urgency list is unresolved', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningRunDialogView], providers: [provideZonelessChangeDetection(), provideRouter([])] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDialogView);
    fixture.componentRef.setInput('routineName', 'nightly');
    fixture.componentRef.setInput('scopes', [gardenScope({ slug: 'web' })]);
    fixture.componentRef.setInput('sweptSlugs', new Set());
    fixture.componentRef.setInput('form', runForm());
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('effectiveUrgency', { id: 'u', slug: 'urgent' });
    await fixture.whenStable();
    const trigger = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="run-urgency"]')!;
    expect(trigger.textContent).toContain('Routine default (urgent)');
    fixture.destroy();
  });
});
