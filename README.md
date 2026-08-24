# HA Component Backend

<img src="custom_components/ha_component_backend/brand/icon.png" alt="HA Component Backend icon" width="96">

The Home Assistant backend for HA Component Library. It provides durable state,
acknowledged services and reusable dashboard preferences without proliferating
helpers, automations or per-feature integrations.

## Install

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/brayden276/HA-UI-Backend` as an **Integration**.
3. Download **HA Component Backend**, then restart Home Assistant when prompted.
4. In **Settings > Devices & services**, choose **Add integration**, select **HA Component Backend**, and submit.

The dashboard library detects `sensor.ha_component_backend` automatically once its matching frontend release is installed.

## Current split-system services

- `ha_component_backend.configure_room`
- `ha_component_backend.update_room`
- `ha_component_backend.set_timer`
- `ha_component_backend.resume_room`
- `ha_component_backend.upsert_profile`
- `ha_component_backend.remove_profile`
- `ha_component_backend.remove_room`

Each room is stored under a stable `room_id` in Home Assistant storage. Future backend features belong inside this one integration, without another HACS installation.

All mutation services support an optional Home Assistant response containing the
committed revision, whether stored state changed and the resulting room record.
Callers that do not request response data remain backwards compatible.

## Dashboard preference API

The frontend can store compact shared preferences through three authenticated
WebSocket commands:

- `ha_component_backend/preferences/get`
- `ha_component_backend/preferences/update`
- `ha_component_backend/preferences/remove`

Each key is stored in the same atomic Home Assistant Store document as the room
registry, but preference values are deliberately not exposed as sensor
attributes. Values must be valid JSON and are limited to 64 KiB per key. Update
and remove accept an optional per-key `expected_revision` for optimistic
concurrency, so an unrelated room-state write cannot invalidate an open editor.
Per-key revisions remain monotonic across removal and recreation; a deleted key
retains only its revision tombstone, not its value.

This is the intended replacement for small `input_text` JSON stores and copied
frontend persistence helpers. It is not a general database and should not hold
entity history, secrets or large media payloads.

Store writes are copy-on-write: the live in-memory revision is published only
after Home Assistant confirms the Store save. A disk/write failure therefore
cannot leave the sensor reporting state that was never persisted.

## Release

This is a Home Assistant Python integration, so there is no JavaScript bundle to build. The release workflow validates the HACS contract, validates Python syntax, updates both version files, commits, tags and pushes a Git tag. HACS can install from the default branch or a published GitHub release.

```powershell
npm run check
npm run release:dry-run
npm run release
```

`npm run release` publishes the next patch version. Use `npm run release:prepare -- minor` or `npm run release:prepare -- major` to prepare a larger version without publishing it. The release command requires a clean working tree and GitHub push credentials.
