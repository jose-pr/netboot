import typing as _ty
from typing import Union

from netimps import Host, split_host
from pathapi import Pathname
from pathapi import PosixPathname as RepoPath
from uripath import Source, URIPath

from ..logging import LOGGER
from ..utils.config import Namespace as _NS


class Resource(_NS):
    """A file in a repository: `path` within it, `src` naming the repo."""

    path: Pathname
    src: str

    def __truediv__(self, key):
        return Resource(path=self.path / key, src=self.src)

    @classmethod
    def _parse_path(cls, path: Union[str, Pathname]):
        if not isinstance(path, Pathname):
            path = RepoPath(path)
        return path


class Repository(_NS):
    """Where artifacts are served from.

    `services` maps a scheme name to a base path or full URI; `local` is the
    filesystem view. `address` fills in the authority for a relative service
    path.
    """

    address: Host
    services: dict[str, URIPath]
    local: "_ty.Optional[URIPath]"

    def __init__(self, **kwargs) -> None:
        self.services = {}
        kwargs.setdefault("address", None)
        kwargs.setdefault("local", None)
        super().__init__(**kwargs)
        if not isinstance(self.address, Host):
            self.address = Host(self.address)
        if self.local and not isinstance(self.local, URIPath):
            text = str(self.local)
            if len(text) > 1 and text[1] == ":" and text[0].isalpha():
                # A drive letter is not a URI scheme: "C:\\tftp" parsed as
                # scheme "c" produced a path nothing could read. Spell it as
                # the file URI it is.
                self.local = URIPath("file:///" + text.replace("\\", "/"))
            else:
                self.local = URIPath(text)

    def __getitem__(self, key: tuple[str, str]) -> URIPath:
        rel_path, service = key
        return self.get(rel_path, service=service)

    def __truediv__(self, key) -> "Repository":
        return self.joinpath(key)

    def joinpath(self, subpath: str = None):
        """A copy of this repository with `subpath` appended to every service.

        `subpath` is always relative: `repo / "/abs"` extends the base like
        `repo / "abs"` does, rather than replacing it -- a leading slash in a
        config value is a typo, not a request to discard the repository root.
        """
        sub = str(subpath).strip("/") if subpath else None
        repo = Repository(address=self.address, services={}, local=self.local)
        for srvc in self.services:
            repo.services[srvc] = (
                f"{str(self.services[srvc]).rstrip('/')}/{sub}"
                if sub
                else self.services[srvc]
            )
        if self.local:
            repo.local = self.local / sub if sub else self.local
        else:
            # Keep .local a Pathname (not a raw str) so chained joinpath works.
            repo.local = RepoPath(sub) if sub else None
        return repo

    def get(self, *path: Union[RepoPath, str], service: str = None) -> URIPath:
        """Join `path` onto a service's base URI, or `None` if undefined."""
        path: RepoPath = RepoPath(
            *[(p if isinstance(p, Pathname) else RepoPath(p)).as_posix() for p in path]
        )
        rel_path = path.as_posix().lstrip("/")
        baseuri = self.service(service)
        if not baseuri:
            return None
        return baseuri / rel_path

    def _service_host(self, scheme: str) -> "tuple[str, _ty.Optional[int]]":
        """The authority for a relative service path: host and optional port.

        The port is split off **before** anything is resolved: an address like
        `mirror.example:8080` is a host and a port, and asking DNS for the whole
        string is a lookup that can only fail (slowly, and differently on every
        machine).

        TLS is the exception to resolving at all: a certificate is issued for a
        name, and a name-based virtual host needs one too, so `https` keeps the
        address as configured. Other schemes use the resolved IP, because a PXE
        client often has no working DNS when it fetches its boot files.

        The split is netimps' `split_host`, which netboot duplicated for a
        while. It returns a v6 literal **unbracketed**, which is what
        uripath's `Source` wants -- it brackets one itself.
        """
        address = str(self.address)
        if not address:
            LOGGER.warning(
                "repository has no address, so %s:// URLs are built without a "
                "host; give the repo an `address`, or write the service as a "
                "full URI",
                scheme,
            )
            return "", None
        try:
            host, port = split_host(address)
        except ValueError as exc:
            # `mirror.example:http` is a config mistake. The duplicate this
            # replaced treated it as a hostname, so it reached DNS as one.
            from .. import PixieConfigError

            raise PixieConfigError(
                f"repository address {address!r} is not a host or host:port: {exc}"
            ) from exc
        if scheme == "https":
            return host, port
        # An unresolvable name still has to build a URL, so it is used as written.
        ip = Host(host).ip()
        return (str(ip) if ip else host), port

    def service(self, name: str):
        """The base URI for a service name, `None` for `.local` when unset.

        A relative service path gets its authority from `.address`: resolved to an
        IP for every scheme except `https`, which keeps the configured name.
        """
        if name is None:
            baseuri = self.local
            name = "file"
            host, port = "", None
        else:
            baseuri = self.services.get(name, None)
            if baseuri is None:
                return None
            host, port = self._service_host(name)
        if baseuri is None:
            return None
        if not isinstance(baseuri, URIPath):
            baseuri = URIPath(baseuri)
        if not baseuri.source:
            # host=None writes `file:/srv/tftp`; "" would write an empty
            # authority (`file:///srv/tftp`) and change every URL built here.
            baseuri = baseuri.with_source(
                Source(scheme=name, userinfo=None, host=host or None, port=port)
            )

        return baseuri
