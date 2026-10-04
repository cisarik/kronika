"""Server-operated AI configuration and diagnostics CLI."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from framenest.infrastructure.ai.provider_activity import classify_provider_exception
from framenest.infrastructure.ai.still_frame_smoke import (
    FrameNestStillFrameSmokeError,
    STILL_FRAME_SMOKE_INVALID_MESSAGE,
    build_still_frame_smoke_request,
    prepare_still_frame_smoke_images,
)
from framenest.configuration import (
    FrameNestConfigurationError,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from framenest.infrastructure.ai.configuration import (
    AiConfigurationError,
    AiServerConfig,
    AiStatusSnapshot,
    AiTestState,
    default_ai_config_path,
    load_ai_server_config,
    load_ai_server_config_snapshot,
    now_ms,
    validate_model_id,
    validate_provider_id,
    write_ai_server_config,
    write_ai_status_snapshot,
    write_ai_test_state,
)
from framenest.infrastructure.ai.activity_lock import (
    AiActivityLock,
    AiActivityLockError,
    acquire_ai_activity_lock,
)
from framenest.infrastructure.ai.constants import (
    BUILTIN_PROVIDER_IDS,
    DEFAULT_PROVIDER_ID,
    VERCEL_AI_GATEWAY_PROVIDER_ID,
)
from framenest.infrastructure.ai.credentials import load_ai_credential
from framenest.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
    AiProviderRecordError,
    validate_credential_environment_name,
    validate_declared_base_url,
    validate_declared_capabilities,
    validate_declared_protocol,
    validate_model_identifier,
    validate_provider_display_name,
    validate_provider_identifier,
)
from framenest.infrastructure.ai.registry import (
    AiProviderDefinition,
    ResolvedAiProvider,
    provider_definitions,
    resolve_ai_provider,
)
from framenest.infrastructure.ai.vision_probe import (
    EXPECTED_COLOR,
    VISION_PROBE_PROMPT,
    VisionProbeState,
    default_vision_probe_state_path,
    load_vision_probe_fixture,
    match_expected_color,
    write_vision_probe_state,
)

Input = Callable[[str], str]
Output = Callable[[str], None]

PROVIDER_ORDER = (VERCEL_AI_GATEWAY_PROVIDER_ID, DEFAULT_PROVIDER_ID)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise SystemExit(2)


@dataclass(frozen=True, slots=True)
class _CliContext:
    config_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-ai", add_help=True)
    parser.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help="Override the non-secret server AI configuration path.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    status = subcommands.add_parser("status", help="Show network-free AI configuration status.")
    status.add_argument(
        "--no-write",
        action="store_true",
        help="Resolve status without writing a local status snapshot.",
    )
    configure = subcommands.add_parser(
        "configure",
        help="Configure non-secret AI provider state.",
    )
    configure.add_argument("--provider-id", default=None, help="Non-interactive provider ID.")
    configure.add_argument("--model-id", default=None, help="Non-interactive model ID.")
    configure.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a non-interactive provider/model configuration.",
    )
    provider = subcommands.add_parser(
        "provider",
        help="Manage operator-declared non-secret provider records.",
    )
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    provider_add = provider_commands.add_parser(
        "add",
        help="Create or update one declared provider record.",
    )
    provider_add.add_argument("--provider-id", required=True, help="Declared provider ID.")
    provider_add.add_argument("--name", required=True, help="Provider display name.")
    provider_add.add_argument(
        "--protocol",
        required=True,
        help="Declared provider protocol (openai-chat-completions).",
    )
    provider_add.add_argument("--base-url", required=True, help="HTTPS base URL.")
    provider_add.add_argument(
        "--credential-env",
        required=True,
        help="Credential environment variable name, never a value.",
    )
    provider_add.add_argument(
        "--model-id",
        action="append",
        dest="model_ids",
        required=True,
        help="Declared model ID. Repeat for more models.",
    )
    provider_add.add_argument(
        "--model-name",
        action="append",
        dest="model_names",
        default=[],
        help="Optional model display name. Provide one per model ID or none.",
    )
    provider_add.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        default=[],
        help="Declared model capability applied to all listed models. Repeatable.",
    )
    provider_add.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the non-interactive record write.",
    )
    provider_commands.add_parser(
        "list",
        help="List built-in and declared provider records without provider calls.",
    )
    provider_remove = provider_commands.add_parser(
        "remove",
        help="Remove one declared provider record.",
    )
    provider_remove.add_argument("--provider-id", required=True, help="Declared provider ID.")
    provider_remove.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the non-interactive record removal.",
    )
    subcommands.add_parser("test", help="Run one explicit text-only provider connection test.")
    vision_probe = subcommands.add_parser(
        "vision-probe",
        help="Run one non-persistent color vision probe with the committed fixture.",
    )
    vision_probe.add_argument(
        "--confirm-cloud-upload",
        action="store_true",
        dest="confirm_cloud_upload",
        help="Required explicit confirmation before contacting the configured provider.",
    )
    still_frame_smoke = subcommands.add_parser(
        "still-frame-smoke",
        help="Run one non-persistent still-frame provider smoke with local images.",
    )
    still_frame_smoke.add_argument(
        "--image",
        action="append",
        dest="images",
        required=True,
        type=Path,
        help="Local still image path. Repeat one to three times.",
    )
    still_frame_smoke.add_argument(
        "--confirm-cloud-upload",
        action="store_true",
        dest="confirm_cloud_upload",
        help="Required explicit confirmation before contacting the configured provider.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config_path = args.config_path or default_ai_config_path()
        context = _CliContext(config_path=config_path)
        if args.command == "status":
            return status_command(context, write_snapshot=not args.no_write)
        if args.command == "configure":
            if args.provider_id is not None or args.model_id is not None or args.yes:
                if args.provider_id is None or args.model_id is None or not args.yes:
                    raise AiConfigurationError(
                        "Non-interactive AI configuration requires provider, model, and confirmation."
                    )
                return configure_non_interactive_command(
                    context,
                    provider_id=args.provider_id,
                    model_id=args.model_id,
                )
            return configure_command(context)
        if args.command == "provider":
            if args.provider_command == "add":
                return provider_add_command(
                    context,
                    provider_id=args.provider_id,
                    name=args.name,
                    protocol=args.protocol,
                    base_url=args.base_url,
                    credential_env=args.credential_env,
                    model_ids=tuple(args.model_ids),
                    model_names=tuple(args.model_names),
                    capabilities=tuple(args.capabilities),
                    confirmed=args.yes,
                )
            if args.provider_command == "list":
                return provider_list_command(context)
            if args.provider_command == "remove":
                return provider_remove_command(
                    context,
                    provider_id=args.provider_id,
                    confirmed=args.yes,
                )
        if args.command == "test":
            return test_command(context)
        if args.command == "vision-probe":
            return vision_probe_command(
                context,
                confirm_cloud_upload=args.confirm_cloud_upload,
            )
        if args.command == "still-frame-smoke":
            return still_frame_smoke_command(
                context,
                image_paths=tuple(args.images),
                confirm_cloud_upload=args.confirm_cloud_upload,
            )
    except (AiConfigurationError, AiProviderRecordError) as exc:
        print(f"AI configuration error: {exc}", file=sys.stderr)
        return 2
    return 2


def status_command(
    context: _CliContext,
    *,
    output: Output = print,
    write_snapshot: bool = True,
) -> int:
    """Print sanitized network-free AI status."""
    resolved = _resolve(context)
    if write_snapshot:
        write_ai_status_snapshot(
            AiStatusSnapshot(
                provider_id=resolved.provider_id,
                model_id=resolved.model_id,
                configuration_state="configured" if resolved.configured else "not_configured",
                checked_at_ms=now_ms(),
            ),
            resolved.status_snapshot_path,
        )
    output("AI status")
    output(f"Active provider: {_optional_text(resolved.display_name)}")
    output(f"Model: {_optional_text(resolved.model_id)}")
    output(f"Configuration source: {resolved.source}")
    output(f"Provider source: {_optional_text(resolved.provider_source)}")
    output(f"Credential available to this process: {_yes_no(resolved.credential_available)}")
    output(f"Analysis state: {'configured' if resolved.configured else 'not configured'}")
    output(f"Last connection test: {_last_test_text(resolved)}")
    return 0


def configure_command(
    context: _CliContext,
    *,
    prompt: Input = input,
    output: Output = print,
) -> int:
    """Interactively write non-secret provider/model selection."""
    snapshot = load_ai_server_config_snapshot(context.config_path)
    existing = snapshot.config
    revision = snapshot.revision
    provider_models = {} if existing is None else dict(existing.provider_models)
    definitions = provider_definitions(existing)
    ordered_provider_ids = _ordered_provider_ids(definitions)
    default_provider = existing.active_provider_id if existing is not None else VERCEL_AI_GATEWAY_PROVIDER_ID
    if default_provider not in definitions:
        default_provider = VERCEL_AI_GATEWAY_PROVIDER_ID
    output("AI provider configuration")
    for index, provider_id in enumerate(ordered_provider_ids, start=1):
        definition = definitions[provider_id]
        marker = " [default]" if provider_id == default_provider else ""
        origin = "built-in" if definition.builtin else "declared"
        output(f"{index}. {definition.display_name} ({provider_id}, {origin}){marker}")
    selection = prompt("Select provider [1]: ").strip()
    if selection.lower() in {"q", "quit", "cancel"}:
        output("Cancelled. No configuration was changed.")
        return 1
    provider_id = _selected_provider(selection, default_provider, ordered_provider_ids)
    provider_definition = definitions[provider_id]
    proposed_model = provider_models.get(provider_id) or provider_definition.default_model_id
    output(f"Proposed model: {proposed_model}")
    model_input = prompt("Model ID [default above]: ").strip()
    if model_input.lower() in {"q", "quit", "cancel"}:
        output("Cancelled. No configuration was changed.")
        return 1
    model_id = _selection_model(model_input or proposed_model, provider_definition)
    output("")
    output(f"Provider: {provider_definition.display_name} ({provider_id})")
    output(f"Model: {model_id}")
    confirm = prompt("Activate this non-secret server AI configuration? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        output("Cancelled. No configuration was changed.")
        return 1
    provider_models[provider_id] = model_id
    config = AiServerConfig(
        active_provider_id=provider_id,
        provider_models=provider_models,
        updated_at_ms=now_ms(),
        providers={} if existing is None else dict(existing.providers),
        research=None if existing is None else existing.research,
    )
    write_ai_server_config(config, context.config_path, expected_revision=revision)
    output("AI configuration saved.")
    output(f"Active provider: {provider_definition.display_name}")
    output(f"Model: {model_id}")
    output(f"Configuration path: {context.config_path}")
    output(f"Required credential environment variable: {provider_definition.credential_environment_name}")
    return 0


def configure_non_interactive_command(
    context: _CliContext,
    *,
    provider_id: str,
    model_id: str,
    output: Output = print,
) -> int:
    """Write non-secret provider/model selection for automation."""
    selected_provider_id = validate_provider_id(provider_id)
    selected_model_id = validate_model_id(model_id)
    snapshot = load_ai_server_config_snapshot(context.config_path)
    existing = snapshot.config
    definitions = provider_definitions(existing)
    definition = definitions.get(selected_provider_id)
    if definition is None:
        raise AiConfigurationError("AI provider is not supported.")
    selected_model_id = _selection_model(selected_model_id, definition)
    provider_models = {} if existing is None else dict(existing.provider_models)
    provider_models[selected_provider_id] = selected_model_id
    config = AiServerConfig(
        active_provider_id=selected_provider_id,
        provider_models=provider_models,
        updated_at_ms=now_ms(),
        providers={} if existing is None else dict(existing.providers),
        research=None if existing is None else existing.research,
    )
    write_ai_server_config(
        config, context.config_path, expected_revision=snapshot.revision
    )
    output("AI configuration saved.")
    output(f"Active provider: {definition.display_name}")
    output(f"Model: {selected_model_id}")
    output(f"Configuration path: {context.config_path}")
    output(f"Required credential environment variable: {definition.credential_environment_name}")
    return 0


def provider_add_command(
    context: _CliContext,
    *,
    provider_id: str,
    name: str,
    protocol: str,
    base_url: str,
    credential_env: str,
    model_ids: Sequence[str],
    model_names: Sequence[str],
    capabilities: Sequence[str],
    confirmed: bool,
    output: Output = print,
) -> int:
    """Create or update one declared non-secret provider record."""
    if not confirmed:
        raise AiConfigurationError("AI provider record writes require --yes.")
    selected_provider_id = validate_provider_identifier(provider_id)
    if selected_provider_id in BUILTIN_PROVIDER_IDS:
        raise AiConfigurationError("Built-in AI providers cannot be declared.")
    selected_name = validate_provider_display_name(name)
    selected_protocol = validate_declared_protocol(protocol)
    selected_base_url = validate_declared_base_url(base_url)
    selected_credential_env = validate_credential_environment_name(credential_env)
    if not model_ids:
        raise AiConfigurationError("At least one --model-id is required.")
    if model_names and len(model_names) != len(model_ids):
        raise AiConfigurationError(
            "Provide one --model-name per --model-id or none at all."
        )
    selected_capabilities = validate_declared_capabilities(list(capabilities))
    resolved_model_names = list(model_names) if model_names else list(model_ids)
    models: list[AiProviderModel] = []
    seen_model_ids: set[str] = set()
    for raw_model_id, raw_model_name in zip(model_ids, resolved_model_names):
        selected_model_id = validate_model_identifier(raw_model_id)
        if selected_model_id in seen_model_ids:
            raise AiConfigurationError("AI model IDs must be unique.")
        seen_model_ids.add(selected_model_id)
        models.append(
            AiProviderModel(
                model_id=selected_model_id,
                display_name=validate_provider_display_name(raw_model_name),
                capabilities=selected_capabilities,
            )
        )
    record = AiProviderRecord(
        provider_id=selected_provider_id,
        display_name=selected_name,
        protocol=selected_protocol,
        base_url=selected_base_url,
        credential_env=selected_credential_env,
        models=tuple(models),
        source="declared",
    )
    snapshot = load_ai_server_config_snapshot(context.config_path)
    existing = snapshot.config
    providers = {} if existing is None else dict(existing.providers)
    updated = selected_provider_id in providers
    providers[selected_provider_id] = record
    config = AiServerConfig(
        active_provider_id=(
            existing.active_provider_id if existing is not None else VERCEL_AI_GATEWAY_PROVIDER_ID
        ),
        provider_models={} if existing is None else dict(existing.provider_models),
        updated_at_ms=now_ms(),
        providers=providers,
        research=None if existing is None else existing.research,
    )
    write_ai_server_config(
        config, context.config_path, expected_revision=snapshot.revision
    )
    output("AI provider record updated." if updated else "AI provider record saved.")
    output(f"Provider: {selected_name} ({selected_provider_id})")
    output(f"Protocol: {selected_protocol}")
    output(f"Base URL: {selected_base_url}")
    output(f"Credential environment variable: {selected_credential_env}")
    for model in models:
        output(f"Model: {model.model_id}")
    return 0


def provider_list_command(
    context: _CliContext,
    *,
    output: Output = print,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Print sanitized built-in and declared provider records."""
    source = os.environ if environ is None else environ
    existing = load_ai_server_config(context.config_path)
    definitions = provider_definitions(existing)
    output("AI providers")
    for provider_id in _ordered_provider_ids(definitions):
        definition = definitions[provider_id]
        credential = load_ai_credential(definition.credential_environment_name, source)
        model_ids = ", ".join(model.model_id for model in definition.models)
        output(
            f"- {definition.provider_id} | {definition.display_name} | "
            f"source={definition.source} | protocol={definition.protocol} | "
            f"base_url={definition.base_url} | "
            f"credential_env={definition.credential_environment_name} | "
            f"credential_available={_yes_no(credential is not None)} | models={model_ids}"
        )
    return 0


