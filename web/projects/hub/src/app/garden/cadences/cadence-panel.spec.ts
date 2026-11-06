import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ChoreView } from 'fleet';
import {
  gardenCadenceAttentionView,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  gardenErrandRunView,
} from 'fleet/testing';

import type { CadenceDraft } from './cadence-form';
import { type CadencePanelVm, type ChoreEditEvent, FleetCadencePanel } from './cadence-panel';
import type { ChoreDraft } from './chore-form';
import { cadencePanelVm } from './gardening-cadence-detail.model';

const CADENCE = gardenCadenceView({
  slug: 'evening',
  name: 'Evening sweep',
  chores: [gardenChoreView(), gardenChoreView({ chore_id: 'chr_2', key: 'odd', mode: 'sampled' as ChoreView['mode'], scopes: ['web'] })],
  attention: [gardenCadenceAttentionView()],
});
const ERRANDS = [
  gardenCadenceErrandView({ scope: 'blizzard', last_run: gardenErrandRunView() }),
  gardenCadenceErrandView({ scope: 'gone', state: 'stopped', due_at: null, attention: [gardenCadenceAttentionView()] }),
  gardenCadenceErrandView({ chore: 'odd', scope: 'web', state: 'due' }),
];
const VM = cadencePanelVm(CADENCE, null, ERRANDS)!;

