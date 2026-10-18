from anycfg import OpaqueMerge, TypedNamespace

__all__ = ["Namespace"]


class Namespace(TypedNamespace, OpaqueMerge):
    """Base for netboot config objects.

    :class:`~anycfg.TypedNamespace` applies ``_parse_<prop>`` coercers at
    construction; :class:`~anycfg.OpaqueMerge` marks the built object opaque
    to ``typed_merge`` (last object wins). A netboot config object is fully built by
    its ``__init__`` (IP/MAC/network fields already normalised), so re-merging it
    field by field is neither wanted nor sound.
    """
