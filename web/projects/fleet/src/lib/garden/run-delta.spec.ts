import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { FleetRunDelta, type RunDeltaVm } from './run-delta';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const VM: RunDeltaVm = {
  chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  routineName: 'architecture',
  scopeSlug: 'runner-daemon',
  mintedAt: '2026-01-10T00:00:00Z',
  escalation: null,
  sets: [
    {
      findingSetId: 'fins_01M1KANH0RZEABSD44RCEH6G9B',
      revisionsLabel: 'blizzard@396d00708fb7092463f38a0dcbc035e7a9e39e4c, blizzard-context@b7db19f4940ff08178836f2a8a20069811e09aee',
      measurement: '3 findings',
      added: [
        { findingId: 'fin_1', findingClass: 'style', locus: 'a.py:1', summary: 'unused import', introduced: null },
        { findingId: null, findingClass: 'style', locus: 'a.py:2', summary: 'no finding id yet', introduced: null },
      ],
      observed: [
        { findingId: 'fin_2', findingClass: 'perf', locus: 'b.py:7', summary: 'still reproducing' },
        // The id names no finding row, so nothing descriptive comes back — the entry
        // must still render by reference rather than being dropped.
        { findingId: 'fin_8', findingClass: null, locus: null, summary: null },
      ],
      gone: [{ findingId: 'fin_3', note: 'resolved' }],
    },
    {
      findingSetId: 'fins_2',
      revisionsLabel: 'web@fedcba',
      measurement: null,
      added: [],
      observed: [],
      gone: [{ findingId: 'fin_9', note: 'no longer present' }],
    },
  ],
};

