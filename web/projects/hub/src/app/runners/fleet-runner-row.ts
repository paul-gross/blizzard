import { ChangeDetectionStrategy, Component, type TemplateRef, computed, input, output, viewChild } from '@angular/core';

import {
  type ChunkStatus,
  compactRef,
  harnessName,
  KitAsyncState,
  KitBadge,
  KitButton,
  type KitFact,
  KitFactList,
  STATUS_TONE,
  type Tone,
} from 'fleet';
import { CapabilityBadgeGroup } from './capability-badge-group';
import { GateBadgeGroup } from './gate-badge-group';
import { runnerLight, runnerStateBadges } from './connection-status.model';
import { busyAgents, declaredAgents } from './host-rows.model';
import { decliningLabels, runnerSeenLabel, runnerToggleHint, type RunnerRow, versionStandingBadge } from './runner-rows.model';
import { RunnerSubscription } from './runner-subscription';
import { ServedProjectGroup } from './served-project-group';
import { StatusLight } from './status-light';

/**
 * One runner as a wide row on the Fleet tab's detail pane: a header line (its light, name,
 * short ref, harness and agents, last seen, and the hub's Pause/Resume brake behind
 * `runner:pause`), then a fact grid — what it binds or serves, what it spends, its
 * harnesses, gates and state — beside the chunks it holds.
 *
 * Presentational only: the container owns the permission, the pending set and the mutation.
 */
@Component({
  selector: 'app-fleet-runner-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CapabilityBadgeGroup,
    GateBadgeGroup,
    KitAsyncState,
    KitBadge,
    KitButton,
    KitFactList,
    RunnerSubscription,
    ServedProjectGroup,
    StatusLight,
  ],
  templateUrl: './fleet-runner-row.html',
  styleUrl: './fleet-runner-row.css',
})
export class FleetRunnerRow {
  readonly row = input.required<RunnerRow>();

  /** Whether the runner sits under a host — switching its served line to bindings mode. */
  readonly hosted = input(false);

  /** Whether to render the hub brake — the identity's `runner:pause`. */
  readonly canPause = input(false);

  /** Whether this runner's pause/resume is in flight. */
  readonly pausePending = input(false);

  /** The row to flip the hub brake on; the container reads `hub_paused` off it. */
  readonly togglePause = output<RunnerRow>();

  protected readonly seenLabel = runnerSeenLabel;
  protected readonly toggleHint = runnerToggleHint;

  private readonly serves = viewChild.required<TemplateRef<unknown>>('serves');
  private readonly spends = viewChild.required<TemplateRef<unknown>>('spends');
  private readonly harness = viewChild.required<TemplateRef<unknown>>('harness');
  private readonly gates = viewChild.required<TemplateRef<unknown>>('gates');
  private readonly state = viewChild.required<TemplateRef<unknown>>('state');
  private readonly version = viewChild.required<TemplateRef<unknown>>('version');
  private readonly declining = viewChild.required<TemplateRef<unknown>>('declining');

  protected readonly light = computed(() => runnerLight(this.row()));

  protected readonly stateBadges = computed(() => runnerStateBadges(this.row()));

  /** Every throttle holding the runner back, in the gate's order; empty when none is, or a brake holds it. */
  protected readonly decliningReasons = computed(() => decliningLabels(this.row()));

  protected readonly ref = computed(() => compactRef(this.row().runner_id));

  /** The hub's judgement of the runner's version, or `null` before it has judged one. */
  protected readonly standing = computed(() => versionStandingBadge(this.row()));

  /** "Claude Code · 4 agents · 2 busy" — its harnesses, and its agents where it binds any. */
  protected readonly summary = computed(() => {
    const row = this.row();
    const parts = (row.capabilities ?? []).map((capability) => harnessName(capability.harness_id));
    const declared = declaredAgents([row]);
    if (declared !== null) parts.push(`${declared} ${declared === 1 ? 'agent' : 'agents'}`, `${busyAgents([row])} busy`);
    return parts.length === 0 ? null : parts.join(' · ');
  });

  /** Whether the Binds/Serves line has nothing to show — no binding or served project, and
   * no UNASSIGNED or UNRESOLVED marker standing in for one. */
  protected readonly bindsNothing = computed(() => {
    const row = this.row();
    const lines = this.hosted() ? row.bindingLines.length : row.served.length;
    return lines === 0 && !row.unassigned && row.unresolved.length === 0;
  });

  protected readonly facts = computed<readonly KitFact[]>(() => [
    { label: this.hosted() ? 'Binds' : 'Serves', template: this.serves() },
    ...(this.hosted() && this.row().subscriptionName !== null
      ? [{ label: 'Spends', template: this.spends(), testid: 'runner-subscription' }]
      : []),
    { label: 'Harness', template: this.harness() },
    { label: 'Gates', template: this.gates() },
    { label: 'Version', template: this.version(), testid: 'fleet-runner-version' },
    { label: 'State', template: this.state() },
    ...(this.decliningReasons().length > 0
      ? [{ label: 'Declining', template: this.declining(), testid: 'fleet-runner-declining-reasons' }]
      : []),
  ]);

  protected toneFor(status: ChunkStatus): Tone {
    return STATUS_TONE(status);
  }
}
