import { CdkDrag, CdkDropList, type CdkDragDrop } from '@angular/cdk/drag-drop';
import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import type { LandingPositionRequest } from 'fleet';

import type { LandingSection, LandingSections } from './project-landing-order.model';
import { ProjectLinkPanel } from './project-link-panel';
import type { ProjectLinkRowVm } from './projects.model';

const row = (key: string, name: string): ProjectLinkRowVm => ({ key, name, detail: '', builtIn: false, revision: 1 });
const SECTIONS: LandingSections = { ranked: [row('repo_a', 'alpha'), row('repo_b', 'bravo')], unranked: [row('repo_x', 'xray')] };

describe('ProjectLinkPanel', () => {
  async function mount(canEdit: boolean, sections: LandingSections | null = SECTIONS) {
    await TestBed.configureTestingModule({
      imports: [ProjectLinkPanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProjectLinkPanel);
    const set = (name: string, value: unknown) => fixture.componentRef.setInput(name, value);
    set('heading', 'Linked');
    set('explainer', '');
    set('rows', [...SECTIONS.ranked, ...SECTIONS.unranked]);
    set('emptyText', 'None.');
    set('linkLabel', '+ Link');
    set('canEdit', canEdit);
    set('sections', sections);
    set('testid', 'p');
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  const names = (el: HTMLElement, section: string) =>
    [...el.querySelectorAll(`[data-testid="p-${section}"] .name`)].map((node) => node.textContent?.trim());

  /** Fire a drop on one section's list through its real `(cdkDropListDropped)` binding. A pointer drag is an e2e
   * concern; this owns the index arithmetic the event carries. */
  function drop(fixture: ComponentFixture<ProjectLinkPanel>, from: LandingSection, to: LandingSection, key: string, previousIndex: number, currentIndex: number) {
    const lists = fixture.debugElement.queryAll(By.directive(CdkDropList)).map((list) => list.injector.get(CdkDropList));
    const target = lists.find((list) => list.data === to)!;
    const source = lists.find((list) => list.data === from)!;
    target.dropped.emit({ item: { data: key }, previousContainer: source, container: target, previousIndex, currentIndex } as unknown as CdkDragDrop<unknown>);
  }

  it('renders the ranked section first, then "Unranked", in the given order', async () => {
    const el = (await mount(true)).nativeElement as HTMLElement;

    expect(names(el, 'ranked')).toEqual(['alpha', 'bravo']);
    expect(names(el, 'unranked')).toEqual(['xray']);
    expect(el.querySelector('[data-testid="p-unranked-heading"]')?.textContent).toContain('Unranked');
  });

  it('arms dragging only for canEdit; a read-only viewer sees the same two plain sections', async () => {
    const armed = await mount(true);
    expect(armed.debugElement.queryAll(By.directive(CdkDropList))).toHaveLength(2);
    expect(armed.debugElement.queryAll(By.directive(CdkDrag))).toHaveLength(3);
    TestBed.resetTestingModule();

    const plain = await mount(false);
    const el = plain.nativeElement as HTMLElement;
    expect(plain.debugElement.queryAll(By.directive(CdkDropList))).toHaveLength(0);
    expect(plain.debugElement.queryAll(By.directive(CdkDrag))).toHaveLength(0);
    expect(names(el, 'ranked')).toEqual(['alpha', 'bravo']);
    expect(names(el, 'unranked')).toEqual(['xray']);
  });

  it('emits the one position a drop asks for, and nothing for a drop that changes nothing', async () => {
    const fixture = await mount(true);
    const emitted: LandingPositionRequest[] = [];
    fixture.componentInstance.reposition.subscribe((request) => emitted.push(request));

    drop(fixture, 'unranked', 'ranked', 'repo_x', 0, 1);
    drop(fixture, 'ranked', 'unranked', 'repo_a', 0, 0);
    drop(fixture, 'ranked', 'ranked', 'repo_b', 1, 1);

    expect(emitted).toEqual([
      { repository: 'repo_x', after_repository: 'repo_a' },
      { repository: 'repo_a', unranked: true },
    ]);
  });

  it('lists the rows flat without sections', async () => {
    const el = (await mount(true, null)).nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="p-ranked"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="p-rows"] .name')).toHaveLength(3);
  });
});
