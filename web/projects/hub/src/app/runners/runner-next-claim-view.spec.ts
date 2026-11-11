import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { type hubApi } from 'fleet';
import { IN_APP_LINK, type InAppLink } from 'fleet/shell';
import { RunnerNextClaimView } from './runner-next-claim-view';

const entry: hubApi.NextClaimEntry = { chunk_id: 'ch_next', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Example', project_slug: 'example', urgency: { urgency_id: 'u', slug: 'urgent' } };
const blocked: hubApi.NextClaimPassedEntry = { ...entry, chunk_id: 'ch_passed', reason: 'blocked', prerequisite: 'ch_prereq', prerequisite_count: 3 };
const data: hubApi.NextClaimResponse = { next: entry, passed: [blocked], passed_total: 22, held_at: [blocked], if_holding: null, unserved: 2 };

describe('RunnerNextClaimView', () => {
  beforeEach(() => TestBed.configureTestingModule({
    imports: [RunnerNextClaimView],
    providers: [provideZonelessChangeDetection(), provideRouter([]), { provide: IN_APP_LINK, useValue: (link: InAppLink) => ['/p/example/board', ...(link as string[]).slice(1)] }],
  }));
  it('shows the hub walk, its truncated details and lens-aware links', async () => {
    const fixture = TestBed.createComponent(RunnerNextClaimView);
    fixture.componentRef.setInput('runnerId', 'rn_1');
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('data', data);
    fixture.componentRef.setInput('statuses', { ch_prereq: 'running' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="runner-next-link"]')?.getAttribute('href')).toBe('/p/example/board/chunk/ch_next');
    expect(el.textContent).toContain('urgent');
    expect(el.textContent).toContain('2 ahead from unserved projects');
    el.querySelector<HTMLButtonElement>('[data-testid="accordion-section-head"]')!.click();
    await fixture.whenStable();
    expect(el.textContent).toContain('running');
    expect(el.textContent).toContain('and 2 more');
    expect(el.textContent).toContain('21 more passed');
    expect(el.textContent).toContain('If holding per project:');
    expect(el.querySelector('a[href="/p/example/board/chunk/ch_prereq"]')).not.toBeNull();
  });
  it('does not claim emptiness while loading or on error', async () => {
    const fixture = TestBed.createComponent(RunnerNextClaimView);
    fixture.componentRef.setInput('runnerId', 'rn_1');
    fixture.componentRef.setInput('state', 'error');
    await fixture.whenStable();
    expect(fixture.nativeElement.textContent).toContain('Next unavailable');
    expect(fixture.nativeElement.textContent).not.toContain('Nothing claimable');
  });
});
