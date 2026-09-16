import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitMasterDetail } from './kit-master-detail';

/**
 * Markup order is the reverse of pane order — detail projected before list — the same
 * proof {@link ChunkPageShell}'s own spec uses: a component that just rendered
 * `<ng-content>` in whatever order the caller wrote it would fail the ordering
 * assertion below.
 */
@Component({
  selector: 'fleet-kit-master-detail-test-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail paneId="kmd-test" backLabel="Node history">
      <div kit-master-detail-detail data-testid="slot-detail">detail</div>
      <div kit-master-detail-list data-testid="slot-list">list</div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailTestHost {}

@Component({
  selector: 'fleet-kit-master-detail-caption-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail paneId="kmd-test" backLabel="Node history" listCaption="Timeline" detailCaption="Step">
      <div kit-master-detail-list data-testid="slot-list">list</div>
      <div kit-master-detail-detail data-testid="slot-detail">detail</div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailCaptionHost {}

@Component({
  selector: 'fleet-kit-master-detail-drilldown-list-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail paneId="kmd-test" backLabel="Node history" [drilldown]="true" [hasSelection]="false">
      <div kit-master-detail-list data-testid="slot-list">list</div>
      <div kit-master-detail-detail data-testid="slot-detail">detail</div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailDrilldownListHost {}

@Component({
  selector: 'fleet-kit-master-detail-drilldown-detail-host',
  imports: [KitMasterDetail],
  template: `
    <fleet-kit-master-detail
      paneId="kmd-test"
      backLabel="Node history"
      testidPrefix="node-history"
      [drilldown]="true"
      [hasSelection]="true"
      (back)="backCount = backCount + 1"
    >
      <div kit-master-detail-list data-testid="slot-list">list</div>
      <div kit-master-detail-detail data-testid="slot-detail">detail</div>
    </fleet-kit-master-detail>
  `,
})
class KitMasterDetailDrilldownDetailHost {
  backCount = 0;
}

describe('KitMasterDetail', () => {
  it('projects each slot into its own pane in list-then-detail order regardless of markup order', async () => {
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const order = Array.from(el.querySelectorAll('[data-testid]')).map((node) => node.getAttribute('data-testid'));
    expect(order).toEqual(['slot-list', 'slot-detail']);
    expect(el.querySelector('.kmd-list')?.contains(el.querySelector('[data-testid="slot-list"]'))).toBe(true);
    expect(el.querySelector('.kmd-detail')?.contains(el.querySelector('[data-testid="slot-detail"]'))).toBe(true);
  });

  it('renders no caption node for either pane when both captions are omitted', async () => {
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailTestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailTestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.s-head')).toBeNull();
    expect(el.querySelector('.tag')).toBeNull();
    expect(el.querySelector('.kmd-list')?.getAttribute('role')).toBeNull();
    expect(el.querySelector('.kmd-detail')?.getAttribute('role')).toBeNull();
  });

  it('labels a captioned pane through role="region"/aria-labelledby, id-derived from paneId', async () => {
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailCaptionHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailCaptionHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const list = el.querySelector('.kmd-list') as HTMLElement;
    const detail = el.querySelector('.kmd-detail') as HTMLElement;
    expect(list.getAttribute('role')).toBe('region');
    expect(detail.getAttribute('role')).toBe('region');
    expect(document.getElementById(list.getAttribute('aria-labelledby')!)?.textContent).toBe('Timeline');
    expect(document.getElementById(detail.getAttribute('aria-labelledby')!)?.textContent).toBe('Step');
    expect(list.getAttribute('aria-labelledby')).not.toBe(detail.getAttribute('aria-labelledby'));
  });

  it('renders only the list pane, with no Back control, under drilldown with no selection', async () => {
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailDrilldownListHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailDrilldownListHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="slot-list"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="slot-detail"]')).toBeNull();
    expect(el.querySelector('.kmd-back')).toBeNull();
  });

  it('renders only the detail pane, with a Back control rooted at testidPrefix, under drilldown with a selection', async () => {
    await TestBed.configureTestingModule({
      imports: [KitMasterDetailDrilldownDetailHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitMasterDetailDrilldownDetailHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="slot-list"]')).toBeNull();
    expect(el.querySelector('[data-testid="slot-detail"]')).not.toBeNull();
    const back = el.querySelector<HTMLButtonElement>('[data-testid="node-history-back"]');
    expect(back).not.toBeNull();

    back!.click();
    expect(fixture.componentInstance.backCount).toBe(1);
  });
});
