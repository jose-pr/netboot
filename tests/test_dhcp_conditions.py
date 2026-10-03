"""`dhcp_when:` — named conditions, and what each backend makes of one.

The worked example is the iPXE chainload: serve the iPXE binary to a PXE ROM and
the script to iPXE, or iPXE loads itself forever. The point of the feature is to
say that **once**, under a name, and have each backend use the name for its own
construct — a Windows policy, a dhcpd group or inline `if`, a Kea client class.
"""

import json
import logging

import pytest

import netboot
from netboot.dhcp import DhcpServer
from netboot.dhcp.conditions import (
    MATCH_KEYS,
    ConditionMissing,
    ConditionUnsupported,
    DhcpCondition,
    build_conditions,
)

IPXE = {
    "ipxe": {
        "match": {"user-class": "iPXE"},
        "options": {"boot-file-name": "boot.ipxe"},
    }
}


def _engine(**overrides):
    config = {
        "images": {
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
                "dhcp_when": dict(IPXE),
            }
        },
        "dhcpzones": {"lan": {"network": "10.0.0.0/24", "subnet_id": 1}},
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


def _ctx(engine=None, target="web01"):
    engine = engine or _engine()
    return engine.make_context(engine.lookup_target(target))


# -- the model --------------------------------------------------------------


def test_a_condition_is_keyed_by_the_name_backends_will_use():
    conditions = build_conditions(_ctx())
    assert list(conditions) == ["ipxe"]
    assert conditions["ipxe"].name == "ipxe"
    assert conditions["ipxe"].match == {"user-class": "iPXE"}
    assert conditions["ipxe"].option_codes == {77: "iPXE"}


def test_a_target_overrides_an_image_condition_of_the_same_name():
    # Keying by name is what makes this a replacement rather than a second
    # policy that also matches.
    engine = _engine(
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
                "dhcp_when": {
                    "ipxe": {
                        "match": {"user-class": "iPXE"},
                        "options": {"boot-file-name": "special.ipxe"},
                    }
                },
            }
        }
    )
    conditions = build_conditions(_ctx(engine))
    assert list(conditions) == ["ipxe"]
    assert conditions["ipxe"].options["boot-file-name"] == "special.ipxe"


def test_a_zone_condition_and_an_image_condition_both_apply():
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcp_when": {
                    "winpe": {
                        "match": {"vendor-class": "PXEClient:Arch:00000"},
                        "options": {"boot-file-name": "wdsnbp.com"},
                    }
                },
            }
        }
    )
    assert sorted(build_conditions(_ctx(engine))) == ["ipxe", "winpe"]


