import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { asyncState } from 'fleet';
import { injectUrgenciesQuery } from '../core/urgencies.query';
import { claimUrgencyIcons, prerequisiteIds } from './runner-next-claim.model';
import { injectClaimUrgencyIconUrls, injectPrerequisiteStatuses, injectRunnerNextClaimQuery } from './runner-next-claim.query';
import { RunnerNextClaimView } from './runner-next-claim-view';
@Component({
  selector: 'app-runner-next-claim', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerNextClaimView],
  templateUrl: './runner-next-claim.html',
})
export class RunnerNextClaim {
  readonly runnerId = input.required<string>();
  protected readonly next = injectRunnerNextClaimQuery(this.runnerId);
  protected readonly urgencies = injectUrgenciesQuery();
  protected readonly urgencyIconUrl = injectClaimUrgencyIconUrls(() => claimUrgencyIcons(this.next.data(), this.urgencies.data()));
  private readonly prerequisites = injectPrerequisiteStatuses(() => prerequisiteIds(this.next.data()));
  protected readonly statuses = this.prerequisites;
  protected readonly state = computed(() => asyncState(this.next, false));
}
