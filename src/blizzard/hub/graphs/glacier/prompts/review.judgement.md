# Review — judgement

Render your review verdict. Your findings ride forward as the `review-findings` asset — if you have not yet run `blizzard runner attach --name review-findings` with your findings on stdin, do that now before you record this verdict. State what you reviewed per axis and every blocking issue you found.

Select `pass` if the work meets the plan and the item's intent with no blocking issue on any axis — the chunk proceeds to the pre-push review. Select `fail` if any blocking issue remains; your findings ride back into the build node.

Alongside your verdict, submit this node's **retrospective** as its `retrospective` asset: run `blizzard runner attach --name retrospective` with a few honest lines on stdin — what went well, what didn't, and what the next node (or the next run) should know. The terminal retrospective node synthesizes these.
