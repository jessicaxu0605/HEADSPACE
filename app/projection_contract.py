"""Compatibility identifiers shared by projection writers and readers.

Projection construction belongs to the rebuild workflow. These values remain
neutral because independently deployed readers must select rows produced by the
same persisted format.
"""

from typing import Literal, TypeAlias


NODE_PROJECTION_VERSION = 1
EDGE_PROJECTION_VERSION = 1
ProjectionType: TypeAlias = Literal["identity", "profile"]
