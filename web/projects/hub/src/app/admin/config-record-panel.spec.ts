import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { RevisionRowVm } from './config-history.model';
import { ConfigRecordPanel, type ConfigRecordVm } from './config-record-panel';

const SECRET: ConfigRecordVm = {
  name: 'gh-blizzard',
  badges: [],
  facts: [{ label: 'Replaced by', value: 'pgross' }],
  revision: 3,
  note: null,
  links: {
    heading: 'Referred to by',
    links: [{ kind: 'repository', name: 'blizzard', route: ['/admin', 'repositories', 'blizzard'] }],
    emptyText: 'Nothing refers to this secret.',
  },
  hasHistory: true,
};
const BUILT_IN: ConfigRecordVm = {
  name: 'hub',
  badges: [{ label: 'built-in', tone: 'spawning' }],
  facts: [],
  revision: null,
  note: 'not configurable',
  links: null,
  hasHistory: false,
};
const REVISIONS: readonly RevisionRowVm[] = [
  { id: 4, revision: 3, op: 'replace', fields: '', at: '2026-01-03T00:00:00Z', actor: 'ana', door: 'board' },
  { id: 1, revision: 1, op: 'create', fields: '', at: '2026-01-01T00:00:00Z', actor: 'pgross', door: 'cli' },
];

describe('ConfigRecordPanel', () => {
  async function mount(vm: ConfigRecordVm | null, extra: { cliCommand?: string | null; state?: 'ready' | 'empty' } = {}) {
    await TestBed.configureTestingModule({
      imports: [ConfigRecordPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ConfigRecordPanel);
    fixture.componentRef.setInput('vm', vm);
    fixture.componentRef.setInput('state', extra.state ?? 'ready');
    fixture.componentRef.setInput('lastChange', { revision: 3, at: '2026-01-03T00:00:00Z', actor: 'ana', door: 'board' });
    fixture.componentRef.setInput('revisions', REVISIONS);
    fixture.componentRef.setInput('revisionsState', 'ready');
    fixture.componentRef.setInput('cliCommand', extra.cliCommand ?? null);
    fixture.componentRef.setInput('emptyText', 'Pick a secret.');
    fixture.componentRef.setInput('testidPrefix', 'sec');
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders the fields, the revision with its last actor and door, and the revisions', async () => {
    const el = await mount(SECRET);
    expect(el.querySelector('[data-testid="sec-facts"]')?.textContent).toContain('pgross');
    const revision = el.querySelector('[data-testid="sec-revision"]')?.textContent?.replace(/\s+/g, ' ');
    expect(revision).toContain('r3');
    expect(revision).toContain('by ana via board');
    expect(el.querySelectorAll('[data-testid="sec-revisions"] li')).toHaveLength(2);
  });

  it('links each referring record', async () => {
    const el = await mount(SECRET);
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="sec-links"] a');
    expect(link?.textContent).toBe('blizzard');
    expect(link?.getAttribute('href')).toBe('/admin/repositories/blizzard');
  });

  it('shows the CLI command only when given one', async () => {
    expect((await mount(SECRET)).querySelector('[data-testid="sec-cli"]')).toBeNull();
    TestBed.resetTestingModule();
    const el = await mount(SECRET, { cliCommand: 'blizzard hub secret set gh-blizzard' });
    expect(el.querySelector('[data-testid="sec-cli"]')?.textContent).toContain('blizzard hub secret set gh-blizzard');
  });

  it('shows a built-in record badged, with no revision and no history', async () => {
    const el = await mount(BUILT_IN);
    expect(el.textContent).toContain('built-in');
    expect(el.querySelector('[data-testid="sec-revision"]')).toBeNull();
    expect(el.querySelector('[data-testid="sec-revisions"]')).toBeNull();
  });

  it('renders the rest copy with nothing selected', async () => {
    const el = await mount(null, { state: 'empty' });
    expect(el.querySelector('[data-testid="sec-detail-empty"]')?.textContent).toContain('Pick a secret.');
  });
});
