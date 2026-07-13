# Embedded frontend assets — the packaging seam

This directory is the wheel-embedded frontend seam (D-061, D-096). The one wheel
ships the compiled Angular apps as static assets here, so the released artifact
needs no Node at install or runtime.

## Contract for the frontend + CI builders

- `hub/` holds the compiled **hub app**; `runner/` holds the compiled **runner
  app**. Each is served by its daemon at `/` with an SPA fallback to `index.html`
  (`blizzard.foundation.web.mount_web_app`), alongside `/api` from the same process.
- Each directory ships a **committed placeholder `index.html`** so the mount is
  live for local dev before any Angular build exists. The placeholders are the
  only tracked files under `hub/` and `runner/` (see the repo `.gitignore`).
- The **CI build pipeline owns filling these dirs**: it runs the Angular
  production build for each app, writes the output here (overwriting the
  placeholder `index.html` and adding the hashed JS/CSS bundles), then runs
  `uv build`. `pyproject.toml`'s `force-include` guarantees the whole tree — real
  built assets included — ships in the wheel even though the built files are
  gitignored.
- Locating these dirs at runtime is `blizzard.foundation.assets.frontend_dir(name)`;
  do not hard-code paths elsewhere.
