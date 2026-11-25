# review/findings-delivered-format

The shape of the `review-findings-delivered` asset the hub writes on a chunk when `record-findings` delivers its newest
`review-finding-delta` (`review/finding-format`). No worker or graph writes it. It is the chunk's record of what the
delivery minted, written together with those findings.

The content is one JSON object, and `outcome` says which of two it is. Both carry `findings`.

A chunk's review findings are delivered once. A later `record-findings` pass leaves the asset as it is, even if a newer
delta exists. An asset with empty content records only that a delivery happened, not what it minted.

## A delivery that minted findings

`outcome` is `"minted"`, and `findings` holds one item per `deferred` entry, in the delta's order:

- `ref` — the delta entry's `ref`.
- `finding_id` — the hub finding that entry minted.
- `scope_slug` — the scope the finding was filed under.

`fixed` and `refuted` entries mint nothing, so they are not listed.

### MintedReviewFindingsDelivered

```json
{
  "outcome": "minted",
  "findings": [{ "ref": "F1", "finding_id": "fin_01J00000000000000000000000", "scope_slug": "blizzard" }]
}
```

## A delivery that deferred nothing

`outcome` is `"none-deferred"`: the delta deferred no entry, so no finding was minted. `findings` is empty.

### NoneDeferredReviewFindingsDelivered

```json
{ "outcome": "none-deferred", "findings": [] }
```
