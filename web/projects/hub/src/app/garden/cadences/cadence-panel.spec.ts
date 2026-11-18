import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { provideRouter } from '@angular/router';
import { gardenCadenceErrandView, gardenErrandRunView } from 'fleet/testing';

import { gardenCadence, gardenCadenceAttention, gardenChore } from '../core/cadence.builders';
import { type CadenceDraft, type ChoreDraft, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { toChoreDraft, toDraft } from '../core/cadence.translate';
import { cadenceEditRules } from './cadence.form';
import { type CadencePanelVm, FleetCadencePanel } from './cadence-panel';
import { choreAddRules, choreEditRules } from './chore.form';
import { cadencePanelVm } from './gardening-cadence-detail.model';

const CADENCE = gardenCadence({
  slug: 'evening',
  name: 'Evening sweep',
  chores: [gardenChore(), gardenChore({ id: 'chr_2', key: 'odd', mode: { unlisted: 'sampled' }, scopes: ['web'] })],
  attention: [gardenCadenceAttention()],
});
const ERRANDS = [
  gardenCadenceErrandView({ scope: 'blizzard', last_run: gardenErrandRunView() }),
  gardenCadenceErrandView({ scope: 'gone', state: 'stopped', due_at: null, attention: [{ chore: 'nightly', scope: 'gone', reason: 'scope_unknown' }] }),
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

  it.each(['loading', 'error', 'empty'] as const)('shows errands %s inline without hiding controls', async (state) => {
    const vm = { ...VM, chores: VM.chores.map((chore) => ({ ...chore, errandsState: state })) };
    const { q } = await mount({ vm });
    expect(q(`gardening-cadence-errands-${state}-nightly`)).not.toBeNull();
    expect(q('gardening-cadence-errands-nightly')).toBeNull();
    expect(q('gardening-cadence-panel-edit')).not.toBeNull();
    expect(q('gardening-cadence-chore-edit-nightly')).not.toBeNull();
    expect(q('gardening-cadence-chore-add')).not.toBeNull();
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

  it('asks its container to remove a chore, and renders the confirm exactly while it holds `removing`', async () => {
    const { fixture, click, q } = await mount();
    const events: string[] = [];
    fixture.componentInstance.startRemoveChore.subscribe((key) => events.push(`start:${key}`));
    fixture.componentInstance.confirmRemoveChore.subscribe(() => events.push('confirm'));
    fixture.componentInstance.cancelRemoveChore.subscribe(() => events.push('cancel'));

    await click('gardening-cadence-chore-remove-odd');
    expect(events).toEqual(['start:odd']);
    expect(q('confirm-dialog')).toBeNull();

    fixture.componentRef.setInput('removing', 'odd');
    await fixture.whenStable();
    expect(q('confirm-dialog')).not.toBeNull();
    await click('confirm-dialog-confirm');
    expect(events).toEqual(['start:odd', 'confirm']);
  });

  it('asks its container to start a chore add or edit, and renders a chore form exactly while handed one', async () => {
    const { fixture, click, q } = await mount();
    const events: (string | undefined)[] = [];
    fixture.componentInstance.startAddChore.subscribe(() => events.push('add'));
    fixture.componentInstance.startEditChore.subscribe((key) => events.push(`edit:${key}`));
    fixture.componentInstance.saveChore.subscribe(() => events.push('save'));
    fixture.componentInstance.cancelChore.subscribe(() => events.push('cancel'));

    await click('gardening-cadence-chore-add');
    await click('gardening-cadence-chore-edit-odd');
    expect(events).toEqual(['add', 'edit:odd']);
    expect(q('gardening-chore-add')).toBeNull();
    expect(q('gardening-chore-edit-odd')).toBeNull();

    const addTree = TestBed.runInInjectionContext(() => form(signal<ChoreDraft>(EMPTY_CHORE_DRAFT), choreAddRules));
    fixture.componentRef.setInput('choreForm', { key: null, form: addTree });
    await fixture.whenStable();
    expect(q('gardening-chore-add')).not.toBeNull();
    expect(q('gardening-cadence-chore-add')).toBeNull();
    await click('gardening-chore-add-cancel');
    expect(events.at(-1)).toBe('cancel');

    const editTree = TestBed.runInInjectionContext(() =>
      form(signal<ChoreDraft>(toChoreDraft(CADENCE.chores[1])), choreEditRules),
    );
    fixture.componentRef.setInput('choreForm', { key: 'odd', form: editTree });
    await fixture.whenStable();
    expect(q('gardening-chore-add')).toBeNull();
    expect(q('gardening-chore-edit-odd')).not.toBeNull();
    expect(q('gardening-chore-edit-odd-key')).toBeNull();
    expect(q('gardening-cadence-chore-add')).not.toBeNull();
    expect((q('gardening-cadence-chore-edit-odd') as HTMLButtonElement).disabled).toBe(true);
    expect(q('gardening-chore-edit-odd')!.textContent).toContain('sampled');

    fixture.componentRef.setInput('vm', { ...VM, revision: VM.revision + 1 });
    await fixture.whenStable();
    expect(q('gardening-chore-edit-odd')).not.toBeNull();

    await click('gardening-chore-edit-odd-save');
    expect(events.at(-1)).toBe('save');
  });

  it('asks its container to start an edit, and renders the edit form exactly while handed a field tree', async () => {
    const { fixture, click, q } = await mount();
    let started = 0;
    fixture.componentInstance.startEdit.subscribe(() => started++);

    await click('gardening-cadence-panel-edit');
    expect(started).toBe(1);
    expect(q('gardening-cadence-edit')).toBeNull();

    const tree = TestBed.runInInjectionContext(() => form(signal<CadenceDraft>(toDraft(CADENCE)), cadenceEditRules));
    fixture.componentRef.setInput('editForm', tree);
    await fixture.whenStable();
    expect((q('gardening-cadence-edit-cron') as HTMLInputElement).value).toBe('0 20 * * *');
    expect((q('gardening-cadence-panel-edit') as HTMLButtonElement).disabled).toBe(true);

    fixture.componentRef.setInput('vm', { ...VM, revision: VM.revision + 1 });
    await fixture.whenStable();
    expect(q('gardening-cadence-edit')).not.toBeNull();
  });

  it('disables every remove while an edit is in flight', async () => {
    const { q } = await mount({ editPending: true });
    expect((q('gardening-cadence-chore-remove-nightly') as HTMLButtonElement).disabled).toBe(true);
  });
});
