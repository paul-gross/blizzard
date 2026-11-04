import type { APIRequestContext, TestInfo } from '@playwright/test';
import { DEFAULT_PROVIDER, type Landing, requireProvider, usernameFor, walk } from './auth';
import { type HubApi, hubApi } from './client/clients';
import type { ClockStack, SessionHolder } from './clock-stack';
import type { Ledger } from './client/ledger';
import type { Role } from './client/wire/hub/types.gen';
import type { HubCoordinates } from './hubs';
import { sharesTenant, type Level, type Tenant, type TestHub } from './tenancy';

/** One signed-in person in the test's tenant. */
export interface Person {
  email: string;
  /** The `username` the hub's `GET /api/me` answers for them: the email, slugified. */
  username: string;
  /** The role the person was invited as. */
  role: Role;
  /** The `user_id` the hub's `GET /api/me` answers for them. */
  userId: string;
  /** Holds the person's `bz_session`; disposed when the test ends. */
  context: APIRequestContext;
  /** A hub client in the person's session, bound to the test's tenant. */
  api: HubApi;
}

/** Where a sign-in walk landed, with the person's context and client when it set a session. */
export interface SignedIn extends Landing {
  context?: APIRequestContext;
  /** A client in the person's session with no tenant bound: a headerless route settles on their one membership. */
  api?: HubApi;
}

export interface People {
  /** Invites a person into the test's tenant as the tenant's admin does, then walks with the invitation's token, through `provider` when named. */
  create(options?: { role?: Role; provider?: string }): Promise<Person>;
  /**
   * Walks for an identity the test names and returns where it landed: a refused, uninvited sign-in, or the bootstrap on a
   * fresh hub. It binds no tenant, so it never forces the superuser's sign-in.
   */
  signIn(email: string, options?: { invite?: string; provider?: string }): Promise<SignedIn>;
}

export interface PeopleContext {
  /** The stack's clock: on a controlled one, each person's session is signed in again before their next request once its window has passed. */
  clock: ClockStack;
  testHub: TestHub;
  /** Resolved only when a person is created: the superuser must have signed in by then. */
  tenantOf: () => Promise<Tenant>;
  ledger: Ledger;
  testInfo: TestInfo;
  level: Level;
  newContext: () => Promise<APIRequestContext>;
  /** A name unique to this test and run. */
  unique: (prefix: string) => string;
}

/** `user` and `people` need a hub whose configuration turns authentication on; the refusal names the profile that does. */
export function requireAuth(hub: Pick<HubCoordinates, 'auth'>): void {
  if (hub.auth === undefined) {
    throw new Error('`user` and `people` need a hub with authentication on, and this hub runs with it off. Put the test under the `auth` profile (tests/service/hub/auth/) or give its fresh hub an `auth` hubConfig.');
  }
}

/** The people fixture, and the contexts it made, which `disposeAll` closes. */
export function peopleFactory(context: PeopleContext): { people: People; disposeAll: () => Promise<void> } {
  requireAuth(context.testHub.coordinates);
  const made: APIRequestContext[] = [];
  const open = async (): Promise<APIRequestContext> => {
    const created = await context.newContext();
    made.push(created);
    return created;
  };
  const personApi = (person: APIRequestContext, tenantId: string | undefined, beforeRequest?: () => Promise<void>): HubApi =>
    hubApi({
      beforeRequest,
      context: person,
      testInfo: context.testInfo,
      tenantId,
      narrow: sharesTenant(context.level),
      level: context.level,
      clockMode: context.clock.mode,
      ledger: context.ledger,
      logContext: () => context.testHub.logTail(tenantId),
    });

  /** Registers a person's context as a holder, stamped with the hub's reading now; the person signs in again as `email` with no invitation. */
  const hold = async (own: APIRequestContext, email: string, provider: string | undefined): Promise<() => Promise<void>> => {
    const holder: SessionHolder = {
      signedInAt: await context.clock.hubReading(),
      resign: async () => {
        const landing = await walk({ context: own, hub: context.testHub.coordinates, email, provider });
        if (!landing.session) throw new Error(`signing ${email} in again set no session; the walk landed at ${landing.status} ${landing.location ?? ''}`);
        holder.signedInAt = await context.clock.hubReading();
      },
    };
    return context.clock.register(holder).refresh;
  };

  const signIn: People['signIn'] = async (email, options = {}) => {
    requireProvider(context.testHub.coordinates, options.provider ?? DEFAULT_PROVIDER);
    const own = await open();
    const landing = await walk({ context: own, hub: context.testHub.coordinates, email, invite: options.invite, provider: options.provider });
    if (!landing.session) {
      made.splice(made.indexOf(own), 1);
      await own.dispose();
      return landing;
    }
    return { ...landing, context: own, api: personApi(own, undefined, await hold(own, email, options.provider)) };
  };

  const create: People['create'] = async (options = {}) => {
    const role = options.role ?? 'contributor';
    requireProvider(context.testHub.coordinates, options.provider ?? DEFAULT_PROVIDER);
    // The superuser signs in on a fresh hub here, before the first call that needs its session.
    await context.testHub.superuser();
    const tenant = await context.tenantOf();
    const admin = personApi(context.testHub.context, tenant.id, () => context.testHub.refreshSession());
    const email = `${context.unique('')}@blizzard.test`;
    const invited = await admin.post('/api/invitations', { data: { email, role } });
    if (invited.status !== 201) throw new Error(`inviting ${email} as ${role} answered ${invited.status}: ${JSON.stringify(invited.body)}`);
    context.ledger.add('invitation', invited.body.invitation.invitation_id as string);

    const own = await open();
    const landing = await walk({ context: own, hub: context.testHub.coordinates, email, invite: invited.body.token as string, provider: options.provider });
    if (!landing.session) {
      throw new Error(`signing ${email} in with their invitation set no session; the walk landed at ${landing.status} ${landing.location ?? ''}: ${landing.body.slice(0, 300)}`);
    }
    const api = personApi(own, tenant.id, await hold(own, email, options.provider));
    const me = await api.unfiltered.get('/api/me');
    if (me.status !== 200) throw new Error(`GET /api/me for ${email} answered ${me.status}: ${JSON.stringify(me.body)}`);
    context.ledger.add('person', me.body.user_id as string);
    return { email, username: usernameFor(email), role, userId: me.body.user_id as string, context: own, api };
  };

  return {
    people: { create, signIn },
    disposeAll: async () => {
      await Promise.all(made.map((created) => created.dispose()));
    },
  };
}
