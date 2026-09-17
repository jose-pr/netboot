"""The ISC dhcpd backend, against a stub OMAPI connection.

`statements` is a fragment of dhcpd's config language, so most of these tests
are about values that must not be able to end a statement and start another.
"""

import pytest

import netboot
from netboot.dhcp import DhcpServer
from netboot.dhcp.dhcpd import render_statements

pytest.importorskip("pypureomapi", reason="the dhcpd backend needs netboot[dhcpd]")


class _StubOmapi:
    """Records what the backend would have sent to dhcpd."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.added: list = []
        self.deleted: list = []
        self.closed = False
        self.raise_on_delete = None
        _StubOmapi.instances.append(self)

    def add_host_supersede(self, ip, mac, name, **kwargs):
        self.added.append((ip, mac, name, kwargs))

    def del_host(self, mac):
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        self.deleted.append(mac)

    def close(self):
        self.closed = True


@pytest.fixture
def stub(monkeypatch):
    _StubOmapi.instances = []

    def build(uri="dhcpd://10.0.0.1:7911/"):
        server = DhcpServer(uri)
        monkeypatch.setattr(server, "connect", lambda: _StubOmapi())
        return server

    return build


def _engine(**overrides):
    config = {
        "images": {"debian": {"template_path": []}},
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


def _ctx(engine):
    return engine.make_context(engine.lookup_target("web01"))


def test_add_sends_one_host_with_rendered_statements(stub):
    server = stub("dhcpd://10.0.0.1:7911/?domain-name=example.com")
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {
                    "boot-file-name": "pxelinux.0",
                    "next-server": "10.0.0.2",
                },
            }
        }
    )
    server.add_target(_ctx(engine))

    connection = _StubOmapi.instances[-1]
    assert connection.closed
    ip, mac, name, kwargs = connection.added[0]
    assert (ip, mac, name) == ("10.0.0.10", "aa:bb:cc:dd:ee:ff", "web01")
    statements = kwargs["statements"]
    assert 'filename "pxelinux.0";' in statements
    assert "next-server 10.0.0.2;" in statements
    assert 'option domain-name "example.com";' in statements
    assert "option routers 10.0.0.1;" in statements  # dhcpd's plural spelling


def test_remove_deletes_by_mac_and_tolerates_a_missing_host(stub):
    import pypureomapi

    server = stub()
    server.remove_target(_ctx(_engine()))
    assert _StubOmapi.instances[-1].deleted == ["aa:bb:cc:dd:ee:ff"]

    server2 = stub()
    engine = _engine()
    ctx = _ctx(engine)
    connection = _StubOmapi()
    connection.raise_on_delete = pypureomapi.OmapiErrorNotFound()
    server2.connect = lambda: connection
    server2.remove_target(ctx)  # must not raise
    assert connection.closed


def test_another_omapi_error_propagates(stub):
    import pypureomapi

    server = stub()
    connection = _StubOmapi()
    connection.raise_on_delete = pypureomapi.OmapiError("server said no")
    server.connect = lambda: connection
    with pytest.raises(pypureomapi.OmapiError):
        server.remove_target(_ctx(_engine()))


@pytest.mark.parametrize(
    "hostile",
    [
        'x"; option domain-name-servers 6.6.6.6; #',
        'x\\; filename "evil"',
        'plain " quote',
    ],
)
def test_a_hostile_text_value_cannot_end_its_statement(hostile):
    from netboot.dhcp.options import DhcpOptions

    statements = render_statements(DhcpOptions({"domain-name": hostile}))
    # Exactly one statement: one unescaped `;` at the very end.
    body = statements.rstrip(";")
    assert ";" not in _outside_quotes(body), statements
    assert statements.startswith('option domain-name "')
    assert statements.endswith('";')


def _outside_quotes(text: str) -> str:
    """The parts of `text` that are not inside a quoted, escaped string."""
    out, inside, escaped = [], False, False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            inside = not inside
            continue
        if not inside:
            out.append(ch)
    return "".join(out)


def test_a_newline_in_a_value_is_refused():
    from netboot.dhcp.options import DhcpOptions

    with pytest.raises(netboot.PixieConfigError, match="newline"):
        render_statements(DhcpOptions({"domain-name": "one\ntwo"}))


def test_a_non_address_where_an_address_belongs_is_refused():
    from netboot.dhcp.options import DhcpOptions

    with pytest.raises(netboot.PixieConfigError, match="address or number"):
        render_statements(DhcpOptions({"router": "not-an-address"}))


def test_raw_lines_are_appended_verbatim():
    from netboot.dhcp.options import DhcpOptions

    statements = render_statements(
        DhcpOptions({"router": "10.0.0.1"}), ['ddns-hostname "web01";']
    )
    assert statements.endswith('ddns-hostname "web01";')


def test_a_numeric_option_becomes_a_dhcp_option_code():
    from netboot.dhcp.options import DhcpOptions

    assert "option dhcp-option-252" in render_statements(
        DhcpOptions({"option-252": "10.0.0.2"})
    )


def test_a_keyname_without_a_secret_is_reported(monkeypatch):
    monkeypatch.delenv("PIXIE_DHCPD_OMAPI_KEY", raising=False)
    server = DhcpServer("dhcpd://10.0.0.1:7911/?keyname=omapi_key")
    with pytest.raises(netboot.PixieConfigError, match="PIXIE_DHCPD_OMAPI_KEY"):
        server.connect()


def test_a_target_without_a_mac_is_named(stub, monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    server = stub()
    engine = _engine(
        targets={"nomac": {"ip": "10.0.0.11", "image": "debian", "dhcpzone": "lan"}}
    )
    ctx = engine.make_context(engine.lookup_target("nomac"))
    with pytest.raises(netboot.PixieLookupError, match="nomac"):
        server.add_target(ctx)
