# Human authentication

Human authentication puts an operator's login to the hub's own web/API surface behind SSO — distinct from runner
authentication ([runner-auth.md](./runner-auth.md)), the machine identity plane.

## Enabling SSO on the hub

The hub's `[auth]` table in `blizzard-hub.toml`, scaffolded by `hub init`, is the rollout knob: `mode = "none"` (the
shipped default) resolves every request to the implicit operator/superuser identity with no store read, so a fresh or
upgraded hub works unauthenticated until an operator opts in. `mode = "oauth"` activates the session/permission seam and
requires at least one `[[auth.oauth.provider]]` entry; a provider's `type` selects `"github"` (an OAuth App) or `"oidc"`
(a generic issuer discovered via `<issuer>/.well-known/openid-configuration`), and the scaffold's commented provider
block is the field-by-field reference.

A provider's `client_secret_env` names an environment variable, never the secret itself; the secret is a deployment
credential in the hub's runtime env file, like `BZ_FORGE_TOKEN`.

`blizzard hub rotate-signing-key` mints a fresh current IdP signing key, demoting the old to previous; runners re-fetch
JWKS on an unknown `kid`, no restart needed; under oauth the verb is gated on `user:manage` and needs a logged-in
session.

## CLI sessions

`blizzard hub login` opens a browser to the hub's own authorize endpoint (PKCE, an ephemeral 127.0.0.1 loopback
redirect) so login completes at the hub, storing the session token locally; `--paste` swaps in a paste-code fallback for
a headless or remote shell with no reachable loopback listener. `blizzard hub logout` deletes the local session and
revokes it at the hub, so a leaked token stops resolving.

## Roles

Roles are a total order: pending < guest < contributor < admin < superuser.

A freshly-logged-in identity lands as pending, holding nothing beyond the public self routes (`GET /api/me`, login,
logout) — no board read, no writes. guest reads the fleet's state and mutates nothing; reading a chunk's stored
transcript segments takes `transcript:read`, held at contributor and above, since a transcript carries everything a
worker saw.

An admin — promoted via `POST /api/users/{id}/role`, gated on `user:manage` — can move a subject freely among pending,
guest, and contributor, but only a superuser actor may grant or revoke admin itself.

`[auth].superuser` names one email as the bootstrap identity, ensured at every hub boot: a verified login matching it is
promoted to superuser; until claimed, the intent sits pre-provisioned, surfaced in the boot log and an `auth_facts`
entry on every restart. Changing `superuser` to a different email demotes the previous claimant to admin; at most one
bootstrapped superuser exists, this is the only way into or out of the role, and it is never assignable through the
admin API.

### Transcript surfaces

