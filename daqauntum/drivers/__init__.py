"""DaQauntum v0.4.1 device driver SDK.

Optional adapters for approved hardware and IoT sources. Every driver is
opt-in, scoped to an explicit allowlist, read-only unless writes are separately
enabled, and unable to publish events itself - the DriverManager does that, so
the physical world reaches DaQauntum through one audited path.
"""

from drivers.base import (
    Capability,
    DeviceDriver,
    DriverError,
    DriverNotConfigured,
    DriverStatus,
    DriverUnavailable,
    DriverWriteNotPermitted,
)
from drivers.ble import BLEDriver
from drivers.homeassistant import HomeAssistantDriver
from drivers.manager import DriverManager
from drivers.mqtt_subscriber import MQTTSubscriberDriver, topic_matches
from drivers.parsers import PARSERS, parse
from drivers.serial_sensor import SerialSensorDriver

__all__ = [
    "PARSERS",
    "BLEDriver",
    "Capability",
    "DeviceDriver",
    "DriverError",
    "DriverManager",
    "DriverNotConfigured",
    "DriverStatus",
    "DriverUnavailable",
    "DriverWriteNotPermitted",
    "HomeAssistantDriver",
    "MQTTSubscriberDriver",
    "SerialSensorDriver",
    "parse",
    "topic_matches",
]
