import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, submit } from '@angular/forms/signals';
import { page } from 'vitest/browser';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { emptyTriageDraft } from './finding-triage.domain';
import { findingTriageRules } from './finding-triage.form';
import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/** Mounts the view over a real `form()`, as the container does — the view takes the form, so the sweep needs one. */
@Component({
  selector: 'app-triage-sweep-host',
  imports: [GardeningFindingTriageDialogView],
  template: '<app-gardening-finding-triage-dialog-view [verb]="verb()" [form]="f" />',
})
class Host {
  readonly verb = input.required<FindingTriageVerb>();
  readonly draft = signal(emptyTriageDraft('resolve'));
  readonly f = form(this.draft, findingTriageRules);
}

/**
 * The findings triage dialog's own half of `web:shell-sweep` — the note field renders without overflowing the
 * dialog panel, the `supersede` verb's extra absorbing-finding field renders below/beside the note field with no
 * overlap, the form's action row (Save/Cancel) genuinely sits side by side inside the panel, and a form-line error
 * stays inside the panel, at the phone and desktop widths the dialog is reachable at — real CSS layout claims jsdom
 * cannot make.
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mount(width: number, verb: FindingTriageVerb, formError?: string) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [Host],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(Host);
  fixture.componentRef.setInput('verb', verb);
  fixture.componentInstance.draft.set(emptyTriageDraft(verb));
  await fixture.whenStable();
  if (formError !== undefined) {
    const f = fixture.componentInstance.f;
    f.note().value.set('a note');
    f().markAsTouched();
    void submit(f, async () => [{ kind: 'hub', fieldTree: f, message: formError }]);
  }
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 800);
  await fixture.whenStable();
  return { fixture, root };
}

function expectActionRowInside(root: HTMLElement, width: number): void {
  const panelRect = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!.getBoundingClientRect();
  const cancelRect = root.querySelector<HTMLElement>('[data-testid="kit-form-cancel"]')!.getBoundingClientRect();
  const submitRect = root.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.getBoundingClientRect();
  expect(
    Math.abs(cancelRect.top - submitRect.top),
    `${width}px: Save and Cancel must sit on the same row, not stacked`,
  ).toBeLessThan(2);
  const [left, right] = cancelRect.left < submitRect.left ? [cancelRect, submitRect] : [submitRect, cancelRect];
  expect(right.left, `${width}px: the action buttons must not overlap`).toBeGreaterThanOrEqual(left.right);
  for (const rect of [cancelRect, submitRect]) {
    expect(rect.right, `${width}px: an action button overflows the dialog panel`).toBeLessThanOrEqual(panelRect.right + 1);
    expect(rect.left, `${width}px: an action button sits left of the dialog panel`).toBeGreaterThanOrEqual(panelRect.left - 1);
  }
}

describe('GardeningFindingTriageDialogView shell sweep (web:shell-sweep)', () => {
  for (const width of [1400, 390, 320]) {
    it(`renders the note field inside the panel and sits Save/Cancel side by side at ${width}px (resolve)`, async () => {
      const { root } = await mount(width, 'resolve');
      try {
        const panelRect = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!.getBoundingClientRect();
        const noteRect = root.querySelector<HTMLElement>('[data-testid="finding-triage-note-input"]')!.getBoundingClientRect();
        expect(noteRect.right, `${width}px: note field overflows the dialog panel`).toBeLessThanOrEqual(panelRect.right + 1);
        expect(noteRect.left, `${width}px: note field sits left of the dialog panel`).toBeGreaterThanOrEqual(panelRect.left - 1);
        expectActionRowInside(root, width);
      } finally {
        root.remove();
      }
    });

    it(`renders the absorbing-finding field below/beside the note field with no overlap at ${width}px (supersede)`, async () => {
      const { root } = await mount(width, 'supersede');
      try {
        const panelRect = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!.getBoundingClientRect();
        const supersededByRect = root.querySelector<HTMLElement>('[data-testid="finding-triage-superseded-by-input"]')!.getBoundingClientRect();
        const noteRect = root.querySelector<HTMLElement>('[data-testid="finding-triage-note-input"]')!.getBoundingClientRect();
        const stackedBelow = noteRect.top >= supersededByRect.bottom;
        const sideBySide =
          noteRect.left >= supersededByRect.right && Math.abs(noteRect.top - supersededByRect.top) < 2;
        expect(
          stackedBelow || sideBySide,
          `${width}px: the note field overlaps the absorbing-finding field instead of sitting below or beside it`,
        ).toBe(true);
        expect(supersededByRect.right, `${width}px: absorbing-finding field overflows the dialog panel`).toBeLessThanOrEqual(
          panelRect.right + 1,
        );
        expectActionRowInside(root, width);
      } finally {
        root.remove();
      }
    });

    it(`keeps a form-line error inside the panel at ${width}px`, async () => {
      const message = 'The hub refused this transition because the finding was already resolved by another operator.';
      const { root } = await mount(width, 'resolve', message);
      try {
        await vi.waitFor(() => expect(root.querySelector('[data-testid="kit-form-line"]')).toBeTruthy());
        const panelRect = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!.getBoundingClientRect();
        const lineRect = root.querySelector<HTMLElement>('[data-testid="kit-form-line"]')!.getBoundingClientRect();
        expect(lineRect.right, `${width}px: form-line error overflows the dialog panel`).toBeLessThanOrEqual(panelRect.right + 1);
        expect(lineRect.left, `${width}px: form-line error sits left of the dialog panel`).toBeGreaterThanOrEqual(panelRect.left - 1);
        expectActionRowInside(root, width);
      } finally {
        root.remove();
      }
    });
  }
});
