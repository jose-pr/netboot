"""`pixie dhcp-config` — print what the servers need, or apply it.

This is the home for anything privileged: `initiate` runs unattended from hooks
and provisioning systems, so a prompt there would hang a pipeline. Printing
touches nothing, which is what makes it safe to run and to paste into a ticket.
"""

import os

import dhcpctl
import duho
import pytest

import netboot
from netboot.cli import dhcp_config

IPXE = {
    "ipxe": {
        "match": {"user-class": "iPXE"},
        "options": {"boot-file-name": "boot.ipxe"},
    }
}


def _command(target=None, apply=False, run_as=None):
    """The command as `pixie dhcp-config` parses it."""
    argv = [target] if target else []
    argv += ["--apply"] if apply else []
    argv += ["--as", run_as] if run_as else []
    return duho.parse(dhcp_config.DhcpConfig, argv)


def _run(engine, **options):
    command = _command(**options)
    command._netboot_ = engine
    return command()


@pytest.fixture
def backend():
    """Register a backend class under its own name as scheme, unregistered after."""
    registered = []

    def register(cls):
        dhcpctl.register_backend(cls.__name__, cls)
        registered.append(cls.__name__)
        return cls

    yield register
    for scheme in registered:
        dhcpctl.unregister_backend(scheme)


def _engine(servers=("windhcp://dhcp01/?transport=ssh",), when=IPXE):
    image = {"template_path": [], "dhcp_options": {"boot-file-name": "undionly.kpxe"}}
    if when:
        image["dhcp_when"] = dict(when)
    return netboot.Pixie(
        images={"debian": image},
        dhcpzones={"lan": {"network": "10.0.0.0/24", "dhcpservers": list(servers)}},
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    )


def test_it_parses_its_arguments():
    parsed = duho.parse(dhcp_config.DhcpConfig, ["web01", "--apply"])
    assert parsed.target == "web01" and parsed.apply is True
    assert duho.parse(dhcp_config.DhcpConfig, ["--as", "admin"]).run_as == "admin"
    assert duho.parse(dhcp_config.DhcpConfig, []).target is None


