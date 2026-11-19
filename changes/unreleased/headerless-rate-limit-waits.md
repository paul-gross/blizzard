---
kind: fixed
---

A GitHub answer saying its API rate limit was exceeded, but carrying no rate headers, was reported as a refused
credential on a landing or work-source call. Blizzard now reads it as a rate refusal and waits for the budget to lift,
as it already did when minting an App installation token.
