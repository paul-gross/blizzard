import { type Type, provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { type SaveOutcome, submitForm } from 'fleet';

import { ConfigFieldTreeDialog } from './config-field-tree-dialog';
import type { ConfigFieldView } from './config-field-tree.model';

import { SourceDialogHost, TimeoutDialogHost, UrgencyDialogHost } from './config-field-tree-dialog.story-host';

async function mount<T>(host: Type<T>, inputs: Record<string, unknown> = {}) {
  await TestBed.configureTestingModule({ imports: [host], providers: [provideZonelessChangeDetection()] }).compileComponents();
  const fixture: ComponentFixture<T> = TestBed.createComponent(host);
  for (const [name, value] of Object.entries(inputs)) fixture.componentRef.setInput(name, value);
  document.body.appendChild(fixture.nativeElement);
  await fixture.whenStable();
  const q = <E extends HTMLElement>(id: string) => document.querySelector<E>(`[data-testid="${id}"]`);
  const type = async (id: string, text: string) => {
    const el = q(id)!;
    const input =
      el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement ? el : el.querySelector<HTMLInputElement>('input,textarea')!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  };
  const pick = async (id: string, label: string) => {
    q(id)!.click();
    await fixture.whenStable();
    [...document.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === label)!.click();
    await fixture.whenStable();
  };
  const submit = async () => {
    q('kit-form-save')!.click();
    await fixture.whenStable();
    await fixture.whenStable();
  };
  return { fixture, host: fixture.componentInstance, q, type, pick, submit };
}

describe('ConfigFieldTreeDialog', () => {
  afterEach(() => (document.body.innerHTML = ''));

  describe('create draft', () => {
    it('marks required fields from the schema, refuses an empty submit by touching and focusing, then saves and closes nothing itself', async () => {
      const { fixture, host, q, type, pick, submit } = await mount(SourceDialogHost);
      expect(q('src-field-name')).not.toBeNull();
      expect(q('src-hint')?.textContent).toContain('feeds work in');
      expect(document.querySelectorAll('fleet-kit-field .req').length).toBe(2);

      await submit();
      expect(host.saved).toEqual([]);
      expect(document.querySelector('fleet-kit-field')!.textContent).toContain('Required');
      expect(document.activeElement).toBe(q('src-field-name'));

      await type('src-field-name', 'ledger');
      await pick('src-field-kind', 'Forge');
      await submit();
      expect(host.saved).toEqual([{ name: 'ledger', kind: 'forge', project: '', token: '' }]);
      expect(host.closes).toBe(0);
      expect(fixture).toBeTruthy();
    });

    it('shows a conditional field only while its condition holds, and a hidden required field does not block Create', async () => {
      const { host, q, type, pick, submit } = await mount(SourceDialogHost);
      expect(q('src-field-token')).toBeNull();
      await type('src-field-name', 'ledger');
      await pick('src-field-kind', 'Forge');
      await submit();
      expect(host.saved).toHaveLength(1);

      await pick('src-field-kind', 'Tracker');
      expect(q('src-field-token')).not.toBeNull();
      await submit();
      expect(host.saved).toHaveLength(1);
      await type('src-field-token', 's3cret');
      await submit();
      expect(host.saved).toHaveLength(2);
    });

    it('filters a dependent field by its parent and clears it when the parent changes', async () => {
      const { fixture, host, q, type, pick, submit } = await mount(SourceDialogHost);
      await type('src-field-name', 'ledger');
      await pick('src-field-kind', 'Forge');
      q('src-field-project')!.click();
      await fixture.whenStable();
      expect([...document.querySelectorAll('[role="option"]')].map((o) => o.textContent?.trim())).toEqual(['Blizzard', 'Winter']);
      [...document.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Winter')!.click();
      await fixture.whenStable();
      expect(host.f.project().value()).toBe('winter');
      await pick('src-field-kind', 'Tracker');
      expect(host.f.project().value()).toBe('');
      await type('src-field-token', 'x');
      await submit();
      expect(host.saved.at(-1)?.project).toBe('');
    });

    it('does not clear a dependent when the form seeds its parent', async () => {
      const { host } = await mount(SourceDialogHost, { kind: 'forge' });
      host.f.project().value.set('winter');
      expect(host.f.kind().value()).toBe('forge');
      expect(host.f.project().value()).toBe('winter');
    });

    it('puts a field refusal on its field, focused, and a root refusal on the form line', async () => {
      const field: SaveOutcome<unknown> = { ok: false, fields: { name: 'That name is taken.' }, form: null };
      const a = await mount(SourceDialogHost, { outcome: field });
      await a.type('src-field-name', 'ledger');
      await a.pick('src-field-kind', 'Forge');
      await a.submit();
      expect(document.querySelector('fleet-kit-field')!.textContent).toContain('That name is taken.');
      expect(document.activeElement).toBe(a.q('src-field-name'));
      expect(a.q('kit-form-line')).toBeNull();
    });

    it('cancel and Escape emit closed', async () => {
      const { host, q, fixture } = await mount(SourceDialogHost);
      q('kit-form-cancel')!.click();
      expect(host.closes).toBe(1);
      document.querySelector('.scrim')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await fixture.whenStable();
      expect(host.closes).toBe(2);
    });

    it('does not close while the save is in flight', async () => {
      const { host, q, type, pick, fixture } = await mount(SourceDialogHost);
      await type('src-field-name', 'ledger');
      await pick('src-field-kind', 'Forge');
      let release!: () => void;
      const pending = submitForm(host.f, () => new Promise((resolve) => (release = () => resolve({ ok: true }))), { refusalState: host.refusal });
      await fixture.whenStable();
      q('kit-form-cancel')!.click();
      document.querySelector('.scrim')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      expect(host.closes).toBe(0);
      release();
      await pending;
    });
  });

  describe('edit draft', () => {
    it('holds Save with "Nothing yet." until an edit, then names the change with labels', async () => {
      const { host, q, type, submit } = await mount(UrgencyDialogHost);
      const save = () => q<HTMLButtonElement>('kit-form-save')!.disabled;
      expect(save()).toBe(true);
      expect(q('urg-diff-empty')).not.toBeNull();
      expect(q('kit-form-cancel')).not.toBeNull();

      await type('urg-field-label', 'Pressing');
      expect(save()).toBe(false);
      expect(q('urg-diff')!.textContent).toContain('Label');
      expect(q('urg-diff')!.textContent).toContain('Pressing');
      await submit();
      expect(host.saved).toEqual([{ ...host.baseline, label: 'Pressing' }]);
    });

    it('judges a number from the host schema, leaving the text as typed', async () => {
      const { host, type, q, submit } = await mount(UrgencyDialogHost);
      await type('urg-field-rank', '7x');
      expect(host.f.rank().value()).toBe('7x');
      await submit();
      expect(host.saved).toEqual([]);
      expect(q('urg-diff')!.textContent).toContain('7x');
    });

    it('keeps the diff against the baseline for a secret, a checkbox and an uploaded mark; holds Save while the file reads; keeps the value on a bad file', async () => {
      const { fixture, host, q, type } = await mount(UrgencyDialogHost);
      await type('urg-field-note', 'line one');
      q('urg-field-active')!.click();
      await fixture.whenStable();
      expect(host.f.active().value()).toBe(true);
      expect(q('urg-diff')!.textContent).toContain('Active');

      const file = q<HTMLInputElement>('urg-field-mark-image')!;
      const choose = (chosen: File) => Object.defineProperty(file, 'files', { value: [chosen], configurable: true });
      choose(new File(['x'], 'a.txt', { type: 'text/plain' }));
      file.dispatchEvent(new Event('change'));
      await fixture.whenStable();
      expect(document.body.textContent).toContain('Choose a PNG or WebP image.');
      expect(host.f.mark().value()).toBe('');

      choose(new File([new Uint8Array([137, 80])], 'a.png', { type: 'image/png' }));
      const readAsDataURL = FileReader.prototype.readAsDataURL;
      let readComplete!: Promise<void>;
      const spy = vi.spyOn(FileReader.prototype, 'readAsDataURL').mockImplementation(function (this: FileReader, blob) {
        readComplete = new Promise((resolve) => this.addEventListener('loadend', () => resolve(), { once: true }));
        readAsDataURL.call(this, blob);
      });
      file.dispatchEvent(new Event('change'));
      fixture.detectChanges();
      expect(q<HTMLButtonElement>('kit-form-save')!.disabled).toBe(true);
      await readComplete;
      spy.mockRestore();
      expect(host.f.mark().value()).toMatch(/^data:image\/png/);
      await fixture.whenStable();
      expect(q('urg-diff')!.textContent).toContain('Image');
      expect(q<HTMLButtonElement>('kit-form-save')!.disabled).toBe(false);
    });

    it('shows a root refusal on the form line', async () => {
      const { q, type, submit } = await mount(UrgencyDialogHost, {
        outcome: { ok: false, fields: {}, form: 'The hub could not save this right now.' },
      });
      await type('urg-field-label', 'Pressing');
      await submit();
      expect(q('kit-form-line')!.textContent).toContain('could not save');
    });
  });

  describe('a number field with a default choice', () => {
    const chip = () => document.querySelector<HTMLButtonElement>('[data-testid="kit-or-default-chip"]')!;
    const hours = () => document.querySelector<HTMLInputElement>('[data-testid="tmo-field-hours"] input, input[data-testid="tmo-field-hours"]')!;
    const save = () => document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!;

    it('draws the default choice beside the hours, disabling them while it is chosen', async () => {
      const { host, fixture } = await mount(TimeoutDialogHost);
      expect(chip().getAttribute('aria-pressed')).toBe('true');
      expect(hours().disabled).toBe(true);
      chip().click();
      await fixture.whenStable();
      expect(host.f.useDefault().value()).toBe(false);
      expect(hours().disabled).toBe(false);
    });

    it('holds Save while the default stays chosen, and names a change from the default by its label', async () => {
      const { host, fixture, q, type, submit } = await mount(TimeoutDialogHost);
      expect(save().disabled).toBe(true);
      expect(q('tmo-diff-empty')).not.toBeNull();
      chip().click();
      await fixture.whenStable();
      await type('tmo-field-hours', '6');
      expect(q('tmo-diff')!.textContent).toContain('Default');
      expect(q('tmo-diff')!.textContent).toContain('6');
      await submit();
      expect(host.saved).toEqual([{ policy: 'park', useDefault: false, hours: '6' }]);
    });

    it('reads choosing the default over stated hours as a change, the typed text kept', async () => {
      const { host, fixture, q } = await mount(TimeoutDialogHost, { useDefault: false });
      await fixture.whenStable();
      expect(save().disabled).toBe(true);
      chip().click();
      await fixture.whenStable();
      expect(save().disabled).toBe(false);
      expect(q('tmo-diff')!.textContent).toContain('12');
      expect(q('tmo-diff')!.textContent).toContain('Default');
      expect(host.f.hours().value()).toBe('12');
    });

    it('marks stated hours from the host rules', async () => {
      const { host, fixture, type, submit } = await mount(TimeoutDialogHost);
      chip().click();
      await fixture.whenStable();
      await type('tmo-field-hours', '0');
      await submit();
      expect(host.saved).toEqual([]);
      expect(document.querySelector('fleet-kit-dialog')!.textContent).toContain('Enter whole hours');
    });

    it('hides the field and its choice while the condition does not hold', async () => {
      const { host, fixture } = await mount(TimeoutDialogHost);
      host.f.policy().value.set('merge');
      await fixture.whenStable();
      expect(document.querySelector('[data-testid="kit-or-default-chip"]')).toBeNull();
    });
  });

  it('stops holding Save for a mark whose read was in flight when it left the form', async () => {
    interface D {
      kind: string;
      mark: string;
    }
    const fields: readonly ConfigFieldView<D>[] = [
      { key: 'kind', label: 'Kind', kind: 'text' },
      { key: 'mark', label: 'Mark', kind: 'mark', shownWhen: { key: 'kind', value: 'm' } },
    ];
    await TestBed.configureTestingModule({
      imports: [ConfigFieldTreeDialog],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent<ConfigFieldTreeDialog<D>>(ConfigFieldTreeDialog);
    const f = TestBed.runInInjectionContext(() => form(signal<D>({ kind: 'm', mark: '' })));
    fixture.componentRef.setInput('heading', 'H');
    fixture.componentRef.setInput('mode', 'create');
    fixture.componentRef.setInput('form', f);
    fixture.componentRef.setInput('fields', fields);
    fixture.componentRef.setInput('submitLabel', 'Save');
    fixture.componentRef.setInput('testidPrefix', 'd');
    await fixture.whenStable();
    const save = () => document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!;
    const reading = FileReader.prototype.readAsDataURL;
    const spy = vi.spyOn(FileReader.prototype, 'readAsDataURL').mockImplementation(() => undefined);
    const file = document.querySelector<HTMLInputElement>('[data-testid="d-field-mark-image"]')!;
    Object.defineProperty(file, 'files', { value: [new File(['x'], 'a.png', { type: 'image/png' })], configurable: true });
    file.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    expect(save().disabled).toBe(true);
    f.kind().value.set('other');
    await fixture.whenStable();
    expect(document.querySelector('[data-testid="d-field-mark-image"]')).toBeNull();
    expect(save().disabled).toBe(false);
    spy.mockRestore();
    expect(FileReader.prototype.readAsDataURL).toBe(reading);
  });
});