def provider_remove_command(
    context: _CliContext,
    *,
    provider_id: str,
    confirmed: bool,
    output: Output = print,
) -> int:
    """Remove one declared non-secret provider record."""
    if not confirmed:
        raise AiConfigurationError("AI provider record removal requires --yes.")
    selected_provider_id = validate_provider_identifier(provider_id)
    if selected_provider_id in BUILTIN_PROVIDER_IDS:
        raise AiConfigurationError("Built-in AI providers cannot be removed.")
    snapshot = load_ai_server_config_snapshot(context.config_path)
    existing = snapshot.config
    if existing is None or selected_provider_id not in existing.providers:
        raise AiConfigurationError("AI provider record was not found.")
    if existing.active_provider_id == selected_provider_id:
        raise AiConfigurationError("The active AI provider cannot be removed.")
    providers = dict(existing.providers)
    providers.pop(selected_provider_id)
    provider_models = {
        key: value
        for key, value in existing.provider_models.items()
        if key != selected_provider_id
    }
    config = AiServerConfig(
        active_provider_id=existing.active_provider_id,
        provider_models=provider_models,
        updated_at_ms=now_ms(),
        providers=providers,
        research=existing.research,
    )
    write_ai_server_config(
        config, context.config_path, expected_revision=snapshot.revision
    )
    output("AI provider record removed.")
    output(f"Provider: {selected_provider_id}")
    return 0


