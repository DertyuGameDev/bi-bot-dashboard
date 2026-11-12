"""Extensible Matplotlib renderer registry. Importing a module registers a type."""

from .registry import REGISTRY, register, render
from . import categorical, lines, statistical  # noqa: F401

__all__ = ["REGISTRY", "register", "render"]
