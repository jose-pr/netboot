"""A DhcpServer backend that prints instead of touching real DHCP.

Run the example with `--load-module recording` so the `recording://` scheme in
the config resolves to this class.
"""

from netboot.dhcp import DhcpServer


class recording(DhcpServer):  # handles recording://...
    def add_target(self, ctx):
        print(f"[dhcp] arm   {ctx.target._id} -> {ctx.target.ip} ({self.uri})")
        print(ctx.render("boot.cfg"))

    def remove_target(self, ctx):
        print(f"[dhcp] disarm {ctx.target._id} ({self.uri})")