def test_command(context: _CliContext, *, output: Output = print) -> int:
    """Run one explicit text-only provider connection test."""
    resolved = _resolve(context)
    if resolved.source == "unconfigured":
        output("AI test: not configured")
        output("Configure server AI provider state before testing.")
        return 2
    if not resolved.credential_available or resolved.provider is None:
        output("AI test: authentication_failed")
        output("Credential available to this process: no")
        output(f"Required credential environment variable: {resolved.credential_environment_name}")
        return 2
    lock_path = resolved.test_state_path.parent / ".test.lock"
    lock = _acquire_test_lock(lock_path)
    if lock is None:
        output("AI test: provider_error")
        output("Another AI connection test is already running.")
        return 2
    try:
        category = "success"
        exit_code = 0
        try:
            resolved.provider.test_connection()
        except Exception as exc:
            category = classify_provider_exception(exc)
            exit_code = 2
        write_ai_test_state(
            AiTestState(
                provider_id=resolved.provider_id,
                model_id=resolved.model_id,
                status=category,
                tested_at_ms=now_ms(),
            ),
            resolved.test_state_path,
        )
        output(f"AI test: {category}")
        output(f"Provider: {resolved.display_name}")
        output(f"Model: {resolved.model_id}")
        return exit_code
    finally:
        lock.release()


