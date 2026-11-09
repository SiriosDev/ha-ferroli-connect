"""Opt-in boiler water setpoint controls with telemetry-confirmed writes."""
import time

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import codec
from .const import DOMAIN


NUMBERS = (
    ("heating_water_target_temperature", "heating_water_target", "Heating water target",
     25, 85, False),
    ("dhw_target_temperature", "domestic_hot_water_target", "Domestic hot water target",
     30, 65, True),
)


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.options.get("confirm_boiler_setpoints", False):
        async_add_entities([FerroliWaterTargetNumber(hass, entry, *description)
                            for description in NUMBERS])


class FerroliWaterTargetNumber(NumberEntity):
    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry, state_key, unique_suffix, name, minimum, maximum, dhw):
        self.hass = hass
        self.entry = entry
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self.state_key = state_key
        self._attr_name = name
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_value = None
        self._attr_available = False
        device = entry.data["device"]
        self._attr_unique_id = f"{device.get('slug')}_{unique_suffix}"
        if dhw:
            self.flag = 0x2003
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{device.get('slug')}_dhw")},
                "name": "Agua caliente sanitaria (ACS)", "manufacturer": "Ferroli",
            }
        else:
            self.flag = 0x200B
            self._attr_device_info = {
                "identifiers": {(DOMAIN, str(device.get('slug')))},
                "name": "Calefacción", "manufacturer": "Ferroli",
            }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update))
        self._handle_update(self.runtime.state)

    @callback
    def _handle_update(self, state):
        value = state.get(self.state_key)
        self._attr_available = isinstance(value, (int, float)) and not isinstance(value, bool)
        self._attr_native_value = value if self._attr_available else None
        self.async_write_ha_state()

    async def async_set_native_value(self, value):
        if not self.entry.options.get("confirm_boiler_setpoints", False):
            raise RuntimeError("Enable boiler setpoint controls in integration options first")
        current = self.runtime.state.get(self.state_key)
        if current is None:
            raise RuntimeError("A current boiler setpoint telemetry value is required")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not float(value).is_integer():
            raise ValueError("Boiler setpoint must be an integer Celsius value")
        value = int(value)
        if not self._attr_native_min_value <= value <= self._attr_native_max_value:
            raise ValueError("Boiler setpoint is outside the supported range")
        packet = (codec.dhw_target_command if self.flag == 0x2003 else codec.target_command)(
            value, msg_id=self.runtime.next_message_id(),
            timezone_offset_hours=max(0, min(255, 12 - int(time.timezone / 3600))))
        await self.runtime.control_boiler_setpoint(
            packet, state_key=self.state_key, expected_value=float(value))
