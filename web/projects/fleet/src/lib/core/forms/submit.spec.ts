import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, FormField, required } from '@angular/forms/signals';
import type { SaveOutcome } from './outcome';
import { submitForm, toFormErrors } from './submit';

interface Draft {
  name: string;
  slug: string;
}

@Component({
  selector: 'fleet-submit-host',
  imports: [FormField],
  template: `
    <!-- eslint-disable-next-line no-restricted-syntax -- The contract is tested on native inputs bound by formField; the kit controls are not the unit under test. -->
    <input id="name" [formField]="f.name" />
    <!-- eslint-disable-next-line no-restricted-syntax -- As above. -->
    <input id="slug" [formField]="f.slug" />
  `,
})
class Host {
  readonly model = signal<Draft>({ name: '', slug: '' });
  readonly f = form(this.model, (p) => {
    required(p.name);
    required(p.slug);
  });
}

function setup() {
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  return { fixture, host: fixture.componentInstance, el: fixture.nativeElement as HTMLElement };
}

describe('submitForm', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('never saves an invalid form, touches every field, and focuses the first invalid control', async () => {
    const { fixture, host, el } = setup();
    document.body.append(el);
    const save = vi.fn(async (): Promise<SaveOutcome<Draft>> => ({ ok: true }));
    expect(await submitForm(host.f, save)).toBe(false);
    expect(save).not.toHaveBeenCalled();
    expect(host.f.name().touched()).toBe(true);
    expect(host.f.slug().touched()).toBe(true);
    fixture.detectChanges();
    expect(document.activeElement).toBe(el.querySelector('#name'));
    el.remove();
  });

  it('returns true for an ok outcome', async () => {
    const { host } = setup();
    host.model.set({ name: 'a', slug: 'b' });
    const save = vi.fn(async (): Promise<SaveOutcome<Draft>> => ({ ok: true }));
    expect(await submitForm(host.f, save)).toBe(true);
    expect(save).toHaveBeenCalledWith({ name: 'a', slug: 'b' });
  });

  it('lands a hub field error on its field, focuses it, and clears it when the field is edited', async () => {
    const { fixture, host, el } = setup();
    document.body.append(el);
    host.model.set({ name: 'a', slug: 'b' });
    const ok = await submitForm(host.f, async () => ({ ok: false, fields: { slug: 'taken' }, form: null }));
    expect(ok).toBe(false);
    expect(host.f.slug().errors().map((e) => e.message)).toEqual(['taken']);
    expect(host.f.name().errors()).toEqual([]);
    expect(host.f().errors()).toEqual([]);
    fixture.detectChanges();
    expect(document.activeElement).toBe(el.querySelector('#slug'));
    host.f.slug().value.set('c');
    expect(host.f.slug().errors()).toEqual([]);
    el.remove();
  });

  it('lands a form error on the root and focuses nothing', async () => {
    const { host, el } = setup();
    document.body.append(el);
    host.model.set({ name: 'a', slug: 'b' });
    const ok = await submitForm(host.f, async () => ({ ok: false, fields: {}, form: 'conflict' }));
    expect(ok).toBe(false);
    expect(host.f().errors().map((e) => e.message)).toEqual(['conflict']);
    expect(el.contains(document.activeElement)).toBe(false);
    el.remove();
  });

  it('fails a refused outcome that names nothing (D7)', async () => {
    const { host } = setup();
    host.model.set({ name: 'a', slug: 'b' });
    expect(await submitForm(host.f, async () => ({ ok: false, fields: {}, form: null }))).toBe(false);
    expect(host.f().errors().length).toBe(1);
  });
});

describe('toFormErrors', () => {
  afterEach(() => TestBed.resetTestingModule());
  it('is undefined for an ok outcome', () => {
    const { host } = setup();
    expect(toFormErrors({ ok: true }, host.f)).toBeUndefined();
  });
  it('targets each field and the root', () => {
    const { host } = setup();
    const errors = toFormErrors<Draft>({ ok: false, fields: { name: 'n' }, form: 'f' }, host.f);
    expect(errors).toEqual([
      { kind: 'hub', fieldTree: host.f.name, message: 'n' },
      { kind: 'hub', fieldTree: host.f, message: 'f' },
    ]);
  });
});
