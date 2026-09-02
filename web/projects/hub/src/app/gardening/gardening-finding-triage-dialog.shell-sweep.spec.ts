import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/**
 * The findings triage dialog's own half of `web:shell-sweep` (blizzard#402 Phase 4) —
 * the note field renders without overflowing the dialog panel, the `supersede` verb's
 * extra absorbing-finding field renders below/beside the note field with no overlap,
 * and the footer's Cancel/submit buttons genuinely sit side by side without
 * overflowing the panel, at the phone and desktop widths the dialog is reachable at —
 * real CSS layout claims jsdom cannot make.
 *
 * Mounts `GardeningFindingTriageDialogView` directly with plain inputs — no query
 * double, matching how the container actually feeds it
 * (`bzh:frontend-container-presentational`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mount(width: number, verb: 'resolve' | 'supersede', findingIds: readonly string[]) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GardeningFindingTriageDialogView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningFindingTriageDialogView);
  fixture.componentRef.setInput('verb', verb);
  fixture.componentRef.setInput('findingIds', findingIds);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 800);
  await fixture.whenStable();
  return { fixture, root };
}

describe('GardeningFindingTriageDialogView shell sweep (web:shell-sweep, blizzard#402 Phase 4)', () => {
  for (const width of [1400, 390, 320]) {
    it(`renders the note field without overflowing the panel, and sits Cancel/submit side by side, at ${width}px (resolve)`, async () => {
      const { root } = await mount(width, 'resolve', ['fin_1', 'fin_2']);
      try {
        const panel = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!;
        const panelRect = panel.getBoundingClientRect();

        const note = root.querySelector<HTMLElement>('[data-testid="finding-triage-note-input"]')!;
        const noteRect = note.getBoundingClientRect();
        expect(noteRect.right, `${width}px: note field overflows the dialog panel`).toBeLessThanOrEqual(
          panelRect.right + 1,
        );
        expect(noteRect.left, `${width}px: note field sits left of the dialog panel`).toBeGreaterThanOrEqual(
          panelRect.left - 1,
        );

        const cancel = root.querySelector<HTMLElement>('[data-testid="finding-triage-dialog-cancel"]')!;
        const submit = root.querySelector<HTMLElement>('[data-testid="finding-triage-dialog-submit"]')!;
        const cancelRect = cancel.getBoundingClientRect();
        const submitRect = submit.getBoundingClientRect();
        expect(
          Math.abs(cancelRect.top - submitRect.top),
          `${width}px: Cancel and submit must sit on the same row, not stacked`,
        ).toBeLessThan(2);
        expect(submitRect.left, `${width}px: submit must not overlap Cancel`).toBeGreaterThanOrEqual(cancelRect.right);
        expect(submitRect.right, `${width}px: submit must not overflow the dialog panel`).toBeLessThanOrEqual(
          panelRect.right + 1,
        );
      } finally {
        root.remove();
      }
    });

    it(`renders the absorbing-finding field below/beside the note field with no overlap, and sits Cancel/submit side by side, at ${width}px (supersede)`, async () => {
      const { root } = await mount(width, 'supersede', ['fin_1']);
      try {
        const panel = root.querySelector<HTMLElement>('[data-testid="gardening-finding-triage-dialog"]')!;
        const panelRect = panel.getBoundingClientRect();

        const supersededBy = root.querySelector<HTMLElement>('[data-testid="finding-triage-superseded-by-input"]')!;
        const note = root.querySelector<HTMLElement>('[data-testid="finding-triage-note-input"]')!;
        const supersededByRect = supersededBy.getBoundingClientRect();
        const noteRect = note.getBoundingClientRect();
        const stackedBelow = noteRect.top >= supersededByRect.bottom;
        const sideBySide =
          noteRect.left >= supersededByRect.right &&
          Math.abs(noteRect.top - supersededByRect.top) < 2;
        expect(
          stackedBelow || sideBySide,
          `${width}px: the note field (top ${noteRect.top}, left ${noteRect.left}) overlaps the absorbing-finding field (bottom ${supersededByRect.bottom}, right ${supersededByRect.right}) instead of sitting below or beside it`,
        ).toBe(true);
        expect(supersededByRect.right, `${width}px: absorbing-finding field overflows the dialog panel`).toBeLessThanOrEqual(
          panelRect.right + 1,
        );

        const cancel = root.querySelector<HTMLElement>('[data-testid="finding-triage-dialog-cancel"]')!;
        const submit = root.querySelector<HTMLElement>('[data-testid="finding-triage-dialog-submit"]')!;
        const cancelRect = cancel.getBoundingClientRect();
        const submitRect = submit.getBoundingClientRect();
        expect(
          Math.abs(cancelRect.top - submitRect.top),
          `${width}px: Cancel and submit must sit on the same row, not stacked`,
        ).toBeLessThan(2);
        expect(submitRect.left, `${width}px: submit must not overlap Cancel`).toBeGreaterThanOrEqual(cancelRect.right);
        expect(submitRect.right, `${width}px: submit must not overflow the dialog panel`).toBeLessThanOrEqual(
          panelRect.right + 1,
        );
      } finally {
        root.remove();
      }
    });
  }
});