The chunk board page's Transcripts tab is gated on `transcript:read`: without it the tab option is hidden, and a held
deep link renders a permission notice rather than a generic error. `transcript:read` also gates the Node history tab's
per-step Transcript accordion; that tab option is never hidden (a step's artifacts still show) — the accordion renders
an in-place no-permission notice off the same backend 403.

The runner's machine panel serves the same chunk route; its Node history wires no per-step transcript because the hub's
transcript routes structurally reject a runner-authenticated bearer, and its own Transcripts tab reads the runner's own
shipped segments via its bearer token instead ([runner-auth.md](./runner-auth.md),
[runner-doors.md](./runner-doors.md)). A runner reading back its own shipped segments sits outside the role ladder
entirely, gated on its runner bearer token ([runner-auth.md](./runner-auth.md)).

## Federating a runner's web surface

A runner wanting its human web surface reachable via the hub's SSO bounce declares `public_url` in
`blizzard-runner.toml` — the browser-reachable base URL(s) it answers on, deriving the redirect URIs
(`<public_url>/api/auth/callback` each) it presents to the hub. `public_url` empty (the scaffold default) registers no
federation identity, leaving the runner's human surface unreachable via SSO — also the correct state when the hub runs
mode `"none"`, since there is no IdP to bounce to.

`public_url` takes one URL or a list; more than one matters because the browser, not the runner, POSTs the federation
token to the redirect URI, which therefore resolves in the browsing device's own network namespace. Every declared
origin registers with the hub, which exact-matches presented redirect URIs against the set; the runner selects among
declared origins per request by the arriving Host, an unmatched Host falling back to the first declared origin — the
canonical one the hub records as the runner's URL. Selection is membership in the declared set, never construction from
the request: a forged or unrecognized Host resolves only to a declared origin, logged as a warning and falling back,
never reflected into a redirect URI. A runner whose Host is rewritten fails like an undeclared origin: the bounce falls
back to the canonical origin, the hub accepts it, and the browser is sent wherever that origin names — its own machine,
for a loopback canonical; the runner logs a warning naming the arriving Host and the declared set when the fallback
fires, the signal to check the proxy.

Each `public_url` entry must equal the origin the browser shows exactly — scheme, host, and port — because selection
compares it against the request's Host; a proxy terminating TLS on 443 makes the visible origin
`https://runner.example`, which a declared `https://runner.example:8431` never matches, and the mismatch is silent
because the fallback lands on a registered origin and the hub raises nothing. `localhost` and `127.0.0.1` are distinct
origins to browser and guard alike, each needing its own entry; two entries a browser cannot distinguish — differing
only in scheme or in an explicit-versus-default port — are refused at config load. A non-URL `public_url` value, an
entry carrying a path, userinfo, or a non-numeric port, and two entries naming one browser origin all fail at config
load rather than surfacing later as an opaque unregistered-redirect_uri refusal.

Registration happens on the runner's reconciliation tick: a widened set reaches the hub on the first tick after a
restart, and a login attempted before that tick is refused as an unregistered redirect URI.

Only two origin classes complete a bounce: loopback at either scheme, and non-loopback only as https — on non-loopback
plain http the bounce cookies cannot be Secure, browsers refuse `SameSite=None` without Secure, and the cross-site
callback arrives cookie-less, failing every time. A loopback origin is exempt: browsers treat loopback as potentially
trustworthy at any scheme, which is why a 127.0.0.1 runner federates against a hosted hub with no proxy configuration.

For a runner whose `public_url` declares a proxied https origin, `trusted_proxies` is a hard requirement, not a
refinement: the federation token returns by cross-site `form_post` and needs `SameSite=None` bounce cookies, which
browsers honor only alongside Secure — a scheme read as http drops them to Lax, the browser withholds them on the
cross-site POST, and the callback fails its state check, surfacing as "bad or expired state".

## Runner-local roles

Runner-local role resolution is a separate `[auth]` table living only on the runner, never in the hub store or its admin
page. `hub_role_default` is the fallback runner-local role for a hub identity with no override: `"mirror"` (the default)
trusts the hub's own role claim verbatim, while a fixed cap (`"contributor"`/`"guest"`/`"pending"`) floors every
unmatched identity regardless of hub role. `[auth.users]` overrides that default per hub username, resolved from the
JWT's username claim only — never email, which is mutable and may be null.

The runner's `[auth].superuser` names a hub username as this runner's own sovereign — config-only, never assignable
through a JWT claim, mirroring the hub's bootstrap identity.

## Behind a reverse proxy

Behind a TLS-terminating reverse proxy two connection-derived decisions go wrong: the daemon sees plain http from the
proxy, minting the session cookie without Secure even on an end-to-end HTTPS deployment, and every request arrives from
the proxy's own IP, collapsing all clients into one login-throttle and `auth_facts`-actor bucket; both are correct on
direct exposure.

`trusted_proxies` — a top-level key in both `blizzard-hub.toml` and `blizzard-runner.toml` — lists proxy addresses or
CIDRs whose forwarded headers are trusted; when and only when the direct peer matches, `X-Forwarded-Proto` decides the
effective scheme (the cookie Secure flag, on both daemons) and the rightmost untrusted hop of `X-Forwarded-For` becomes
the throttle and fact client IP. An unlisted peer keeps its direct-connection values whatever headers it carries, so a
direct client cannot forge its scheme or spoof `X-Forwarded-For`; empty (the default) ignores both headers from every
peer. Only `X-Forwarded-*` headers are honored — RFC 7239 `Forwarded` and proxy-protocol framing are not consulted, and
no `X-Forwarded-Host` is read, so a rewritten Host cannot be recovered.

The proxy must set `X-Forwarded-Proto` and `X-Forwarded-For`, and in front of a runner must also pass the browser's
original Host through unchanged, since per-origin callback selection reads that header and nothing else; nginx replaces
Host by default, so set `proxy_set_header Host $host` explicitly. Caddy's `reverse_proxy` and `tailscale serve` set all
three headers automatically.

An nginx proxy in front of the hub also needs `client_max_body_size` raised for the transcript lane: a shipped record
may reach the hub's per-record cap (10 MB by default, settable) while nginx's own default of 1 MB 413s such a record
before the hub adjudicates it; raise it whenever `record_max_bytes` rises — Caddy has no equivalent default limit.
