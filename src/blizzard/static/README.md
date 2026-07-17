# Embedded frontend assets — the packaging seam

This directory is the wheel-embedded frontend seam. The one wheel
ships the compiled Angular apps as static assets here, so the released artifact
needs no Node at install or runtime.

## Contract for the frontend + CI builders

- `hub/` holds the compiled **hub app**; `runner/` holds the compiled **runner
  app**. Each is served by its daemon at `/` with an SPA fallback to `index.html`
  (`blizzard.foundation.web.mount_web_app`), alongside `/api` from the same process.
- **Nothing under `hub/` or `runner/` is tracked in git** — the whole build
  output, `index.html` included, is gitignored (see the repo `.gitignore`), so a
  clean worktree can never restore a stale placeholder over a real build. When no
  build has run, `index.html` is absent and the daemon serves a **runtime
  placeholder** (`blizzard.foundation.web.mount_web_app`), keeping the mount live
  for local dev.
- The **CI build pipeline (or a local `npm run build`) owns filling these dirs**:
  it runs the Angular production build for each app, writes the output here
  (`index.html` plus the hashed JS/CSS bundles), then runs `uv build`.
  `pyproject.toml`'s `artifacts` glob guarantees the whole tree — the real built
  assets — ships in the wheel even though every file here is gitignored.
- Locating these dirs at runtime is `blizzard.foundation.assets.frontend_dir(name)`;
  do not hard-code paths elsewhere.
