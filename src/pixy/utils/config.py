from yaconfiglib import OpaqueMerge, TypedNamespace


class Namespace(TypedNamespace, OpaqueMerge):
    """Base for pixy config objects.

    :class:`~yaconfiglib.TypedNamespace` applies ``_parse_<prop>`` coercers at
    construction; :class:`~yaconfiglib.OpaqueMerge` marks the built object opaque
    to ``typed_merge`` (last object wins). A pixy config object is fully built by
    its ``__init__`` (IP/MAC/network fields already normalised), so re-merging it
    field by field is neither wanted nor sound.
    """
