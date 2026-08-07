# Build and Deployment


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


## Current frontend build

`npm run build` invokes:

```text
node build.mjs
node gen_html.mjs
```

using esbuild/PostCSS dependencies.

## Rules

- `dist/` is generated output and SHOULD not become a second editable source tree.
- Build input order for classic/global scripts SHALL remain explicit where order is required.
- A production build SHOULD fail on missing declared source assets.
- Bundle generation SHOULD preserve page-specific CSS/JS ownership.

## Backend packaging

Current `pyproject.toml` intentionally packages multiple top-level packages and
modules. Do not force a `src/` conversion without a concrete benefit.

## Release checklist

1. backend tests;
2. frontend build;
3. smoke Dashboard;
4. WebSocket reconnect;
5. expiry change;
6. Option Chain scrolling;
7. paper trading smoke;
8. artifact/version notes.
