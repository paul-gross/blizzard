/** A gesture on a host card, emitted once the operator has confirmed it: release one of
 * its runners, retire it (forced after the hub refused), revoke its token, enroll it, or
 * dismiss the refusal or enrolled token on screen. */
export type HostVerbEvent =
  | { readonly kind: 'release'; readonly hostId: string; readonly runnerId: string }
  | { readonly kind: 'retire'; readonly hostId: string; readonly force: boolean }
  | { readonly kind: 'revoke-token'; readonly hostId: string }
  | { readonly kind: 'enroll'; readonly hostId: string }
  | { readonly kind: 'dismiss' };

/** What the host cards render of the verbs: whether they are offered at all, which hosts and
 * runners have one in flight, the hub's refusal of an unforced retire, and a token just
 * enrolled — the last two shown only on the card they name. */
export interface HostVerbsVm {
  readonly canRetire: boolean;
  readonly pendingHostIds: readonly string[];
  readonly pendingRunnerIds: readonly string[];
  readonly refusal: { readonly hostId: string; readonly message: string } | null;
  readonly enrolled: { readonly hostId: string; readonly token: string } | null;
}

/** The verbs withheld — what a card renders before the container says otherwise. */
export const NO_HOST_VERBS: HostVerbsVm = {
  canRetire: false,
  pendingHostIds: [],
  pendingRunnerIds: [],
  refusal: null,
  enrolled: null,
};