@pytest.mark.parametrize(
    "entry, message",
    [
        ("not-a-mapping", "must be a mapping"),
        ({"match": {}, "options": {"boot-file-name": "x"}}, "non-empty `match`"),
        ({"match": {"user-class": "iPXE"}}, "non-empty `options`"),
        (
            {"match": {"hostname": "x"}, "options": {"boot-file-name": "y"}},
            "cannot match",
        ),
        (
            {"match": {"user-class": "x"}, "options": {"nonsense": "y"}},
            "unknown dhcp option",
        ),
        (
            {
                "match": {"user-class": "x"},
                "options": {"boot-file-name": "y"},
                "zzz": 1,
            },
            "unknown key",
        ),
    ],
)
def test_a_malformed_condition_names_the_mistake(entry, message):
    engine_args = {
        "images": {"debian": {"template_path": [], "dhcp_when": {"bad": entry}}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    }
    with pytest.raises(netboot.PixieConfigError, match=message):
        build_conditions(_ctx(netboot.Pixie(**engine_args)))


def test_a_name_that_no_backend_could_use_is_refused():
    # The name reaches a PowerShell argument, a dhcpd identifier and a Kea class
    # name; it is kept to the intersection rather than quoted three ways.
    engine = netboot.Pixie(
        images={
            "debian": {
                "template_path": [],
                "dhcp_when": {
                    "bad name": {
                        "match": {"user-class": "x"},
                        "options": {"boot-file-name": "y"},
                    }
                },
            }
        },
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={
            "web01": {
                "hostname": "web01",
                "ip": "10.0.0.10",
                "mac": "aa:bb:cc:dd:ee:ff",
                "image": "debian",
            }
        },
    )
    with pytest.raises(netboot.PixieConfigError, match="letters, digits"):
        build_conditions(_ctx(engine))


def test_the_accepted_match_keys_are_the_ones_every_backend_can_test():
    assert set(MATCH_KEYS) == {"user-class", "vendor-class"}


# -- windhcp: a scope-level policy -----------------------------------------


def _windhcp(uri="windhcp://dhcp01/"):
    server = DhcpServer(uri)
    sent = []
    server.run = lambda payload, body: sent.append((payload, body))
    return server, sent


def _payload_of(sent, key):
    return next(payload for payload, _ in sent if key in payload)


def test_windhcp_creates_the_policy_before_the_reservation():
    server, sent = _windhcp()
    server.add_target(_ctx())
    assert "Policies" in sent[0][0], "the policy script must come first"
    assert "IPAddress" in sent[1][0]
    policy = _payload_of(sent, "Policies")["Policies"][0]
    # Per target by default, and named for the MAC, because a Windows policy is
    # scope-level and the MAC is the only condition that narrows it to one host.
    assert policy["Name"] == "netboot-aabbccddeeff-ipxe"
    assert policy["UserClass"] == "iPXE"
    assert policy["MacAddress"] == "aabbccddeeff"
    assert policy["Options"] == [{"Id": 67, "Value": ["boot.ipxe"]}]


def test_an_overridden_option_is_kept_off_the_reservation():
    # The rule that shapes all of this: a reservation's option outranks every
    # policy (reservation > scope policy > server policy > scope > server, per
    # option), so leaving 67 on the reservation would make the condition dead and
    # the server's configuration would look perfectly correct while doing it.
    server, sent = _windhcp()
    server.add_target(_ctx())
    reservation = _payload_of(sent, "IPAddress")
    ids = [option["Id"] for option in reservation["Options"]]
    assert 67 not in ids, "the conditioned option must come from a policy"
    # Everything no condition touches stays where it was (1 = subnet mask, which
    # the zone's network always yields).
    assert 1 in ids


def test_the_base_policy_serves_what_the_reservation_no_longer_can():
    server, sent = _windhcp()
    server.add_target(_ctx())
    policies = _payload_of(sent, "Policies")["Policies"]
    conditional, base = policies[0], policies[1]
    assert base["Name"] == "netboot-aabbccddeeff"
    assert base["UserClass"] == "" and base["MacAddress"] == "aabbccddeeff"
    assert base["Options"] == [{"Id": 67, "Value": ["undionly.kpxe"]}]
    # Order is explicit: the conditional policy must be consulted first, or the
    # base one answers an iPXE client and the chainload never happens.
    assert conditional["Order"] < base["Order"]


def test_the_netsh_method_also_keeps_the_option_off_the_reservation():
    server, sent = _windhcp("windhcp://dhcp01/?method=netsh")
    server.add_target(_ctx())
    commands = _payload_of(sent, "Commands")["Commands"]
    for command in commands:
        if "reservedoptionvalue" in command["Args"]:
            assert "67" not in command["Args"], "67 must come from the policy"


def test_shared_mode_has_one_policy_for_every_target_and_no_base():
    # The netsh-only shape: an administrator creates one policy per condition
    # once, rather than one per machine forever. netboot still keeps the option
    # off the reservation, but the base value is then not netboot's to serve.
    server, sent = _windhcp("windhcp://dhcp01/?conditions=shared")
    server.add_target(_ctx())
    policies = _payload_of(sent, "Policies")["Policies"]
    assert [p["Name"] for p in policies] == ["ipxe"]
    assert policies[0]["MacAddress"] == ""
    ids = [o["Id"] for o in _payload_of(sent, "IPAddress")["Options"]]
    assert 67 not in ids


def test_an_unknown_conditions_mode_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="target or shared"):
        DhcpServer("windhcp://dhcp01/?conditions=global")