def vision_probe_command(
    context: _CliContext,
    *,
    confirm_cloud_upload: bool,
    output: Output = print,
) -> int:
    """Run one non-persistent provider-neutral color vision probe."""
    if not confirm_cloud_upload:
        output("AI vision probe: confirmation_required")
        output("Pass --confirm-cloud-upload to contact the configured provider.")
        return 2
    resolved = _resolve(context)
    if resolved.source == "unconfigured":
        output("AI vision probe: not configured")
        output("Configure server AI provider state before the vision probe.")
        return 2
    if not resolved.credential_available or resolved.provider is None:
        output("AI vision probe: authentication_failed")
        output("Credential available to this process: no")
        output(f"Required credential environment variable: {resolved.credential_environment_name}")
        return 2
    if not _model_declares_vision(resolved):
        output("AI vision probe: unsupported_model_capability")
        output("The selected model does not declare the vision_input capability.")
        return 2
    state_path = default_vision_probe_state_path(resolved.config_path)
    lock_path = state_path.parent / ".vision-probe.lock"
    lock = _acquire_vision_probe_lock(lock_path)
    if lock is None:
        output("AI vision probe: provider_error")
        output("Another AI provider operation is already running.")
        return 2
    try:
        status = "provider_error"
        matched = False
        observed_color: str | None = None
        exit_code = 2
        try:
            image_png = load_vision_probe_fixture()
            content_text = resolved.provider.probe_vision(
                prompt=VISION_PROBE_PROMPT,
                image_png=image_png,
            )
            matched, observed_color = match_expected_color(content_text)
            status = "success" if matched else "mismatch"
            exit_code = 0 if matched else 2
        except Exception as exc:
            status = classify_provider_exception(exc)
        write_vision_probe_state(
            VisionProbeState(
                provider_id=resolved.provider_id,
                model_id=resolved.model_id,
                status=status,
                matched=matched,
                observed_color=observed_color,
                probed_at_ms=now_ms(),
            ),
            state_path,
        )
        output(f"AI vision probe: {status}")
        output(f"Expected color: {EXPECTED_COLOR}")
        observed_text = "none" if observed_color is None else observed_color
        output(f"Observed color: {observed_text}")
        return exit_code
    finally:
        lock.release()


