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
