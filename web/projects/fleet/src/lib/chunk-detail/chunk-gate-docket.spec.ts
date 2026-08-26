import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { DocketEntryView } from '../api/hub';
import { ChunkGateDocket } from './chunk-gate-docket';

const CREATE_ENTRY: DocketEntryView = {
  proposal_id: 'wip_01',
  node_name: 'build',
  kind: 'create',
  payload: { kind: 'create', title: 'fix the widget', body: 'details here', stated_priority: 'normal' },
  malformed: false,
  struck: false,
};

const UPDATE_ENTRY: DocketEntryView = {
  proposal_id: 'wip_02',
  node_name: 'build',
  kind: 'update',
  payload: { kind: 'update', source: 'default', ref: '9', evidence: 'still reproduces' },
  malformed: false,
  struck: false,
};

const MALFORMED_ENTRY: DocketEntryView = {
  proposal_id: 'wip_03',
  node_name: 'build',
  kind: 'create',
  payload: null,
  malformed: true,
  struck: false,
};

const STRUCK_ENTRY: DocketEntryView = {
  proposal_id: 'wip_04',
  node_name: 'build',
  kind: 'create',
  payload: { kind: 'create', title: 'already refused', body: 'details here', stated_priority: 'normal' },
  malformed: false,
  struck: true,
  struck_by: 'alice',
  struck_at: '2026-08-25T00:00:00Z',
};

describe('ChunkGateDocket', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkGateDocket],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders no docket section when there are no pending proposals', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gate-docket"]')).toBeNull();
  });

  it('renders each entry with its kind, proposing node, and kind-shaped payload', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY, UPDATE_ENTRY]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const kinds = [...el.querySelectorAll('[data-testid="docket-kind"]')].map((n) => n.textContent?.trim());
    expect(kinds).toEqual(['create', 'update']);
    const titles = [...el.querySelectorAll('[data-testid="docket-title"]')].map((n) => n.textContent?.trim());
    expect(titles).toEqual(['fix the widget', 'default#9']);
    expect(el.querySelector('[data-testid="docket-node"]')?.textContent).toContain('build');
  });

  it('renders a malformed proposal bare rather than failing', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [MALFORMED_ENTRY]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="docket-kind"]')?.textContent).toContain('create');
    expect(el.querySelector('[data-testid="docket-title"]')?.textContent).toContain('unreadable');
  });

  it('withholds the strike toggle without gate:resolve', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="docket-entry"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="docket-strike"]')).toBeNull();
  });

  it('tracks toggled proposal ids in struckIds, and untoggling removes them', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY, UPDATE_ENTRY]);
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const toggles = el.querySelectorAll<HTMLInputElement>('[data-testid="docket-strike"]');
    toggles[0].click();
    expect(fixture.componentInstance.struckIds()).toEqual(['wip_01']);

    toggles[1].click();
    expect(fixture.componentInstance.struckIds()).toEqual(['wip_01', 'wip_02']);

    toggles[0].click();
    expect(fixture.componentInstance.struckIds()).toEqual(['wip_02']);
  });

  it('starts with no proposal struck', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY]);
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();

    expect(fixture.componentInstance.struckIds()).toEqual([]);
  });

  it('emits struckChange with the full toggled-id set on every toggle', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY, UPDATE_ENTRY]);
    fixture.componentRef.setInput('canResolve', true);
    const emissions: (readonly string[])[] = [];
    fixture.componentInstance.struckChange.subscribe((ids) => emissions.push(ids));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const toggles = el.querySelectorAll<HTMLInputElement>('[data-testid="docket-strike"]');
    toggles[0].click();
    toggles[1].click();

    expect(emissions).toEqual([['wip_01'], ['wip_01', 'wip_02']]);
  });

  it('renders an already-struck entry as struck with no toggle, and never re-strikes it', async () => {
    const fixture = TestBed.createComponent(ChunkGateDocket);
    fixture.componentRef.setInput('entries', [CREATE_ENTRY, STRUCK_ENTRY]);
    fixture.componentRef.setInput('canResolve', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const entries = el.querySelectorAll('[data-testid="docket-entry"]');
    expect(entries[1].classList.contains('struck')).toBe(true);
    expect(el.querySelectorAll('[data-testid="docket-strike"]').length).toBe(1); // only CREATE_ENTRY's
    expect(fixture.componentInstance.struckIds()).toEqual([]);
  });
});