def still_frame_smoke_command(
    context: _CliContext,
    *,
    image_paths: tuple[Path, ...],
    confirm_cloud_upload: bool,
    output: Output = print,
) -> int:
    """Run one non-persistent still-frame provider smoke."""
    if not confirm_cloud_upload:
        output("AI still-frame smoke: confirmation_required")
        output("Pass --confirm-cloud-upload to contact the configured provider.")
        return 2
    resolved = _resolve(context)
    if resolved.source == "unconfigured":
        output("AI still-frame smoke: not configured")
        output("Configure server AI provider state before still-frame smoke.")
        return 2
    if resolved.provider_id != DEFAULT_PROVIDER_ID:
        output("AI still-frame smoke: unsupported_provider")
        output("Still-frame smoke requires the configured NVIDIA NIM provider.")
        return 2
    if not resolved.credential_available or resolved.provider is None:
        output("AI still-frame smoke: authentication_failed")
        output("Credential available to this process: no")
        output(f"Required credential environment variable: {resolved.credential_environment_name}")
        return 2
    try:
        images = prepare_still_frame_smoke_images(image_paths)
        request = build_still_frame_smoke_request(images)
    except FrameNestStillFrameSmokeError:
        output("AI still-frame smoke: invalid_input")
        output(STILL_FRAME_SMOKE_INVALID_MESSAGE)
        return 2
    try:
        suggestion = resolved.provider.suggest(request)
    except Exception as exc:
        output(f"AI still-frame smoke: {classify_provider_exception(exc)}")
        return 2
    output("AI still-frame smoke: success")
    output(f"Provider: {resolved.display_name}")
    output(f"Model: {resolved.model_id}")
    output(f"Sent frames: {len(images)}")
    for index, image in enumerate(images, start=1):
        output(
            f"Frame {index}: {image.width}x{image.height} {image.format_name} {image.byte_size} bytes"
        )
    output(f"Suggestion title length: {len(suggestion.title)}")
    output(f"Suggestion tag count: {len(suggestion.tags)}")
    output(f"Prompt version: {suggestion.prompt_version}")
    return 0


