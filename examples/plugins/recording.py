"""A DHCP backend that prints instead of touching real DHCP.

Run the example with `--load-module recording` so the `recording://` scheme in
the config resolves to this class: importing the module registers it.
"""

import dhcpctl


class RecordingBackend(dhcpctl.DHCPServer):
    def add(self, reservation):
        target, ctx = reservation.target, reservation.context
        print(f"[dhcp] arm   {target.name} -> {target.ip} ({self.uri})")
        print(ctx.render("boot.cfg"))

    def remove(self, reservation):
        print(f"[dhcp] disarm {reservation.target.name} ({self.uri})")


dhcpctl.register_backend("recording", RecordingBackend, replace=True)
