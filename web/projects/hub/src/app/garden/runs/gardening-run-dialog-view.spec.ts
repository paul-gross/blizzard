import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ScopeView } from 'fleet';
import { GardeningRunDialogView, type RunSubmission } from './gardening-run-dialog-view';

describe('GardeningRunDialogView read states', () => {
  it('blocks submission if a ready read becomes loading, error, or empty', async () => {
    await TestBed.configureTestingModule({ imports: [GardeningRunDialogView], providers: [provideZonelessChangeDetection(), provideRouter([])] }).compileComponents();
    const fixture = TestBed.createComponent(GardeningRunDialogView);
    fixture.componentRef.setInput('routineName', 'nightly');
    fixture.componentRef.setInput('scopes', [{ slug: 'web', description: 'web' } as ScopeView]);
    fixture.componentRef.setInput('sweptSlugs', new Set());
    fixture.componentRef.setInput('baselines', []);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const submissions: RunSubmission[] = [];
    fixture.componentInstance.runSubmitted.subscribe((value) => submissions.push(value));
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="run-dialog-submit"]')!;
    expect(button.disabled).toBe(false);
    for (const state of ['loading', 'error', 'empty']) {
      fixture.componentRef.setInput('state', state);
      await fixture.whenStable();
      expect(button.disabled).toBe(true);
      button.click();
    }
    expect(submissions).toEqual([]);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    button.click();
    expect(submissions).toHaveLength(1);
  });
});
