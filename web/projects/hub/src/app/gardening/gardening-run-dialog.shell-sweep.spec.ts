import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page, userEvent } from 'vitest/browser';

import type { RoutineBaselineView, ScopeView } from 'fleet';
import { GardeningRunDialogView } from './gardening-run-dialog-view';

/**
 * The gardening run dialog's own half of `web:shell-sweep` (blizzard#399 D6) — the
 * three fields' real layout at the widths the dialog is reachable at (a phone-width
 * routines page and the desktop board), which `KitDialog`'s own sweep
 * (`kit-dialog.shell-sweep.spec.ts`) does not cover: the scope field's radio rows
 * genuinely stack, the mode field's delta baseline block genuinely stacks its
 * finding-set-id line above its per-repo landed-since lines, the new-scope near-match
 * warning genuinely renders below both new-scope inputs rather than overlapping them,
 * and the footer's Cancel/Run buttons genuinely sit side by side with neither
 * overflowing the panel — real CSS layout claims jsdom (this repo's default unit-test
 * environment, whose `getBoundingClientRect` never lays anything out) cannot make.
 *
 * Mounts `GardeningRunDialogView` directly with plain inputs — no query double,
 * matching how the container actually feeds it (`bzh:frontend-container-
 * presentational`) — and drives the scope field and mode radios through real pointer
 * events to reach the delta-baseline and near-match states.
 *
 * Excluded from the default `ng test hub` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const SCOPES: ScopeView[] = [
  { slug: 'web', description: 'the frontend', created_at: '2026-01-01T00:00:00Z', retired: false },
  { slug: 'blizzard', description: 'the hub itself', created_at: '2026-01-01T00:00:00Z', retired: false },
];

const BASELINES: RoutineBaselineView[] = [
  {
    scope_slug: 'web',
    finding_set_id: 'fins_02SWEEP0000000000000000000',
    recorded_at: '2026-02-01T00:00:00Z',
    repos: [
      { repo: 'blizzard', revision: 'abc123d', landed_since: 3 },
      { repo: 'blizzard-context', revision: 'def456a', landed_since: 1 },
    ],
  },
];

async function mount(width: number) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GardeningRunDialogView],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningRunDialogView);
  fixture.componentRef.setInput('open', true);
  fixture.componentRef.setInput('routineName', 'gardening');
  fixture.componentRef.setInput('scopes', SCOPES);
  fixture.componentRef.setInput('sweptSlugs', new Set(['web']));
  fixture.componentRef.setInput('existingSlugs', new Set(SCOPES.map((s) => s.slug)));
  fixture.componentRef.setInput('baselines', BASELINES);
  fixture.componentRef.setInput('state', 'ready');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 800);
  await fixture.whenStable();
  return { fixture, root };
}

describe('GardeningRunDialogView shell sweep (web:shell-sweep, blizzard#399 D6)', () => {
  for (const width of [390, 1024]) {
    it(`stacks the scope field's options and the footer's buttons sit side by side at ${width}px`, async () => {
      const { root } = await mount(width);
      try {
        const options = root.querySelectorAll<HTMLElement>('[data-testid^="run-scope-option-"]');
        expect(options.length, 'fixture defect: expected at least three scope options (two scopes + mint-new)').toBeGreaterThanOrEqual(
          3,
        );
        for (let i = 1; i < options.length; i += 1) {
          const prev = options[i - 1].getBoundingClientRect();
          const cur = options[i].getBoundingClientRect();
          expect(cur.top, `scope option ${i} did not land below option ${i - 1}`).toBeGreaterThanOrEqual(prev.bottom);
        }

        const cancel = root.querySelector<HTMLElement>('[data-testid="run-dialog-cancel"]')!;
        const submit = root.querySelector<HTMLElement>('[data-testid="run-dialog-submit"]')!;
        const cancelRect = cancel.getBoundingClientRect();
        const submitRect = submit.getBoundingClientRect();
        expect(
          Math.abs(cancelRect.top - submitRect.top),
          'Cancel and Run must sit on the same row, not stacked',
        ).toBeLessThan(2);
        expect(submitRect.left, 'Run must not overlap Cancel').toBeGreaterThanOrEqual(cancelRect.right);

        const panel = root.querySelector<HTMLElement>('[data-testid="gardening-run-dialog"]')!;
        const panelRect = panel.getBoundingClientRect();
        expect(submitRect.right, 'Run must not overflow the dialog panel').toBeLessThanOrEqual(panelRect.right + 1);
      } finally {
        root.remove();
      }
    });
  }

  it('stacks the delta baseline block’s finding-set-id line above its per-repo landed-since lines', async () => {
    const { root, fixture } = await mount(1024);
    try {
      await userEvent.click(root.querySelector('[data-testid="run-mode-delta"]')!);
      await fixture.whenStable();

      const baseline = root.querySelector<HTMLElement>('[data-testid="run-mode-baseline"]')!;
      const idLine = baseline.querySelector('p')!;
      const repoLines = baseline.querySelectorAll('li');
      expect(repoLines.length, 'fixture defect: expected two per-repo landed-since lines').toBe(2);

      const idRect = idLine.getBoundingClientRect();
      const firstRepoRect = repoLines[0].getBoundingClientRect();
      const secondRepoRect = repoLines[1].getBoundingClientRect();
      expect(firstRepoRect.top, 'the first repo line did not land below the finding-set-id line').toBeGreaterThanOrEqual(
        idRect.bottom,
      );
      expect(secondRepoRect.top, 'the second repo line did not land below the first').toBeGreaterThanOrEqual(
        firstRepoRect.bottom,
      );
    } finally {
      root.remove();
    }
  });

  it('renders the near-match warning below both new-scope inputs, not overlapping them', async () => {
    const { root, fixture } = await mount(1024);
    try {
      await userEvent.click(root.querySelector('[data-testid="run-scope-option-new"]')!);
      await fixture.whenStable();

      const slugInput = root.querySelector<HTMLInputElement>('[data-testid="run-new-scope-slug"]')!;
      await userEvent.type(slugInput, 'webb');
      await fixture.whenStable();

      const descInput = root.querySelector<HTMLInputElement>('[data-testid="run-new-scope-description"]')!;
      const warning = root.querySelector<HTMLElement>('[data-testid="run-scope-near-match-warning"]')!;

      const descRect = descInput.getBoundingClientRect();
      const warningRect = warning.getBoundingClientRect();
      expect(warningRect.top, 'the near-match warning did not land below the description input').toBeGreaterThanOrEqual(
        descRect.bottom,
      );
      expect(warning.textContent, 'the warning must name the close existing slug').toContain('web');
    } finally {
      root.remove();
    }
  });
});
