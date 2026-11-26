"""GNOME screenshot portal helper, run with Ubuntu's system Python for GIO."""

import sys
from math import ceil, floor
from pathlib import Path
from uuid import uuid4

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQUEST = "org.freedesktop.portal.Request"
DISPLAY_CONFIG = "org.gnome.Mutter.DisplayConfig"
APP_ID = "io.github.tomgu885.TelegramMonitor"
ERRORS = {
    2: "Install the Ubuntu screenshot dependencies listed in the README.",
    3: "Install the Telegram Monitor desktop entry listed in the README.",
    4: "The desktop did not grant a screenshot; run python -m app --check-screenshot.",
    5: "Could not verify the GNOME primary display or save its screenshot.",
}


def primary_crop(state, image_width, image_height):
    """Map Mutter's primary logical monitor into the composite PNG."""
    _, monitors, logical_monitors, properties = state
    if sum(monitor[4] for monitor in logical_monitors) != 1:
        raise ValueError("No unique primary display")
    if len(logical_monitors) == 1:
        return 0, 0, image_width, image_height
    if properties.get("layout-mode") != 1:
        raise ValueError("Expected logical monitor layout")

    resolutions = {
        spec: (width, height)
        for spec, modes, _ in monitors
        for _, width, height, *_, flags in modes
        if flags.get("is-current")
    }
    # The Shell paints the complete stage using its highest view scale.
    scale = max(1.0, *(monitor[2] for monitor in logical_monitors))
    rectangles = []
    for x, y, monitor_scale, rotation, primary, specs, _ in logical_monitors:
        width, height = resolutions[specs[0]]
        if rotation % 2:
            width, height = height, width
        width = floor(width / monitor_scale + 0.5)
        height = floor(height / monitor_scale + 0.5)
        rectangles.append((x, y, x + width, y + height, primary))

    if (
        min(rect[0] for rect in rectangles) != 0
        or min(rect[1] for rect in rectangles) != 0
        or any(rect[0] < 0 or rect[1] < 0 for rect in rectangles)
        or abs(max(rect[2] for rect in rectangles) * scale - image_width) > 0.51
        or abs(max(rect[3] for rect in rectangles) * scale - image_height) > 0.51
    ):
        raise ValueError("Screenshot does not match the display layout")

    left, top, right, bottom, _ = next(rect for rect in rectangles if rect[4])
    # Round inwards so that a fractional boundary cannot include a second display.
    x, y = ceil(left * scale), ceil(top * scale)
    width = floor(right * scale) - x
    height = floor(bottom * scale) - y
    if width <= 0 or height <= 0:
        raise ValueError("Empty primary display")
    return x, y, width, height


def request_screenshot(bus, Gio, GLib, parent_window):
    token = "screenshot_" + uuid4().hex
    sender = bus.get_unique_name()[1:].replace(".", "_")
    expected = f"{PORTAL_PATH}/request/{sender}/{token}"
    loop = GLib.MainLoop()
    response = None

    def on_response(_bus, _sender, _path, _interface, _signal, values):
        nonlocal response
        response = values.unpack()
        loop.quit()

    subscription = bus.signal_subscribe(
        PORTAL, REQUEST, "Response", expected, None, Gio.DBusSignalFlags.NONE, on_response
    )
    try:
        (handle,) = bus.call_sync(
            PORTAL,
            PORTAL_PATH,
            "org.freedesktop.portal.Screenshot",
            "Screenshot",
            GLib.Variant(
                "(sa{sv})",
                (
                    parent_window,
                    {
                        "handle_token": GLib.Variant("s", token),
                        "interactive": GLib.Variant("b", False),
                    },
                ),
            ),
            GLib.VariantType.new("(o)"),
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        ).unpack()
        if handle != expected:
            return None
        # The parent enforces the overall deadline and terminates this connection on timeout.
        loop.run()
        status, results = response
        return results.get("uri") if status == 0 else None
    finally:
        bus.signal_unsubscribe(subscription)


def capture(output: Path, parent_window: str = "") -> int:
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, Gio, GLib
    except (ImportError, ValueError):
        return 2

    def display_state():
        return bus.call_sync(
            DISPLAY_CONFIG,
            "/org/gnome/Mutter/DisplayConfig",
            DISPLAY_CONFIG,
            "GetCurrentState",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        ).unpack()

    try:
        # GTK may already have called a portal on the shared GIO bus connection.
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        state = display_state()
    except GLib.Error:
        return 5
    try:
        bus.call_sync(
            PORTAL,
            PORTAL_PATH,
            "org.freedesktop.host.portal.Registry",
            "Register",
            GLib.Variant("(sa{sv})", (APP_ID, {})),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
    except GLib.Error:
        return 3
    try:
        uri = request_screenshot(bus, Gio, GLib, parent_window)
    except GLib.Error:
        return 4
    if not uri:
        return 4

    try:
        file = Gio.File.new_for_uri(uri)
        if not file.is_native() or not file.get_path():
            return 5
        stream = file.read(None)
        try:
            # Once open, the portal's copy is no longer needed on disk.
            file.delete(None)
            if display_state()[0] != state[0]:
                return 5
            image = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        finally:
            stream.close(None)
        crop = primary_crop(state, image.get_width(), image.get_height())
        image.new_subpixbuf(*crop).savev(str(output), "png", [], [])
    except (GLib.Error, KeyError, ValueError, IndexError):
        return 5
    return 0


def authorize_and_capture(output: Path) -> int:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("GdkWayland", "4.0")
        from gi.repository import GdkWayland, GLib, Gtk
    except (ImportError, ValueError):
        return 2
    if not Gtk.init_check():
        return 4

    # GNOME can present consent to a window exported by the Wayland compositor.
    loop = GLib.MainLoop()
    window = Gtk.Window(title="Telegram Monitor", default_width=340, default_height=90)
    window.set_child(Gtk.Label(label="正在请求主屏截图权限…"))
    status = 4

    def on_exported(_surface, handle):
        nonlocal status
        status = capture(output, f"wayland:{handle}")
        loop.quit()

    def on_close(_window):
        loop.quit()
        return False

    window.connect("close-request", on_close)
    window.present()
    surface = window.get_surface()
    try:
        if isinstance(surface, GdkWayland.WaylandToplevel) and surface.export_handle(on_exported):
            loop.run()
    finally:
        window.destroy()
    return status


if __name__ == "__main__":
    output = Path(sys.argv[-1])
    if sys.argv[1] == "--authorize":
        raise SystemExit(authorize_and_capture(output))
    raise SystemExit(capture(output))