def test_completing_a_kept_target_removes_only_its_own_policies():
    server, sent = _windhcp()
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
                "dhcp_when": dict(IPXE),
                "dhcp_complete": {"keep": True},
            }
        }
    )
    server.complete_target(_ctx(engine))
    names = _payload_of(sent, "Names")["Names"]
    assert names == ["netboot-aabbccddeeff-ipxe", "netboot-aabbccddeeff"]
    assert "Remove-DhcpServerv4Policy" in "\n".join(sent[0][1])


def test_shared_policies_are_never_removed_on_completion():
    server, sent = _windhcp("windhcp://dhcp01/?conditions=shared")
    engine = _engine(
        images={
            "debian": {
                "template_path": [],
                "dhcp_options": {"boot-file-name": "undionly.kpxe"},
                "dhcp_when": dict(IPXE),
                "dhcp_complete": {"keep": True},
            }
        }
    )
    server.complete_target(_ctx(engine))
    # Only the reservation rewrite; the estate's policy is left alone.
    assert not any("Names" in payload for payload, _ in sent)


def test_windhcp_defines_the_user_class_the_policy_needs():
    # Measured on Windows Server 2025: Add-DhcpServerv4Policy refuses a class it
    # does not know ("The specified User class iPXE does not exist").
    server, sent = _windhcp()
    server.add_target(_ctx())
    policy = _payload_of(sent, "Policies")["Policies"][0]
    assert policy["Classes"] == [{"Name": "iPXE", "Type": "User", "Data": "iPXE"}]
    body = "\n".join(_payload_of(sent, "Policies") and sent[0][1])
    assert "Add-DhcpServerv4Class" in body and "Get-DhcpServerv4Class" in body


def test_windhcp_only_creates_a_policy_that_is_absent():
    server, sent = _windhcp()
    server.add_target(_ctx())
    body = "\n".join(sent[0][1])
    assert "Get-DhcpServerv4Policy" in body
    assert "if (-not $existing)" in body


def test_windhcp_policies_go_through_the_cmdlets_whatever_the_method():
    # netsh has no policy verb at all (measured), so `method=netsh` still uses
    # the cmdlets for this part and only the reservation goes through netsh.
    server, sent = _windhcp("windhcp://dhcp01/?method=netsh")
    server.add_target(_ctx())
    assert "Add-DhcpServerv4Policy" in "\n".join(sent[0][1])
    assert "Commands" in sent[1][0]


def test_windhcp_explains_a_host_without_the_dhcpserver_module():
    server, _ = _windhcp()

    def _no_cmdlets(payload, body):
        if "Policies" in payload:
            raise RuntimeError("NETBOOT-NO-POLICY-CMDLETS")

    server.run = _no_cmdlets
    with pytest.raises(ConditionMissing) as excinfo:
        server.add_target(_ctx())
    message = str(excinfo.value)
    assert "no DhcpServer PowerShell module" in message
    assert "Add-DhcpServerv4Policy -Name 'ipxe'" in message
    assert "Add-DhcpServerv4Class" in message


def test_the_windhcp_recipe_is_the_text_the_error_carries():
    server, _ = _windhcp()
    ctx = _ctx()
    recipe = server.condition_recipe(ctx, build_conditions(ctx)["ipxe"])
    assert "Add-DhcpServerv4Policy -Name 'ipxe'" in recipe
    assert "-UserClass EQ,'iPXE'" in recipe
    assert "Set-DhcpServerv4OptionValue -PolicyName 'ipxe'" in recipe


# -- dhcpd: an inline `if`, or a group -------------------------------------


