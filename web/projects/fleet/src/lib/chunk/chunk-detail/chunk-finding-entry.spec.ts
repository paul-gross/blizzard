import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkFindingEntry, type FindingEntryView } from './chunk-finding-entry';

const ENTRY: FindingEntryView = {
  class: 'wide-seam',
  locus: 'src/a.py::IHarnessAdapter',
  summary: 'Declares 15 methods spanning five unrelated jobs.',
  introduced: '38faf3daf1c0de4b5a6e7f8091a2b3c4d5e6f708',
  ref: 'F1',
};

async function mount(entry: FindingEntryView, summaryTestid?: string) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkFindingEntry],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkFindingEntry);
  fixture.componentRef.setInput('entry', entry);
  if (summaryTestid !== undefined) fixture.componentRef.setInput('summaryTestid', summaryTestid);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('ChunkFindingEntry', () => {
  it('renders the class, locus, ref, and summary', async () => {
    const el = await mount(ENTRY);

    expect(el.textContent).toContain('wide-seam');
    expect(el.textContent).toContain('src/a.py::IHarnessAdapter');
    expect(el.textContent).toContain('F1');
    expect(el.textContent).toContain('Declares 15 methods');
  });

  it('shortens a long introduced sha, keeping the full value in a title', async () => {
    const el = await mount(ENTRY);

    const introduced = el.querySelector('.fe-introduced');
    expect(introduced?.textContent).toContain('38faf3daf1…');
    expect(introduced?.textContent).not.toContain(ENTRY.introduced!);
    expect(introduced?.querySelector(`[title="${ENTRY.introduced}"]`)).toBeTruthy();
  });

  it('leaves an already-short introduced sha unellipsed', async () => {
    const el = await mount({ ...ENTRY, introduced: '38faf3d' });

    expect(el.querySelector('.fe-introduced')?.textContent).toContain('38faf3d');
    expect(el.querySelector('.fe-introduced')?.textContent).not.toContain('…');
  });

  it('omits the ref and introduced lines when the entry carries neither', async () => {
    const el = await mount({ ...ENTRY, ref: null, introduced: null });

    expect(el.querySelector('.fe-ref')).toBeNull();
    expect(el.querySelector('.fe-introduced')).toBeNull();
    // The three required fields still render.
    expect(el.textContent).toContain('wide-seam');
  });

  it("roots the summary prose block at the parent's supplied testid", async () => {
    const el = await mount(ENTRY, 'artifact-survey-candidate-summary-0');

    expect(el.querySelector('[data-testid="artifact-survey-candidate-summary-0"]')?.textContent).toContain(
      'Declares 15 methods',
    );
  });
});