def test_printing_contacts_nothing_and_shows_the_recipe(capsys):
    engine = _engine()
    for server in engine.dhcpzones["lan"].dhcpservers:
        server.run = lambda *a, **k: pytest.fail("printing must not contact anything")
    assert (
        _run(
            engine,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "windhcp://dhcp01/?transport=ssh -- dhcp_when.ipxe" in out
    # Target mode: the per-target policy arming this target creates.
    assert "Add-DhcpServerv4Policy -Name 'netboot-aabbccddeeff-ipxe'" in out
    assert "Add-DhcpServerv4Class" in out


def test_a_password_in_the_uri_is_never_printed(capsys):
    engine = _engine(servers=("windhcp://admin:s3cret@dhcp01/?transport=ssh",))
    assert (
        _run(
            engine,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "windhcp://admin:***@dhcp01/?transport=ssh -- dhcp_when.ipxe" in out
    assert "s3cret" not in out


def test_a_config_with_no_conditions_says_so(caplog):
    import logging

    engine = _engine(when=None)
    with caplog.at_level(logging.INFO, logger="netboot"):
        assert (
            _run(
                engine,
            )
            == 0
        )
    assert "nothing is needed" in caplog.text


def test_one_target_can_be_named(capsys):
    assert _run(_engine(), target="web01") == 0
    assert "dhcp_when.ipxe" in capsys.readouterr().out


def test_an_unknown_target_is_an_error(caplog):
    assert _run(_engine(), target="nosuch") == 1
    assert "Target not found" in caplog.text


def test_apply_ensures_each_condition(caplog, backend):
    import logging

    applied = []

    @backend
    class applies(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            applied.append(condition.name)
            return condition.name

    engine = _engine(servers=("applies://host/",))
    with caplog.at_level(logging.INFO, logger="netboot"):
        assert _run(engine, apply=True) == 0
    assert applied == ["ipxe"]
    assert "is in place" in caplog.text


def test_apply_reports_a_backend_that_cannot_be_configured(caplog, backend):
    @backend
    class refuses(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            raise dhcpctl.ConditionUnsupportedError("no conditions here")

    engine = _engine(servers=("refuses://host/",))
    assert _run(engine, apply=True) == 1
    assert "no conditions here" in caplog.text


def test_apply_reports_missing_rights_with_the_recipe(caplog, backend):
    @backend
    class needsrights(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            raise dhcpctl.ConditionMissingError(
                "cannot create it", "Add-Something -Name x"
            )

    engine = _engine(servers=("needsrights://host/",))
    assert _run(engine, apply=True) == 1
    assert "Add-Something -Name x" in caplog.text


def test_one_failing_server_does_not_stop_the_others(caplog, backend):
    applied = []

    @backend
    class good(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            applied.append(self.uri)
            return condition.name

    @backend
    class bad(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            raise dhcpctl.ConditionUnsupportedError("nope")

    engine = _engine(servers=("bad://a", "good://b"))
    assert _run(engine, apply=True) == 1
    assert applied == ["good://b"]


# -- the credential prompt -------------------------------------------------


def test_as_without_a_terminal_says_where_the_password_goes(monkeypatch, caplog):
    monkeypatch.setattr(dhcp_config, "_console_stdin", lambda: False)
    assert _run(_engine(), run_as="admin") == 1
    assert "dhcpservers entry" in caplog.text


def test_as_prompts_once_and_keeps_the_password_out_of_the_log(
    monkeypatch, caplog, capsys, backend
):
    monkeypatch.setattr(dhcp_config, "_console_stdin", lambda: True)
    prompts = []
    monkeypatch.setattr(
        dhcp_config.getpass,
        "getpass",
        lambda prompt="": prompts.append(prompt) or "s3cret",
    )

    applied = []

    @backend
    class applies2(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            applied.append((self.secret("user"), self.secret("password")))
            return condition.name

    engine = _engine(servers=("applies2://host/",))
    assert _run(engine, run_as="admin") == 0
    assert len(prompts) == 1 and "admin" in prompts[0]
    # It reaches the backend, as that server's secrets and nobody else's...
    assert applied == [("admin", "s3cret")]
    assert "s3cret" not in str(os.environ)
    # ... and nowhere else.
    assert "s3cret" not in caplog.text
    assert "s3cret" not in capsys.readouterr().out


def test_an_empty_password_applies_nothing(monkeypatch, caplog, backend):
    monkeypatch.setattr(dhcp_config, "_console_stdin", lambda: True)
    monkeypatch.setattr(dhcp_config.getpass, "getpass", lambda prompt="": "")
    applied = []

    @backend
    class never(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            applied.append(condition.name)

    engine = _engine(servers=("never://host/",))
    assert _run(engine, run_as="admin") == 1
    assert applied == []
    assert "No password given" in caplog.text


def test_as_overrides_the_user_the_uri_names(monkeypatch, backend):
    monkeypatch.setattr(dhcp_config, "_console_stdin", lambda: True)
    monkeypatch.setattr(dhcp_config.getpass, "getpass", lambda prompt="": "s3cret")
    applied = []

    @backend
    class named(dhcpctl.DHCPServer):
        def ensure_condition(self, reservation, condition):
            applied.append((self.secret("user"), self.secret("password")))
            return condition.name

    engine = _engine(servers=("named://svc:old@host/",))
    assert _run(engine, run_as="admin") == 0
    assert applied == [("admin", "s3cret")]


def test_the_null_device_is_no_terminal_to_prompt_on():
    # Windows' NUL reports isatty(); a getpass prompt on it never returns.
    import subprocess
    import sys

    probe = "from netboot.cli import dhcp_config; print(dhcp_config._console_stdin())"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert result.stdout.strip() == "False"


def _shared_engine(**targets):
    return netboot.Pixie(
        images={"debian": {"template_path": [], "dhcp_when": dict(IPXE)}},
        dhcpzones={"lan": {"network": "10.0.0.0/24", "dhcpservers": ["Shared://x"]}},
        targets=targets,
    )


class Shared(dhcpctl.DHCPServer):
    """One recipe for every target, as a shared condition has."""

    def add(self, reservation): ...

    def remove(self, reservation): ...

    def condition_recipe(self, reservation, condition):
        return f"class {condition.name}"


def test_every_target_mode_skips_a_target_without_a_zone(backend, capsys, caplog):
    backend(Shared)
    engine = _shared_engine(
        **{
            "aa:bb:cc:dd:ee:01": {"image": "debian"},  # no ip, no zone
            "web01": {"ip": "10.0.0.10", "mac": "aa-bb-cc-dd-ee-02", "image": "debian"},
        }
    )
    assert (
        _run(
            engine,
        )
        == 0
    )
    assert "skipping aa:bb:cc:dd:ee:01" in caplog.text
    assert "class ipxe" in capsys.readouterr().out


def test_a_recipe_targets_share_is_printed_once(backend, capsys):
    backend(Shared)
    engine = _shared_engine(
        web01={"ip": "10.0.0.10", "mac": "aa-bb-cc-dd-ee-01", "image": "debian"},
        web02={"ip": "10.0.0.11", "mac": "aa-bb-cc-dd-ee-02", "image": "debian"},
    )
    assert (
        _run(
            engine,
        )
        == 0
    )
    assert capsys.readouterr().out.count("class ipxe") == 1
