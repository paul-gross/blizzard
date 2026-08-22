# Human authentication (OAuth login)

Distinct from [Runner authentication](./runner-auth.md): this plane authenticates an **operator** logging into the hub's
own web/API surface, not a runner authenticating itself to the hub. The hub's `[auth]` table (scaffolded into
`blizzard-hub.toml` by `blizzard hub
init`) is the human-auth rollout knob:

```toml
[auth]
mode = "none"                    # "none" (the shipped default) or "oauth"
# superuser = "ada@example.com"  # the bootstrap superuser's email — see below

# [[auth.oauth.provider]]
# name = "github"                    # the provider's identity; identities key on it
# type = "github"                    # "github" or "oidc"
# display_name = "GitHub"            # the login button's label
# client_id = "..."                  # the OAuth app's client id
# client_secret_env = "BZ_OAUTH_GITHUB_SECRET"  # names an env var — the secret itself
#                                                 # lives in this runtime's env file
# issuer = "https://accounts.example.com"        # oidc only: the discovery issuer
# api_base = "https://ghe.example.internal"       # optional: override the provider's
#                                                  # default host (github type only)
```

`mode = "none"` (the shipped default) resolves every request to the implicit operator/superuser identity with no store
read — a fresh or upgraded hub keeps working unauthenticated until an operator deliberately opts in. `mode = "oauth"`
activates the session/permission seam and requires at least one `[[auth.oauth.provider]]` entry. `type` selects the
conformer: `"github"` (an OAuth App) or `"oidc"` (a generic OIDC issuer, discovered via
`<issuer>/.well-known/openid-configuration`). `client_secret_env` mirrors `[[work_source]] token_env`'s indirection
exactly — it names an environment variable, never the secret itself; the secret goes in the hub's runtime env file (e.g.
`/etc/blizzard/hub.env` under the [systemd layout](./install.md)), a deployment credential like
`BZ_FORGE_TOKEN`/`BZ_WORK_SOURCE_TOKEN`.

## The superuser bootstrap

`[auth].superuser` names one email as the fleet's bootstrap identity, ensured at every hub boot: once a verified login
matches that email, the hub promotes that user to `superuser`; until then, the intent is pre-provisioned and unclaimed,
and the boot log (plus an `auth_facts` entry) surfaces that on every restart rather than failing silently. Changing
`superuser` to a different email demotes whichever user the previous target had claimed back to `admin` — at most one
user is ever the bootstrapped superuser at a time, and this is the *only* way a user becomes (or stops being)
`superuser`; the role is never assignable through the admin API.

## Roles, in one paragraph

A hub-local user carries one of five roles, a total order — `pending < guest < contributor < admin < superuser`. A
freshly-logged-in identity lands as `pending`: the lobby, holding no permissions at all beyond the public self routes
(`GET /api/me`, login, logout) — no board read, no writes. `guest` reads the fleet's state (the board, chunks, graphs,
events) and mutates nothing, but not a chunk's stored transcript segments — an operator's read of those needs
`contributor`+ (`transcript:read`) on this role ladder, since a transcript carries everything a worker saw. A second
reader sits outside this ladder entirely and outside this table: a runner reading back its own shipped segments, gated
on a runner bearer token rather than a hub-local role — see [Runner authentication](./runner-auth.md). An `admin`
(promoted from the admin page, `POST /api/users/{id}/role`, gated on `user:manage`) can move a subject freely among
`pending`/`guest`/`contributor`, but only a `superuser` actor may grant or revoke `admin` itself, and `superuser` is
never assignable through that API in either direction — it is bootstrap-only, per the previous section.

## The hub board's Transcripts tab

The hub's chunk board page (`/board/chunk/:chunkId`) carries a Transcripts tab last in its four-tab strip — General,
Node history, Artifacts, Transcripts — gated on `transcript:read` the same way the API route above is: an operator
without it never sees the tab option, and a held deep link to one renders an honest permission notice rather than a
generic error. Open, it lists the chunk's node-history steps, each holding the transcript segments a runner shipped
while working that step; opening a segment fetches its turns lazily, including any nested subagent conversation and the
harness's own private reasoning, and a step whose one attempt spans multiple segments (a resumed session within the same
node and epoch) links them end to end so that attempt's whole conversation reads in order. A bounce back into an earlier
node (a build that failed review and ran again) is a **later epoch** — its own step, never stitched to the attempt
before it.

