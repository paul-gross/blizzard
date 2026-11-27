import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { Urgency } from 'fleet/core';
import { APP_SCOPE, type InAppLink } from 'fleet/shell';
import { RunnerNextClaimView } from './runner-next-claim-view';
import type { NextClaim, NextClaimCandidate, NextClaimPassed, NextClaimScenario } from './runner-next-claim.domain';

const entry: NextClaimCandidate = { chunk_id: 'ch_next', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Example', project_slug: 'example', urgency: { id: 'u', slug: 'urgent' } };
const blocked: NextClaimPassed = { ...entry, chunk_id: 'ch_passed', reason: 'blocked', prerequisite: 'ch_prereq', prerequisite_count: 3 };
const data: NextClaim = { next: entry, passed: [blocked], passed_total: 22, held_at: [blocked], if_holding: null, unserved: 2, without_repository_targets: null };

const olderHub = (body: NextClaim) => Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'without_repository_targets'));

describe('RunnerNextClaimView', () => {
  beforeEach(() => TestBed.configureTestingModule({
    imports: [RunnerNextClaimView],
    providers: [provideZonelessChangeDetection(), provideRouter([]), { provide: APP_SCOPE, useValue: { path: (link: InAppLink) => ['/p/example/board', ...(link as string[]).slice(1)], query: () => ({}) } }],
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
  it('shows each passed, held and alternative urgency, including unresolved slugs', async () => {
    const fixture = TestBed.createComponent(RunnerNextClaimView);
    const urgent = { id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', default: false } as Urgency;
    const standard = { ...urgent, id: 's', name: 'Standard', default: true } as Urgency;
    fixture.componentRef.setInput('runnerId', 'rn_1');
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('urgencies', [urgent, standard]);
    fixture.componentRef.setInput('data', { ...data, if_holding: { ...entry, urgency: { id: 's', slug: 'standard' } }, held_at: [{ ...blocked, urgency: { id: 'missing', slug: 'unknown' } }] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="accordion-section-head"]')!.click();
    await fixture.whenStable();
    expect(el.querySelectorAll('fleet-kit-urgency-pill').length).toBe(2);
    expect(el.textContent).toContain('Standard');
    expect(el.textContent).toContain('unknown');
  });
  it('labels both scenarios, shows the excluded count and resolves the alternative through the same markup', async () => {
    const fixture = TestBed.createComponent(RunnerNextClaimView);
    const alternative: NextClaimScenario = { next: { ...entry, chunk_id: 'ch_alt', position: 9 }, passed: [], passed_total: 0, unserved: 0, if_holding: null, held_at: [blocked], excluded: 2 };
    fixture.componentRef.setInput('runnerId', 'rn_1');
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('data', { ...data, without_repository_targets: alternative });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="runner-next-scenario-target"]')?.textContent).toContain('Preparing at repository targets');
    expect(el.querySelector('[data-testid="runner-next-scenario-untargeted"]')?.textContent).toContain('Not preparing at repository targets');
    expect(el.querySelector('[data-testid="runner-next-alt-link"]')?.getAttribute('href')).toBe('/p/example/board/chunk/ch_alt');
    expect(el.querySelector('[data-testid="runner-next-excluded"]')?.textContent).toContain('2 ahead');
    expect(el.querySelector('[data-testid="runner-next-link"]')?.getAttribute('href')).toBe('/p/example/board/chunk/ch_next');
  });
  it('says the alternative is not reported, keeping the primary account, when the hub omits it', async () => {
    for (const absent of [undefined, null]) {
      const fixture = TestBed.createComponent(RunnerNextClaimView);
      fixture.componentRef.setInput('runnerId', 'rn_1');
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('data', absent === undefined ? olderHub(data) : { ...data, without_repository_targets: absent });
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="runner-next-alt-unreported"]')?.textContent).toContain('not reported by this hub');
      expect(el.querySelector('[data-testid="runner-next-alt-none"]')).toBeNull();
      expect(el.querySelector('[data-testid="runner-next-link"]')).not.toBeNull();
    }
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
