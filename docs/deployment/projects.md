# Projects

A tenant divides its work into projects. Every chunk belongs to exactly one project, from the moment it is minted, and
so do the tenant's scopes, routines, findings and garden proposals. Work sources, repositories, secrets, graphs, members
and runners stay the tenant's: a project draws from a work source, and lands in a repository, only through a link, and a
runner works the projects it declares.

## Naming a project

A project carries three names, each with one job:

| Name         | What it is                                                                                                                                                                                                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `project_id` | `proj_` followed by a ULID. It never changes, and everything that refers to the project stores it                                                                                                                                    |
| `slug`       | The handle you type: in board URLs, `--project`, API paths and a runner's configuration. 1 to 64 lowercase letters, digits and hyphens, a hyphen only between two letters or digits. Unique in the tenant; two tenants may share one |
| `name`       | The display name: 1 to 128 characters with no leading or trailing whitespace, unique nowhere. Only ever shown, never looked up                                                                                                       |

Wherever a project is accepted, its id or its slug names it; a display name never does. An id names its project whatever
the slug is now.

A project's slug and name may change, and nothing that refers to the project moves. The slug a project leaves becomes
its **former slug**: a URL, a script or a runner configuration written against it keeps reaching the project. The API
serves it as the project; the board rewrites it to the current slug. Any project of the tenant may take a former slug,
and from then on it names its new holder. The one place a former slug is refused is a configuration document, which
answers `422` naming the project that left it ([config-documents.md](./config-documents.md)). A configuration that must
survive any slug change, such as a runner's, names projects by id.

## A project's color

A project carries a `color`: one of twelve palette names, in order `red`, `orange`, `amber`, `lime`, `green`, `teal`,
`cyan`, `blue`, `indigo`, `violet`, `pink` and `slate`. The board draws each name in its own shade; the name, not the
shade, is what the hub stores and the API carries. The board's cards and list rows take their project's color as their
background, and a runner's panel, reading each served project's color from its hub, tints its chunks the same way.

A color may instead be a custom `#rrggbb` hex. The hub lowercases it, so `#14B8A6` is stored as `#14b8a6`, and the board
draws it adjusted for legibility on its dark surfaces, so a very dark shade shows lighter than stated. Alpha, `rgb()`,
`hsl()` and named CSS colors are not accepted.

A create that states no color takes the color the fewest of the tenant's projects hold, and picks at random among
equally unused ones, so a tenant's first twelve projects get twelve different colors. A create or an edit that states a
color stores it, and the change log records it like any other field. A value that is neither a palette name nor a
`#rrggbb` answers `422` naming `color` and both forms. A custom color counts toward no palette name, so the least-used
choice considers palette names only. A project that existed before colors were introduced was given one when the hub
upgraded, distinct among the first twelve of its tenant. The hub never recomputes a color: it changes only when someone
edits it.

## The `default` project

Every tenant holds a project with the slug `default`, named `Default`, from the moment it exists: creating a tenant
creates it in the same transaction, and an upgraded hub gives one to every tenant it carries over. A new tenant can
therefore ingest, hold a routine and offer work to a runner before anyone configures a project. Nothing else sets it
apart: you may rename it or change its slug like any other project.

## Managing projects

Creating and editing a project, and linking or unlinking it, take the tenant role that holds `config:edit`. Reading
projects and their links takes `fleet:view`.

| Verb                                                                                                     | Route                                                               |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `blizzard hub project create <slug> [--name <name>] [--description <text>] [--color <name\|#rrggbb>]`    | `POST /api/projects`                                                |
| `blizzard hub project list`                                                                              | `GET /api/projects`                                                 |
| `blizzard hub project show <project>`                                                                    | `GET /api/projects/{project}`, with its links                       |
| `blizzard hub project edit <project> [--slug …] [--name …] [--description …] [--color …] [--if-match N]` | `PATCH /api/projects/{project}`                                     |
| `blizzard hub project link <project> --source <source>`                                                  | `PUT /api/projects/{project}/source-links/{source}`                 |
| `blizzard hub project link <project> --repository <repository>`                                          | `PUT /api/projects/{project}/repository-links/{repository}`         |
| `blizzard hub project unlink <project> --source <source>`                                                | `POST /api/projects/{project}/source-links/{source}/retire`         |
| `blizzard hub project unlink <project> --repository <repository>`                                        | `POST /api/projects/{project}/repository-links/{repository}/retire` |

