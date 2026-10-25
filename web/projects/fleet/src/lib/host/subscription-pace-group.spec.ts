import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { SubscriptionPace } from './subscription-pace.model';
import { SubscriptionPaceGroup } from './subscription-pace-group';

/** A fully-shaped {@link SubscriptionPace}, defaulting to "never sampled, no miss" —
 * each spec overrides only the fields its case cares about. */
function pace(over: Partial<SubscriptionPace> & Pick<SubscriptionPace, 'slug' | 'name'>): SubscriptionPace {
  return {
    paceBars: [],
    condition: null,
    sampledAt: null,
    refreshedLabel: null,
    freshness: null,
    missReason: null,
    ...over,
  };
}

async function render(subscriptionPaces: readonly SubscriptionPace[]): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(SubscriptionPaceGroup);
  fixture.componentRef.setInput('subscriptionPaces', subscriptionPaces);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('SubscriptionPaceGroup', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SubscriptionPaceGroup],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('keeps two subscriptions sharing an identical window label distinct, each under its own slug', async () => {
    const el = await render([
      pace({
        slug: 'anthropic-default',
        name: 'Anthropic (default)',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
        sampledAt: '2026-07-16T11:55:00.000Z',
      }),
      pace({
        slug: 'anthropic-secondary',
        name: 'Anthropic (secondary)',
        paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 55 }],
        sampledAt: '2026-07-16T11:55:00.000Z',
      }),
    ]);

    const groups = el.querySelectorAll('[data-testid="subscription-pace-group"]');
    expect(groups).toHaveLength(2);

    const defaultGroup = el.querySelector('[data-subscription-slug="anthropic-default"]');
    const secondaryGroup = el.querySelector('[data-subscription-slug="anthropic-secondary"]');
    expect(defaultGroup?.querySelector('[data-testid="subscription-pace-group-name"]')?.textContent).toContain(
      'Anthropic (default)',
    );
    expect(secondaryGroup?.querySelector('[data-testid="subscription-pace-group-name"]')?.textContent).toContain(
      'Anthropic (secondary)',
    );

    // Both groups report a "5h" window — each stays scoped to its own group rather
    // than merging into one shared bar list.
    expect(defaultGroup?.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);
    expect(secondaryGroup?.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);
    const filled = (group: Element | null) => group?.querySelectorAll('[data-pace-window="5h"] .cell.on').length;
    expect(filled(defaultGroup)).toBe(16);
    expect(filled(secondaryGroup)).toBe(36);
  });

  it('reports no usage windows for a subscription with a sampled empty window list', async () => {
    const el = await render([
      pace({ slug: 'anthropic-default', name: 'Anthropic (default)', sampledAt: '2026-07-16T11:55:00.000Z' }),
    ]);

    const group = el.querySelector('[data-subscription-slug="anthropic-default"]');
    expect(group?.querySelector('[data-testid="runner-pace-bar"]')).toBeNull();
    const unsampled = group?.querySelector('[data-testid="subscription-pace-group-unsampled"]');
    expect(unsampled).not.toBeNull();
    expect(unsampled?.textContent?.trim()).toBe('NO USAGE WINDOWS REPORTED');
    expect(unsampled?.getAttribute('aria-label')).toBe('Anthropic (default) sample reported no usage windows');
  });

  it('renders a lapsed credential in place of the empty-windows message', async () => {
    const el = await render([pace({ slug: 'openai', name: 'OpenAI', condition: 'credential_lapsed' })]);

    const group = el.querySelector('[data-subscription-slug="openai"]');
    expect(group?.querySelector('[data-testid="runner-pace-bar"]')).toBeNull();
    expect(group?.querySelector('[data-testid="subscription-pace-group-unsampled"]')).toBeNull();
    const lapsed = group?.querySelector('[data-testid="subscription-pace-group-lapsed"]');
    expect(lapsed).not.toBeNull();
    expect(lapsed?.textContent?.trim()).toBe('credential lapsed — log in again on this runner');
  });

  it('renders the lapsed notice in place of the bars even over a surviving last-good sample', async () => {
    const el = await render([
      pace({
        slug: 'anthropic-default',
        name: 'Anthropic (default)',
        condition: 'credential_lapsed',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
        sampledAt: '2026-07-16T11:20:00.000Z',
        refreshedLabel: 'refreshed 40m ago',
        refreshedTag: '[40m]',
        freshness: 'aging',
      }),
    ]);

    const group = el.querySelector('[data-subscription-slug="anthropic-default"]');
    expect(group?.querySelector('[data-testid="runner-pace-bar"]')).toBeNull();
    expect(group?.querySelector('[data-testid="subscription-pace-group-lapsed"]')?.textContent?.trim()).toBe(
      'credential lapsed — log in again on this runner',
    );
    // The refreshed tag and its tier still render — they describe the last good
    // sample, not the lapsed notice itself.
    const refreshed = group?.querySelector('[data-testid="subscription-pace-group-refreshed"]');
    expect(refreshed?.textContent?.trim()).toBe('[40m]');
    expect(refreshed?.getAttribute('title')).toBe('refreshed 40m ago');
    expect(refreshed?.getAttribute('data-freshness')).toBe('aging');
  });

  it('reads "no sample yet" for a declared, never-sampled slug with no miss', async () => {
    const el = await render([pace({ slug: 'probe', name: 'Probe' })]);

    const group = el.querySelector('[data-subscription-slug="probe"]');
    expect(group?.querySelector('[data-testid="runner-pace-bar"]')).toBeNull();
    expect(group?.querySelector('[data-testid="subscription-pace-group-refreshed"]')).toBeNull();
    const noSample = group?.querySelector('[data-testid="subscription-pace-group-no-sample"]');
    expect(noSample?.textContent?.trim()).toBe('NO SAMPLE YET');
  });

  it('names the newest miss reason verbatim on a "no sample yet" row', async () => {
    const el = await render([pace({ slug: 'probe', name: 'Probe', missReason: 'endpoint_unreachable' })]);

    const noSample = el.querySelector('[data-testid="subscription-pace-group-no-sample"]');
    expect(noSample?.textContent?.trim()).toBe('NO SAMPLE YET — endpoint_unreachable');
  });

  it.each([
    ['fresh', 'refreshed 5m ago', '[5m]'],
    ['aging', 'refreshed 30m ago', '[30m]'],
    ['stale', 'refreshed 2h ago', '[2h]'],
  ] as const)('renders the %s tier tag beside the name, carrying its own data-freshness attribute', async (tier, label, tag) => {
    const el = await render([
      pace({
        slug: 'anthropic-default',
        name: 'Anthropic (default)',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
        sampledAt: '2026-07-16T11:55:00.000Z',
        refreshedLabel: label,
        refreshedTag: tag,
        freshness: tier,
      }),
    ]);

    const refreshed = el.querySelector('.sub-lead [data-testid="subscription-pace-group-refreshed"]');
    expect(refreshed?.textContent?.trim()).toBe(tag);
    expect(refreshed?.getAttribute('title')).toBe(label);
    expect(refreshed?.getAttribute('data-freshness')).toBe(tier);
  });

  it('renders nothing at all for a runner with no declared subscriptions', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="subscription-pace-group"]')).toBeNull();
  });

  it('renders a sample line with its provider in place of the pace branch', async () => {
    const el = await render([
      pace({
        slug: 'codex',
        name: 'Codex',
        provider: 'openai',
        sample: { conditionLabel: 'miss: endpoint unreachable', sampledAgo: '2m ago', ok: false },
      }),
    ]);

    const group = el.querySelector('[data-subscription-slug="codex"]');
    expect(group?.querySelector('[data-testid="subscription-pace-group-provider"]')?.textContent).toBe('openai');
    const condition = group?.querySelector('[data-testid="subscription-pace-group-condition"]');
    expect(condition?.textContent?.trim()).toBe('miss: endpoint unreachable');
    expect(condition?.classList.contains('miss')).toBe(true);
    expect(group?.querySelector('[data-testid="subscription-pace-group-sampled"]')?.textContent).toBe('sampled 2m ago');
    expect(group?.querySelector('[data-testid="subscription-pace-group-no-sample"]')).toBeNull();
  });

  it('does not tone an ok sample line as a miss', async () => {
    const el = await render([pace({ slug: 'codex', name: 'Codex', sample: { conditionLabel: 'ok', sampledAgo: '1s ago', ok: true } })]);

    const condition = el.querySelector('[data-testid="subscription-pace-group-condition"]');
    expect(condition?.classList.contains('miss')).toBe(false);
  });

  it('names the renewal only when the row carries it, and never the spenders', async () => {
    const el = await render([
      pace({ slug: 'anthropic', name: 'Anthropic', spenders: ['rA', 'rB'], renewalLabel: 'renewed 1m ago' }),
      pace({ slug: 'openai', name: 'OpenAI', spenders: [] }),
    ]);

    const spent = el.querySelector('[data-subscription-slug="anthropic"]');
    expect(spent?.textContent).not.toContain('rA');
    expect(spent?.querySelector('[data-testid="subscription-pace-group-renewal"]')?.textContent).toBe(
      'renewal: renewed 1m ago',
    );
    const unspent = el.querySelector('[data-subscription-slug="openai"]');
    expect(unspent?.querySelector('[data-testid="subscription-pace-group-renewal"]')).toBeNull();
    expect(unspent?.querySelector('[data-testid="subscription-pace-group-provider"]')).toBeNull();
  });
});
