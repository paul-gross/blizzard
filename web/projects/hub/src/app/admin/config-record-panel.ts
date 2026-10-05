import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
  type TemplateRef,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitButton,
  type KitFact,
  KitFactList,
} from 'fleet';

import type { ConfigActionsVm } from './config-actions.model';
import type { LastChangeVm, RevisionRowVm } from './config-history.model';
import type { ConfigBadgeVm } from './config-record-list';
import { ConfigRevisions } from './config-revisions';

/** One link from a record to another record. */
export interface ConfigLinkVm {
  /** The linked record's kind, as text. */
  readonly kind: string;
  readonly name: string;
  /** The router commands that open it, or `null` for a kind the board has no surface for. */
  readonly route: readonly string[] | null;
}

/** A titled group of links, with its copy for when the group is empty. */
export interface ConfigLinksVm {
  readonly heading: string;
  readonly links: readonly ConfigLinkVm[];
  readonly emptyText: string;
}

/** A config record's detail, as the panel renders it. */
export interface ConfigRecordVm {
  readonly name: string;
  readonly badges: readonly ConfigBadgeVm[];
  /** The record's fields, as plain text. */
  readonly facts: readonly { readonly label: string; readonly value: string }[];
  /** The record's revision, or `null` for a record with none (the built-in source). */
  readonly revision: number | null;
  /** A standing note under the fields, or `null` for none. */
  readonly note: string | null;
  readonly links: ConfigLinksVm | null;
  /** Whether the record carries revision history at all. */
  readonly hasHistory: boolean;
}

/**
 * A config record's detail — its name and badges, its fields, its revision with who
 * last changed it and through which door, the records it links to, the CLI command
 * that changes it (on a phone), and its Revisions list. Presentational.
 */
@Component({
  selector: 'app-config-record-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigRevisions, FleetWhen, KitAsyncState, KitBadge, KitButton, KitFactList, RouterLink],
  templateUrl: './config-record-panel.html',
  styleUrl: './config-record-panel.css',
})
export class ConfigRecordPanel {
  readonly vm = input.required<ConfigRecordVm | null>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly lastChange = input<LastChangeVm | null>(null);
  readonly revisions = input<readonly RevisionRowVm[]>([]);
  readonly revisionsState = input<KitAsyncStateValue>('loading');
  /** The CLI command shown in place of write controls, or `null` for none. */
  readonly cliCommand = input<string | null>(null);
  /** The rest-state copy while nothing is selected. */
  readonly emptyText = input.required<string>();
  readonly testidPrefix = input.required<string>();
  /** The write controls, or `null` for none — a phone, a viewer, a built-in record. */
  readonly actions = input<ConfigActionsVm | null>(null);
  /** Whether a write on this record is in flight. */
  readonly busy = input(false);
  /** The refusal from the last retire or enable, shown beside the controls. */
  readonly actionError = input<string | null>(null);

  readonly edit = output<void>();
  readonly replace = output<void>();
  readonly lifecycle = output<'retire' | 'enable'>();

  private readonly revisionTemplate = viewChild.required<TemplateRef<unknown>>('revisionFact');

  protected readonly factRows = computed<readonly KitFact[]>(() => {
    const vm = this.vm();
    if (vm === null) return [];
    const facts: KitFact[] = vm.facts.map((fact) => ({
      label: fact.label,
      value: fact.value,
    }));
    if (vm.revision !== null) {
      facts.push({
        label: 'Revision',
        template: this.revisionTemplate(),
        testid: `${this.testidPrefix()}-revision`,
      });
    }
    return facts;
  });
}
