from __future__ import annotations


class UnsupportedSourceError(ValueError):
    """Raised when a source file cannot be handled by the selected parser.

    Covers both routing ("no bundled plugin handles this file type") and adapter
    validation ("this file is not the shape the plugin expects").
    """
