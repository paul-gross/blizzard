import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, submit } from '@angular/forms/signals';
import { provideRouter } from '@angular/router';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';
import { SCOPE_DESCRIPTION_RULES } from './scope.form';

const VM: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the blizzard monorepo',
  retired: false,
  renderedRetired: false,
  relatedRoutines: [{ slug: 'nightly', name: 'nightly', isDefault: true }],
  swept: { at: '2026-03-05T00:00:00Z', notSwept: 'not in 28 days' },
};

describe('FleetScopePanel', () => {
  const draft = signal({ description: VM.description });
  let descriptionForm: ReturnType<typeof makeForm>;
  const makeForm = () => form(draft, SCOPE_DESCRIPTION_RULES);

  async function mount(inputs: {
    vm?: ScopePanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    canEdit?: boolean;
    actionError?: string | null;
    editPending?: boolean;
    editing?: boolean;
    lifecyclePending?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetScopePanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    draft.set({ description: VM.description });
    descriptionForm = TestBed.runInInjectionContext(makeForm);
    const fixture = TestBed.createComponent(FleetScopePanel);
    fixture.componentRef.setInput('vm', inputs.vm === undefined ? VM : inputs.vm);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('hasProjectLens', true);
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? false);
    fixture.componentRef.setInput('actionError', inputs.actionError ?? null);
    fixture.componentRef.setInput('editPending', inputs.editPending ?? false);
    fixture.componentRef.setInput('editing', inputs.editing ?? false);
    fixture.componentRef.setInput('descriptionForm', descriptionForm);
    fixture.componentRef.setInput('lifecyclePending', inputs.lifecyclePending ?? false);
    await fixture.whenStable();
    return fixture;
  }

  /** What the container does on `editStart`: open the editor on the form. */
  async function openEditor(fixture: Awaited<ReturnType<typeof mount>>) {
    fixture.componentRef.setInput('editing', true);
    await fixture.whenStable();
  }

  it('renders the slug, its enabled state, and the description as plain text without graph:edit', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-scope-panel"]');
    expect(panel?.textContent).toContain('blizzard');
    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('enabled');
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeNull();
    expect(panel?.textContent).toContain('the blizzard monorepo');
  });

  it('names the project beside the slug, and calls out another project holding the same slug', async () => {
    const fixture = await mount({});
    fixture.componentRef.setInput('project', 'winter');
    fixture.componentRef.setInput('sameSlugIn', ['blizzard']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-project"]')?.textContent).toContain('winter');
    const callout = el.querySelector('[data-testid="gardening-scope-panel-same-slug"]')?.textContent ?? '';
    expect(callout).toContain('blizzard also has a scope called');
    expect(callout).toContain('a different record');
  });

  it('shows no project chip or same-slug callout without them', async () => {
    const el = (await mount({})).nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-project"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-scope-panel-same-slug"]')).toBeNull();
  });

  it('marks a retired scope distinctly', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('retired');
  });

  it('shows Edit and Retire in the header for an identity with graph:edit, the editor opening on Edit', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    const head = el.querySelector('.sp-head')!;
    expect(head.querySelector('[data-testid="gardening-scope-panel-edit"]')).toBeTruthy();
    expect(head.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeNull();
    await openEditor(fixture);
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-enable"]')).toBeNull();
  });

  it('shows Re-enable instead of Retire once retired', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeNull();
  });

  it('emits editStart on Edit, and the editor opens once the container says so', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let started = 0;
    fixture.componentInstance.editStart.subscribe(() => started++);

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-edit"]')?.click();

    expect(started).toBe(1);
    expect(el.querySelector('[data-testid="gardening-scope-panel-description-input"]')).toBeNull();
  });

  it('binds the editor to the description draft, and emits saveDescription on Set', async () => {
    const fixture = await mount({ canEdit: true, editing: true });
    const el = fixture.nativeElement as HTMLElement;
    let saves = 0;
    fixture.componentInstance.saveDescription.subscribe(() => saves++);

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-scope-panel-description-input"]')!;
    expect(input.value).toBe(VM.description);
    input.value = 'updated description';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.click();

    expect(draft().description).toBe('updated description');
    expect(saves).toBe(1);
  });

  it('emits editCancel on Cancel', async () => {
    const fixture = await mount({ canEdit: true, editing: true });
    let cancels = 0;
    fixture.componentInstance.editCancel.subscribe(() => cancels++);

    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-cancel"]')
      ?.click();

    expect(cancels).toBe(1);
  });

  it('shows a blank description as an error at the field once the form is submitted', async () => {
    const fixture = await mount({ canEdit: true, editing: true });
    const el = fixture.nativeElement as HTMLElement;
    draft.set({ description: '   ' });
    await submit(descriptionForm, async () => undefined);
    await fixture.whenStable();

    expect(el.querySelector('.edit-field')?.textContent).toContain('Enter a description');
  });

  it('shows a refusal no field owns on the form line', async () => {
    const fixture = await mount({ canEdit: true, editing: true });
    const el = fixture.nativeElement as HTMLElement;
    await submit(descriptionForm, async () => [{ kind: 'hub', fieldTree: descriptionForm, message: 'Set description failed.' }]);
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-scope-panel-edit-error"]')?.textContent).toContain('Set description failed.');
  });

  it('disables Set while the edit mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ canEdit: true, editPending: true });
    const el = fixture.nativeElement as HTMLElement;
    await openEditor(fixture);

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.disabled).toBe(
      true,
    );

    fixture.componentRef.setInput('editPending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-description-submit"]')?.disabled).toBe(
      false,
    );
  });

  it('disables Retire while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ canEdit: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.disabled).toBe(false);
  });

  it('disables Re-enable while the lifecycle mutation is pending, re-enabling once it settles', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true }, canEdit: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('lifecyclePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.disabled).toBe(false);
  });

  it('emits retire with the slug once the operator confirms', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.retire.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('blizzard');
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('emits enable with the slug once the operator confirms', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.enable.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-panel-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('blizzard');
  });

  it('lists the routines related to this scope, marking the one that defaults here', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-scope-panel-routines"]');
    expect(section?.textContent).toContain('nightly');
    expect(section?.querySelector('[data-testid="gardening-scope-panel-routine-default"]')).toBeTruthy();
  });

  it('says so when no routine is related, and omits the default marker for a non-defaulting one', async () => {
    const fixture = await mount({
      vm: { ...VM, relatedRoutines: [{ slug: 'other', name: 'other', isDefault: false }] },
    });
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-scope-panel-routines"]');
    expect(section?.textContent).toContain('other');
    expect(section?.querySelector('[data-testid="gardening-scope-panel-routine-default"]')).toBeNull();

    fixture.componentRef.setInput('vm', { ...VM, relatedRoutines: [] });
    fixture.componentRef.setInput('relatedRoutinesState', 'empty');
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')?.textContent).toContain(
      'No routine tends this scope.',
    );
  });

  it.each([
    ['loading', 'Loading related routines'],
    ['error', 'Related routines unavailable'],
    ['empty', 'No routine tends this scope'],
  ] as const)('renders the relation %s state without stale rows', async (state, text) => {
    const fixture = await mount({});
    fixture.componentRef.setInput('relatedRoutinesState', state);
    await fixture.whenStable();
    const section = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="gardening-scope-panel-routines"]');
    expect(section?.textContent).toContain(text);
    expect(section?.textContent).not.toContain('nightly');
  });

  it('omits related routines without a project lens', async () => {
    const fixture = await mount({ vm: { ...VM, relatedRoutines: null } });
    fixture.componentRef.setInput('hasProjectLens', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-routines"]')).toBeNull();
  });

  it('renders the action error beside the controls that raise it', async () => {
    const fixture = await mount({ canEdit: true, actionError: 'Retire failed.' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-error"]')?.textContent).toBe('Retire failed.');
  });

  it('lists the description and when the scope was last swept, relatively, or that the window holds none', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-description"]')?.textContent).toContain(
      'the blizzard monorepo',
    );
    expect(el.querySelector('[data-testid="gardening-scope-panel-last-swept"]')?.textContent).toMatch(/ago|yesterday/);

    fixture.componentRef.setInput('vm', { ...VM, swept: { at: null, notSwept: 'not in 28 days' } });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-scope-panel-last-swept"]')?.textContent).toContain('not in 28 days');
  });

  it('links each tending routine to its own detail', async () => {
    const el = (await mount({})).nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-routine-nightly"]')?.getAttribute('href')).toBe(
      '/gardening/routines/nightly',
    );
  });

  it("links a routine by its slug and shows its display name", async () => {
    const el = (
      await mount({ vm: { ...VM, relatedRoutines: [{ slug: 'nightly-v2', name: 'Nightly sweep', isDefault: false }] } })
    ).nativeElement as HTMLElement;

    const link = el.querySelector('[data-testid="gardening-scope-panel-routine-nightly-v2"]');
    expect(link?.getAttribute('href')).toBe('/gardening/routines/nightly-v2');
    expect(link?.textContent?.trim()).toBe('Nightly sweep');
  });

  // --- Pending lifecycle override (`bzh:frontend-pending-override`) ----------------
  //
  // The merge itself (`renderedRetired` vs. the real `retired`) is the container's,
  // not this presentational component's — this panel only renders whatever the
  // container already resolved (`gardening-scope-detail.ts`'s `overrideRetired`/
  // `scopePanelVm` own that merge and its own coverage). What this panel still
  // owns: the badge tracks `renderedRetired` even where it disagrees with the real
  // `retired`, and the control choice stays keyed off the real `retired` regardless
  // (`graph-detail-header.spec.ts`'s own equivalent coverage).

  it('renders the retired badge from renderedRetired, even though the real retired is false', async () => {
    const fixture = await mount({ vm: { ...VM, retired: false, renderedRetired: true } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('retired');
  });

  it('renders the enabled badge from renderedRetired, even though the real retired is true', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: false } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-state"]')?.textContent).toContain('enabled');
  });

  it('keeps the control keyed off the real retired, not renderedRetired — a pending retire still shows Retire, not Enable', async () => {
    const fixture = await mount({
      vm: { ...VM, retired: false, renderedRetired: true },
      canEdit: true,
      lifecyclePending: true,
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-panel-enable"]')).toBeNull();
  });

  it('shows the empty state when nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeTruthy();
  });
});