describe('FleetRunDelta', () => {
  async function mount(inputs: { vm?: RunDeltaVm | null; state?: KitAsyncStateValue }) {
    await TestBed.configureTestingModule({
      imports: [FleetRunDelta],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRunDelta);
    fixture.componentRef.setInput('vm', inputs.vm ?? VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    await fixture.whenStable();
    return fixture;
  }

  it('links the chunk id to its board detail page, rendered through compactRef with the full id in title', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="gardening-run-delta-chunk-link"]');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(link?.textContent).toBe('C-3YJ9');
    expect(link?.getAttribute('title')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('renders the header meta line as one uniform, id-first, ·-separated sequence', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const meta = el.querySelector('[data-testid="gardening-run-delta-meta"]');
    expect(meta?.firstElementChild?.getAttribute('data-testid')).toBe('gardening-run-delta-chunk-link');
    expect(meta?.textContent).toContain('C-3YJ9');
    expect(meta?.textContent).toContain('architecture');
    expect(meta?.textContent).toContain('runner-daemon');
    expect(meta?.querySelector('fleet-when')).toBeTruthy();
  });

  it('omits the minted time from the meta line when the container has no matching run row', async () => {
    const fixture = await mount({ vm: { ...VM, mintedAt: null } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-meta"] fleet-when')).toBeNull();
  });

  it('titles each finding-set heading "Finding Set" plus its compactRef, with the full id in title', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const heading = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"] h4');
    expect(heading?.textContent?.trim()).toBe('Finding Set FS-6G9B');
    expect(heading?.querySelector('[title]')?.getAttribute('title')).toBe('fins_01M1KANH0RZEABSD44RCEH6G9B');
  });

  it('keeps each delivered set’s added/observed/gone in their own distinct groups, never merged across sets', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const set1 = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"]')!;
    expect(set1.querySelector('[data-testid="rd-group-added"]')?.textContent).toContain('unused import');
    expect(set1.querySelector('[data-testid="rd-group-observed"]')?.textContent).toContain('F-2');
    expect(set1.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('resolved');

    const set2 = el.querySelector('[data-testid="gardening-run-delta-set-fins_2"]')!;
    expect(set2.querySelector('[data-testid="rd-group-added"]')).toBeNull();
    expect(set2.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('no longer present');
  });

  it('restructures an added entry into a ref/class/locus head line and a prose-block summary', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const added = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"] [data-testid="rd-group-added"]')!;
    const head = added.querySelector('.rd-entry-head')!;
    const linked = head.querySelector<HTMLAnchorElement>('a.rd-ref');
    expect(linked?.getAttribute('href')).toBe('/gardening/findings/fin_1');
    expect(linked?.textContent).toBe('F-1');
    expect(head.querySelector('.rd-entry-class')?.textContent).toBe('style');
    expect(head.querySelector('.rd-entry-locus')?.textContent).toBe('a.py:1');
    expect(added.querySelector('fleet-kit-prose-block')?.textContent).toContain('unused import');
  });

  it('renders an added entry with no findingId as plain unlinked text, never a dead link', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const items = el.querySelectorAll(
      '[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"] [data-testid="rd-group-added"] .rd-entry',
    );
    const secondItem = items[1];
    expect(secondItem.querySelector('a.rd-ref')).toBeNull();
    expect(secondItem.querySelector('.rd-entry-ref-none')).toBeTruthy();
    expect(secondItem.textContent).toContain('no finding id yet');
  });

  it('renders an observed finding with the same class, locus, and prose an added one carries', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const observed = el.querySelector('[data-testid="rd-group-observed"] .rd-entry')!;
    const head = observed.querySelector('.rd-entry-head')!;
    expect(head.querySelector('a.rd-ref')?.textContent).toBe('F-2');
    expect(head.querySelector('.rd-entry-class')?.textContent).toBe('perf');
    expect(head.querySelector('.rd-entry-locus')?.textContent).toBe('b.py:7');
    expect(observed.querySelector('[data-testid="rd-observed-summary-fin_2"]')?.textContent).toContain(
      'still reproducing',
    );
  });

  it('renders an observed finding whose id names no row by reference alone, never dropping it', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const entries = el.querySelectorAll('[data-testid="rd-group-observed"] .rd-entry');
    expect(entries.length).toBe(2);

    const unresolved = entries[1];
    expect(unresolved.querySelector('a.rd-ref')?.textContent).toBe('F-8');
    expect(unresolved.querySelector('.rd-entry-class')).toBeNull();
    expect(unresolved.querySelector('.rd-entry-locus')).toBeNull();
    expect(unresolved.querySelector('fleet-kit-prose-block')).toBeNull();
  });

  it('links every observed and gone finding to its detail route, rendered through compactRef', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const set1 = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"]')!;
    const observedLink = set1.querySelector<HTMLAnchorElement>('[data-testid="rd-group-observed"] a.rd-ref');
    expect(observedLink?.getAttribute('href')).toBe('/gardening/findings/fin_2');
    expect(observedLink?.textContent).toBe('F-2');

    const goneLink = set1.querySelector<HTMLAnchorElement>('[data-testid="rd-group-gone"] a.rd-ref');
    expect(goneLink?.getAttribute('href')).toBe('/gardening/findings/fin_3');
    expect(set1.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('resolved');
  });

  it('hides the added, observed, and gone groups entirely when a set has none, rather than an empty "none" block', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const set2 = el.querySelector('[data-testid="gardening-run-delta-set-fins_2"]')!;
    expect(set2.querySelector('[data-testid="rd-group-added"]')).toBeNull();
    expect(set2.querySelector('[data-testid="rd-group-observed"]')).toBeNull();
    expect(set2.querySelector('[data-testid="rd-group-gone"]')).toBeTruthy();
    expect(el.textContent).not.toContain('none');
  });

  it('colors the added group accent red and the gone group accent green (swapped from the old ladder)', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const set1 = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"]')!;
    expect(set1.querySelector('.rd-group--added')).toBeTruthy();
    expect(set1.querySelector('.rd-group--gone')).toBeTruthy();
  });

  it('renders the set measurement through fleet-kit-prose-block', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const prose = el.querySelector('[data-testid="gardening-run-delta-set-fins_01M1KANH0RZEABSD44RCEH6G9B"] fleet-kit-prose-block');
    expect(prose?.textContent).toContain('3 findings');
  });

  it('wraps the full revisions label rather than overflowing it', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const revisions = el.querySelector('.rd-set-revisions');
    expect(revisions?.textContent).toContain('396d00708fb7092463f38a0dcbc035e7a9e39e4c');
  });

  it('renders the open escalation when the run needs a human', async () => {
    const fixture = await mount({
      vm: {
        ...VM,
        escalation: { nodeName: 'survey', takeoverCommand: 'blizzard hub chunk takeover ch_1', wrappedTakeoverCommand: '' },
      },
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-escalation"]')?.textContent).toContain(
      'blizzard hub chunk takeover ch_1',
    );
  });

  it('shows the empty state when no run is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-empty"]')).toBeTruthy();
  });
});
