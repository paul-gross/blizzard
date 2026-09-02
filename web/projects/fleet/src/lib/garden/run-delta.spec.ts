import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { FleetRunDelta, type RunDeltaVm } from './run-delta';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const VM: RunDeltaVm = {
  chunkId: 'ch_1',
  routineName: 'nightly',
  scopeSlug: 'blizzard',
  mode: 'full',
  outcome: 'done',
  escalation: null,
  sets: [
    {
      findingSetId: 'fins_1',
      revisionsLabel: 'blizzard@abc123',
      measurement: '3 findings',
      added: [{ findingId: 'fnd_1', findingClass: 'style', locus: 'a.py:1', summary: 'unused import', introduced: null }],
      observed: ['fnd_2'],
      gone: [{ findingId: 'fnd_3', note: 'resolved' }],
    },
    {
      findingSetId: 'fins_2',
      revisionsLabel: 'web@fedcba',
      measurement: null,
      added: [],
      observed: [],
      gone: [{ findingId: 'fnd_9', note: 'no longer present' }],
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

  it('links the chunk id to its board detail page', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="gardening-run-delta-chunk-link"]');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
  });

  it('keeps each delivered set’s added/observed/gone in their own distinct groups, never merged across sets', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const set1 = el.querySelector('[data-testid="gardening-run-delta-set-fins_1"]')!;
    expect(set1.querySelector('[data-testid="rd-group-added"]')?.textContent).toContain('unused import');
    expect(set1.querySelector('[data-testid="rd-group-observed"]')?.textContent).toContain('fnd_2');
    expect(set1.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('resolved');

    const set2 = el.querySelector('[data-testid="gardening-run-delta-set-fins_2"]')!;
    expect(set2.querySelector('[data-testid="rd-group-added"]')?.textContent).not.toContain('unused import');
    expect(set2.querySelector('[data-testid="rd-group-gone"]')?.textContent).toContain('no longer present');
  });

  it('states plainly that this is a delta, not a current-state snapshot', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-run-delta-caption"]')?.textContent).toContain(
      'not a current-state snapshot',
    );
  });

  it('names the run show CLI verb with the run’s own chunk id', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('hub run show ch_1');
  });

  it('renders the open escalation when the run needs a human', async () => {
    const fixture = await mount({
      vm: {
        ...VM,
        outcome: 'needs_human',
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
