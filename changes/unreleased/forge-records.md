---
kind: added
surfaces: [http-api, cli, store]
---

Every repository and work source names a forge, a record of its tenant holding the forge's kind and addresses. The hub's
migration creates one forge per API origin its records already name, keeping a port such as a feature environment's, and
points each record at it. `POST` and `PATCH` on `/api/repositories` and `/api/work-sources` take `forge`, a forge's id
or slug, and every repository and work-source response carries `forge_id`, `forge_slug`, `forge_name` and `warnings`, as
the configuration apply's response carries `warnings`. A write naming an unknown or retired forge is refused
`422 forge_unavailable`, a repository naming none `422 forge_required`, an address that disagrees with the named forge
`422 forge_conflict`, and a forge of another kind than a work source's provider `422 forge_kind_mismatch`. A write that
names its forge by an old address field records a `config-old-field-used` warning event. `blizzard hub repo create`
takes exactly one of `--forge` and `--forge-api-url`, and `blizzard hub repo` and `blizzard hub source` take `--forge`.