class _FakeOmapi:
    def __init__(self, calls):
        self.calls = calls

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def _dhcpd(uri="dhcpd://secret@dhcp01/"):
    server = DhcpServer(uri)
    calls = []
    server.connect = lambda: _FakeOmapi(calls)
    return server, calls


def test_dhcpd_inlines_the_conditional_by_default():
    server, calls = _dhcpd()
    server.add_target(_ctx())
    statements = calls[0][2]["statements"]
    assert 'filename "undionly.kpxe";' in statements
    assert 'if exists user-class and option user-class = "iPXE"' in statements
    # The unconditional option comes first, so the conditional overrides it.
    assert statements.index("undionly.kpxe") < statements.index("user-class")
    assert not any(call[0] == "add_group" for call in calls)


def test_dhcpd_group_mode_creates_the_group_and_attaches_the_host():
    server, calls = _dhcpd("dhcpd://secret@dhcp01/?conditions=group")
    server.add_target(_ctx())
    names = [call[0] for call in calls]
    assert names == ["add_group", "add_host_supersede", "change_group", "close"]
    assert calls[0][1][0] == "ipxe"
    assert "if exists user-class" in calls[0][1][1]
    # The host joins after it exists: change_group takes the host's name.
    assert calls[2][1] == ("web01", "ipxe")


def test_dhcpd_group_mode_tolerates_a_group_that_is_already_there(caplog):
    server, calls = _dhcpd("dhcpd://secret@dhcp01/?conditions=group")

    class _Existing(_FakeOmapi):
        def add_group(self, name, statements):
            raise RuntimeError("group already exists")

    server.connect = lambda: _Existing(calls)
    with caplog.at_level(logging.DEBUG, logger="netboot"):
        server.add_target(_ctx())
    assert any(call[0] == "change_group" for call in calls)


def test_dhcpd_refuses_a_match_value_that_would_escape_its_test():
    from netboot.dhcp.dhcpd import render_condition

    condition = DhcpCondition(
        "evil", {"user-class": 'x" { filename "pwned"; } #'}, {"boot-file-name": "y"}
    )
    with pytest.raises(netboot.PixieConfigError, match="quote cannot appear"):
        render_condition(condition)


def test_an_unknown_dhcpd_conditions_mode_is_refused():
    with pytest.raises(netboot.PixieConfigError, match="must be 'if' or 'group'"):
        DhcpServer("dhcpd://secret@dhcp01/?conditions=policies")


def test_the_dhcpd_recipe_is_a_config_group_that_survives_a_restart():
    server, _ = _dhcpd()
    ctx = _ctx()
    recipe = server.condition_recipe(ctx, build_conditions(ctx)["ipxe"])
    assert 'group "ipxe" {' in recipe
    assert "survives a restart" in recipe


# -- kea: a client class ---------------------------------------------------


def _kea(class_exists=False):
    server = DhcpServer("kea://ctrl01/")
    sent = []

    def command(cmd, arguments=None):
        sent.append((cmd, arguments))
        if cmd == "class-get":
            return {"result": 0 if class_exists else 3}
        return {"result": 0}

    server.command = command
    return server, sent


def test_kea_uses_a_class_that_already_exists():
    server, sent = _kea(class_exists=True)
    server.add_target(_ctx())
    assert [cmd for cmd, _ in sent] == ["class-get", "reservation-add"]


def test_kea_adds_the_class_when_it_can():
    server, sent = _kea(class_exists=False)
    server.add_target(_ctx())
    assert [cmd for cmd, _ in sent] == ["class-get", "class-add", "reservation-add"]
    entry = dict(sent[1][1])["client-classes"][0]
    assert entry["name"] == "ipxe"
    assert entry["test"] == "option[77].text == 'iPXE'"
    assert entry["option-data"] == [{"name": "boot-file-name", "data": "boot.ipxe"}]


