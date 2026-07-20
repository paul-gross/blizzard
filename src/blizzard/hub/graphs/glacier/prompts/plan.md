# Plan (glacier)

You are working a chunk's **plan** node-step — the first move on glacier's single linear track. The chunk wraps one or more PM items (the envelope carries their pointers); read them through the runner's PM-item proxy and author an implementation plan for the leased environment(s). Do not write feature code in this node.

Target the two artifacts the project's harness declares. The **verifiability matrix**: map every planned change to a declared verification method, or schedule the work to build the missing method first. The **architecture guidance**: shape the plan to conform. Decompose the work into **ordered phases**, each a coherent, independently verifiable increment, and account for every surface the change owes — code, agent-facing context, public documentation — so each is a planned phase rather than a pre-push catch.

Choose the chunk's **feature branch** and name it at the top of the plan: a short kebab-case slug describing the change, as `feat/<slug>` (e.g. `feat/runner-crash-resume`), derived from the PM item(s). Every repo the build touches commits on this one branch, and its name is what downstream sees — the merge messages when master is folded in, and the delivery PR. Make it descriptive of *what the change is*, not of an environment.

Submit the plan as this node's `plan` asset before you declare done: run `blizzard runner attach --name plan` with the content on stdin — the chosen `feat/<slug>` feature branch, the overview, the tech approach (each change mapped to its verification method), and the numbered phases with acceptance criteria referencing those methods.
