"""The `extras()` extension point: extra commands and conditions per backend.

The worked example throughout is the iPXE chainload, because it is the reason
this hook exists: a PXE ROM must be handed the iPXE binary, and iPXE itself must
then be handed the script, or it loads itself forever. No backend models that, so
each one has to be told in its own language -- from config (`raw.<backend>`) or
from a subclass that can see the target.
"""

import pytest

import netboot
from netboot.dhcp import DhcpServer
from netboot.dhcp.options import PHASES

#: What each backend is told, in its own language, to chainload iPXE.
IPXE_DHCPD = (
    'if exists user-class and option user-class = "iPXE" '
    '{ filename "boot.ipxe"; } else { filename "undionly.kpxe"; }'
)
IPXE_DNSMASQ = ("dhcp-match=set:ipxe,77,iPXE", "dhcp-boot=tag:ipxe,boot.ipxe")


def _engine(**overrides):
    config = {
        "images": {
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
            }
        },
        "dhcpzones": {"lan": {"network": "10.0.0.0/24", "gateway": "10.0.0.1"}},
        "targets": {
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    }
    config.update(overrides)
    return netboot.Pixie(**config)


def _ctx(engine=None):
    engine = engine or _engine()
    return engine.make_context(engine.lookup_target("web01"))


# -- the hook itself --------------------------------------------------------


def test_extras_defaults_to_the_configured_raw_lines():
    server = DhcpServer("dhcpd://key@dhcp01/?raw.dhcpd=" + "log(concat(1));")
    assert server.extras(_ctx(), "add") == ["log(concat(1));"]
    # A bare `raw.<backend>` belongs to arming, not teardown.
    assert server.extras(_ctx(), "remove") == []


def test_a_raw_fragment_can_name_the_teardown_phase():
    server = DhcpServer("dhcpd://key@dhcp01/?raw.dhcpd.remove=" + "log(concat(2));")
    assert server.extras(_ctx(), "add") == []
    assert server.extras(_ctx(), "remove") == ["log(concat(2));"]


def test_both_phases_can_be_given_at_once():
    server = DhcpServer(
        "dhcpd://key@dhcp01/?raw.dhcpd=a;&raw.dhcpd.add=b;&raw.dhcpd.remove=c;"
    )
    # The unsuffixed one first: it was written as "the" fragment.
    assert server.extras(_ctx(), "add") == ["a;", "b;"]
    assert server.extras(_ctx(), "remove") == ["c;"]


def test_an_unknown_phase_is_refused():
    server = DhcpServer("dhcpd://key@dhcp01/")
    with pytest.raises(ValueError, match="phase must be one of"):
        server.extras(_ctx(), "bootstrap")
    assert PHASES == ("add", "remove")


def test_a_subclass_can_decide_per_target():
    # The hook's reason for existing: config cannot look at the target.
    class ipxe(DhcpServer):  # scheme `ipxe://`
        applied: list = []

        def extras(self, ctx, phase="add"):
            lines = super().extras(ctx, phase)
            if phase == "add" and getattr(ctx.image, "_id", "") == "debian":
                lines.append(IPXE_DHCPD)
            return lines

        def add_target(self, ctx):
            ipxe.applied = self.extras(ctx, "add")

    server = DhcpServer("ipxe://host/?raw.ipxe=" + "authoritative;")
    server.add_target(_ctx())
    assert ipxe.applied == ["authoritative;", IPXE_DHCPD]


# -- dhcpd ------------------------------------------------------------------


def test_dhcpd_emits_the_conditional_in_its_statements(monkeypatch):
    pytest.importorskip("pypureomapi")
    server = DhcpServer("dhcpd://secret@dhcp01/?raw.dhcpd=" + IPXE_DHCPD)
    captured = {}
    monkeypatch.setattr(
        server, "_add_host", lambda **kwargs: captured.update(kwargs), raising=False
    )
    sent = []
    monkeypatch.setattr(
        server, "run", lambda *a, **k: sent.append((a, k)), raising=False
    )

    from netboot.dhcp.dhcpd import render_statements

    statements = render_statements(
        server.options_for(_ctx()), server.extras(_ctx(), "add")
    )
    # The modelled option is still there, and the escape hatch follows it, so a
    # conditional written last is what wins in dhcpd.
    assert 'filename "undionly.kpxe";' in statements
    assert statements.index("undionly.kpxe") < statements.index("user-class")
    assert IPXE_DHCPD in statements


# -- windhcp ----------------------------------------------------------------