def test_the_kea_reservation_names_its_classes():
    server, sent = _kea(class_exists=True)
    server.add_target(_ctx())
    reservation = dict(sent[-1][1])["reservation"]
    assert reservation["client-classes"] == ["ipxe"]


def test_kea_without_the_class_cmds_hook_hands_over_the_json():
    server = DhcpServer("kea://ctrl01/")

    def command(cmd, arguments=None):
        if cmd == "class-get":
            return {"result": 3}
        if cmd == "class-add":
            return {"result": 2}  # unsupported command: hook not loaded
        return {"result": 0}

    server.command = command
    ctx = _ctx()
    with pytest.raises(ConditionMissing) as excinfo:
        server.add_target(ctx)
    message = str(excinfo.value)
    assert "class_cmds" in message
    payload = message[message.index("{") :]
    assert json.loads(payload)["name"] == "ipxe"


def test_a_kea_test_cannot_be_escaped_by_a_match_value():
    from netboot.dhcp.kea import _class

    condition = DhcpCondition(
        "evil", {"user-class": "x' or 'a'=='a"}, {"boot-file-name": "y"}
    )
    with pytest.raises(netboot.PixieConfigError, match="quote or newline"):
        _class(condition)


# -- dnsmasq: refuses, and the run survives --------------------------------


def test_dnsmasq_refuses_a_condition_with_the_reason_and_the_recipe(tmp_path):
    hosts = tmp_path / "h"
    hosts.mkdir()
    server = DhcpServer(f"dnsmasq:///?hostsfile={hosts.as_posix()}")
    with pytest.raises(ConditionUnsupported) as excinfo:
        server.add_target(_ctx())
    message = str(excinfo.value)
    assert "dhcp-match" in message and "dhcp-optsfile" in message
    assert "dhcp-match=set:ipxe,77,iPXE" in message


def test_dnsmasq_writes_nothing_when_it_refuses(tmp_path):
    hosts = tmp_path / "h"
    hosts.mkdir()
    server = DhcpServer(f"dnsmasq:///?hostsfile={hosts.as_posix()}")
    with pytest.raises(ConditionUnsupported):
        server.add_target(_ctx())
    assert list(hosts.iterdir()) == [], "a refusal must not half-apply"


def test_one_backend_refusing_still_arms_the_others(caplog, tmp_path):
    # Best-effort arming (0.2.3) is what makes refusing safe: the zone's other
    # servers are armed and the run succeeds.
    armed = []

    class supports(DhcpServer):  # scheme `supports://`
        def add_target(self, ctx):
            for condition in self.conditions_for(ctx).values():
                armed.append(condition.name)

    hosts = tmp_path / "h"
    hosts.mkdir()
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": [
                    f"dnsmasq:///?hostsfile={hosts.as_posix()}",
                    "supports://a",
                ],
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="netboot"):
        engine.initialize(engine.lookup_target("web01"))
    assert armed == ["ipxe"], "the supporting backend must still be armed"
    assert "dnsmasq" in caplog.text


def test_a_zone_whose_only_backend_refuses_fails(tmp_path):
    hosts = tmp_path / "h"
    hosts.mkdir()
    engine = _engine(
        dhcpzones={
            "lan": {
                "network": "10.0.0.0/24",
                "dhcpservers": [f"dnsmasq:///?hostsfile={hosts.as_posix()}"],
            }
        }
    )
    with pytest.raises(ConditionUnsupported):
        engine.initialize(engine.lookup_target("web01"))


# -- the base contract -----------------------------------------------------


def test_a_backend_that_says_nothing_about_conditions_refuses_them():
    class silent(DhcpServer):  # scheme `silent://`
        pass

    server = DhcpServer("silent://host/")
    ctx = _ctx()
    with pytest.raises(ConditionUnsupported, match="cannot serve conditional"):
        server.ensure_condition(ctx, build_conditions(ctx)["ipxe"])
    # ... and has nothing to detach, which must not raise.
    assert server.remove_condition_member(ctx, build_conditions(ctx)["ipxe"]) is None
