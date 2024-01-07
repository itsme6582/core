"""Platform allowing several sensors to be grouped into one sensor to provide numeric combinations."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import statistics
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    DEVICE_CLASSES_SCHEMA,
    DOMAIN,
    PLATFORM_SCHEMA as PARENT_PLATFORM_SCHEMA,
    STATE_CLASSES_SCHEMA,
    UNIT_CONVERTERS,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_DEVICE_CLASS,
    CONF_ENTITIES,
    CONF_NAME,
    CONF_TYPE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State, async_get_hass, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.entity import (
    get_capability,
    get_device_class,
    get_unit_of_measurement,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType, StateType

from . import GroupEntity
from .const import CONF_IGNORE_NON_NUMERIC

DEFAULT_NAME = "Sensor Group"

ATTR_MIN_VALUE = "min_value"
ATTR_MIN_ENTITY_ID = "min_entity_id"
ATTR_MAX_VALUE = "max_value"
ATTR_MAX_ENTITY_ID = "max_entity_id"
ATTR_MEAN = "mean"
ATTR_MEDIAN = "median"
ATTR_LAST = "last"
ATTR_LAST_ENTITY_ID = "last_entity_id"
ATTR_RANGE = "range"
ATTR_SUM = "sum"
ATTR_PRODUCT = "product"
SENSOR_TYPES = {
    ATTR_MIN_VALUE: "min",
    ATTR_MAX_VALUE: "max",
    ATTR_MEAN: "mean",
    ATTR_MEDIAN: "median",
    ATTR_LAST: "last",
    ATTR_RANGE: "range",
    ATTR_SUM: "sum",
    ATTR_PRODUCT: "product",
}
SENSOR_TYPE_TO_ATTR = {v: k for k, v in SENSOR_TYPES.items()}

# No limit on parallel updates to enable a group calling another group
PARALLEL_UPDATES = 0

PLATFORM_SCHEMA = PARENT_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITIES): cv.entities_domain(
            [DOMAIN, NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN]
        ),
        vol.Required(CONF_TYPE): vol.All(cv.string, vol.In(SENSOR_TYPES.values())),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_IGNORE_NON_NUMERIC, default=False): cv.boolean,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): str,
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_STATE_CLASS): STATE_CLASSES_SCHEMA,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Switch Group platform."""
    async_add_entities(
        [
            SensorGroup(
                hass,
                config.get(CONF_UNIQUE_ID),
                config[CONF_NAME],
                config[CONF_ENTITIES],
                config[CONF_IGNORE_NON_NUMERIC],
                config[CONF_TYPE],
                config.get(CONF_UNIT_OF_MEASUREMENT),
                config.get(CONF_STATE_CLASS),
                config.get(CONF_DEVICE_CLASS),
            )
        ]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Switch Group config entry."""
    registry = er.async_get(hass)
    entities = er.async_validate_entity_ids(
        registry, config_entry.options[CONF_ENTITIES]
    )
    async_add_entities(
        [
            SensorGroup(
                hass,
                config_entry.entry_id,
                config_entry.title,
                entities,
                config_entry.options.get(CONF_IGNORE_NON_NUMERIC, True),
                config_entry.options[CONF_TYPE],
                None,
                None,
                None,
            )
        ]
    )


@callback
def async_create_preview_sensor(
    name: str, validated_config: dict[str, Any]
) -> SensorGroup:
    """Create a preview sensor."""
    hass = async_get_hass()
    return SensorGroup(
        hass,
        None,
        name,
        validated_config[CONF_ENTITIES],
        validated_config.get(CONF_IGNORE_NON_NUMERIC, False),
        validated_config[CONF_TYPE],
        None,
        None,
        None,
    )


def calc_min(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float | None]:
    """Calculate min value."""
    val: float | None = None
    entity_id: str | None = None
    for sensor_id, sensor_value, _ in sensor_values:
        if val is None or val > sensor_value:
            entity_id, val = sensor_id, sensor_value

    attributes = {ATTR_MIN_ENTITY_ID: entity_id}
    if TYPE_CHECKING:
        assert val is not None
    return attributes, val


def calc_max(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float | None]:
    """Calculate max value."""
    val: float | None = None
    entity_id: str | None = None
    for sensor_id, sensor_value, _ in sensor_values:
        if val is None or val < sensor_value:
            entity_id, val = sensor_id, sensor_value

    attributes = {ATTR_MAX_ENTITY_ID: entity_id}
    if TYPE_CHECKING:
        assert val is not None
    return attributes, val


def calc_mean(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float | None]:
    """Calculate mean value."""
    result = (sensor_value for _, sensor_value, _ in sensor_values)

    value: float = statistics.mean(result)
    return {}, value


def calc_median(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float | None]:
    """Calculate median value."""
    result = (sensor_value for _, sensor_value, _ in sensor_values)

    value: float = statistics.median(result)
    return {}, value


def calc_last(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float | None]:
    """Calculate last value."""
    last_updated: datetime | None = None
    last_entity_id: str | None = None
    last: float | None = None
    for entity_id, state_f, state in sensor_values:
        if last_updated is None or state.last_updated > last_updated:
            last_updated = state.last_updated
            last = state_f
            last_entity_id = entity_id

    attributes = {ATTR_LAST_ENTITY_ID: last_entity_id}
    return attributes, last


def calc_range(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float]:
    """Calculate range value."""
    max_result = max((sensor_value for _, sensor_value, _ in sensor_values))
    min_result = min((sensor_value for _, sensor_value, _ in sensor_values))

    value: float = max_result - min_result
    return {}, value


def calc_sum(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float]:
    """Calculate a sum of values."""
    result = 0.0
    for _, sensor_value, _ in sensor_values:
        result += sensor_value

    return {}, result


def calc_product(
    sensor_values: list[tuple[str, float, State]],
) -> tuple[dict[str, str | None], float]:
    """Calculate a product of values."""
    result = 1.0
    for _, sensor_value, _ in sensor_values:
        result *= sensor_value

    return {}, result


CALC_TYPES: dict[
    str,
    Callable[
        [list[tuple[str, float, State]]], tuple[dict[str, str | None], float | None]
    ],
] = {
    "min": calc_min,
    "max": calc_max,
    "mean": calc_mean,
    "median": calc_median,
    "last": calc_last,
    "range": calc_range,
    "sum": calc_sum,
    "product": calc_product,
}


class SensorGroup(GroupEntity, SensorEntity):
    """Representation of a sensor group."""

    _attr_available = False
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str | None,
        name: str,
        entity_ids: list[str],
        mode: bool,
        sensor_type: str,
        unit_of_measurement: str | None,
        state_class: SensorStateClass | None,
        device_class: SensorDeviceClass | None,
    ) -> None:
        """Initialize a sensor group."""
        self.hass = hass
        self._entity_ids = entity_ids
        self._sensor_type = sensor_type
        self._attr_state_class = self._calculate_state_class(state_class)
        self._attr_device_class = self._calculate_device_class(device_class)
        self._attr_native_unit_of_measurement = self._calculate_unit_of_measurement(
            unit_of_measurement
        )
        self._attr_name = name
        if name == DEFAULT_NAME:
            self._attr_name = f"{DEFAULT_NAME} {sensor_type}".capitalize()
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: entity_ids}
        self._attr_unique_id = unique_id
        self.mode = all if mode is False else any
        self._state_calc: Callable[
            [list[tuple[str, float, State]]],
            tuple[dict[str, str | None], float | None],
        ] = CALC_TYPES[self._sensor_type]
        self._state_incorrect: set[str] = set()
        self._extra_state_attribute: dict[str, Any] = {}

    @callback
    def async_update_group_state(self) -> None:
        """Query all members and determine the sensor group state."""
        states: list[StateType] = []
        valid_states: list[bool] = []
        sensor_values: list[tuple[str, float, State]] = []
        for entity_id in self._entity_ids:
            if (state := self.hass.states.get(entity_id)) is not None:
                states.append(state.state)
                try:
                    numeric_state = float(state.state)
                    if (device_class := self.device_class) in UNIT_CONVERTERS and (
                        uom := state.attributes["unit_of_measurement"]
                    ) in UNIT_CONVERTERS[device_class].VALID_UNITS:
                        numeric_state = UNIT_CONVERTERS[device_class].convert(
                            numeric_state, uom, self.native_unit_of_measurement
                        )
                    sensor_values.append((entity_id, numeric_state, state))
                    if entity_id in self._state_incorrect:
                        self._state_incorrect.remove(entity_id)
                except ValueError:
                    valid_states.append(False)
                    if entity_id not in self._state_incorrect:
                        self._state_incorrect.add(entity_id)
                        _LOGGER.warning(
                            "Unable to use state. Only numerical states are supported,"
                            " entity %s with value %s excluded from calculation",
                            entity_id,
                            state.state,
                        )
                    continue
                except (KeyError, HomeAssistantError):
                    valid_states.append(False)
                    if entity_id not in self._state_incorrect:
                        self._state_incorrect.add(entity_id)
                        _LOGGER.warning(
                            "Unable to use state. Only entities with correct unit of measurement"
                            " is supported when having a device class,"
                            " entity %s, value %s with device class %s"
                            " and unit of measurement %s excluded from calculation",
                            entity_id,
                            state.state,
                            self.device_class,
                            state.attributes.get("unit_of_measurement"),
                        )
                    continue
                valid_states.append(True)

        # Set group as unavailable if all members do not have numeric values
        self._attr_available = any(numeric_state for numeric_state in valid_states)

        valid_state = self.mode(
            state not in (STATE_UNKNOWN, STATE_UNAVAILABLE) for state in states
        )
        valid_state_numeric = self.mode(numeric_state for numeric_state in valid_states)

        if not valid_state or not valid_state_numeric:
            self._attr_native_value = None
            return

        # Calculate values
        self._extra_state_attribute, self._attr_native_value = self._state_calc(
            sensor_values
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return {ATTR_ENTITY_ID: self._entity_ids, **self._extra_state_attribute}

    @property
    def icon(self) -> str | None:
        """Return the icon.

        Only override the icon if the device class is not set.
        """
        if not self.device_class:
            return "mdi:calculator"
        return None

    def _calculate_state_class(
        self, state_class: SensorStateClass | None
    ) -> SensorStateClass | None:
        """Calculate state class."""
        if state_class:
            return state_class
        state_classes: list[SensorStateClass | None] = []
        for entity_id in self._entity_ids:
            try:
                _state_class = get_capability(self.hass, entity_id, "state_class")
            except HomeAssistantError:
                return None
            if not _state_class:
                return None
            state_classes.append(_state_class)

        if all(x == state_classes[0] for x in state_classes):
            return state_classes[0]
        return None

    def _calculate_device_class(
        self, device_class: SensorDeviceClass | None
    ) -> SensorDeviceClass | None:
        """Calculate device class."""
        if device_class:
            return device_class
        device_classes: list[SensorDeviceClass | None] = []
        for entity_id in self._entity_ids:
            try:
                _device_class = get_device_class(self.hass, entity_id)
            except HomeAssistantError:
                return None
            if not _device_class:
                return None
            device_classes.append(SensorDeviceClass(_device_class))

        if all(x == device_classes[0] for x in device_classes):
            return device_classes[0]
        return None

    def _calculate_unit_of_measurement(
        self, unit_of_measurement: str | None
    ) -> str | None:
        """Calculate the unit of measurement."""
        if unit_of_measurement:
            return unit_of_measurement

        unit_of_measurements: list[str | None] = []
        for entity_id in self._entity_ids:
            try:
                _unit_of_measurement = get_unit_of_measurement(self.hass, entity_id)
            except HomeAssistantError:
                return None
            if not _unit_of_measurement:
                return None
            unit_of_measurements.append(_unit_of_measurement)

        if (device_class := self.device_class) in UNIT_CONVERTERS and any(
            x == unit_of_measurements[0]
            for x in UNIT_CONVERTERS[device_class].VALID_UNITS
        ):
            return unit_of_measurements[0]
        return None