`create` names the project after its slug unless `--name` is given. A slug that is another project's current slug
answers `409`; a malformed slug, or a blank or padded name, answers `422` naming the field. A project cannot be retired.
On the board the same lives under Admin → Projects. A configuration document may declare projects and their links too
([config-documents.md](./config-documents.md)), and every change to a project or a link is recorded in the change log
([config-changes.md](./config-changes.md)).

## Links

**A new work source or repository serves no project until it is linked.** Creating one adds it to the tenant; a project
draws from it only once you link the two. Linking is never implicit: nothing links a record when it is created, when
work is ingested from it, or when work lands in it.

- **A source link** lets a project ingest from a work source, named by its id or its slug; the link holds the source's
  id, so it survives the slug changing ([work-sources.md](./work-sources.md#naming-a-work-source)). Every project draws
  from the built-in `hub` source without a link, and that link cannot be removed. When `blizzard hub chunk ingest` names
  no `--project`, the hub uses the one project every pointer's source is linked to; a source linked to several projects,
  or pointers whose sources are linked to different ones, answer `422 project_required`. A pointer whose source is not
  linked to the chunk's project answers `422 source_not_linked`, naming the source
  ([work-sources.md](./work-sources.md#ingesting-work-items)).
- **A repository link** lets a project's chunks land in a repository, named by its id or its slug; the link holds the
  repository's id, so it survives the slug changing ([repositories.md](./repositories.md#naming-a-repository)). A
  `deliver` step resolves a chunk's commits only against the repositories its project links; one that would resolve to a
  repository the project does not link refuses the step as `repository-not-linked`
  ([repositories.md](./repositories.md#how-delivery-uses-a-record)). A repository no project links receives nothing.

Unlinking retires the link: no new work is ingested through it, and the next `deliver` step no longer lands there. A
step already running finishes. `link` relinks a link unlinked earlier.

A hub step's `run:` commands see the chunk's project as `BZ_HUB_PROJECT_ID`, its id, and `BZ_HUB_PROJECT`, its slug when
the step starts.

## A call that names no project

A call that needs a project — creating a scope, routine, operator garden proposal, hub item or link, or looking up a
scope by slug or a routine by name — and names none acts in the tenant's project when the tenant holds exactly one. When
it holds several, the call is refused, never resolved across projects by a name only one of them holds:

```json
{
  "detail": {
    "code": "project_required",
    "message": "name a project (this tenant holds: default, web)",
    "projects": [
      { "project_id": "proj_01JABCDEFGHJKMNPQRSTVWXYZ0", "slug": "default", "name": "Default" },
      { "project_id": "proj_01JABCDEFGHJKMNPQRSTVWXYZ1", "slug": "web", "name": "Web board" }
    ]
  }
}
```

The answer is `422`. A listing that names no project needs none: it reads the whole tenant. A project a call names and
the tenant does not hold answers `404` in a path and `422` in a query or a body.

## `--project` on the CLI

The CLI keeps no saved project, as it keeps no saved tenant. Every verb that acts in a project takes
`--project <id|slug>`, sent to the hub as written:

- A verb that creates a project's record, or looks one up by name, takes `--project` when the tenant holds several
  projects. Without it, such a tenant answers with the refusal above, and the CLI prints the tenant's projects, ids
  beside slugs and names, and asks for `--project`.
- A listing verb takes `--project` to narrow what it shows to that project. Without it, it shows every project's
  records.
- `blizzard hub chunk ingest --project` picks the project to mint the chunk in, over the inference above.

The tenant is settled first, by `--tenant` ([tenants.md](./tenants.md#naming-the-tenant-a-call-acts-in)); the project is
looked up within it. The tenant-wide reads take the same filter over HTTP as `?project=<id|slug>`: chunks, chunk counts,
the queue and backlog, open questions and decisions, runs, spend, the analytics reads, the event feed and the event
stream.

## Runners serve projects

A runner is offered only the chunks of the projects it serves, and the hub rechecks the project when the runner claims
one. The runner's `[[runner]]` in `blizzard-host.toml` names them as bindings, each a project by id or by slug, the
`[[workspace]]` it works in, and the `agents` that may work it at once:

```toml
[[runner]]
# The projects this runner serves; [] serves none. The key is required.
projects = [{ project = "default", workspace = "workspace-local", agents = 1 }]
```

The runner sends the project list at every registration. A `[[runner]]` with no `projects` key is halted, naming the
key, and only `projects = []` declares that the runner serves nothing.

A runner may bind its projects to several workspaces. Each binding's `agents` caps that binding alone, and a full
binding asks the hub for nothing, so a busy project never stands in front of another project's work. The environments of
one `[[workspace]]` form one pool, shared by every runner of the host that binds a project to it. Two entries that
resolve to the same project leave the runner claiming nothing until one is removed; its status names both.

`blizzard-runner init` writes a binding of `default` into a fresh host file, so a new runner is offered work on its
first registration with no edit. A repeated `--project <id|slug>` names other projects instead, and the runner is added
serving them; a project the tenant does not hold refuses the add, and nothing is added. `blizzard hub runner add
<name>`
takes the same repeatable `--project`. Without one, the runner is added serving the tenant's project when the tenant
holds one, and serving nothing when it holds several.

Registration never refuses over the declaration, because it is also the runner's heartbeat. An entry no project of the
tenant answers to serves nothing, and is looked up again at every registration, so it starts serving once a project
takes that slug. `blizzard-runner status` reads the hub's latest answer from the runner's own store and prints a
`serves:` line naming each project served, each entry marked `unresolved`, and each not answered yet. On the board a
runner that serves nothing reads as unassigned, and a project no live runner serves is marked unserved on Admin →
Projects and under its lens on the board. A runner that is paused, retired, refused on version, or reached only through
withheld bindings does not count as serving.

[runner-auth.md](./runner-auth.md#adding-a-runner) owns adding a runner.

## The board's project lens

The board shows the whole tenant, or one project through its lens. The lens lives in the URL, so a reload or a shared
link keeps it:

```text
/{view}/…               every project of the tenant (All)
/p/{project}/{view}/…   one project
```

The lens control sits beside the view tabs and shows the project's display name, each project's chip led by its color;
changing it opens the same view under the other lens. A lensed URL naming a project by id or by a former slug is
rewritten to the current slug. One naming no project of the tenant opens the same view on All, with a notice.

| View                                     | On one project                                                               | On All                     |
| ---------------------------------------- | ---------------------------------------------------------------------------- | -------------------------- |
| Board, and the header's counts           | The project's chunks, in the tenant's order                                  | Every chunk                |
| Events                                   | Events of the project's chunks, and of runners that serve it                 | Every event                |
| Gardening                                | The project's scopes, routines, runs, findings and proposals                 | One card per project       |
| Admin → Projects                         | That project                                                                 | Every project              |
| Admin → Work sources, Repositories       | The records the project links                                                | Every record of the tenant |
| Admin → Changes                          | The project's changes and the tenant-wide ones                               | Every change               |
| Graphs, Admin → Members, Admin → Secrets | Unchanged: these are the tenant's, and the page says the lens does not apply | —                          |

A tenant with one project opens Gardening straight on its garden, with no doorway.