`transcript:read` governs a second board surface too: the Node history tab's own per-step Transcript accordion, which
opens that step's segments inline rather than through the dedicated tab. The gate presents differently there — the Node
history tab option itself is never hidden (a step's own artifacts still show without the permission), but its Transcript
accordion renders an in-place "NO PERMISSION TO READ TRANSCRIPTS" notice off the same backend 403 instead of a segment,
rather than the hidden tab option the dedicated Transcripts tab uses.

The runner's own machine panel serves the same route on its own host, with its own four-tab strip — General, Node
history, Artifacts, Transcripts. Its Node history tab mirrors the hub's own timeline and per-step artifacts, but stops
there: it wires no per-step transcript of its own, because the hub's transcript routes are declared
`dependencies=[Depends(reject_runner_principal)]`, structurally refusing a runner-authenticated bearer. The runner's own
Transcripts tab reads that chunk's segments a different way — a runner reading back its own shipped segments, gated on
its own bearer token rather than this hub-scoped `transcript:read` gating; see [Runner authentication](./runner-auth.md)
and [The runner's two doors](./runner-doors.md).

## Operator verbs

`blizzard hub login` logs an operator into the hub: by default it opens a browser to the hub's own authorize endpoint
(PKCE, an ephemeral `127.0.0.1` loopback redirect) — the user completes login *at the hub*, and the resulting session
token is stored locally. `--paste` swaps that for the paste-code fallback (the hub renders a short one-time code the
user pastes back into the prompt), for a headless/remote shell with no reachable loopback listener.
`blizzard hub logout` deletes the locally stored session and revokes it at the hub, so it stops resolving even if it
leaked. `blizzard hub rotate-signing-key` rotates the hub's IdP signing keypair — mints a fresh current key, demoting
the old current to previous; runners pick up the new key by re-fetching JWKS on an unknown `kid`, no restart needed.
Under `mode = "oauth"`, `rotate-signing-key` is itself gated on `user:manage` and requires a logged-in session.

## Runner-side federation

A runner that wants its own human web surface reachable via the hub's SSO bounce declares `public_url` in
`blizzard-runner.toml` — the browser-reachable base URL(s) it answers on, from which the runner derives the redirect
URIs it presents to the hub's IdP authorize endpoint (`<public_url>/api/auth/callback` each). Empty (the fresh-scaffold
default) means this runner registers no federation identity, so its human web surface stays unreachable via SSO — and,
since there is no IdP to bounce to either way, that is also the correct state when the hub itself runs
`auth.mode = "none"`.

`public_url` takes **one URL or a list of them**. More than one matters because the hub delivers the federation token by
making the *browser* POST to the redirect URI, so a redirect URI is followed by the browser rather than the runner, and
so resolves in the network namespace of whichever device is holding it: a runner declaring only `http://127.0.0.1:8431`
is reachable from a browser on its own host and nowhere else, since any other device follows that address to itself.

Two constraints bound what is worth declaring. First, **only two origin classes can complete a bounce**: a loopback
origin at either scheme, and a non-loopback origin only as `https`. A non-loopback plain-`http` origin is not merely
insecure — the bounce cookies cannot be `Secure` there, browsers refuse `SameSite=None` without it, and the cross-site
callback arrives cookie-less, so `http://192.168.1.5:8431` fails every time it is tried. Second, **each entry must equal
the origin the browser shows, exactly** — scheme, host, and port — because selection compares it against the request's
`Host`. A proxy terminating TLS on 443 makes the browser-visible origin `https://runner.example`, which a declared
`https://runner.example:8431` does not match; the mismatch is silent, since the fallback lands on a registered origin
and the hub raises nothing.

`localhost` and `127.0.0.1` are also distinct origins to both the browser and the hub's exact-match guard, so each needs
its own entry. Two spellings that a browser cannot tell apart — differing only in scheme, or in an
explicit-versus-default port — are refused at load rather than silently resolving to whichever was declared first:

```toml
public_url = ["http://127.0.0.1:8431", "http://localhost:8431", "https://runner.example"]
```

Every declared origin is registered with the hub, which exact-matches the presented redirect URI against that registered
set. The runner then selects among the declared origins per request, by the arriving `Host`; a request whose `Host`
matches none falls back to the first declared origin, which is the canonical one the hub records as this runner's URL.
Selection is membership in the declared set and never construction from the request, so an unrecognized or forged `Host`
can only ever resolve to an origin the operator already declared — it is logged as a warning and falls back, never
reflected into a redirect URI. A value that is not a URL or a list of them, an entry carrying a path, userinfo, or a
port that is not a number, and two entries naming one browser origin all fail at config load rather than surfacing later
as an opaque `unregistered redirect_uri` refusal from the hub.

Registration happens on the runner's reconciliation tick, so a widened set reaches the hub on the next tick after a
restart — a login attempted between the restart and that tick is refused as an unregistered redirect URI. Any `https`
entry served through a TLS-terminating proxy also needs `trusted_proxies`, which is a hard requirement for that case
rather than a refinement, and needs the proxy to preserve the browser's `Host`; both are covered under
[Behind a TLS-terminating reverse proxy](#behind-a-tls-terminating-reverse-proxy).

Runner-local role resolution is a separate `[auth]` table, living only on the runner — never in the hub store or its
admin page:

```toml
[auth]
# superuser = "<hub-username>"   # this runner's own sovereign, config-only
hub_role_default = "mirror"      # "mirror" (reproduce the hub's own role claim) or a
                                  # fixed cap ("contributor"/"guest"/"pending")

[auth.users]
# ada = "admin"                  # per-hub-username role overrides
```

`superuser` names a hub **username** as this runner's own sovereign — never assignable through a JWT claim, a
config-only designation mirroring the hub's own `auth.superuser` bootstrap identity. `hub_role_default` is the fallback
runner-local role for a hub identity with no `[auth.users]` override: `"mirror"` (the default) trusts the hub's own
`role` claim verbatim, or a fixed cap (`"contributor"`/`"guest"`/`"pending"`) floors every unmatched identity regardless
of hub role. `[auth.users]` overrides that default per hub username, resolved from the JWT's `username` claim only
(never `email`, which is mutable and may be null).

## Behind a TLS-terminating reverse proxy

The two decisions this plane derives from the connection — the session cookie's `Secure` flag (from the request scheme)
and the login throttle / `auth_facts` actor IP (from the peer address) — are correct when a daemon is exposed
**directly** (localhost, tailnet) and wrong behind a TLS-terminating reverse proxy (nginx, Caddy, an ALB). The proxy
speaks HTTPS to the browser but plain HTTP to the daemon, so the daemon sees `http` (and mints the session cookie
*without* `Secure`, even though the deployment is HTTPS end to end), and every request arrives from the proxy's own IP
(so one noisy client collapses the whole fleet into a single throttle bucket).

For a **runner** whose `public_url` declares a proxied `https` origin, this is not a degradation but an outright failure
of the SSO bounce, so `trusted_proxies` is a hard requirement there. The hub returns the federation token by a
cross-site `form_post`, and the runner's bounce cookies only get `SameSite=None` (which browsers honor only alongside
`Secure`) on an origin it believes is secure. Reading the scheme as `http` drops them to `SameSite=Lax`, the browser
withholds them on that cross-site POST, and the callback fails its state check — surfacing as `bad or expired state`,
which names nothing about the cause. A **loopback** origin is exempt: browsers treat loopback as potentially trustworthy
whatever the scheme, which is why a `127.0.0.1` runner federates against a hosted hub with no proxy configuration at
all.

`trusted_proxies` — a top-level key in **both** `blizzard-hub.toml` and `blizzard-runner.toml` — lists the proxy
addresses or CIDRs whose forwarded headers are trusted:

```toml
# hub or runner runtime config
trusted_proxies = ["10.0.0.0/8", "192.168.1.7"]
```

When — and only when — the direct peer matches a listed proxy, `X-Forwarded-Proto` decides the effective scheme (the
cookie `Secure` flag, on both daemons) and the **rightmost untrusted hop** of `X-Forwarded-For` becomes the throttle /
fact client IP. A request from any other peer keeps its direct-connection values regardless of what headers it carries —
so a direct client cannot forge its scheme or spoof an `X-Forwarded-For` to dodge the throttle. Empty (the default)
ignores both headers from every peer, byte-identical to a direct-exposure deployment.

The proxy must set both headers, and in front of a **runner** it must additionally pass the browser's original `Host`
through unchanged — per-origin callback selection reads that header and nothing else, so a proxy that replaces it makes
every request look like it arrived on the proxy's own upstream address. nginx replaces it by default
(`Host: $proxy_host`), which is why it is set explicitly here:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
client_max_body_size               16m;
```

`client_max_body_size` is there for the transcript lane, not for headers: one shipped record may reach the hub's
per-record cap — 10 MB by default, and settable — while nginx's own default is **1 MB**, so a proxy left at that default
rejects a large record with a 413 before the hub ever adjudicates it. Raise it whenever the hub's `record_max_bytes`
rises. Caddy has no equivalent default limit.

Caddy's `reverse_proxy` and Tailscale's `tailscale serve` set all three headers automatically. Only `X-Forwarded-*` is
honored — `Forwarded` (RFC 7239) and proxy-protocol framing are not consulted, and no `X-Forwarded-Host` is read, so a
rewritten `Host` cannot be recovered.

A runner whose `Host` is rewritten fails the way an undeclared origin does: the bounce falls back to the canonical
origin, the hub accepts it as registered, and the browser is sent to whatever that origin names — its own machine, for a
loopback canonical. The runner logs a warning naming the arriving `Host` and the declared set when that fallback fires,
which is the signal to check this configuration.