describe('FleetCadencePanel', () => {
  async function mount(inputs: { vm?: CadencePanelVm | null; canEdit?: boolean; editPending?: boolean } = {}) {
    await TestBed.configureTestingModule({
      imports: [FleetCadencePanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetCadencePanel);
    fixture.componentRef.setInput('vm', inputs.vm === undefined ? VM : inputs.vm);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? true);
    fixture.componentRef.setInput('editPending', inputs.editPending ?? false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    const click = async (testid: string) => {
      q(testid)!.click();
      await fixture.whenStable();
    };
    const type = async (testid: string, value: string) => {
      const input = q(testid) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await fixture.whenStable();
    };
    return { fixture, el, q, click, type };
  }

  it('shows the rhythm and all five next moments', async () => {
    const { el, q } = await mount();

    expect(q('gardening-cadence-panel-rhythm')?.textContent).toContain('0 20 * * * America/Chicago, at least 10 days apart');
    const moments = el.querySelectorAll('[data-testid="gardening-cadence-panel-moment"]');
    expect(moments).toHaveLength(5);
    expect(moments[0].textContent).toContain(VM.nextMoments[0].text);
  });

  it('says so when no moment is scheduled', async () => {
    const { q } = await mount({ vm: { ...VM, nextMoments: [] } });
    expect(q('gardening-cadence-panel-no-moments')).not.toBeNull();
  });

  it('lists what needs attention', async () => {
    const { q } = await mount();
    expect(q('gardening-cadence-panel-attention')?.textContent?.replace(/\s+/g, ' ')).toContain('nightly over gone : the scope does not exist');
  });

  it("tables each chore's errands: scope, state, last run linked to its run, and when it is due", async () => {
    const { q, el } = await mount();

    const table = q('gardening-cadence-errands-nightly')!;
    expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
    const done = q('gardening-cadence-errand-nightly-blizzard')!;
    expect(done.textContent).toContain('waiting');
    const run = done.querySelector<HTMLAnchorElement>('[data-testid="gardening-cadence-errand-run"]')!;
    expect(run.getAttribute('href')).toBe('/gardening/runs/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(done.querySelector('[data-testid="gardening-cadence-errand-due"]')).not.toBeNull();
    const stopped = q('gardening-cadence-errand-nightly-gone')!;
    expect(stopped.textContent).toContain('stopped');
    expect(stopped.textContent).toContain('the scope does not exist');
    expect(stopped.textContent).toContain('never');
    expect(q('gardening-cadence-errand-odd-web')?.textContent).toContain('due');
    expect(q('gardening-cadence-chore-odd')?.textContent).toContain('sampled');
    expect(el.querySelector('a[href="/gardening/routines/nightly"]')).not.toBeNull();
  });

  it('withholds every control from an identity that may not edit', async () => {
    const { q } = await mount({ canEdit: false });
    expect(q('gardening-cadence-panel-edit')).toBeNull();
    expect(q('gardening-cadence-panel-retire')).toBeNull();
    expect(q('gardening-cadence-chore-add')).toBeNull();
    expect(q('gardening-cadence-chore-remove-nightly')).toBeNull();
  });

  it('confirms a retire before emitting it, and offers Re-enable on a retired cadence', async () => {
    const { fixture, click, q } = await mount();
    let retired = 0;
    fixture.componentInstance.retire.subscribe(() => retired++);

    await click('gardening-cadence-panel-retire');
    expect(retired).toBe(0);
    await click('confirm-dialog-confirm');
    expect(retired).toBe(1);

    fixture.componentRef.setInput('vm', { ...VM, retired: true, renderedRetired: true });
    await fixture.whenStable();
    expect(q('gardening-cadence-panel-enable')).not.toBeNull();
    expect(q('gardening-cadence-panel-state')?.textContent).toContain('retired');
  });

  it('confirms a chore removal before emitting its key', async () => {
    const { fixture, click } = await mount();
    const removed: string[] = [];
    fixture.componentInstance.removeChore.subscribe((key) => removed.push(key));

    await click('gardening-cadence-chore-remove-odd');
    await click('confirm-dialog-confirm');

    expect(removed).toEqual(['odd']);
  });

  it('adds a chore from the form', async () => {
    const { fixture, click, type } = await mount();
    const added: ChoreDraft[] = [];
    fixture.componentInstance.addChore.subscribe((draft) => added.push(draft));

    await click('gardening-cadence-chore-add');
    await type('gardening-chore-add-key', 'weekly');
    await type('gardening-chore-add-routine', 'deep');
    await click('gardening-chore-add-save');

    expect(added).toEqual([{ key: 'weekly', routine: 'deep', scopes: 'all', mode: 'full', promote: true, note: '' }]);
  });

  it('edits a chore in place, keeping its key and its unknown mode', async () => {
    const { fixture, click, type, q } = await mount();
    const edits: ChoreEditEvent[] = [];
    fixture.componentInstance.editChore.subscribe((event) => edits.push(event));

    await click('gardening-cadence-chore-edit-odd');
    expect(q('gardening-chore-edit-odd-key')?.tagName.toLowerCase()).toBe('span');
    await type('gardening-chore-edit-odd-scopes', 'web, hub');
    await click('gardening-chore-edit-odd-save');

    expect(edits).toEqual([
      { key: 'odd', draft: { key: 'odd', routine: 'nightly', scopes: 'web, hub', mode: 'sampled', promote: true, note: '' } },
    ]);
  });

  it('keeps the edit form open beside a refusal, and closes it once a new revision reads back', async () => {
    const { fixture, click, type, q } = await mount();
    const saved: CadenceDraft[] = [];
    fixture.componentInstance.edit.subscribe((draft) => saved.push(draft));

    await click('gardening-cadence-panel-edit');
    await type('gardening-cadence-edit-cron', '0 21 * * *');
    await click('gardening-cadence-edit-save');
    expect(saved[0].cron).toBe('0 21 * * *');

    fixture.componentRef.setInput('actionError', 'the cadence changed since it was read');
    await fixture.whenStable();
    expect(q('gardening-cadence-edit')).not.toBeNull();
    expect(q('gardening-cadence-edit-error')?.textContent).toContain('the cadence changed since it was read');

    fixture.componentRef.setInput('actionError', null);
    fixture.componentRef.setInput('vm', { ...VM, revision: VM.revision + 1 });
    await fixture.whenStable();
    expect(q('gardening-cadence-edit')).toBeNull();
  });

  it('disables every save and remove while an edit is in flight', async () => {
    const { click, q } = await mount({ editPending: true });
    expect((q('gardening-cadence-chore-remove-nightly') as HTMLButtonElement).disabled).toBe(true);
    await click('gardening-cadence-chore-add');
    expect((q('gardening-chore-add-save') as HTMLButtonElement).disabled).toBe(true);
  });
});
