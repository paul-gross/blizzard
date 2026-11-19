import { provideZonelessChangeDetection, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ScopeView } from 'fleet';
import { EMPTY_RUN_DRAFT, type RunDraft } from '../core/run.domain';
import { runRules } from './run.form';
import { GardeningRunDialogView } from './gardening-run-dialog-view';

/** The field tree the container hands the view, over `draft`. */
function runForm(draft: Partial<RunDraft> = {}) {
  return TestBed.runInInjectionContext(() =>
    form(signal<RunDraft>({ ...EMPTY_RUN_DRAFT, scope: 'web', ...draft }), runRules(() => new Set(['web']), () => new Set())),
  );
}

describe('GardeningRunDialogView read states', () => {
  it('blocks submission if a ready read becomes loading, error, or empty', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningRunDialogView], providers: [provideZonelessChangeDetection(), provideRouter([])] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDialogView);
    fixture.componentRef.setInput('routineName', 'nightly');
    fixture.componentRef.setInput('scopes', [{ slug: 'web', description: 'web' } as ScopeView]);
    fixture.componentRef.setInput('sweptSlugs', new Set());
    fixture.componentRef.setInput('form', runForm());
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    let submissions = 0;
    fixture.componentInstance.save.subscribe(() => (submissions += 1));
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    expect(button.disabled).toBe(false);
    for (const state of ['loading', 'error', 'empty']) {
      fixture.componentRef.setInput('state', state);
      await fixture.whenStable();
      expect(button.disabled).toBe(true);
      button.click();
    }
    expect(submissions).toBe(0);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    button.click();
    expect(submissions).toBe(1);
  });
});
