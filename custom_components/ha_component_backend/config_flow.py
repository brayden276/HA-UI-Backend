"""Config flow for the HA Component Backend."""

from homeassistant import config_entries

from .const import DOMAIN


class HaComponentBackendConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the singleton backend entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create the singleton backend."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="HA Component Backend", data={})
