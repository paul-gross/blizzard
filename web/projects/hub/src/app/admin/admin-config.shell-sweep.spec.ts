import { Injector, provideZonelessChangeDetection, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { Urgency } from 'fleet';

import type { ConfigActionsVm } from './config-actions.model';
import { ConfigMaster } from './config-master';
import { ConfigFieldTreeDialog } from './config-field-tree-dialog';
import { ConfigFormDialog } from './config-form-dialog';
import type { RevisionRowVm } from './config-history.model';
import { ConfigRecordPanel, type ConfigRecordVm } from './config-record-panel';
import { SECRET_CREATE_FIELD_VIEWS } from './secrets/secrets.model';
import { secretCreateRules } from './secrets/secret.form';
import type { SecretCreateDraft } from './secrets/secret.domain';
import { UrgenciesList } from './urgencies/urgencies-list';
import { UrgencyDetailView } from './urgencies/urgency-detail-view';
import { urgencyRecordVm } from './urgencies/urgency-detail.model';

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
const REVISIONS: readonly RevisionRowVm[] = ['create', 'retire', 'enable'].map((op, index) => ({
  id: index + 1,
  revision: index + 1,
  op,
  fields: op === 'create' ? 'description: A long urgency description for a newly created record' : '',
  at: '2026-01-01T12:00:00Z',
  actor: 'a-very-long-actor-name-that-must-wrap-without-squeezing-the-description',
  door: 'cli',
}));
const URGENCY: Urgency = {
  id: 'u1', slug: LONG_NAME, name: LONG_NAME, color: 'red', emoji: '🔥', icon: null,
  retired: false, default: false, revision: 2, openChunks: 3, place: 0, createdAt: '', createdBy: 'operator',
};

async function mountUrgenciesList() {
  await TestBed.configureTestingModule({
    imports: [UrgenciesList],
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
  }).compileComponents();
  const fixture = TestBed.createComponent(UrgenciesList);
  fixture.componentRef.setInput('canEdit', false);
  fixture.componentRef.setInput('mobile', true);
  fixture.componentRef.setInput('pending', false);
  fixture.componentRef.setInput('settingsReady', true);
  fixture.componentRef.setInput('defaultOptions', []);
  fixture.componentRef.setInput('lifecycle', 'active');
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', [URGENCY, { ...URGENCY, id: 'u2', emoji: null, slug: 'unmarked' }]);
  await fixture.whenStable();
  return fixture;
}

async function mountUrgencyDetail(retired: boolean) {
  await TestBed.configureTestingModule({
    imports: [UrgencyDetailView],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(UrgencyDetailView);
  const record = { ...URGENCY, retired };
  fixture.componentRef.setInput('vm', urgencyRecordVm(record));
  fixture.componentRef.setInput('record', record);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('changesState', 'ready');
  fixture.componentRef.setInput('mobile', true);
  fixture.componentRef.setInput('cli', `blizzard hub urgency edit ${record.slug}`);
  fixture.componentRef.setInput('destinationOptions', []);
  await fixture.whenStable();
  return fixture;
}

async function mountPanel(write: boolean) {
  await TestBed.configureTestingModule({
    imports: [ConfigRecordPanel],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ConfigRecordPanel);
  fixture.componentRef.setInput('vm', { ...VM, hasHistory: true });
  fixture.componentRef.setInput('revisions', REVISIONS);
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
    { key: LONG_NAME, title: LONG_NAME, sub: ['3 references'], badges: [], revision: 3, retired: false },
  ]);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('emptyText', 'No secrets.');
  fixture.componentRef.setInput('backLabel', 'Secrets');
  fixture.componentRef.setInput('testidPrefix', 'sec');
  fixture.componentRef.setInput('canCreate', canCreate);
  await fixture.whenStable();
  return fixture;
}

async function mountForgeForm() {
  await TestBed.configureTestingModule({
    imports: [ConfigFormDialog], providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ConfigFormDialog);
  fixture.componentRef.setInput('heading', 'New repository');
  fixture.componentRef.setInput('mode', 'create');
  fixture.componentRef.setInput('fields', [
    { key: 'forge', label: 'Forge', kind: 'select', required: true, options: [
      { value: 'frg_github', label: 'github · GitHub Enterprise with a long display name' },
    ] },
  ]);
  fixture.componentRef.setInput('submitLabel', 'Create');
  fixture.componentRef.setInput('testidPrefix', 'forge-form');
  await fixture.whenStable();
  return fixture;
}

const PEM = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo\n-----END RSA PRIVATE KEY-----\n';

async function mountSecretForm() {
  await TestBed.configureTestingModule({
    imports: [ConfigFieldTreeDialog], providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ConfigFieldTreeDialog<SecretCreateDraft>);
  const draft = form(signal<SecretCreateDraft>({ name: 'app-key', value: PEM }), secretCreateRules, {
    injector: TestBed.inject(Injector),
  });
  fixture.componentRef.setInput('heading', 'New secret');
  fixture.componentRef.setInput('mode', 'create');
  fixture.componentRef.setInput('form', draft);
  fixture.componentRef.setInput('fields', SECRET_CREATE_FIELD_VIEWS);
  fixture.componentRef.setInput('submitLabel', 'Create');
  fixture.componentRef.setInput('testidPrefix', 'secret-form');
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
  it.each([390, 320])('urgency list at %ipx: marks, labels and CLI guidance without write controls or overflow', async (width) => {
    await sweep(width, (root) => {
      expect(has(root, 'admin-urgencies-new')).toBe(false);
      expect(has(root, 'admin-urgencies-default')).toBe(false);
      expect(root.querySelector('.drag-handle')).toBeNull();
      const hints = root.querySelector<HTMLElement>('[data-testid="admin-urgencies-cli"]')!;
      for (const command of ['urgency create', 'settings edit --default-urgency', 'urgency position']) {
        expect(hints.textContent).toContain(`blizzard hub ${command}`);
      }
      const rows = root.querySelectorAll<HTMLElement>('.urgency-item');
      expect(rows.length).toBe(2);
      expect(rows[0].textContent).toContain('🔥');
      expect(rows[0].textContent).toContain(LONG_NAME);
      expect(rows[1].querySelector('.pill .name')?.textContent).toContain(LONG_NAME);
      for (const el of [root, hints, ...rows]) {
        expect(el.scrollWidth, `${el.tagName}.${el.className} overflows at ${width}px`).toBeLessThanOrEqual(el.clientWidth);
      }
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
    }, mountUrgenciesList);
  });

  it.each([390, 320])('urgency detail at %ipx: active and retired CLI guidance without write controls or overflow', async (width) => {
    for (const retired of [false, true]) {
      await sweep(width, (root) => {
        for (const action of ['edit', 'replace', 'retire', 'enable']) expect(has(root, `admin-urgency-${action}`)).toBe(false);
        const hints = root.querySelector<HTMLElement>('[data-testid="admin-urgency-cli-actions"]')!;
        expect(hints.textContent).toContain(`blizzard hub urgency edit ${LONG_NAME}`);
        expect(hints.textContent).toContain(`blizzard hub urgency ${retired ? 'enable' : 'retire'} ${LONG_NAME}`);
        if (!retired) {
          expect(hints.textContent).toContain('--move-to <slug>');
          expect(hints.textContent).toContain('blizzard hub settings edit --default-urgency');
          expect(hints.textContent).toContain('blizzard hub urgency position');
        }
        expectNoOverflow(root, 'admin-urgency-detail', width);
        expectNoOverflow(root, 'admin-urgency-cli-actions', width);
        expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
      }, () => mountUrgencyDetail(retired));
      TestBed.resetTestingModule();
    }
  });
  it.each([1280, 390, 320])('forge picker at %ipx: usable without horizontal overflow', async (width) => {
    await sweep(width, async (root) => {
      const trigger = root.querySelector<HTMLButtonElement>('[data-testid="forge-form-field-forge"]')!;
      expect(trigger).not.toBeNull();
      expect((root.querySelector('[data-testid="forge-form-submit"]') as HTMLButtonElement).disabled).toBe(true);
      trigger.click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const option = document.querySelector<HTMLElement>('[role="option"]')!;
      expect(option?.textContent).toContain('GitHub Enterprise');
      option.click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect((root.querySelector('[data-testid="forge-form-submit"]') as HTMLButtonElement).disabled).toBe(false);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
    }, mountForgeForm);
  });
  it.each([1280, 390, 320])('secret form at %ipx: the value is a masked multi-line control', async (width) => {
    await sweep(width, () => {
      const value = document.querySelector<HTMLTextAreaElement>('[data-testid="secret-form-field-value"]')!;
      const name = document.querySelector<HTMLInputElement>('[data-testid="secret-form-field-name"]')!;
      expect(value?.tagName).toBe('TEXTAREA');
      expect(getComputedStyle(value).getPropertyValue('-webkit-text-security')).toBe('disc');
      expect(value.value).toBe(PEM);
      expect(value.getBoundingClientRect().height).toBeGreaterThan(2 * name.getBoundingClientRect().height);
      expect(value.getBoundingClientRect().right).toBeLessThanOrEqual(width);
      expect(document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!.disabled).toBe(false);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
    }, mountSecretForm);
  });

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

  it.each([390, 320])('phone at %ipx: revisions stay readable and detail links remain usable', async (width) => {
    await sweep(width, (root) => {
      const rows = root.querySelectorAll<HTMLElement>('.rev-row');
      expect(rows.length).toBe(3);
      rows.forEach((row, index) => {
        const description = row.querySelector<HTMLElement>('.what')!;
        const actor = row.querySelector<HTMLElement>('.who')!;
        expect(description.textContent).toContain(REVISIONS[index].op);
        expect(description.getBoundingClientRect().width).toBeGreaterThan(150);
        expect(actor.getBoundingClientRect().top).toBeGreaterThan(description.getBoundingClientRect().top);
        expect(row.scrollWidth).toBeLessThanOrEqual(row.clientWidth);
      });
      expectNoOverflow(root, 'sec-detail', width);
      expectNoOverflow(root, 'sec-revisions', width);
      const link = root.querySelector<HTMLAnchorElement>('[data-testid="sec-links"] a')!;
      expect(link.getAttribute('href')).toContain('/admin/repositories/');
      expect(link.getBoundingClientRect().width).toBeGreaterThan(0);
    }, () => mountPanel(false));
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
