import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitButton, KitErrorLine } from 'fleet/kit';

import { CredentialCheckTable } from './credential-check-table';
import { checkFailure, type CredentialCheckTarget, isCheckFor, shownCheckVm } from './credentials.model';
import { injectCheckCredentialMutation } from './credentials.mutations';

/**
 * The Check action on a record acting with a credential — a container: a button posting the read-only check of its
 * target, disabled while it runs, the call's failure line, and the answer's table. An answer or failure shows only
 * while it is for the current target.
 */
@Component({
  selector: 'app-credential-check',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CredentialCheckTable, KitButton, KitErrorLine],
  templateUrl: './credential-check.html',
  styleUrl: './credential-check.css',
})
export class CredentialCheck {
  /** The credential to check and the uses to check it against. */
  readonly target = input.required<CredentialCheckTarget>();

  protected readonly mutation = injectCheckCredentialMutation();
  private readonly current = computed(() => isCheckFor(this.mutation.variables(), this.target()));
  protected readonly vm = computed(() => shownCheckVm(this.mutation.data(), this.current()));
  protected readonly error = computed(() => checkFailure(this.mutation.isError(), this.mutation.error(), this.current()));

  protected run(): void {
    this.mutation.mutate(this.target());
  }
}
