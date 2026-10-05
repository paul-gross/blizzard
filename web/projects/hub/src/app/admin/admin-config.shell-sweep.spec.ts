import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ConfigActionsVm } from './config-actions.model';
import { ConfigMaster } from './config-master';
import { ConfigRecordPanel, type ConfigRecordVm } from './config-record-panel';

/**
 * The Admin config surfaces' real-render proof (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method): a desktop
 * detail carries its write controls, a phone's carries none and names the CLI command
 * instead, and neither overflows horizontally — a real CSS layout claim jsdom cannot
 * make. The container decides which of the two a width gets
 * (`config-actions.model.ts`, unit-tested); this proves what each renders.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const LONG_NAME = 'a-very-long-secret-name-that-should-wrap-rather-than-overflow-its-detail-panel';
const VM: ConfigRecordVm = {
  name: LONG_NAME,
  badges: [{ label: 'unused', tone: 'waiting' }],
  facts: [
    { label: 'Value', value: '•••••••• write-only, never shown' },
    { label: 'Replaced by', value: 'pgross' },
  ],
  revision: 3,
  note: null,
  links: {
    heading: 'Referred to by',
    links: [{ kind: 'repository', name: LONG_NAME, route: ['/admin', 'repositories', LONG_NAME] }],
    emptyText: 'Nothing refers to this secret.',
  },
  hasHistory: false,
};
const ACTIONS: ConfigActionsVm = { edit: false, replace: true, lifecycle: 'retire', retireBlocked: null };
const CLI = `blizzard hub secret set ${LONG_NAME}`;
const WRITE_IDS = ['sec-edit', 'sec-replace', 'sec-retire', 'sec-enable'];

async function mountPanel(write: boolean) {
  await TestBed.configureTestingModule({
    imports: [ConfigRecordPanel],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ConfigRecordPanel);
  fixture.componentRef.setInput('vm', VM);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('revisionsState', 'ready');
  fixture.componentRef.setInput('emptyText', 'Pick a secret.');
  fixture.componentRef.setInput('testidPrefix', 'sec');
  fixture.componentRef.setInput('actions', write ? ACTIONS : null);
  fixture.componentRef.setInput('cliCommand', write ? null : CLI);
  await fixture.whenStable();
  return fixture;
}

async function mountMaster(canCreate: boolean) {
  await TestBed.configureTestingModule({
    imports: [ConfigMaster],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ConfigMaster);
  fixture.componentRef.setInput('paneId', 'admin-sweep');
  fixture.componentRef.setInput('listCaption', 'Secrets');
  fixture.componentRef.setInput('filter', 'active');
  fixture.componentRef.setInput('rows', [
    { key: LONG_NAME, title: LONG_NAME, sub: ['replaced by pgross'], badges: [], revision: 3, retired: false },
  ]);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('emptyText', 'No secrets.');
  fixture.componentRef.setInput('backLabel', 'Secrets');
  fixture.componentRef.setInput('testidPrefix', 'sec');
  fixture.componentRef.setInput('canCreate', canCreate);
  await fixture.whenStable();
  return fixture;
}

async function sweep(
  width: number,
  body: (root: HTMLElement) => Promise<void> | void,
  make: () => Promise<{ nativeElement: HTMLElement; whenStable(): Promise<unknown> }>,
) {
  const pageErrors: string[] = [];
  const onError = (e: ErrorEvent) => pageErrors.push(e.message);
  const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);

  const fixture = await make();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  try {
    await page.viewport(width, 900);
    await new Promise((resolve) => requestAnimationFrame(resolve));
    await body(root);
  } finally {
    root.remove();
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  }
  expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
}

const has = (root: HTMLElement, id: string) => root.querySelector(`[data-testid="${id}"]`) !== null;

function expectNoOverflow(root: HTMLElement, id: string, width: number) {
  const el = root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
  expect(el, id).not.toBeNull();
  expect(
    el.scrollWidth,
    `${id} overflows horizontally at ${width}px (${el.scrollWidth} > ${el.clientWidth})`,
  ).toBeLessThanOrEqual(el.clientWidth);
}

describe('admin config surfaces shell sweep (web:shell-sweep)', () => {
  it('desktop: the detail carries its write controls and no CLI command, with no overflow', async () => {
    await sweep(
      1280,
      (root) => {
        expect(has(root, 'sec-replace')).toBe(true);
        expect(has(root, 'sec-retire')).toBe(true);
        expect(has(root, 'sec-cli')).toBe(false);
        expectNoOverflow(root, 'sec-detail', 1280);
      },
      () => mountPanel(true),
    );
  });

  it('desktop: the list offers New', async () => {
    await sweep(
      1280,
      (root) => expect(has(root, 'sec-new')).toBe(true),
      () => mountMaster(true),
    );
  });

  it.each([390, 320])('phone at %ipx: no write control, the CLI command shown, no overflow', async (width) => {
    await sweep(
      width,
      (root) => {
        for (const id of WRITE_IDS) expect(has(root, id), id).toBe(false);
        expect(root.querySelector('[data-testid="sec-cli"]')?.textContent).toContain(CLI);
        expectNoOverflow(root, 'sec-detail', width);
        expectNoOverflow(root, 'sec-cli', width);
      },
      () => mountPanel(false),
    );
  });

  it.each([390, 320])('phone at %ipx: the list has no New and does not overflow', async (width) => {
    await sweep(
      width,
      (root) => {
        expect(has(root, 'sec-new')).toBe(false);
        expect(root.scrollWidth, `list overflows at ${width}px`).toBeLessThanOrEqual(
          document.documentElement.clientWidth,
        );
      },
      () => mountMaster(false),
    );
  });
});
