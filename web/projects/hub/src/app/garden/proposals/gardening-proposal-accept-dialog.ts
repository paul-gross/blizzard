import { ChangeDetectionStrategy, Component, computed, input, type OnInit, output, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { createRefusalState, submitForm } from 'fleet/forms';

import { injectDomainUrgenciesQuery } from '../../core/urgencies.query';
import { defaultUrgencyName, urgencyOptions } from '../../core/urgency-options.model';
import { acceptanceDraftFor, type ProposalAcceptanceDraft } from './proposal-acceptance.domain';
import { proposalAcceptanceRules } from './proposal-acceptance.form';
import { injectProposalAcceptanceService } from './proposal-acceptance.service';
import { GardeningProposalAcceptDialogView } from './gardening-proposal-accept-dialog-view';

/**
 * The garden proposal docket's Accept dialog container —
 * `blizzard hub garden-proposal accept <id> [--reason] [--body-file] [--urgency] [--no-work-item]`'s own UI. It owns
 * the draft and its `form()`, and answers the view's `save` with `submitForm` over the acceptance service. Minting is
 * the default and submits in one click; declining to mint is gated on its own required reason. Closure is terminal,
 * so a 409 (a raced second close) lands on the form line, keeps the draft, and is never retried.
 *
 * The host page mounts this with `@if` around the accepting proposal, so each open is a fresh instance: the draft is
 * seeded from the proposal body once, and nothing afterwards writes the body back over the operator's edits.
 */
@Component({
  selector: 'app-gardening-proposal-accept-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningProposalAcceptDialogView],
  templateUrl: './gardening-proposal-accept-dialog.html',
})
export class GardeningProposalAcceptDialog implements OnInit {
  readonly proposalId = input.required<string>();
  readonly proposalTitle = input.required<string>();
  readonly proposalBody = input.required<string>();

  readonly closed = output<void>();

  private readonly urgenciesQuery = injectDomainUrgenciesQuery();
  private readonly service = injectProposalAcceptanceService();

  private readonly draft = signal(acceptanceDraftFor(''));

  private readonly refusal = createRefusalState<ProposalAcceptanceDraft>();

  protected readonly acceptForm = form(this.draft, (path) => {
    apply(path, proposalAcceptanceRules);
    apply(path, this.refusal.schema);
  });

  /** Every live urgency, led by the tenant default's empty-value row. */
  protected readonly urgencyOptions = computed(() =>
    urgencyOptions(this.urgenciesQuery.data() ?? [], {
      key: 'slug',
      retired: 'exclude',
      inherit: { label: 'Tenant default', name: defaultUrgencyName(this.urgenciesQuery.data() ?? []) },
    }),
  );

  ngOnInit(): void {
    this.draft.set(acceptanceDraftFor(this.proposalBody()));
  }

  protected async onSave(): Promise<void> {
    const ok = await submitForm(this.acceptForm, (draft) => this.service.accept(this.proposalId(), draft), {
      refusalState: this.refusal,
    });
    if (ok) this.closed.emit();
  }
}
