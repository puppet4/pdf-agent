# Legacy API Migration Guide

This project keeps temporary compatibility endpoints for legacy routes:

- `GET /api/tools`
- `GET /api/executions`
- `POST /api/executions`
- `GET /api/workflows`

## Migration Targets

- Legacy execution creation -> `POST /api/conversations/{conversation_id}/messages`
- Legacy execution listing -> `GET /api/conversations`
- Tool discovery -> `GET /api/tools` (deprecated bridge) or use in-app metadata surfaced by chat workflow

## Sunset

- Planned sunset date is controlled by `PDF_AGENT_LEGACY_API_SUNSET_DATE`.
- Bridge mode is controlled by `PDF_AGENT_LEGACY_API_COMPATIBILITY_MODE`:
  - `bridge`: legacy routes stay available with deprecation headers.
  - `disabled`: legacy routes are removed.
- Phase is controlled by `PDF_AGENT_LEGACY_API_PHASE`:
  - `deprecation`: legacy routes return compatibility payloads with warning headers.
  - `warning`: same payloads with stronger warning semantics (`Warning` header).
  - `sunset`: legacy routes return `410 Gone`.

## Migration Lifecycle

1. `deprecation` (default for transition)
   - Keep adapters enabled.
   - Monitor call volume per legacy route.
2. `warning`
   - Keep adapters, notify remaining clients with hard deadline.
3. `sunset`
   - Return 410 and remove client dependencies before setting
     `PDF_AGENT_LEGACY_API_COMPATIBILITY_MODE=disabled`.