def _windhcp(uri):
    server = DhcpServer(uri)
    ran = []
    server.run = lambda payload, body: ran.append((payload, body))
    return server, ran


def test_windhcp_cmdlets_take_an_extra_powershell_line():
    server, ran = _windhcp(
        "windhcp://dhcp01/?raw.windhcp=" + "Add-DhcpServerv4Policy+-Name+ipxe"
    )
    server.add_target(_ctx())
    assert "Add-DhcpServerv4Policy -Name ipxe" in ran[0][1]


def test_windhcp_removal_gets_its_own_phase():
    server, ran = _windhcp(
        "windhcp://dhcp01/?raw.windhcp.remove=" + "Remove-DhcpServerv4Policy+-Name+ipxe"
    )
    server.add_target(_ctx())
    server.remove_target(_ctx())
    assert not any("Remove-DhcpServerv4Policy" in line for line in ran[0][1])
    assert "Remove-DhcpServerv4Policy -Name ipxe" in ran[1][1]


def test_windhcp_netsh_takes_an_extra_netsh_command():
    class ipxe_netsh(DhcpServer):
        pass

    server, ran = _windhcp("windhcp://dhcp01/?method=netsh")
    server.extras = lambda ctx, phase="add": (
        [{"Args": ["dhcp", "server", "add", "policy", "ipxe"], "Ignore": False}]
        if phase == "add"
        else []
    )
    server.add_target(_ctx())
    commands = [c["Args"] for c in ran[0][0]["Commands"]]
    # Last, so the reservation it refers to already exists.
    assert commands[-1] == ["dhcp", "server", "add", "policy", "ipxe"]


def test_a_netsh_command_from_config_is_split_on_whitespace():
    # A query string has nowhere to put a list, so a raw netsh line is accepted
    # as one string and split.
    server, ran = _windhcp("windhcp://dhcp01/?method=netsh")
    server.extras = lambda ctx, phase="add": (
        [{"Args": "dhcp server add policy ipxe"}] if phase == "add" else []
    )
    server.add_target(_ctx())
    assert ran[0][0]["Commands"][-1]["Args"] == [
        "dhcp",
        "server",
        "add",
        "policy",
        "ipxe",
    ]


def test_a_netsh_command_still_runs_under_the_cmdlet_method():
    # An override that knows netsh must not be silently dropped when the method
    # is powershell: it becomes the netsh call it describes.
    server, ran = _windhcp("windhcp://dhcp01/")
    server.extras = lambda ctx, phase="add": (
        [{"Args": ["dhcp", "server", "add", "policy", "it's"], "Ignore": False}]
        if phase == "add"
        else []
    )
    server.add_target(_ctx())
    body = "\n".join(ran[0][1])
    assert "& netsh 'dhcp' 'server' 'add' 'policy' 'it''s'" in body
    assert "$LASTEXITCODE" in body


def test_an_ignored_netsh_command_does_not_abort_the_cmdlet_script():
    server, ran = _windhcp("windhcp://dhcp01/")
    server.extras = lambda ctx, phase="add": (
        [{"Args": ["dhcp", "server", "delete", "policy", "ipxe"], "Ignore": True}]
        if phase == "add"
        else []
    )
    server.add_target(_ctx())
    body = "\n".join(ran[0][1])
    assert "Out-Null" in body and "throw" not in body.split("delete")[-1]


# -- dnsmasq and kea --------------------------------------------------------


def test_dnsmasq_writes_the_extra_lines_into_its_options_file(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.mkdir()
    opts = tmp_path / "opts"
    opts.mkdir()
    uri = (
        f"dnsmasq:///?hostsfile={hosts.as_posix()}&optsfile={opts.as_posix()}"
        "&raw.dnsmasq=" + IPXE_DNSMASQ[0] + "&raw.dnsmasq=" + IPXE_DNSMASQ[1]
    )
    server = DhcpServer(uri)
    server.add_target(_ctx())
    written = "\n".join(p.read_text() for p in opts.iterdir())
    for line in IPXE_DNSMASQ:
        assert line in written


def test_kea_appends_the_extra_option_data():
    # subnet_id belongs to the zone, not the connection -- it is per target.
    engine = _engine(
        dhcpzones={"lan": {"network": "10.0.0.0/24", "subnet_id": 1}},
    )
    server = DhcpServer("kea://ctrl01/?raw.kea=" + "not-a-real-option")
    reservation = server._reservation(_ctx(engine))
    assert "not-a-real-option" in reservation["option-data"]
