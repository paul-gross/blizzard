import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BoardShell } from './board-shell';
import { laneSkeletonCounts, listSkeletonCount } from './board-skeletons.model';

const LANE_SKELETONS = new Map([
  ['notready', 3],
  ['ready', 0],
  ['waiting', 2],
  ['needs', 1],
  ['running', 4],
  ['done', 9],
]);

describe('BoardShell loading skeletons', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('draws each lane its drawn run of card skeletons while loading, and none once the read answers', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('laneSkeletons', LANE_SKELETONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const perLane = () =>
      Object.fromEntries(
        [...el.querySelectorAll('[data-col]')].map((col) => [col.getAttribute('data-col'), col.querySelectorAll('[data-testid="chunk-card-skeleton"]').length]),
      );

    expect(perLane()).toEqual(Object.fromEntries(LANE_SKELETONS));

    fixture.componentRef.setInput('state', 'empty');
    await fixture.whenStable();
    expect(el.querySelectorAll('[data-testid="chunk-card-skeleton"]')).toHaveLength(0);
  });

  it('draws the list its run of skeleton rows while loading', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('layout', 'list');
    fixture.componentRef.setInput('listSkeletons', 7);
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="list-row-skeleton"]')).toHaveLength(7);
  });
});

describe('laneSkeletonCounts', () => {
  it('runs every lane from none up to 4, BACKLOG to 10 and DONE to 20', () => {
    expect(Object.fromEntries(laneSkeletonCounts(() => 0))).toEqual({ notready: 0, ready: 0, waiting: 0, needs: 0, running: 0, done: 0 });
    expect(Object.fromEntries(laneSkeletonCounts(() => 0.9999))).toEqual({ notready: 10, ready: 4, waiting: 4, needs: 4, running: 4, done: 20 });
  });
});

describe('listSkeletonCount', () => {
  it('runs from 6 to 14 rows', () => {
    expect(listSkeletonCount(() => 0)).toBe(6);
    expect(listSkeletonCount(() => 0.9999)).toBe(14);
  });
});
