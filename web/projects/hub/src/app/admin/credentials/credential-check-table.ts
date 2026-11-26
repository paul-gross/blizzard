import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitBadge, KitSectionHead } from 'fleet/kit';

import type { CredentialCheckVm } from './credentials.model';

/**
 * A credential check's answer — presentational: the binding and variant, the mint when the credential mints its
 * token, a warning when the declared variant contradicts the token, one table per repository or work source checked
 * (a use the check did not wholly answer marked unreached), and what the check does not evaluate.
 */
@Component({
  selector: 'app-credential-check-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitSectionHead, NgTemplateOutlet],
  templateUrl: './credential-check-table.html',
  styleUrl: './credential-check-table.css',
})
export class CredentialCheckTable {
  readonly vm = input.required<CredentialCheckVm>();
}
