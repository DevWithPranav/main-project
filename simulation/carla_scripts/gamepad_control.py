"""Gamepad (Xbox-style controller) input for the manual drone flight scripts
(record_flight.py). Uses pygame, which is already in the carlaAir conda env.

Every pygame call must happen on the thread that created the Gamepad — SDL's
event pump has to run on the thread that initialised it — so record_flight.py
builds it inside its control thread, not the main thread.

Layout:
    Left stick      up/down = forward/back, left/right = strafe left/right
    Right stick     left/right = rotate (yaw)
    RT              climb (pressure-sensitive)
    LT              descend (pressure-sensitive)
    LB (hold)       slow, 0.4x speed — for smooth pans while recording
    RB (hold)       fast, 2x speed
    A               take off
    B               land
    Start / Menu    stop and save (same as Esc)

Sticks and triggers are proportional: half-pushed = half speed. Releasing
everything (or unplugging the controller) makes the drone hover. Pressing
both triggers cancels out.

Axis/button numbers below are SDL2's XInput numbering (Xbox controllers on
Windows). PlayStation and generic pads can differ — check yours without CARLA
running, then edit the constants:
    python gamepad_control.py
"""

import os
import time

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")  # keep reading while the CarlaUE4 window has focus
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

AXIS_LEFT_X, AXIS_LEFT_Y, AXIS_RIGHT_X, AXIS_RIGHT_Y = 0, 1, 2, 3
AXIS_LT, AXIS_RT = 4, 5  # rest at -1, fully pressed +1
BUTTON_A, BUTTON_B, BUTTON_LB, BUTTON_RB, BUTTON_START = 0, 1, 4, 5, 7
DEADZONE = 0.15  # worn sticks rest slightly off-centre; ignore anything below this
STEP = 0.1  # sticks are rounded to this, so tiny jitter doesn't resend a flight command every poll
SLOW, FAST = 0.4, 2.0


# Device names pygame enumerates but that aren't a real controller someone is holding: AirSim's
# own install (or other flight-sim tooling) commonly also installs vJoy, a virtual joystick driver
# with no physical input of its own. Found 2026-10-01: on this machine pygame.joystick lists BOTH
# "Xbox 360 Controller" and "vJoy Device", and the old code always opened device index 0 — whichever
# one Windows happened to enumerate first. Binding the real pad vs. the dead virtual one by chance,
# rather than by name, matches "the controller sometimes just doesn't work."
VIRTUAL_DEVICE_MARKERS = ("vjoy", "vxbox", "virtual")


class Gamepad:
    """Reads the first connected REAL controller (skips virtual/vJoy-style devices — see
    VIRTUAL_DEVICE_MARKERS above); handles plugging in / unplugging mid-flight."""

    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        self.joy = None
        self._prev_buttons: set[int] = set()
        idx = self._find_real_device()
        if idx is not None:
            self._open(idx)
        elif pygame.joystick.get_count():
            names = [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]
            print(f"[gamepad] only virtual devices found ({', '.join(names)}) — keyboard only")
        else:
            print("[gamepad] no controller found — keyboard only (plug one in any time)")

    @staticmethod
    def _is_virtual(name: str) -> bool:
        low = name.lower()
        return any(m in low for m in VIRTUAL_DEVICE_MARKERS)

    def _find_real_device(self) -> int | None:
        for i in range(pygame.joystick.get_count()):
            name = pygame.joystick.Joystick(i).get_name()
            if not self._is_virtual(name):
                return i
        return None

    def _open(self, device_index: int) -> None:
        self.joy = pygame.joystick.Joystick(device_index)
        self.joy.init()
        self._prev_buttons = set()
        print(f"[gamepad] connected: {self.joy.get_name()}")

    def _handle_hotplug(self) -> None:
        for e in pygame.event.get():
            if e.type == pygame.JOYDEVICEADDED and self.joy is None:
                name = pygame.joystick.Joystick(e.device_index).get_name()
                if self._is_virtual(name):
                    print(f"[gamepad] ignoring virtual device: {name}")
                    continue
                self._open(e.device_index)
            elif e.type == pygame.JOYDEVICEREMOVED and self.joy is not None \
                    and e.instance_id == self.joy.get_instance_id():
                print("[gamepad] disconnected — hovering")
                self.joy = None

    @staticmethod
    def _shape(v: float) -> float:
        """Deadzone, rescale the rest back to the full range, round to STEP."""
        if abs(v) < DEADZONE:
            return 0.0
        v = (abs(v) - DEADZONE) / (1 - DEADZONE) * (1 if v > 0 else -1)
        return round(v / STEP) * STEP

    def _axis(self, i: int) -> float:
        return self._shape(self.joy.get_axis(i))

    def _trigger(self, i: int) -> float:
        return self._shape((self.joy.get_axis(i) + 1) / 2)  # -1..1 -> 0 (released)..1 (pressed)

    def read(self) -> tuple[tuple[float, float, float, float], set[int]]:
        """Returns ((forward, right, down, yaw), pressed).

        Stick values are in [-1, 1] times the LB/RB speed modifier; forward/right/down
        follow AirSim's body frame (down > 0 descends). `pressed` holds the buttons that
        went down since the previous call, so a held button fires once."""
        self._handle_hotplug()
        if self.joy is None:
            return (0.0, 0.0, 0.0, 0.0), set()
        buttons = {b for b in range(self.joy.get_numbuttons()) if self.joy.get_button(b)}
        pressed = buttons - self._prev_buttons
        self._prev_buttons = buttons
        scale = SLOW if BUTTON_LB in buttons else FAST if BUTTON_RB in buttons else 1.0
        sticks = (
            -self._axis(AXIS_LEFT_Y) * scale,  # stick up = negative axis = forward
            self._axis(AXIS_LEFT_X) * scale,
            (self._trigger(AXIS_LT) - self._trigger(AXIS_RT)) * scale,  # AirSim z points down: RT climbs
            self._axis(AXIS_RIGHT_X) * scale,
        )
        return sticks, pressed


def main() -> None:
    """Print raw axes and buttons, to check a controller's numbering."""
    pad = Gamepad()
    print("Move sticks / press buttons; Ctrl+C to quit.")
    try:
        while True:
            pad._handle_hotplug()
            if pad.joy is not None:
                axes = " ".join(f"{i}:{pad.joy.get_axis(i):+.2f}" for i in range(pad.joy.get_numaxes()))
                buttons = [b for b in range(pad.joy.get_numbuttons()) if pad.joy.get_button(b)]
                print(f"\raxes {axes} | buttons down {buttons}        ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
