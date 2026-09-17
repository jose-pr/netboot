"""Tests for `Pixie.lookup_image` / `Pixie.lookup_dhcpzone` selection rules.

Both rules are documented in the shipped API header and had no coverage: image
selection keeps the *highest* comparable `match` return and falls back to `{}`
(not `None`), and zone selection falls back to IP containment and caches the
result back onto the target.
"""

import pytest

import netboot


class _ScoringImage(netboot.PixieImage):
    """An image whose `match` returns a configured score instead of a bool."""

    def match(self, name: str, check: str):
        return self.scores.get(check, False)


def _netboot_with_scoring_images(scores_by_image):
    config = {
        "images": {
            img: {"scores": scores, "template_path": []}
            for img, scores in scores_by_image.items()
        },
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {},
    }
    p = netboot.Pixie(**config)
    # `images` is typed dict[str, PixieImage]; swap in the scoring subclass with
    # the same payload so `match` is the only thing that differs.
    p.images = {
        name: _ScoringImage(_id=name, scores=scores_by_image[name], template_path=[])
        for name in p.images
    }
    return p


def test_lookup_image_prefers_the_highest_match_score():
    p = _netboot_with_scoring_images(
        {
            "generic": {"debian": 1},
            "specific": {"debian": 2},
            "other": {"ubuntu": 5},
        }
    )
    assert p.lookup_image("debian")._id == "specific"


def test_lookup_image_ignores_non_matching_images():
    p = _netboot_with_scoring_images({"a": {"debian": 1}, "b": {"ubuntu": 9}})
    # "b" scores higher, but not for this name -- match() returned False.
    assert p.lookup_image("debian")._id == "a"


def test_lookup_image_falls_back_to_an_empty_mapping():
    # Documented contract: the fallback is falsy `{}`, not None -- make_context
    # tests it with `if not image`, and `None._id` would be an AttributeError
    # for a hook inspecting the result.
    p = _netboot_with_scoring_images({"a": {"debian": 1}})
    result = p.lookup_image("nothing-matches-this")
    assert result == {}
    assert result is not None


def test_lookup_image_default_match_is_exact_name():
    config = {
        "images": {"debian": {"template_path": []}, "ubuntu": {"template_path": []}},
        "dhcpzones": {},
        "targets": {},
    }
    p = netboot.Pixie(**config)
    assert p.lookup_image("debian")._id == "debian"
    assert p.lookup_image("missing") == {}


def _netboot_two_zones():
    config = {
        "images": {},
        "dhcpzones": {
            "lan": {"network": "10.0.0.0/24"},
            "dmz": {"network": "10.9.0.0/24"},
        },
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.9.0.7"},
        },
    }
    return netboot.Pixie(**config)


def test_lookup_dhcpzone_by_name():
    p = _netboot_two_zones()
    assert p.lookup_dhcpzone("dmz") is p.dhcpzones["dmz"]


def test_lookup_dhcpzone_finds_the_zone_containing_the_target_ip():
    p = _netboot_two_zones()
    target = p.lookup_target("host1")
    assert p.lookup_dhcpzone("", target) is p.dhcpzones["dmz"]


def test_lookup_dhcpzone_caches_the_zone_id_on_the_target():
    p = _netboot_two_zones()
    target = p.lookup_target("host1")
    assert target.dhcpzone == ""
    p.lookup_dhcpzone("", target)
    assert target.dhcpzone == "dmz"  # written back for the next lookup


def test_lookup_dhcpzone_prefers_the_targets_own_zone_over_containment():
    p = _netboot_two_zones()
    target = p.lookup_target("host1")
    target.dhcpzone = "lan"  # explicit config beats the containment scan
    assert p.lookup_dhcpzone("", target) is p.dhcpzones["lan"]


def test_lookup_dhcpzone_returns_none_for_an_unknown_name():
    p = _netboot_two_zones()
    assert p.lookup_dhcpzone("nosuchzone") is None


def _netboot_with_targets(targets):
    return netboot.Pixie(
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets=targets,
    )


_TWO_HOSTS = {
    "aa:bb:cc:00:00:10": {"hostname": "node10", "ip": "10.0.0.10", "image": "debian"},
    "aa:bb:cc:00:00:01": {"hostname": "node1", "ip": "10.0.0.1", "image": "debian"},
}


def test_exact_hostname_beats_an_earlier_prefix_match():
    # Regression: "node1" used to return node10 -- the first prefix hit won,
    # so initiate armed PXE on a machine nobody asked for.
    engine = _netboot_with_targets(_TWO_HOSTS)
    assert engine.lookup_target("node1").hostname == "node1"
    assert engine.lookup_target("node10").hostname == "node10"


def test_a_unique_prefix_still_matches():
    engine = _netboot_with_targets(_TWO_HOSTS)
    assert engine.lookup_target("node1").hostname == "node1"
    engine = _netboot_with_targets(
        {"aa:bb:cc:00:00:10": {"hostname": "node10", "image": "debian"}}
    )
    assert engine.lookup_target("nod").hostname == "node10"


def test_an_ambiguous_prefix_raises_instead_of_guessing():
    engine = _netboot_with_targets(
        {
            "aa:bb:cc:00:00:10": {"hostname": "node10", "image": "debian"},
            "aa:bb:cc:00:00:11": {"hostname": "node11", "image": "debian"},
        }
    )
    with pytest.raises(LookupError, match="ambiguous"):
        engine.lookup_target("node1")


def test_empty_query_matches_nothing():
    # An unset shell variable must not silently select the first target.
    assert _netboot_with_targets(_TWO_HOSTS).lookup_target("") is None


@pytest.mark.parametrize(
    "spelling", ["aa:bb:cc:00:00:01", "AA-BB-CC-00-00-01", "aabb.cc00.0001"]
)
def test_mac_is_matched_in_every_spelling(spelling):
    assert _netboot_with_targets(_TWO_HOSTS).lookup_target(spelling).hostname == "node1"


def test_ip_is_matched():
    assert (
        _netboot_with_targets(_TWO_HOSTS).lookup_target("10.0.0.10").hostname
        == "node10"
    )


def test_mac_less_targets_are_not_all_matched_by_the_null_mac():
    engine = _netboot_with_targets(
        {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
            "host2": {"hostname": "host2", "ip": "10.0.0.6", "image": "debian"},
        }
    )
    assert engine.lookup_target("00:00:00:00:00:00") is None


def test_zone_lookup_without_an_ip_returns_none_instead_of_raising(monkeypatch):
    # A MAC-keyed target whose hostname never resolved has ip == "", and
    # `"" in network` used to raise AttributeError from ipaddress.
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    p = netboot.Pixie(
        images={"debian": {}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"aa:bb:cc:00:00:09": {"image": "debian"}},
    )
    target = p.lookup_target("aa:bb:cc:00:00:09")
    assert not target.ip
    assert p.lookup_dhcpzone("", target) is None
