# HA Component Backend

The Home Assistant backend for HA Component Library. It provides durable state and services for reusable dashboard features; the first feature is the room-keyed Split System Registry.

## Install

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/brayden276/HA-Component-Backend` as an **Integration**.
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

## Release

This is a Home Assistant Python integration, so there is no JavaScript bundle to build. The release workflow validates the HACS contract, validates Python syntax, updates both version files, commits, tags and pushes a GitHub release tag for HACS.

```powershell
npm run check
npm run release:dry-run
npm run release
```

`npm run release` publishes the next patch version. Use `npm run release:prepare -- minor` or `npm run release:prepare -- major` to prepare a larger version without publishing it. The release command requires a clean working tree and GitHub push credentials.
