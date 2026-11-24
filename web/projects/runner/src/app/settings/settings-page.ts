import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { KitSectionHead } from 'fleet';

import { injectHostRunnerDomainsQuery } from '../core/host.query';
import { injectRunnerSessionQuery } from '../core/identity/auth.query';
import { PanelFilters } from '../core/panel-filters';
import { settingsRecords } from './settings.model';
import { SettingsHistory } from './settings-history';
import { SettingsRecordPanel } from './settings-record';

/**
 * The `settings` route — the host's settings record and each hosted runner's, each with its `in_force` line and a
 * one-key edit for the superuser, then History, each record's change log. The Runner filter leaves the host and the
 * runner it names. Whether the session edits is the runner's own judgment, read from the session's `superuser`.
 */
@Component({
  selector: 'app-settings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSectionHead, SettingsHistory, SettingsRecordPanel],
  templateUrl: './settings-page.html',
  styleUrl: './settings-page.css',
})
export class SettingsPage {
  private readonly filters = inject(PanelFilters);
  private readonly session = injectRunnerSessionQuery();
  private readonly runners = injectHostRunnerDomainsQuery();

  protected readonly superuser = computed(() => this.session.data()?.superuser ?? false);
  protected readonly records = computed(() => settingsRecords(this.runners.data() ?? [], this.filters.runner()));
}
