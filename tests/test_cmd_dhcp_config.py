"""`pixie dhcp-config` — print what the servers need, or apply it.

This is the home for anything privileged: `initiate` runs unattended from hooks
and provisioning systems, so a prompt there would hang a pipeline. Printing
touches nothing, which is what makes it safe to run and to paste into a ticket.
"""

import argparse
import os

import pytest

import netboot
from netboot.cmds import dhcp_config
from netboot.dhcp import DhcpServer
from netboot.dhcp.conditions import ConditionMissing, ConditionUnsupported

IPXE = {
    "ipxe": {
        "match": {"user-class": "iPXE"},
        "options": {"boot-file-name": "boot.ipxe"},
    }
}


def _args(**kwargs):
    defaults = {"target": None, "apply": False, "run_as": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _engine(servers=("windhcp://dhcp01/",), when=IPXE):
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


def test_it_registers_its_arguments():
    parser = argparse.ArgumentParser()
    dhcp_config.register(parser, None)
    parsed = parser.parse_args(["web01", "--apply"])
    assert parsed.target == "web01" and parsed.apply is True
    assert parser.parse_args(["--as", "admin"]).run_as == "admin"


def test_printing_contacts_nothing_and_shows_the_recipe(capsys):
    engine = _engine()
    for server in engine.dhcpzones["lan"].dhcpservers:
        server.run = lambda *a, **k: pytest.fail("printing must not contact anything")
    assert dhcp_config.run(engine, _args(), {}) == 0
    out = capsys.readouterr().out
    assert "windhcp://dhcp01/ -- dhcp_when.ipxe" in out
    assert "Add-DhcpServerv4Policy -Name 'ipxe'" in out
    assert "Add-DhcpServerv4Class" in out


def test_a_config_with_no_conditions_says_so(caplog):
    import logging

    engine = _engine(when=None)
    with caplog.at_level(logging.INFO, logger="netboot"):
        assert dhcp_config.run(engine, _args(), {}) == 0
    assert "nothing is needed" in caplog.text


def test_one_target_can_be_named(capsys):
    assert dhcp_config.run(_engine(), _args(target="web01"), {}) == 0
    assert "dhcp_when.ipxe" in capsys.readouterr().out


def test_an_unknown_target_is_an_error(caplog):
    assert dhcp_config.run(_engine(), _args(target="nosuch"), {}) == 1
    assert "Target not found" in caplog.text


def test_apply_ensures_each_condition(caplog):
    import logging

    applied = []

    class applies(DhcpServer):  # scheme `applies://`
        def ensure_condition(self, ctx, condition):
            applied.append(condition.name)
            return condition.name

    engine = _engine(servers=("applies://host/",))
    with caplog.at_level(logging.INFO, logger="netboot"):
        assert dhcp_config.run(engine, _args(apply=True), {}) == 0
    assert applied == ["ipxe"]
    assert "is in place" in caplog.text


def test_apply_reports_a_backend_that_cannot_be_configured(caplog):
    class refuses(DhcpServer):  # scheme `refuses://`
        def ensure_condition(self, ctx, condition):
            raise ConditionUnsupported("no conditions here")

    engine = _engine(servers=("refuses://host/",))
    assert dhcp_config.run(engine, _args(apply=True), {}) == 1
    assert "no conditions here" in caplog.text


def test_apply_reports_missing_rights_with_the_recipe(caplog):
    class needsrights(DhcpServer):  # scheme `needsrights://`
        def ensure_condition(self, ctx, condition):
            raise ConditionMissing("cannot create it", "Add-Something -Name x")

    engine = _engine(servers=("needsrights://host/",))
    assert dhcp_config.run(engine, _args(apply=True), {}) == 1
    assert "Add-Something -Name x" in caplog.text


def test_one_failing_server_does_not_stop_the_others(caplog):
    applied = []

    class good(DhcpServer):  # scheme `good://`
        def ensure_condition(self, ctx, condition):
            applied.append(self.uri)
            return condition.name

    class bad(DhcpServer):  # scheme `bad://`
        def ensure_condition(self, ctx, condition):
            raise ConditionUnsupported("nope")

    engine = _engine(servers=("bad://a", "good://b"))
    assert dhcp_config.run(engine, _args(apply=True), {}) == 1
    assert applied == ["good://b"]


# -- the credential prompt -------------------------------------------------


def test_as_without_a_terminal_names_the_env_var(monkeypatch, caplog):
    from netboot.dhcp.windhcp import PASSWORD_ENV_VAR

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert dhcp_config.run(_engine(), _args(run_as="admin"), {}) == 1
    assert PASSWORD_ENV_VAR in caplog.text


def test_as_prompts_once_and_keeps_the_password_out_of_the_log(
    monkeypatch, caplog, capsys
):
    from netboot.dhcp.windhcp import PASSWORD_ENV_VAR, USER_ENV_VAR

    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
    monkeypatch.delenv(USER_ENV_VAR, raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    monkeypatch.setattr(
        dhcp_config.getpass,
        "getpass",
        lambda prompt="": prompts.append(prompt) or "s3cret",
    )

    applied = []

    class applies2(DhcpServer):  # scheme `applies2://`
        def ensure_condition(self, ctx, condition):
            applied.append(os.environ.get(PASSWORD_ENV_VAR))
            return condition.name

    engine = _engine(servers=("applies2://host/",))
    assert dhcp_config.run(engine, _args(run_as="admin"), {}) == 0
    assert len(prompts) == 1 and "admin" in prompts[0]
    # It reaches the backend...
    assert applied == ["s3cret"]
    assert os.environ[USER_ENV_VAR] == "admin"
    # ... and nowhere else.
    assert "s3cret" not in caplog.text
    assert "s3cret" not in capsys.readouterr().out


def test_an_empty_password_applies_nothing(monkeypatch, caplog):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(dhcp_config.getpass, "getpass", lambda prompt="": "")
    applied = []

    class never(DhcpServer):  # scheme `never://`
        def ensure_condition(self, ctx, condition):
            applied.append(condition.name)

    engine = _engine(servers=("never://host/",))
    assert dhcp_config.run(engine, _args(run_as="admin"), {}) == 1
    assert applied == []
    assert "No password given" in caplog.text


def test_the_account_can_come_from_the_environment(monkeypatch):
    from netboot.dhcp.windhcp import USER_ENV_VAR

    monkeypatch.setenv(USER_ENV_VAR, "admin")
    assert DhcpServer("windhcp://dhcp01/?transport=winrm").user == "admin"
    # The URI still wins, since it is the more specific statement.
    assert DhcpServer("windhcp://svc@dhcp01/?transport=winrm").user == "svc"
