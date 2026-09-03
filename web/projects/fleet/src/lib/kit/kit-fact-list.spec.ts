import { Component, TemplateRef, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { KitFactList, type KitFact } from './kit-fact-list';

const ROWS: readonly KitFact[] = [
  { label: 'pass', value: 'hub garden-proposal pass gprop_1' },
  { label: 'accept', value: 'hub garden-proposal accept gprop_1' },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitFactList],
  template: `<fleet-kit-fact-list [rows]="rows()" testid="facts-a" />`,
})
class TestHost {
  readonly rows = signal<readonly KitFact[]>(ROWS);
}

/** A list mixing a plain-string row with one whose value is markup the host owns. */
@Component({
  selector: 'fleet-test-mixed-host',
  imports: [KitFactList],
  template: `
    <ng-template #rich>
      <em class="host-styled" data-testid="rich-value">marked up</em>
    </ng-template>
    <fleet-kit-fact-list [rows]="mixedRows(rich)" testid="facts-b" />
  `,
})
class MixedHost {
  mixedRows(rich: TemplateRef<unknown>): readonly KitFact[] {
    return [
      { label: 'plain', value: 'just text', testid: 'row-plain' },
      { label: 'rich', template: rich, testid: 'row-rich' },
    ];
  }
}

describe('KitFactList', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost, MixedHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one dt/dd pair per row, in order', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const dl = el.querySelector('[data-testid="facts-a"]');
    const terms = Array.from(dl?.querySelectorAll('dt') ?? []).map((n) => n.textContent);
    const defs = Array.from(dl?.querySelectorAll('dd') ?? []).map((n) => n.textContent);
    expect(terms).toEqual(['pass', 'accept']);
    expect(defs).toEqual(['hub garden-proposal pass gprop_1', 'hub garden-proposal accept gprop_1']);
  });

  it('renders no rows when the list is empty', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.rows.set([]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const dl = el.querySelector('[data-testid="facts-a"]');
    expect(dl?.querySelectorAll('dt').length).toBe(0);
    expect(dl?.querySelectorAll('dd').length).toBe(0);
  });

  it('leaves a row without a testid unmarked', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const first = el.querySelector('[data-testid="facts-a"] dd');
    expect(first?.hasAttribute('data-testid')).toBe(false);
  });

  it('renders a templated row and a string row side by side in one grid', async () => {
    const fixture = TestBed.createComponent(MixedHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const dl = el.querySelector('[data-testid="facts-b"]')!;
    expect(Array.from(dl.querySelectorAll('dt')).map((n) => n.textContent)).toEqual(['plain', 'rich']);
    const defs = dl.querySelectorAll('dd');
    expect(defs.length).toBe(2);
    expect(defs[0].textContent).toBe('just text');
    expect(defs[1].textContent).toContain('marked up');
  });

  it('emits the templated row markup inside the dd the grid itself owns', async () => {
    const fixture = TestBed.createComponent(MixedHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rich = el.querySelector('[data-testid="rich-value"]')!;
    const dd = rich.closest('dd')!;
    expect(dd.getAttribute('data-testid')).toBe('row-rich');
    expect(dd.closest('dl')?.getAttribute('data-testid')).toBe('facts-b');
    expect(rich.tagName.toLowerCase()).toBe('em');
  });

  it('marks each dd with its row testid', async () => {
    const fixture = TestBed.createComponent(MixedHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="row-plain"]')?.tagName.toLowerCase()).toBe('dd');
    expect(el.querySelector('[data-testid="row-rich"]')?.tagName.toLowerCase()).toBe('dd');
  });

  it('drops and reports a row supplying neither a value nor a template, keeping its neighbours', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.rows.set([
      { label: 'orphan' } as unknown as KitFact,
      { label: 'sound', value: 'still here' },
    ]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // Dropped rather than thrown on: a throw inside a `computed` is cached and
    // rethrown on every later read, so one bad row would blank every view above
    // this list instead of the one `<dd>` it describes.
    expect(el.textContent).not.toContain('orphan');
    expect(el.textContent).toContain('still here');
    expect(errors).toHaveBeenCalledWith(expect.stringMatching(/"orphan" must supply exactly one of value or template/));
    errors.mockRestore();
  });

  it('drops and reports a row supplying both a value and a template, keeping its neighbours', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.rows.set([
      { label: 'greedy', value: 'text', template: {} as TemplateRef<unknown> } as unknown as KitFact,
      { label: 'sound', value: 'still here' },
    ]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('greedy');
    expect(el.textContent).toContain('still here');
    expect(errors).toHaveBeenCalledWith(expect.stringMatching(/"greedy" must supply exactly one of value or template/));
    errors.mockRestore();
  });

  it('stays readable after a malformed row, rather than caching a thrown error and rethrowing it', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.rows.set([{ label: 'orphan' } as unknown as KitFact]);
    fixture.detectChanges();

    // The repair lands and renders — a poisoned computed would rethrow here forever.
    fixture.componentInstance.rows.set([{ label: 'repaired', value: 'fixed' }]);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('fixed');
    errors.mockRestore();
  });
});
