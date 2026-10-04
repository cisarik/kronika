"""The single derived source of the product brand for Python-side guards.

Every product string in this repository is pinned against the brand word derived
from the extension display name, exactly as the browser-side agreement guards in
``tests/x_companion_extension.test.js`` pin theirs. Two properties matter and
they are deliberately separate:

- the brand word is **derived**, never spelled here, so a one-sided rename of the
  product breaks the guard that owns the renamed string instead of passing
  silently; and
- the full display name is pinned **independently** of that derivation, so a
  rename of the manifest and of the product together cannot pass unnoticed
  either. A guard that only derived the word would be a tautology.

``extension/manifest.json`` is the only machine-readable identity source and it is
the same file the JavaScript guards already read. No Python code read it before
this module existed, and no production branding constant was added for these
tests to read: the brand lives in test code because the identity lives in the
browser extension manifest, not because the product needs a second constant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "extension" / "manifest.json"

#: The one display name this repository accepts. Pinned as a literal rather than
#: derived, because it is the independent half of the agreement: it is what makes
#: a simultaneous rename of the manifest and of the product detectable.
DISPLAY_NAME = "Kronika X Companion"


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Return the parsed extension manifest."""
    return json.loads(path.read_text(encoding="utf-8"))


def derive_brand(manifest: dict[str, Any]) -> str:
    """Return the product word carried by the extension display name.

    Mirrors ``manifest.name.split(/\\s+/)[0]`` in the JavaScript guards.
    """
    name = manifest["name"]
    assert isinstance(name, str) and name.split(), (
        f"the manifest display name must carry a word, measured {name!r}"
    )
    return name.split()[0]


def require_display_name(manifest: dict[str, Any]) -> str:
    """Pin the full display name independently of the derived brand word."""
    name = manifest["name"]
    assert name == DISPLAY_NAME, (
        "the display name is the single product identity: "
        f"expected {DISPLAY_NAME!r}, measured {name!r}"
    )
    return name


MANIFEST = load_manifest()
BRAND = derive_brand(MANIFEST)


def expected(sentence: str, brand: str | None = None, **fields: object) -> str:
    """Build one complete product sentence from the derived brand word.

    ``sentence`` is written with an explicit ``{brand}`` placeholder so a caller
    can never accidentally assert a substring, a prefix, or a sentence whose
    brand word was left implicit. Any further placeholder an interpolated product
    message carries, such as the URL in a running-server sentence, is supplied by
    the caller through ``fields`` so the expected string stays a complete,
    independently constructed sentence rather than a slice of observed output.
    """
    return sentence.format(brand=BRAND if brand is None else brand, **fields)