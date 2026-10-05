import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ConfigFieldDiff } from './config-field-diff';

describe('ConfigFieldDiff', () => {
  it('renders one old → new row per field', async () => {
    await TestBed.configureTestingModule({
      imports: [ConfigFieldDiff],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ConfigFieldDiff);
    fixture.componentRef.setInput('diff', [
      { field: 'base_branch', old: 'master', new: 'main' },
      { field: 'annotate', old: false, new: true },
      { field: 'web_base', old: null, new: 'https://github.com' },
    ]);
    fixture.componentRef.setInput('testid', 'diff');
    await fixture.whenStable();
    const rows = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="diff"] li'));
    expect(rows.map((row) => Array.from(row.querySelectorAll('span')).map((span) => span.textContent?.trim()).join(' '))).toEqual([
      'base_branch master → main',
      'annotate false → true',
      'web_base — → https://github.com',
    ]);
  });
});
