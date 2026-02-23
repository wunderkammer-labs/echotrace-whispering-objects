"""LED feedback helper for EchoTrace nodes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing_extensions import Protocol

try:  # pragma: no cover - executed when gpiozero is installed
    from gpiozero import PWMLED as _PWMLED  # type: ignore[import]
except ImportError:  # pragma: no cover - executed in test/mock environments
    _PWMLED = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from gpiozero import PWMLED as _GPIOPWMLED  # type: ignore[import]
else:
    _GPIOPWMLED = None


LOGGER = logging.getLogger(__name__)


class _LEDProtocol(Protocol):
    value: float

    def pulse(self, fade_in_time: float, fade_out_time: float) -> None:
        ...

    def blink(self, on_time: float, off_time: float) -> None:
        ...

    def off(self) -> None:
        ...

    def close(self) -> None:
        ...


class _FallbackPWMLED:
    """Very small fallback implementation for environments without gpiozero."""

    def __init__(self, pin: int, frequency: int | None = None) -> None:
        """Initialise a fallback PWM LED on the specified GPIO pin."""
        self.pin = pin
        self.frequency = frequency
        self.value = 0.0

    def pulse(self, fade_in_time: float, fade_out_time: float) -> None:
        self.value = 0.5

    def blink(self, on_time: float, off_time: float) -> None:
        self.value = 0.5

    def off(self) -> None:
        self.value = 0.0

    def close(self) -> None:
        self.off()


class LedFeedback:
    """Simple wrapper around PWMLED supporting glow, blink, and off states."""

    def __init__(self, pin: int, frequency: int = 100) -> None:
        if _PWMLED is not None:
            self._led: _LEDProtocol = _PWMLED(pin, frequency=frequency)
        else:
            self._led = _FallbackPWMLED(pin, frequency=frequency)
        LOGGER.debug("LedFeedback initialised on pin %s", pin)

    def glow(self, level: float) -> None:
        """Set LED brightness to a value between 0 and 1."""
        clamped = max(0.0, min(1.0, level))
        self._led.value = clamped
        LOGGER.debug("LED glow level set to %.2f", clamped)

    def blink(self, on_s: float, off_s: float) -> None:
        """Trigger a simple blink pattern."""
        self._led.blink(on_time=on_s, off_time=off_s)
        LOGGER.debug("LED blink configured (on=%s, off=%s)", on_s, off_s)

    def off(self) -> None:
        """Turn the LED off."""
        self._led.off()
        LOGGER.debug("LED turned off")

    def close(self) -> None:
        """Release the underlying LED resource."""
        self._led.close()


__all__ = ["LedFeedback"]