def _resolve(context: _CliContext) -> ResolvedAiProvider:
    try:
        settings = load_settings()
    except IdentityEnvironmentConfigurationError as exc:
        raise AiConfigurationError(str(exc)) from exc
    except FrameNestConfigurationError as exc:
        raise AiConfigurationError(
            "Kronika configuration could not be loaded."
        ) from exc
    return resolve_ai_provider(settings, config_path=context.config_path)


def _ordered_provider_ids(
    definitions: Mapping[str, AiProviderDefinition],
) -> tuple[str, ...]:
    builtin_order = tuple(
        provider_id for provider_id in PROVIDER_ORDER if provider_id in definitions
    )
    declared_order = tuple(
        sorted(
            provider_id
            for provider_id in definitions
            if provider_id not in BUILTIN_PROVIDER_IDS
        )
    )
    return builtin_order + declared_order


def _selected_provider(
    selection: str,
    default_provider: str,
    ordered_provider_ids: Sequence[str],
) -> str:
    if not selection:
        return validate_provider_id(default_provider)
    if selection.isdigit():
        index = int(selection)
        if 1 <= index <= len(ordered_provider_ids):
            return ordered_provider_ids[index - 1]
        raise AiConfigurationError("AI provider is not supported.")
    provider_id = validate_provider_id(selection)
    if provider_id not in ordered_provider_ids:
        raise AiConfigurationError("AI provider is not supported.")
    return provider_id


def _selection_model(model_id: str, definition: AiProviderDefinition) -> str:
    validated = validate_model_id(model_id)
    if not definition.builtin and all(
        model.model_id != validated for model in definition.models
    ):
        raise AiConfigurationError("Model is not declared by the selected AI provider.")
    return validated


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _optional_text(value: str | None) -> str:
    return "none" if value is None else value


def _last_test_text(resolved: ResolvedAiProvider) -> str:
    if resolved.last_test is None:
        return "not tested"
    if resolved.last_test.status == "success":
        return "success"
    return f"safe failure ({resolved.last_test.status})"


def _acquire_test_lock(lock_path: Path) -> AiActivityLock | None:
    try:
        return acquire_ai_activity_lock(lock_path)
    except AiActivityLockError as exc:
        raise AiConfigurationError("AI test lock could not be created.") from exc


def _acquire_vision_probe_lock(lock_path: Path) -> AiActivityLock | None:
    try:
        return acquire_ai_activity_lock(lock_path)
    except AiActivityLockError as exc:
        raise AiConfigurationError("AI vision probe lock could not be created.") from exc


def _model_declares_vision(resolved: ResolvedAiProvider) -> bool:
    if resolved.model_id is None:
        return False
    return "vision_input" in resolved.capabilities_for(resolved.model_id)


if __name__ == "__main__":
    raise SystemExit(main())
