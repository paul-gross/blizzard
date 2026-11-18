import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { ProjectBranchDialog } from './project-branch-dialog';

describe('ProjectBranchDialog', () => {
  afterEach(() => (document.body.innerHTML = ''));

  it('keeps the draft in the supplied tree and emits distinct save and inherit actions', async () => {
    await TestBed.configureTestingModule({ imports: [ProjectBranchDialog], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(ProjectBranchDialog);
    const tree = TestBed.runInInjectionContext(() => form(signal({ branch: 'main' })));
    for (const [key, value] of Object.entries({ heading: 'Branch', branch: { override: 'main', inherits: 'trunk' }, form: tree, branchField: tree.branch, testidPrefix: 'branch' })) fixture.componentRef.setInput(key, value);
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    let saves = 0;
    let inherits = 0;
    fixture.componentInstance.submitted.subscribe(() => saves++);
    fixture.componentInstance.inherit.subscribe(() => inherits++);
    const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="branch-${id}"]`)!;
    expect((q('submit') as HTMLButtonElement).disabled).toBe(true);
    const input = q('input') as HTMLInputElement;
    input.value = 'release';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    expect(tree.branch().value()).toBe('release');
    q('submit').click();
    q('inherit').click();
    expect([saves, inherits]).toEqual([1, 1]);
    expect(tree.branch().value()).toBe('release');
  });
});
