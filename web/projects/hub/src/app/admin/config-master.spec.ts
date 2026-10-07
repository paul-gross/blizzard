import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ConfigMaster } from './config-master';
import type { ConfigRowVm } from './config-record-list';

const ROWS: readonly ConfigRowVm[] = [
  { key: 'winter', title: 'winter', sub: [], badges: [], revision: 1, retired: false },
];

@Component({
  selector: 'app-test-config-master-host',
  imports: [ConfigMaster],
  template: `
    <app-config-master
      paneId="t"
      listCaption="Things"
      [filter]="filter()"
      [rows]="rows"
      state="ready"
      emptyText="none"
      [selectedKey]="selectedKey()"
      [drilldown]="drilldown()"
      backLabel="Things"
      testidPrefix="t"
      [hasOlder]="hasOlder()"
      (filterChange)="chosen.push($event)"
      (older)="olderClicks = olderClicks + 1"
    >
      <p data-testid="t-projected">detail</p>
    </app-config-master>
  `,
})
class TestHost {
  readonly filter = signal<string | null>('active');
  readonly selectedKey = signal<string | null>(null);
  readonly drilldown = signal(false);
  readonly hasOlder = signal(false);
  readonly rows = ROWS;
  readonly chosen: string[] = [];
  olderClicks = 0;
}

describe('ConfigMaster', () => {
  async function mount() {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the filter chips beside the list and the projected detail', async () => {
    const fixture = await mount();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="t-filters"]')?.textContent).toContain('Retired');
    expect(el.querySelector('[data-testid="t-row-winter"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="t-projected"]')).not.toBeNull();
    el.querySelector<HTMLElement>('[data-testid="config-filter-retired"]')!.click();
    expect(fixture.componentInstance.chosen).toEqual(['retired']);
  });

  it('renders no chips for a list with no filter, and an Older control when a page remains', async () => {
    const fixture = await mount();
    fixture.componentInstance.filter.set(null);
    fixture.componentInstance.hasOlder.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="t-filters"]')).toBeNull();
    el.querySelector<HTMLElement>('[data-testid="t-older"] button, [data-testid="t-older"]')!.click();
    expect(fixture.componentInstance.olderClicks).toBe(1);
  });

  it('drills down on a phone: the list alone, then the detail alone behind Back', async () => {
    const fixture = await mount();
    fixture.componentInstance.drilldown.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="t-row-winter"]')).not.toBeNull();
    expect(el.querySelector('.kmd-detail')?.classList.contains('kmd-pane--hidden')).toBe(true);

    fixture.componentInstance.selectedKey.set('winter');
    await fixture.whenStable();
    expect(el.querySelector('.kmd-list')?.classList.contains('kmd-pane--hidden')).toBe(true);
    expect(el.querySelector('.kmd-detail')?.classList.contains('kmd-pane--hidden')).toBe(false);
    expect(el.querySelector('[data-testid="t-projected"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="t-back"]')).not.toBeNull();
  });
});
