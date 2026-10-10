"""Agreement guard for every product string occurrence in the Python product.

C2d retired forty-nine user-visible and operational occurrences of the former
brand. Thirteen of them were already covered by a behavioural assertion. The
other thirty-six were renamed and then proved correct only by enumeration and
diff review, and the retention ledger's whole-tree occurrence **counter** cannot
tell a correct rename from a wrong one: it fires when *something* moved, never on
*what* it moved to, and it trips on one of four occurrences exactly as readily as
on four of four. This module closes that gap, and it is the Python counterpart of
the browser-side agreement guards added in C2c.

Two independent mechanisms, deliberately kept apart:

**A derived identity source.** `tests.support.kronika_identity` reads the brand
word out of ``extension/manifest.json`` and pins the full display name as a
separate literal, mirroring ``tests/x_companion_extension.test.js``. The
derivation is what makes every expectation below follow a product rename; the
independent display-name pin is what makes a *simultaneous* rename of the
manifest and of the product detectable. Neither is a production constant.

**An occurrence-level structural inventory.** Every brand-bearing runtime string
node under ``src/kronika`` is resolved to a durable structural key --
``(path, enclosing function, node kind, occurrence index within that function)``
-- and compared against a pinned literal. Line numbers are deliberately not
test selectors: they move. The key is occurrence-level rather than per-file or
per-function, so the three identical ``"Kronika is stopped."`` literals and the
four identical category-conflict literals are four distinct rows rather than one.
The comparison therefore fails on a changed value, on a deleted duplicate, on an
added occurrence, and on a duplicate that moves to another function -- and it
fails on each of those independently.

Behavioural guards, which assert what a caller actually observes, live next to
the machinery that already reaches each branch: the development runtime in
``tests/unit/infrastructure/runtime/test_development_runtime.py``, the category
conflict in ``tests/unit/application/test_x_category_conflict.py`` and
``tests/contract/test_x_request_api.py``, the alias API in
``tests/contract/test_media_alias_api.py``, the two operator CLIs in their own
contract modules, and the domain constants in ``tests/unit/domain/``. The argparse
descriptions and help strings are exercised here because no existing harness
builds those parsers in process.

Scope notes that the inventory makes explicit rather than implicit:

- The frozen Alembic ``versions/`` directory is excluded, because Part A of the
  retention ledger pins those bytes and a later cut may move the path but not the
  content. ``test_frozen_revision_occurrences_are_excluded_from_this_inventory``
  keeps that exclusion honest.
- The two externally sent AI prompt bodies are not pinned verbatim, because a
  thousand-character prompt body is not product prose and this cut does not own
  it. Their brand-carrying opening line *is* pinned, and the exclusion list
  itself is pinned, so neither a one-sided rename inside a prompt nor a new
  brand-bearing occurrence anywhere can pass unnoticed.
- The settings class name is itself an occurrence. Renaming the class made its
  two return annotations and the redaction filter's identity check brand-bearing,
  so they are pinned like any other row: a later cut that renames the class
  without updating the filter fails here instead of silently changing which
  settings objects are redacted.
"""

from __future__ import annotations

import argparse
import ast
import types
from pathlib import Path

import pytest

