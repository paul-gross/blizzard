import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { KitTab, KitTabStrip } from 'fleet';

import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { injectProjectsQuery } from '../core/projects.query';
import { tabCount } from './admin-shell.model';
import { injectMembersQuery } from './members/members.query';
import { injectLensLinks } from './projects/lens-links';
import { injectRepositoriesQuery } from './repositories/repositories.query';
import { injectSecretsQuery } from './secrets/secrets.query';
import { injectWorkSourcesQuery } from './work-sources/work-sources.query';

/**
 * The `/admin` route — its own second strip over the hub's administration surfaces,
 * each a child route (`admin.routes.ts`) so a tab is a deep link: Members (the members
 * of the tenant the board acts in), its projects, then one surface per configured
 * noun — work sources, repositories, secrets — and the change log across all of them. Members
 * stays in the strip at every width. Each tab counts what it holds — under a project lens, Work
 * sources and Repositories count what that project links — once its read answers; Members only
 * for an actor who may read them, and Changes, read a page at a time, not at all.
 */
@Component({
  selector: 'app-admin-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './admin-shell.html',
  styleUrl: './admin-shell.css',
})
export class AdminShell {
  private readonly meQuery = injectMeQuery();
  private readonly membersQuery = injectMembersQuery(() => hasPermission(this.meQuery.data(), 'user:manage'));
  private readonly projectsQuery = injectProjectsQuery();
  private readonly sourcesQuery = injectWorkSourcesQuery(() => false);
  private readonly repositoriesQuery = injectRepositoriesQuery(() => false);
  private readonly secretsQuery = injectSecretsQuery(() => false);
  private readonly sourceLinks = injectLensLinks('source');
  private readonly repositoryLinks = injectLensLinks('repository');

  protected readonly counts = computed(() => ({
    members: tabCount(this.membersQuery.data()),
    projects: tabCount(this.projectsQuery.data()),
    sources: tabCount(this.sourcesQuery.data(), this.sourceLinks.names()),
    repositories: tabCount(this.repositoriesQuery.data(), this.repositoryLinks.names()),
    secrets: tabCount(this.secretsQuery.data()),
  }));
}
