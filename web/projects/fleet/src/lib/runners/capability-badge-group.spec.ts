import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { RunnerCapability } from '../api/hub';
import { CapabilityBadgeGroup } from './capability-badge-group';

async function render(capabilities: readonly RunnerCapability[]): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(CapabilityBadgeGroup);
  fixture.componentRef.setInput('capabilities', capabilities);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('CapabilityBadgeGroup', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CapabilityBadgeGroup],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one distinct, aria-labelled badge per reported capability', async () => {
    const el = await render([
      { harness_id: 'claude_code', version: '2.1.281 (Claude Code)', tiers: ['sonnet'], default: true, available: true },
      { harness_id: 'opencode', version: '1.28.32', tiers: [], default: false, available: true },
    ]);

    const badges = el.querySelectorAll('[data-testid="runner-capability-badge"]');
    expect(badges).toHaveLength(2);

    const claude = el.querySelector('[data-harness-id="claude_code"]');
    expect(claude?.textContent?.trim()).toBe('claude code');
    expect(claude?.getAttribute('aria-label')).toBe('claude code, available');

    const opencode = el.querySelector('[data-harness-id="opencode"]');
    expect(opencode?.textContent?.trim()).toBe('opencode');
    expect(opencode?.getAttribute('aria-label')).toBe('opencode, available');
  });

  it('distinguishes an unavailable capability from an available one', async () => {
    const el = await render([
      { harness_id: 'claude', version: '1.0.0', tiers: [], default: true, available: true },
      { harness_id: 'codex', version: '2.0.0', tiers: [], default: false, available: false },
    ]);

    const claude = el.querySelector('[data-harness-id="claude"]');
    const codex = el.querySelector('[data-harness-id="codex"]');
    expect(claude?.getAttribute('data-available')).toBe('true');
    expect(codex?.getAttribute('data-available')).toBe('false');
    expect(claude?.getAttribute('aria-label')).toContain('available');
    expect(codex?.getAttribute('aria-label')).toBe('codex, unavailable');
  });

  it('renders a labelled empty branch for a runner with no reported capabilities', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="runner-capability-badge"]')).toBeNull();
    const empty = el.querySelector('[data-testid="runner-capabilities-empty"]');
    expect(empty).not.toBeNull();
    expect(empty?.textContent?.trim()).toBe('NO CAPABILITIES REPORTED');
  });
});
