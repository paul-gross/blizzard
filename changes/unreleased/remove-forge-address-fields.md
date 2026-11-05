---
kind: removed
surfaces: [http-api]
retires: deprecate-forge-address-fields
breaking: true
breaks:
  - RepositoryCreateRequest.forge_api_url
  - RepositoryPatchRequest.forge_api_url
  - RepositorySummary.forge_api_url
  - WorkSourceCreateRequest.api_base
  - WorkSourceCreateRequest.web_base
  - WorkSourcePatchRequest.api_base
  - WorkSourcePatchRequest.web_base
  - WorkSourceSummary.api_base
  - WorkSourceSummary.web_base
upgrade-guide: forges-replace-the-address-fields
---

Removes the forge address fields of repositories and work sources: `forge_api_url` on a repository, and `api_base` and
`web_base` on a work source, leave the create and edit requests and the responses. A create or edit that sends one is
refused `422 field_moved_to_forge` at the field, naming the forge that carries it, and answers no `Deprecation` header.
A create that names no `forge` is refused `422 forge_required`, so a work source no longer defaults to the
`https://api.github.com` forge. The hub no longer records the `config-old-field-used` event; rows already stored keep
their severity.

## Upgrade

Name each repository's and work source's forge with `forge`, an id or a slug, and read `forge_id`, `forge_slug` and
`forge_name` in place of the address fields; an origin a record carried belongs on its forge.
