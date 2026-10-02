"""Attribute-aware HTML tag scanning shared by the export assemblers.

Tags are walked attribute by attribute, so a ``src=``, ``id=`` or ``>`` inside
another attribute's quoted value is never mistaken for a real attribute or tag end.
"""

from __future__ import annotations

import re

# One attribute inside a tag, for embedding in a larger tag pattern.
HTML_ATTR = r"""[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?"""
# The same grammar with groups: (1) the name, (2) the raw value (quoted or not).
HTML_ATTR_RE = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")
# Any start tag, with HTML comments matched first so a commented-out tag is left alone.
HTML_START_TAG_RE = re.compile(
    rf"(?P<comment><!--.*?-->)|<(?P<name>[A-Za-z][\w:-]*)(?P<attrs>(?:\s+{HTML_ATTR})*)\s*(?P<end>/?>)",
    re.DOTALL,
)
