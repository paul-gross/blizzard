// exemplar: bzh:frontend-container-presentational, bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh.

import { ChangeDetectionStrategy, Component, computed, input, type OnInit, output, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { submitForm } from 'fleet';

import { injectDomainUrgenciesQuery } from '../core/urgencies.query';
import { defaultUrgencyName, urgencyOptions } from '../core/urgency-options.model';
import { ProposalAcceptDialogView } from './proposal-accept-dialog-view';
import { acceptanceDraftFor } from './proposal-acceptance.domain';
import { proposalAcceptanceRules } from './proposal-acceptance.form';
import { injectProposalAcceptanceService } from './proposal-acceptance.service';

/** A proposal's Accept dialog. It owns the draft and its `form()`, and answers the view's `save` with `submitForm` over
 * the acceptance service. The host mounts it under `@if`, so the draft is seeded from the proposal body once, and
 * nothing afterwards writes the body back over the operator's edits. */
@Component({
  selector: 'app-proposal-accept-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ProposalAcceptDialogView],
  templateUrl: './proposal-accept-dialog.html',
})
export class ProposalAcceptDialog implements OnInit {
  readonly proposalId = input.required<string>();
  readonly proposalTitle = input.required<string>();
  readonly proposalBody = input.required<string>();

  readonly closed = output<void>();

  private readonly urgenciesQuery = injectDomainUrgenciesQuery();
  private readonly service = injectProposalAcceptanceService();

  private readonly draft = signal(acceptanceDraftFor(''));

  protected readonly acceptForm = form(this.draft, proposalAcceptanceRules);

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
    const ok = await submitForm(this.acceptForm, (draft) => this.service.accept(this.proposalId(), draft));
    if (ok) this.closed.emit();
  }
}
