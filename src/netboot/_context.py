"""The render context: everything a template sees for one target."""

import datetime
import logging
import typing as _ty
from argparse import Namespace
from pathlib import Path
from typing import Union

import dhcpctl
import netimps
import pathapi
import tplx
from uripath import URIPath

from .content import Repository, Resource
from .dhcp import DHCPZone, arm, disarm
from .exceptions import PixieError
from .logging import LOGGER
from .utils.misc import parse_path
from ._objects import PixieImage, PixieTarget

if _ty.TYPE_CHECKING:
    from .engine import Pixie

__all__ = ["PixieContext"]


class PixieContext(Namespace):
    """Everything a template can see for one target.

    Built by `Pixie.make_context`, never constructed directly: it carries the
    target, its image and zone, the repositories, and its own renderer.
    """

    target: PixieTarget
    image: PixieImage
    dhcpzone: DHCPZone
    repos: dict[str, Repository]
    generated: datetime.datetime
    resources: dict[str, Resource]
    version: str
    _loader: tplx.Loader

    def __init__(self, **kwargs: object) -> None:
        self.generated = datetime.datetime.now()
        super().__init__(**kwargs)

    def resource(
        self, name: Union[str, Resource], service: "str | None" = None
    ) -> "URIPath | None":
        """Resolve a resource to a fetchable URI through its repository.

        Takes a resource id (looked up in `.resources`) or a `Resource` directly.
        `None` if the repo or the resource is unknown.
        """
        if isinstance(name, Resource):
            repo = self.repos.get(getattr(name, "src", None), None)
            path = getattr(name, "path", None)
            if not path:
                return
        else:
            path = None
            repo = self.resource_repo(name)
        if not repo:
            return
        if not path:
            path = self.resources[name].path
        return repo.get(path, service=service)

    def resource_repo(self, name: str) -> "Repository | None":
        """The `Repository` backing resource `name`, or `None`."""
        resource = self.resources.get(name)
        if not resource:
            return
        return self.repos.get(resource.src)

    def pxe_init(self, config: "Pixie") -> "PixieContext":
        """Arm every DHCP backend for this target, succeeding if any one does.

        Every backend is tried. A backend that fails is logged as a warning and
        skipped, so one server being offline does not block the others. When
        none could be armed the first error is raised: as itself when netboot
        or dhcpctl raised it on purpose, else as a `PixieError` chained to it.
        """
        first_error = None
        armed = 0
        if not self.dhcpzone.dhcpservers:
            LOGGER.warning(
                "zone %s has no dhcpservers: nothing was armed for %s",
                self.dhcpzone._id,
                self.target._id,
            )
        for dhcpserver in self.dhcpzone.dhcpservers:
            try:
                arm(self, dhcpserver)
            except Exception as exc:
                LOGGER.warning(
                    "could not arm %s on %s: %s",
                    self.target._id,
                    dhcpserver,
                    exc,
                    exc_info=LOGGER.isEnabledFor(logging.DEBUG),
                )
                first_error = first_error or exc
            else:
                armed += 1
        if first_error is not None and not armed:
            if isinstance(first_error, (PixieError, dhcpctl.DHCPCtlError)):
                raise first_error
            raise PixieError(
                f"no DHCP server armed {self.target._id!r}: {first_error}"
            ) from first_error
        return self

    def pxe_complete(self, config: "Pixie") -> "PixieContext":
        """Disarm every DHCP backend; a failure is a warning, never an error.

        Every backend is tried, and one that fails is logged and skipped, so
        the rest are still disarmed.
        """
        for dhcpserver in self.dhcpzone.dhcpservers:
            try:
                # Removes the entry unless the config says to keep it
                # (`dhcp_complete:`).
                disarm(self, dhcpserver)
            except Exception as exc:
                LOGGER.warning(
                    "could not disarm %s on %s: %s",
                    self.target._id,
                    dhcpserver,
                    exc,
                    exc_info=LOGGER.isEnabledFor(logging.DEBUG),
                )
        return self

    def _template_names(self, suffix: Union[list[str], str], **options) -> list[str]:
        # ``options`` (a ``k=v:name`` template spec) is accepted for forward
        # compatibility; name selection does not use it today.
        suffixes = suffix if isinstance(suffix, list) else [suffix]
        ip = self.target.ip
        # Skip unset values so they never yield a spurious name. The null MAC
        # is the same case as the unspecified IP: every MAC-less target would
        # otherwise share the candidate `00-00-00-00-00-00.<name>`, and one
        # stray file of that name would apply to all of them.
        # `is_wildcard` knows every spelling of "every local address", a
        # `%zone` suffix and `None` included; the literal tuple this replaced
        # knew two of them.
        ip_name = "" if netimps.is_wildcard(ip) else str(ip)
        mac = self.target.mac
        mac_name = mac.format("-") if str(mac) != PixieTarget._NULL_MAC else ""
        names = []
        for version in [
            mac_name,
            self.target.hostname,
            ip_name,
        ]:
            if version:
                for suffix in suffixes:
                    names.append(f"{version}.{suffix}")
        for suffix in suffixes:
            names.append(suffix)

        return names

    @property
    def searchpaths(self) -> list[Path]:
        """Template directories for this context: target first, then image.

        These extend the engine-wide template roots rather than replacing them; a
        relative entry is resolved inside each root.
        """
        return [*self.target.template_path, *self.image.template_path]

    def render(self, filename: str, strict: bool = True) -> "str | bytes | None":
        """Render `filename` against this context and return the result.

        The name is resolved by the template search (MAC, hostname, IP, then the
        bare name), through `tplx`, and so are the includes inside it. The
        template sees `ctx` and each of the context's fields (`target`, `image`,
        ...). Text, or **`bytes`** for a file the copy engine claims, which is
        every suffix no other engine owns -- it is a byte-exact copy, so it is
        never decoded. `strict=False` logs the failure at DEBUG and returns
        `None` instead of raising.
        """
        data = dict(vars(self))
        data["ctx"] = self
        paths = [
            path if isinstance(path, pathapi.Path) else parse_path(str(path))
            for path in self.searchpaths
        ]
        try:
            return self._loader.render(
                filename, data, candidates=self._template_names, paths=paths
            )
        except Exception as e:
            if strict:
                raise e
            else:
                LOGGER.debug(
                    f"While rendering [{filename}] encountered an exeption:\t{repr(e)}"
                )
                return None
