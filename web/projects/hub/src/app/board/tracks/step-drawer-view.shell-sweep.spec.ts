import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import type { hubApi } from 'fleet';
import { settle, turnSegmentView } from 'fleet/testing';
import type { PlacedStep } from './step.model';
import type { EnvTimelineStepView } from './step.query';
import { StepDrawerView } from './step-drawer-view';

/** The design tokens are a global stylesheet, never loaded by a standalone component test —
 * inject the sheet's real text so the drawer lays out at its real sizes. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/**
 * The step drawer's Transcript section head with an executor a newer hub sends that this client
 * does not list — the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method: a real, headless-Chromium proof that the `executor <raw>` label
 * renders inside the section head with no horizontal overflow at 1280/1024px
 * (`bzh:visual-change-needs-a-render`). The drawer docks under the desktop `BoardPage` alone — the
 * mobile shell routes `/board` to the glance board — so no phone width reaches it.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const NOW = Date.parse('2026-07-13T12:00:00Z');

const STEP: PlacedStep = {
  environment_id: 'env_a',
  host_id: 'host_01',
  host_name: 'xps',
  workspace_id: 'ws_1',
  segment: {
    chunk_id: 'ch_01',
    cost_estimated: false,
    cost_usd: 0,
    ended_at: '2026-07-13T10:30:00Z',
    epoch: 2,
    escalation: null,
    escalation_open: false,
    executor: 'a-newer-hub-executor' as hubApi.Executor,
    exit_choice: 'pass',
    failed: false,
    graph_id: 'gr_1',
    graph_name: 'delivery',
    lease_id: 'lease_01',
    node_id: 'nd_build',
    node_name: 'build',
    project_id: null,
    runner_id: 'rn_01',
    runner_name: 'runner-one',
    started_at: '2026-07-13T10:00:00Z',
    title: null,
  },
};

const VIEW: EnvTimelineStepView = {
  artifact_keys: [],
  escalations: [],
  generated_at: '2026-07-13T12:00:00Z',
  invocations: [],
  spend: null,
  journey: [STEP],
  step: STEP,
};

describe('step drawer unlisted executor shell sweep (web:shell-sweep)', () => {
  it.each([1280, 1024])('labels the unlisted executor inside the Transcript head without overflow at %ipx', async (width) => {
    await page.viewport(width, 900);
    await loadDesignTokens();
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideRouter([])] });
    const fixture = TestBed.createComponent(StepDrawerView);
    fixture.componentRef.setInput('nowMs', NOW);
    fixture.componentRef.setInput('view', VIEW);
    fixture.componentRef.setInput('placed', STEP);
    fixture.componentRef.setInput('transcriptState', 'ready');
    fixture.componentRef.setInput('transcript', { turns: [turnSegmentView({ index: 0 })], truncated: false });
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      await settle(fixture);
      const head = root.querySelector<HTMLElement>('.sd-transcript fleet-kit-section-head')!;
      const label = head.querySelector<HTMLElement>('[data-testid="step-drawer-executor-unlisted"]');
      expect(label?.textContent?.trim()).toBe('executor a-newer-hub-executor');
      const labelBox = label!.getBoundingClientRect();
      const headBox = head.getBoundingClientRect();
      expect(labelBox.width).toBeGreaterThan(0);
      expect(labelBox.left).toBeGreaterThanOrEqual(headBox.left);
      expect(labelBox.right).toBeLessThanOrEqual(headBox.right + 0.5);
      expect(head.scrollWidth).toBeLessThanOrEqual(head.clientWidth);
      expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
      expect(root.querySelector('fleet-transcript-viewer')).not.toBeNull();
    } finally {
      root.remove();
      fixture.destroy();
    }
  });
});
