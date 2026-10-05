import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ChangeEntryPanel } from './change-entry-panel';
import type { ChangeEntryVm } from './changes.model';

const ENTRY: ChangeEntryVm = {
  title: 'apply · 2 changes',
  actor: 'pgross',
  door: 'cli',
  at: '2026-01-02T00:00:00Z',
  rows: [
    {
      id: 8,
      title: 'repository winter · edit',
      route: ['/admin', 'repositories', 'winter'],
      diff: [{ field: 'base_branch', old: 'master', new: 'main' }],
      revisionStep: null,
    },
    { id: 7, title: 'secret gh · replace', route: ['/admin', 'secrets', 'gh'], diff: [], revisionStep: 'r1 → r2' },
  ],
};

describe('ChangeEntryPanel', () => {
  it('renders who and the door, each change with its diff, and a value replace as its revision step', async () => {
    await TestBed.configureTestingModule({
      imports: [ChangeEntryPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChangeEntryPanel);
    fixture.componentRef.setInput('vm', ENTRY);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('emptyText', 'Pick a change.');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="admin-change-who"]')?.textContent).toContain('pgross via cli');
    const edit = el.querySelector('[data-testid="admin-change-row-8"]')!;
    expect(edit.querySelector('a')?.getAttribute('href')).toBe('/admin/repositories/winter');
    expect(Array.from(edit.querySelectorAll('.diff-row span')).map((span) => span.textContent?.trim())).toEqual([
      'base_branch',
      'master',
      '→',
      'main',
    ]);
    expect(el.querySelector('[data-testid="admin-change-row-7"]')?.textContent).toContain('value r1 → r2');
  });
});