from kronika.adapters.cli import development as development_cli
from kronika.infrastructure.persistence import cli as database_cli
from kronika.infrastructure.runtime import production
from tests.support.kronika_identity import (
    BRAND,
    DISPLAY_NAME,
    MANIFEST,
    derive_brand,
    expected,
    require_display_name,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"

#: Applied Alembic bytes are frozen by Part A of the retention ledger, so their
#: brand-bearing literals are not this inventory's business. A later cut may move
#: the directory; it may not change those bytes.
FROZEN_VERSIONS_MARKER = "alembic_environment/versions"

DEVELOPMENT_RUNTIME = "src/kronika/infrastructure/runtime/development.py"
APPLICATION_X_ACQUISITION = "src/kronika/application/x_acquisition.py"
API_X_REQUEST = "src/kronika/adapters/api/x_request_api.py"

STOPPED_LITERAL = "Kronika is stopped."
CONFLICT_LITERAL = "Requested category conflicts with the existing Kronika save."


def _literal_text(node: ast.AST) -> str | None:
    """Return the readable text of a string node, or None for anything else.

    An f-string is read as its template with each placeholder rendered from the
    unparsed expression, so ``f"Kronika is running at {self.url}"`` and
    ``f"Kronika is running at {_url(state.port)}"`` stay distinguishable without
    depending on either line number.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
            else:  # pragma: no cover - a nested expression shape this tree lacks
                return None
        return "".join(parts)
    return None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Identify docstrings, which are prose about the code and carry no contract."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def product_string_nodes(relative_path: str) -> dict[tuple[str, str, str, int], str]:
    """Resolve one module's brand-bearing runtime string nodes to structural keys."""
    path = REPOSITORY_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    docstrings = _docstring_nodes(tree)
    resolved: dict[tuple[str, str, str, int], str] = {}
    counters: dict[str, int] = {}

    def visit(node: ast.AST, parent: ast.AST | None, stack: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack = stack + [node.name]
        text = _literal_text(node)
        # A Constant directly inside a JoinedStr is one fragment of an f-string
        # that is already counted as a whole, so it must not be counted twice.
        is_fragment = isinstance(parent, ast.JoinedStr)
        if (
            text is not None
            and BRAND in text
            and id(node) not in docstrings
            and not is_fragment
        ):
            owner = ".".join(stack) if stack else "<module>"
            index = counters.get(owner, 0)
            counters[owner] = index + 1
            resolved[(relative_path, owner, type(node).__name__, index)] = text
        for child in ast.iter_child_nodes(node):
            visit(child, node, stack)

    visit(tree, None, [])
    return resolved


def measured_product_strings() -> dict[tuple[str, str, str, int], str]:
    """Resolve every brand-bearing runtime string node under the product package."""
    resolved: dict[tuple[str, str, str, int], str] = {}
    for path in sorted(SOURCE_ROOT.glob("kronika/**/*.py")):
        relative = str(path.relative_to(REPOSITORY_ROOT))
        if FROZEN_VERSIONS_MARKER in relative:
            continue
        resolved.update(product_string_nodes(relative))
    return resolved


MEASURED = measured_product_strings()
OCCURRENCE_COUNT = 57

IN_SCOPE_OCCURRENCE_COUNT = 55
EXCLUDED_PROMPT_OCCURRENCE_COUNT = 2

EXPECTED: dict[tuple[str, str, str, int], str] = {
    # src/kronika/adapters/api/media_alias_api.py
    ("src/kronika/adapters/api/media_alias_api.py", "<module>", "Constant", 0): "Invalid Kronika media user alias.",
    # src/kronika/adapters/api/tailscale_ingress.py
    ("src/kronika/adapters/api/tailscale_ingress.py", "__call__", "Constant", 0): "The FrameNest or Kronika mutation header is required and must be exactly 1.",
    # src/kronika/adapters/api/x_request_api.py
    ("src/kronika/adapters/api/x_request_api.py", "create_x_request_api_router.submit_x_request", "Constant", 0): "Invalid Kronika media user alias.",
    ("src/kronika/adapters/api/x_request_api.py", "create_x_request_api_router.submit_x_request", "Constant", 1): "Requested category conflicts with the existing Kronika save.",
    # src/kronika/adapters/cli/ai.py
    ("src/kronika/adapters/cli/ai.py", "_resolve", "Constant", 0): "Kronika configuration could not be loaded.",
    # src/kronika/adapters/cli/development.py
    ("src/kronika/adapters/cli/development.py", "build_parser", "Constant", 0): "Control the local Kronika browser-development server.",
    ("src/kronika/adapters/cli/development.py", "main", "JoinedStr", 0): "Kronika launcher error: {exc}",
    ("src/kronika/adapters/cli/development.py", "_run_identity_path_migration", "JoinedStr", 0): "Kronika identity-path migration error: {exc}",
    ("src/kronika/adapters/cli/development.py", "_print_logs", "Constant", 0): "Kronika development log is not yet available.",
    # src/kronika/adapters/cli/youtube.py
    ("src/kronika/adapters/cli/youtube.py", "main", "Constant", 0): "Kronika configuration could not be loaded.",
    ("src/kronika/adapters/cli/youtube.py", "main", "Constant", 1): "The loopback Kronika operator API is unavailable.",
    ("src/kronika/adapters/cli/youtube.py", "main", "Constant", 2): "The loopback Kronika operator API is unavailable.",
    # src/kronika/application/library_workflow.py
    ("src/kronika/application/library_workflow.py", "<module>", "Constant", 0): "Kronika Server",
    # src/kronika/application/x_acquisition.py
    ("src/kronika/application/x_acquisition.py", "_reject_category_conflict", "Constant", 0): "Requested category conflicts with the existing Kronika save.",
    ("src/kronika/application/x_acquisition.py", "_reject_category_conflict", "Constant", 1): "Requested category conflicts with the existing Kronika save.",
    ("src/kronika/application/x_acquisition.py", "_reject_category_conflict", "Constant", 2): "Requested category conflicts with the existing Kronika save.",
    # src/kronika/configuration.py
    ("src/kronika/configuration.py", "validate_private_storage_roots", "Constant", 0): "Kronika private storage paths must not overlap",
    # The two `KronikaSettings` return annotations became brand-bearing when the
    # settings class was renamed, so they are occurrences this inventory now owns.
    ("src/kronika/configuration.py", "validate_ingress_configuration", "Constant", 0): "KronikaSettings",
    ("src/kronika/configuration.py", "validate_private_storage_roots", "Constant", 1): "KronikaSettings",
    # src/kronika/domain/devices.py
    ("src/kronika/domain/devices.py", "<module>", "Constant", 0): "Invalid Kronika device.",
    # src/kronika/domain/identities.py
    ("src/kronika/domain/identities.py", "<module>", "Constant", 0): "Invalid Kronika identity.",
    # src/kronika/domain/libraries.py
    ("src/kronika/domain/libraries.py", "<module>", "Constant", 0): "Invalid Kronika library.",
    ("src/kronika/domain/libraries.py", "<module>", "Constant", 1): "Invalid Kronika library root.",
    # src/kronika/domain/media.py
    ("src/kronika/domain/media.py", "<module>", "Constant", 0): "Invalid Kronika media.",
    ("src/kronika/domain/media.py", "<module>", "Constant", 1): "Invalid Kronika media location.",
    ("src/kronika/domain/media.py", "<module>", "Constant", 2): "Invalid Kronika media relative path.",
    # src/kronika/domain/media_cover.py
    ("src/kronika/domain/media_cover.py", "<module>", "Constant", 0): "Invalid Kronika accepted cover.",
    ("src/kronika/domain/media_cover.py", "<module>", "Constant", 1): "Invalid Kronika cover source observation.",
    # src/kronika/domain/media_metadata.py
    ("src/kronika/domain/media_metadata.py", "<module>", "Constant", 0): "Invalid Kronika media metadata.",
    # src/kronika/domain/media_user_alias.py
    ("src/kronika/domain/media_user_alias.py", "<module>", "Constant", 0): "Invalid Kronika media user alias.",
    # src/kronika/domain/uploads.py
    ("src/kronika/domain/uploads.py", "<module>", "Constant", 0): "Invalid Kronika upload session.",
    # src/kronika/infrastructure/persistence/alembic_environment/env.py
    ("src/kronika/infrastructure/persistence/alembic_environment/env.py", "run_migrations_online", "Constant", 0): "Kronika migration connection is unavailable.",
    # src/kronika/infrastructure/persistence/cli.py
    ("src/kronika/infrastructure/persistence/cli.py", "main", "Constant", 0): "Kronika configuration could not be loaded.",
    ("src/kronika/infrastructure/persistence/cli.py", "_build_parser", "Constant", 0): "Upgrade the Kronika database to head.",
    ("src/kronika/infrastructure/persistence/cli.py", "_build_parser", "Constant", 1): "Inspect the Kronika database revision.",
    # src/kronika/infrastructure/persistence/identity_labels.py
    # The bounded label-maintenance command's two canonical runtime literals:
    # the new NUC device display label and the replacement brand substring.
    ("src/kronika/infrastructure/persistence/identity_labels.py", "<module>", "Constant", 0): "Kronika NUC",
    ("src/kronika/infrastructure/persistence/identity_labels.py", "<module>", "Constant", 1): "Kronika",
    # src/kronika/infrastructure/runtime/development.py
    ("src/kronika/infrastructure/runtime/development.py", "start", "JoinedStr", 0): "Kronika is already running at {self.url}",
    ("src/kronika/infrastructure/runtime/development.py", "start", "Constant", 1): "Kronika did not become healthy in time.",
    ("src/kronika/infrastructure/runtime/development.py", "start", "Constant", 2): "Kronika startup failed. Check logs for details.",
    ("src/kronika/infrastructure/runtime/development.py", "start", "JoinedStr", 3): "Kronika is running at {self.url}",
    ("src/kronika/infrastructure/runtime/development.py", "stop", "Constant", 0): "Kronika is stopped.",
    ("src/kronika/infrastructure/runtime/development.py", "stop", "Constant", 1): "Kronika is stopped.",
    ("src/kronika/infrastructure/runtime/development.py", "stop", "Constant", 2): "Kronika stopped.",
    ("src/kronika/infrastructure/runtime/development.py", "open", "Constant", 0): "Kronika is not running.",
    ("src/kronika/infrastructure/runtime/development.py", "_status_with_state", "Constant", 0): "Kronika is stopped.",
    ("src/kronika/infrastructure/runtime/development.py", "_status_with_state", "JoinedStr", 1): "Kronika is running at {_url(state.port)}",
    ("src/kronika/infrastructure/runtime/development.py", "_status_with_state", "Constant", 2): "Managed Kronika process is running but health is not ready.",
    ("src/kronika/infrastructure/runtime/development.py", "_operation_lock", "Constant", 0): "Another Kronika runtime operation is in progress.",
    # src/kronika/infrastructure/runtime/local_state_migration.py
    # The one canonical brand component the migration spells literally; every
    # other canonical spelling is built from this constant or is lowercase.
    ("src/kronika/infrastructure/runtime/local_state_migration.py", "<module>", "Constant", 0): "Kronika",
    # src/kronika/infrastructure/runtime/production.py
    ("src/kronika/infrastructure/runtime/production.py", "main", "Constant", 0): "Kronika health check failed.",
    ("src/kronika/infrastructure/runtime/production.py", "_build_parser", "Constant", 0): "Verify the Kronika listener answers a local /health request.",
    ("src/kronika/infrastructure/runtime/production.py", "_build_parser", "Constant", 1): "Run the production Kronika server in the foreground.",
    # src/kronika/server.py
    ("src/kronika/server.py", "main", "JoinedStr", 0): "Kronika configuration error: {exc}",
    # src/kronika/structured_logging.py
    ("src/kronika/structured_logging.py", "_is_kronika_settings", "Constant", 0): "KronikaSettings",
}

EXPECTED_DUPLICATE_LITERALS: dict[str, int] = {
    "Invalid Kronika media user alias.": 3,
    "Kronika": 2,
    "Kronika configuration could not be loaded.": 3,
    "Kronika is stopped.": 3,
    "KronikaSettings": 3,
    "Requested category conflicts with the existing Kronika save.": 4,
    "The loopback Kronika operator API is unavailable.": 2,
}

EXCLUDED_PROMPT_OPENINGS: dict[tuple[str, str, str, int], str] = {
    ("src/kronika/application/movie_identification.py", "movie_identification_prompt", "JoinedStr", 0): "You are Kronika's movie identification assistant.",
    ("src/kronika/infrastructure/ai/prompts.py", "<module>", "JoinedStr", 0): "You are Kronika's media metadata assistant.",
}
# ---------------------------------------------------------------------------
# Identity source: derivation plus an independent display-name pin
# ---------------------------------------------------------------------------


def test_the_extension_display_name_is_pinned_independently_of_the_derived_brand() -> None:
    """The literal half of the agreement.

    A guard that only derived the brand word would still pass if the manifest and
    the product were renamed together. This assertion is what prevents that
    silent dual drift, and it is deliberately separate from the derivation.
    """
    assert require_display_name(MANIFEST) == DISPLAY_NAME
    assert DISPLAY_NAME.startswith(f"{BRAND} ")


def test_a_manifest_rename_alone_moves_the_derived_brand_and_fails_the_pin() -> None:
    """Demonstrate that both halves of the agreement are live, in memory.

    No file is altered. A drifted manifest is passed to the same two functions
    the real assertions use, so the demonstration exercises the shipped guards
    rather than a restatement of them.
    """
    drifted = dict(MANIFEST, name="Zonet X Companion")

    assert derive_brand(drifted) == "Zonet"
    assert derive_brand(drifted) != BRAND
    # Every pinned occurrence still names the real product, so a manifest-only
    # rename resolves no occurrence at all instead of quietly agreeing.
    assert not [text for text in MEASURED.values() if "Zonet" in text]
    with pytest.raises(AssertionError, match="single product identity"):
        require_display_name(drifted)


def test_the_derived_brand_is_the_display_name_word() -> None:
    assert BRAND == MANIFEST["name"].split()[0]
    assert BRAND == "Kronika"


# ---------------------------------------------------------------------------
# Occurrence-level structural inventory
# ---------------------------------------------------------------------------


def test_every_product_string_occurrence_matches_the_pinned_inventory() -> None:
    """Pin every brand-bearing runtime string node, occurrence by occurrence.

    One comparison, four independent failure modes. A changed value fails its own
    row; a deleted duplicate fails as a missing row; an added occurrence fails as
    an unexpected row; and a duplicate that moves to another function fails
    because the key carries the enclosing function and the occurrence index.
    """
    excluded = set(EXCLUDED_PROMPT_OPENINGS)
    measured = {key: text for key, text in MEASURED.items() if key not in excluded}

    unexpected = sorted(set(measured) - set(EXPECTED))
    missing = sorted(set(EXPECTED) - set(measured))
    changed = sorted(
        (key, EXPECTED[key], measured[key])
        for key in set(EXPECTED) & set(measured)
        if EXPECTED[key] != measured[key]
    )

    assert not (unexpected or missing or changed), (
        "product string inventory drifted: "
        f"unexpected={unexpected} missing={missing} changed={changed}"
    )


def test_the_product_string_inventory_carries_the_pinned_occurrence_count() -> None:
    """The total is pinned on its own so coverage cannot shrink unnoticed."""
    assert len(MEASURED) == OCCURRENCE_COUNT
    assert len(EXPECTED) == IN_SCOPE_OCCURRENCE_COUNT
    assert len(EXCLUDED_PROMPT_OPENINGS) == EXCLUDED_PROMPT_OCCURRENCE_COUNT
    assert len(MEASURED) == len(EXPECTED) + len(EXCLUDED_PROMPT_OPENINGS)


def test_duplicate_product_strings_keep_their_pinned_occurrence_counts() -> None:
    """Pin multiplicity for every literal that appears more than once.

    Without this, deleting one of three identical literals would leave every
    value assertion green while coverage silently shrank.
    """
    measured: dict[str, int] = {}
    for text in MEASURED.values():
        measured[text] = measured.get(text, 0) + 1
    duplicates = {text: count for text, count in measured.items() if count > 1}

    assert duplicates == EXPECTED_DUPLICATE_LITERALS


@pytest.mark.parametrize(
    ("relative_path", "literal", "expected_occurrences"),
    [
        (DEVELOPMENT_RUNTIME, STOPPED_LITERAL, 3),
        (APPLICATION_X_ACQUISITION, CONFLICT_LITERAL, 3),
        (API_X_REQUEST, CONFLICT_LITERAL, 1),
    ],
)
def test_the_named_duplicate_families_keep_their_per_file_counts(
    relative_path: str,
    literal: str,
    expected_occurrences: int,
) -> None:
    """The three duplicate families the completion plan names, pinned per file."""
    measured = [
        text for text in product_string_nodes(relative_path).values() if text == literal
    ]

    assert len(measured) == expected_occurrences


def test_frozen_revision_occurrences_are_excluded_from_this_inventory() -> None:
    """Applied Alembic bytes belong to Part A, not to this guard."""
    versions = sorted(
        str(path.relative_to(REPOSITORY_ROOT))
        for path in SOURCE_ROOT.glob("kronika/**/alembic_environment/versions/*.py")
    )

    assert versions, "the frozen applied revisions must still exist"
    assert all(FROZEN_VERSIONS_MARKER in relative for relative in versions)
    assert not [key for key in MEASURED if FROZEN_VERSIONS_MARKER in key[0]]
    assert not [key for key in EXPECTED if FROZEN_VERSIONS_MARKER in key[0]]


def test_excluded_prompt_bodies_still_open_with_the_derived_brand() -> None:
    """The two exclusions are pinned by their opening line, not waved through.

    Pinning a thousand-character prompt body verbatim is not this cut's business,
    but a one-sided rename inside one must still fail here rather than only
    tripping the retention ledger's whole-tree counter.
    """
    assert set(EXCLUDED_PROMPT_OPENINGS) <= set(MEASURED)
    for key, opening in EXCLUDED_PROMPT_OPENINGS.items():
        assert MEASURED[key].startswith(opening), (
            f"the excluded prompt body at {key} no longer opens with the pinned line"
        )
        assert BRAND in opening, (
            f"the excluded prompt body at {key} must still name the derived brand"
        )


def test_no_product_string_occurrence_is_left_without_a_pinned_value() -> None:
    """Every resolved occurrence is either pinned in full or pinned by its opening."""
    for key, text in MEASURED.items():
        if key in EXCLUDED_PROMPT_OPENINGS:
            continue
        assert text == EXPECTED[key]
        assert text.count(BRAND) >= 1


# ---------------------------------------------------------------------------
# Argparse descriptions and help strings, exercised as produced parser output
# ---------------------------------------------------------------------------


def subcommand_help(parser: argparse.ArgumentParser, command: str) -> str:
    """Return the complete help sentence the parent parser prints for a command.

    A ``help=`` string passed to ``add_parser`` is rendered in the *parent*
    parser's subcommand listing, not in the child's own help, so this reads the
    listing argparse actually produces. argparse puts the sentence on the
    command's own line when the command name is short enough and wraps it onto
    the following indented lines when it is not, so both layouts are read back
    and joined. Every caller fixes ``COLUMNS`` first, which makes the rendering
    deterministic instead of dependent on the terminal this suite runs on.
    """
    lines = parser.format_help().splitlines()
    wrapped: list[str] = []
    command_indent = 0
    collecting = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(stripped)
        if collecting:
            # A continuation is indented deeper than the command entry it belongs
            # to. A line at the command indent is the next entry, so the sentence
            # ended.
            if stripped and indent > command_indent:
                wrapped.append(stripped)
                continue
            break
        if stripped == command:
            collecting = True
            command_indent = indent
            continue
        if stripped.startswith(f"{command} "):
            return stripped[len(command) :].lstrip()

    assert wrapped, f"the parent parser prints no help entry for {command!r}"
    return " ".join(wrapped)


@pytest.fixture(autouse=True)
def _fixed_help_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the argparse layout so help assertions do not read the terminal."""
    monkeypatch.setenv("COLUMNS", "200")


def test_the_subcommand_help_reader_handles_both_argparse_layouts() -> None:
    """Prove the reader is not layout-naive, on an entry argparse wraps.

    ``check-database-ready`` is the one production subcommand long enough that
    argparse moves its help onto the following lines. Reading it back correctly is
    what makes the four inline entries below trustworthy.
    """
    assert subcommand_help(
        production._build_parser(), "check-database-ready"
    ) == "Verify the configured database is already migrated to head."


def test_development_cli_description_carries_the_derived_brand() -> None:
    """The browser-development server description, read off the built parser."""
    sentence = expected("Control the local {brand} browser-development server.")

    parser = development_cli.build_parser()

    assert parser.description == sentence
    assert sentence in parser.format_help()


def test_production_check_health_help_carries_the_derived_brand() -> None:
    sentence = expected("Verify the {brand} listener answers a local /health request.")

    assert (
        subcommand_help(production._build_parser(), "check-health") == sentence
    )


def test_production_serve_help_carries_the_derived_brand() -> None:
    sentence = expected("Run the production {brand} server in the foreground.")

    assert subcommand_help(production._build_parser(), "serve") == sentence


def test_database_migrate_help_carries_the_derived_brand() -> None:
    sentence = expected("Upgrade the {brand} database to head.")

    assert subcommand_help(database_cli._build_parser(), "migrate") == sentence


def test_database_status_help_carries_the_derived_brand() -> None:
    sentence = expected("Inspect the {brand} database revision.")

    assert subcommand_help(database_cli._build_parser(), "status") == sentence


# ---------------------------------------------------------------------------
# Alembic environment: an entry point Alembic executes, not a module it imports
# ---------------------------------------------------------------------------

ALEMBIC_ENVIRONMENT = (
    SOURCE_ROOT / "kronika" / "infrastructure" / "persistence" / "alembic_environment" / "env.py"
)


def _load_alembic_environment() -> types.ModuleType:
    """Load the Alembic entry point with only its module-level call withheld.

    ``env.py`` is executed by Alembic rather than imported: its last line calls
    ``run_migrations_online()`` unconditionally, so a plain import would attempt a
    migration and would need an Alembic context before the assertion could run.
    The function under test is loaded from the same file the migrations use, and
    withholding the trailing call is asserted rather than assumed.
    """
    source = ALEMBIC_ENVIRONMENT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ALEMBIC_ENVIRONMENT))
    entry_point = tree.body[-1]
    assert isinstance(entry_point, ast.Expr), (
        "the Alembic environment must still end with its entry-point call"
    )
    tree.body = tree.body[:-1]

    module = types.ModuleType("framenest_alembic_environment_under_test")
    exec(compile(tree, str(ALEMBIC_ENVIRONMENT), "exec"), module.__dict__)  # noqa: S102
    return module


def test_migration_without_a_connection_reports_the_derived_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one product string with no caller-facing surface, reached directly."""
    module = _load_alembic_environment()
    monkeypatch.setattr(
        module,
        "context",
        types.SimpleNamespace(config=types.SimpleNamespace(attributes={})),
    )

    with pytest.raises(RuntimeError) as exc_info:
        module.run_migrations_online()

    assert str(exc_info.value) == expected("{brand} migration connection is unavailable.")