---
kind: fixed
surfaces: [board]
---

Automatic GitHub delivery now selects an allowed merge method, including rebase or squash when merge commits are
disabled. Behind branches can be safely rebased and checked again, and exhausted methods report the allowed set and
individual refusals instead of implying that a configured method was rejected.
