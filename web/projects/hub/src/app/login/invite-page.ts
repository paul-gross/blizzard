import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { asyncState, KitAsyncState, type KitAsyncStateValue } from 'fleet';
import { LoginButtons } from '../core/auth/login-buttons';
import { injectAuthProvidersQuery } from '../core/auth/providers.query';

/**
 * `/invite/<token>` — an invitation's landing. Each provider button starts the hub's sign-in carrying the token, which
 * the hub holds through the provider round trip. The hub then admits the account only when it carries the invited
 * email address: an accepted invitation lands in the invited tenant, a refused one on the sign-in page with the
 * reason.
 *
 * Rendered outside the shell whatever the session, so a person already signed in opens it too and goes through the
 * same check, which admits their account into one more tenant.
 */
@Component({
  selector: 'app-invite-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LoginButtons, KitAsyncState],
  templateUrl: './invite-page.html',
  styleUrl: './login-page.css',
})
export class InvitePage {
  private readonly providersQuery = injectAuthProvidersQuery();

  /** The invitation's token, from the link. */
  protected readonly token = inject(ActivatedRoute).snapshot.paramMap.get('token') ?? '';

  protected readonly providers = computed(() => this.providersQuery.data() ?? []);

  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.providersQuery, this.providers().length === 0),
  );
}
