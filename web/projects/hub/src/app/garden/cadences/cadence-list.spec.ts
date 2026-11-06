import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fixtureInstant } from 'fleet/testing';

import { FleetCadenceList, type CadenceRowVm } from './cadence-list';

const ROWS: readonly CadenceRowVm[] = [
  {
    slug: 'evening',
    name: 'Evening sweep',
    retired: false,
    rhythm: '0 20 * * * America/Chicago, at least 10 days apart',
    nextMoment: fixtureInstant(14 * 3600),
    choreCount: 2,
    counts: [
      { state: 'due', label: 'due', tone: 'spawning', count: 1 },
      { state: 'stopped', label: 'stopped', tone: 'stale', count: 3 },
    ],
    attentionCount: 2,
  },
  {
    slug: 'off',
    name: 'off',
    retired: true,
    rhythm: 'at least 1 day apart',
    nextMoment: null,
    choreCount: 1,
    counts: [],
    attentionCount: 0,
  },
];

describe('FleetCadenceList', () => {
  async function mount(inputs: { rows?: readonly CadenceRowVm[]; state?: 'loading' | 'error' | 'empty' | 'ready' } = {}) {
    await TestBed.configureTestingModule({
      imports: [FleetCadenceList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetCadenceList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('selectedSlug', 'evening');
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('renders each cadence with its rhythm, next moment, chore count, and errand counts', async () => {
    const { el } = await mount();

    const row = el.querySelector('[data-testid="gardening-cadence-row-evening"]')!;
    expect(row.textContent).toContain('Evening sweep');
    expect(el.querySelector('[data-testid="gardening-cadence-rhythm-evening"]')?.textContent).toContain(
      '0 20 * * * America/Chicago, at least 10 days apart',
    );
    expect(el.querySelector('[data-testid="gardening-cadence-next-evening"]')?.getAttribute('title')).toBeTruthy();
    const counts = el.querySelector('[data-testid="gardening-cadence-counts-evening"]')!.textContent!.replace(/\s+/g, ' ');
    expect(counts).toContain('2 chores');
    expect(counts).toContain('1 due');
    expect(counts).toContain('3 stopped');
  });

  it('badges attention and a retired cadence, and names no next moment where there is none', async () => {
    const { el } = await mount();

    expect(el.querySelector('[data-testid="gardening-cadence-attention-evening"]')?.textContent).toContain('attention 2');
    expect(el.querySelector('[data-testid="gardening-cadence-attention-off"]')).toBeNull();
    const off = el.querySelector('[data-testid="gardening-cadence-row-off"]')!;
    expect(off.textContent).toContain('retired');
    expect(off.textContent).toContain('1 chore');
    expect(el.querySelector('[data-testid="gardening-cadence-next-off"]')).toBeNull();
  });

  it('emits the picked slug', async () => {
    const { fixture, el } = await mount();
    const picked: string[] = [];
    fixture.componentInstance.cadencePick.subscribe((slug) => picked.push(slug));

    el.querySelector<HTMLElement>('[data-testid="gardening-cadence-row-off"]')!.click();

    expect(picked).toEqual(['off']);
  });

  it('renders the empty state with no cadence', async () => {
    const { el } = await mount({ rows: [], state: 'empty' });
    expect(el.querySelector('[data-testid="gardening-cadences-empty"]')?.textContent).toContain('No cadences kept yet.');
  });
});
