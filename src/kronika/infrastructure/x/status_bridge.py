"""Isolated X status-bridge seam and bounded public-photo transport.

Production inspect and photo download run as
``sys.executable -I -m kronika.infrastructure.x.status_bridge``. The module
calls pinned ``TwitterIE._extract_status`` with an empty cookie jar and no
``.netrc``, browser cookies, CLI config, or plugin discovery. Normalized
inspect JSON never includes CDN URLs. Photo bytes are fetched only inside this
process after the same seam re-resolves the media key.
"""

from __future__ import annotations

import hashlib
import http.client
import http.cookiejar
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import sys
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kronika.domain.x_acquisition import (
    MAX_ASSETS_PER_POST,
    MAX_VIDEO_DURATION_SECONDS,
    XMediaType,
    XNormalizedAssetDescriptor,
    XNormalizedInspection,
    X_VARIANT_ANIMATED_GIF_LITERAL,
    X_VARIANT_ANIMATED_GIF_MP4,
    X_VARIANT_PHOTO_JPEG,
    X_VARIANT_PHOTO_PNG,
    X_VARIANT_VIDEO_MP4,
    accept_x_post_url,
)

PINNED_YTDLP_VERSION = "2026.07.04"
PBS_PHOTO_HOST = "pbs.twimg.com"
PHOTO_CONNECT_TIMEOUT_SECONDS = 30.0
PHOTO_READ_TIMEOUT_SECONDS = 30.0
PHOTO_MAX_BYTES = 64 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG"
_FORBIDDEN_PAYLOAD_HOSTS = ("pbs.twimg.com", "video.twimg.com")
ExtractStatus = Callable[[str], object]
PhotoResolver = Callable[[str, int], list[tuple[str, int]]]
PhotoTransport = Callable[[str, str, str, float], "PhotoHttpResult"]


class StatusBridgeError(RuntimeError):
    """Sanitized failure of the isolated status bridge or photo transport."""

    def __init__(self, code: str, message: str = "X status bridge failed.") -> None:
        super().__init__(message)
        self.code = code


class PhotoHttpResult:
    """Injected or live HTTP response used by photo transport tests."""

    __slots__ = ("status", "headers", "body", "chunks")

    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        chunks: tuple[bytes, ...] | None = None,
    ) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.body = body
        self.chunks = chunks


def attest_pinned_extractor() -> str:
    """Fail closed unless runtime yt-dlp and TwitterIE._extract_status match the pin."""
    try:
        from yt_dlp.extractor.twitter import TwitterIE
        from yt_dlp.version import __version__ as runtime_version
    except Exception as exc:
        raise StatusBridgeError(
            "X_EXTRACTOR_UNAVAILABLE", "X extractor is unavailable."
        ) from exc
    if runtime_version != PINNED_YTDLP_VERSION:
        raise StatusBridgeError(
            "X_EXTRACTOR_UNAVAILABLE", "X extractor version is not pinned."
        )
    extract_status = getattr(TwitterIE, "_extract_status", None)
    if not callable(extract_status):
        raise StatusBridgeError(
            "X_EXTRACTOR_UNAVAILABLE", "X extractor seam is unavailable."
        )
    return runtime_version


def isolated_extractor_context() -> dict[str, object]:
    """Return a cookie-free, config-free, plugin-free TwitterIE context."""
    attest_pinned_extractor()
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.twitter import TwitterIE
    from yt_dlp.globals import plugin_dirs

    previous_plugin_dirs = plugin_dirs.value
    plugin_dirs.value = []
    try:
        ydl = YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "ignoreconfig": True,
                "usenetrc": False,
                "netrc_location": None,
                "netrc_cmd": None,
                "cookiefile": None,
                "cookiesfrombrowser": None,
                "username": None,
                "password": None,
                "skip_download": True,
                "socket_timeout": int(PHOTO_CONNECT_TIMEOUT_SECONDS),
                "cachedir": False,
                "noplaylist": False,
            }
        )
        cookiejar = ydl.cookiejar
        cookiejar.clear()
        if not isinstance(cookiejar, http.cookiejar.CookieJar):
            raise StatusBridgeError(
                "X_EXTRACTOR_UNAVAILABLE", "X extractor cookie jar is unavailable."
            )
        extractor = TwitterIE(ydl)
        return {
            "ydl": ydl,
            "extractor": extractor,
            "cookiejar": cookiejar,
            "usenetrc": bool(ydl.params.get("usenetrc")),
            "ignoreconfig": bool(ydl.params.get("ignoreconfig")),
            "cookiefile": ydl.params.get("cookiefile"),
            "cookiesfrombrowser": ydl.params.get("cookiesfrombrowser"),
            "plugin_dirs": list(plugin_dirs.value),
        }
    finally:
        plugin_dirs.value = previous_plugin_dirs


def extract_legacy_status(
    post_id: str,
    *,
    extract_status: ExtractStatus | None = None,
) -> dict[str, object]:
    """Return the pinned TwitterIE status dict, never the public inspect payload."""
    if not isinstance(post_id, str) or not post_id.isdigit():
        raise StatusBridgeError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
    if extract_status is not None:
        raw = extract_status(post_id)
    else:
        context = isolated_extractor_context()
        extractor = context["extractor"]
        try:
            raw = extractor._extract_status(post_id)
        except StatusBridgeError:
            raise
        except Exception as exc:
            raise _status_error_from_extractor(exc) from exc
    if not isinstance(raw, dict) or not raw:
        raise StatusBridgeError("X_POST_UNAVAILABLE", "X post is not publicly available.")
    return raw


def inspect_post(
    post_id: str,
    submitted_url: str,
    *,
    extract_status: ExtractStatus | None = None,
) -> XNormalizedInspection:
    """Normalize one public X post without emitting raw status JSON or CDN URLs."""
    attest_pinned_extractor()
    identity = accept_x_post_url(submitted_url)
    if identity.post_id != post_id:
        raise StatusBridgeError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
    status = extract_legacy_status(post_id, extract_status=extract_status)
    inspection = normalize_status(status, identity.post_id, identity.canonical_url)
    _reject_forbidden_hosts(inspection)
    return inspection


def download_photo(
    post_id: str,
    submitted_url: str,
    *,
    source_media_key: str,
    selected_variant: str,
    destination: Path,
    extract_status: ExtractStatus | None = None,
    resolver: PhotoResolver | None = None,
    transport: PhotoTransport | None = None,
) -> str:
    """Re-resolve one photo and fetch it into destination without exposing the URL."""
    inspection, internals = inspect_with_internals(
        post_id,
        submitted_url,
        extract_status=extract_status,
    )
    matched = _match_internal(
        internals,
        source_media_key=source_media_key,
        selected_variant=selected_variant,
        media_type=XMediaType.IMAGE,
    )
    if matched is None or matched.orig_url is None:
        raise StatusBridgeError(
            "X_SOURCE_MEDIA_CHANGED", "X source media is no longer available."
        )
    return fetch_pbs_photo(
        matched.orig_url,
        destination,
        expected_variant=selected_variant,
        resolver=resolver,
        transport=transport,
    )


def inspect_with_internals(
    post_id: str,
    submitted_url: str,
    *,
    extract_status: ExtractStatus | None = None,
) -> tuple[XNormalizedInspection, tuple["_InternalMedia", ...]]:
    attest_pinned_extractor()
    identity = accept_x_post_url(submitted_url)
    if identity.post_id != post_id:
        raise StatusBridgeError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
    status = extract_legacy_status(post_id, extract_status=extract_status)
    inspection, internals = _normalize_status_with_internals(
        status, identity.post_id, identity.canonical_url
    )
    _reject_forbidden_hosts(inspection)
    return inspection, internals


def normalize_status(
    status: object,
    post_id: str,
    canonical_url: str,
) -> XNormalizedInspection:
    inspection, _internals = _normalize_status_with_internals(status, post_id, canonical_url)
    return inspection


def inspection_payload(inspection: XNormalizedInspection) -> dict[str, object]:
    """JSON-safe inspect payload with no raw media URLs."""
    payload = {
        "post_id": inspection.post_id,
        "canonical_url": inspection.canonical_url,
        "post_text": inspection.post_text,
        "posted_at_ms": inspection.posted_at_ms,
        "author_stable_id": inspection.author_stable_id,
        "author_handle": inspection.author_handle,
        "author_display_name": inspection.author_display_name,
        "extractor_version": inspection.extractor_version,
        "assets": [
            {
                "ordinal": asset.ordinal,
                "media_type": asset.media_type.value,
                "expected_mime": asset.expected_mime,
                "source_media_key": asset.source_media_key,
                "width": asset.width,
                "height": asset.height,
                "duration_seconds": asset.duration_seconds,
                "selected_variant": asset.selected_variant,
                "provider_download_index": asset.provider_download_index,
            }
            for asset in inspection.assets
        ],
    }
    _reject_forbidden_hosts(inspection)
    return payload


def inspection_from_payload(
    payload: object, *, post_id: str, submitted_url: str
) -> XNormalizedInspection:
    if not isinstance(payload, dict):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    error_code = payload.get("error_code")
    if isinstance(error_code, str) and error_code:
        raise StatusBridgeError(error_code, "X status bridge failed.")
    encoded = json.dumps(payload)
    if any(host in encoded for host in _FORBIDDEN_PAYLOAD_HOSTS):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    identity = accept_x_post_url(submitted_url)
    if payload.get("post_id") != post_id or identity.post_id != post_id:
        raise StatusBridgeError("X_URL_INVALID_POST_ID", "Invalid X post identity.")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    assets: list[XNormalizedAssetDescriptor] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
        media_type = _media_type_value(item.get("media_type"))
        source_media_key = _clean_text(item.get("source_media_key"))
        selected_variant = _clean_text(item.get("selected_variant"))
        if media_type is None or source_media_key is None or selected_variant is None:
            raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
        assets.append(
            XNormalizedAssetDescriptor(
                ordinal=_require_int(item.get("ordinal"), "ordinal"),
                media_type=media_type,
                expected_mime=_require_text(item.get("expected_mime")),
                source_media_key=source_media_key,
                width=_optional_int(item.get("width")),
                height=_optional_int(item.get("height")),
                duration_seconds=_optional_int(item.get("duration_seconds")),
                selected_variant=selected_variant,
                provider_download_index=_optional_int(item.get("provider_download_index")),
            )
        )
    canonical = payload.get("canonical_url")
    canonical_url = canonical if isinstance(canonical, str) else identity.canonical_url
    return XNormalizedInspection(
        post_id=post_id,
        canonical_url=canonical_url,
        post_text=_clean_text(payload.get("post_text")),
        posted_at_ms=_optional_int(payload.get("posted_at_ms")),
        author_stable_id=_clean_text(payload.get("author_stable_id")),
        author_handle=_clean_text(payload.get("author_handle")),
        author_display_name=_clean_text(payload.get("author_display_name")),
        assets=tuple(assets),
        extractor_version=_clean_text(payload.get("extractor_version")),
    )


def fetch_pbs_photo(
    url: str,
    destination: Path,
    *,
    expected_variant: str,
    resolver: PhotoResolver | None = None,
    transport: PhotoTransport | None = None,
) -> str:
    """Fetch one validated pbs.twimg.com orig JPEG/PNG into destination."""
    parsed = validate_pbs_photo_url(url, expected_variant=expected_variant)
    destination = Path(destination)
    if not destination.is_absolute():
        raise StatusBridgeError("X_STAGING_FAILED", "X staging path is invalid.")
    partial = destination.with_name(destination.name + ".part")
    selected_ip = _resolve_global_address(
        parsed.hostname, resolver=resolver or _default_resolver
    )
    request_target = parsed.path
    if parsed.query:
        request_target = f"{parsed.path}?{parsed.query}"
    try:
        result = (transport or _default_transport)(
            selected_ip, PBS_PHOTO_HOST, request_target, PHOTO_CONNECT_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_DOWNLOAD_TIMEOUT", "X photo download timed out.") from exc
    except StatusBridgeError:
        _cleanup_paths(partial, destination)
        raise
    except OSError as exc:
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_STAGING_FAILED", "X photo download failed.") from exc
    if result.status in {301, 302, 303, 307, 308}:
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo redirect is denied.")
    if result.status != 200:
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo response is invalid.")
    content_type = (result.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    expected_mime = (
        "image/jpeg" if expected_variant == X_VARIANT_PHOTO_JPEG else "image/png"
    )
    if content_type not in _accepted_content_types(expected_variant):
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo type is unsupported.")
    content_length = result.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError) as exc:
            _cleanup_paths(partial, destination)
            raise StatusBridgeError(
                "X_MEDIA_TYPE_UNSUPPORTED", "X photo response is invalid."
            ) from exc
        if declared <= 0 or declared > PHOTO_MAX_BYTES:
            _cleanup_paths(partial, destination)
            code = "X_MEDIA_TOO_LARGE" if declared > PHOTO_MAX_BYTES else "X_MEDIA_TYPE_UNSUPPORTED"
            raise StatusBridgeError(code, "X photo exceeds size limit.")
    chunks = result.chunks
    if chunks is None:
        chunks = (result.body or b"",)
    return _stream_photo_to_destination(
        chunks,
        destination=destination,
        partial=partial,
        expected_mime=expected_mime,
    )


def validate_pbs_photo_url(url: str, *, expected_variant: str) -> object:
    """Accept only HTTPS pbs.twimg.com /media orig JPEG/PNG URLs."""
    if not isinstance(url, str) or not url:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    if parsed.hostname != PBS_PHOTO_HOST:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo host is not allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    if parsed.fragment:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    if parsed.port not in {None, 443}:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    if "\\" in parsed.path or not parsed.path.startswith("/media/") or parsed.path == "/media/":
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    allowed_keys = {"name", "format"}
    values: dict[str, str] = {}
    for key, value in pairs:
        if key not in allowed_keys or key in values:
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
        values[key] = value
    if values.get("name") != "orig":
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    fmt = values.get("format")
    if expected_variant == X_VARIANT_PHOTO_JPEG:
        if fmt not in {None, "jpg", "jpeg"}:
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo type is unsupported.")
    elif expected_variant == X_VARIANT_PHOTO_PNG:
        if fmt not in {None, "png"}:
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo type is unsupported.")
    else:
        raise StatusBridgeError("X_SOURCE_MEDIA_CHANGED", "X source media is no longer available.")
    return parsed


def orig_photo_url(media_url_https: str, *, selected_variant: str) -> str:
    """Rebuild a candidate orig URL. Callers must not persist or log the result."""
    if not isinstance(media_url_https, str) or not media_url_https:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo URL is invalid.")
    parsed = urlsplit(media_url_https)
    if parsed.scheme != "https" or parsed.hostname != PBS_PHOTO_HOST:
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo host is not allowed.")
    path = parsed.path.split(":", 1)[0]
    pairs = parse_qsl(parsed.query, keep_blank_values=False)
    original_format = None
    for key, value in pairs:
        if key == "format":
            original_format = value.lower()
    if original_format == "webp" or path.lower().endswith(".webp"):
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo type is unsupported.")
    if selected_variant == X_VARIANT_PHOTO_JPEG:
        fmt = "jpg"
    elif selected_variant == X_VARIANT_PHOTO_PNG:
        fmt = "png"
    else:
        raise StatusBridgeError("X_SOURCE_MEDIA_CHANGED", "X source media is no longer available.")
    rebuilt = urlunsplit(
        (
            "https",
            PBS_PHOTO_HOST,
            path,
            urlencode((("format", fmt), ("name", "orig"))),
            "",
        )
    )
    validate_pbs_photo_url(rebuilt, expected_variant=selected_variant)
    return rebuilt


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X status bridge command is invalid.")
        command = args[0]
        if command == "inspect":
            if len(args) != 2:
                raise StatusBridgeError(
                    "X_EXTRACTOR_MALFORMED", "X status bridge command is invalid."
                )
            post_id = args[1]
            submitted_url = f"https://x.com/bridge/status/{post_id}"
            inspection = inspect_post(post_id, submitted_url)
            sys.stdout.write(json.dumps(inspection_payload(inspection)))
            return 0
        if command == "download-photo":
            if len(args) != 5:
                raise StatusBridgeError(
                    "X_EXTRACTOR_MALFORMED", "X status bridge command is invalid."
                )
            post_id, source_media_key, selected_variant, dest = args[1:5]
            submitted_url = f"https://x.com/bridge/status/{post_id}"
            download_photo(
                post_id,
                submitted_url,
                source_media_key=source_media_key,
                selected_variant=selected_variant,
                destination=Path(dest),
            )
            return 0
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X status bridge command is invalid.")
    except StatusBridgeError as exc:
        sys.stdout.write(json.dumps({"error_code": exc.code}))
        return 2


class _InternalMedia:
    __slots__ = (
        "descriptor",
        "orig_url",
    )

    def __init__(
        self,
        descriptor: XNormalizedAssetDescriptor,
        *,
        orig_url: str | None,
    ) -> None:
        self.descriptor = descriptor
        self.orig_url = orig_url


def _normalize_status_with_internals(
    status: object,
    post_id: str,
    canonical_url: str,
) -> tuple[XNormalizedInspection, tuple[_InternalMedia, ...]]:
    if not isinstance(status, dict):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    if status.get("user") and isinstance(status["user"], dict) and status["user"].get("protected"):
        raise StatusBridgeError("X_POST_PROTECTED", "X post is not publicly available.")
    media_rows = _media_rows(status)
    assets: list[XNormalizedAssetDescriptor] = []
    internals: list[_InternalMedia] = []
    provider_download_index = 0
    for row in media_rows:
        classified = _classify_media_row(row)
        if classified is None:
            continue
        media_type, expected_mime, selected_variant, orig_url, duration_seconds, width, height, source_media_key = classified
        download_index = None
        if media_type in {XMediaType.VIDEO, XMediaType.ANIMATED_GIF}:
            download_index = provider_download_index
            provider_download_index += 1
        descriptor = XNormalizedAssetDescriptor(
            ordinal=len(assets),
            media_type=media_type,
            expected_mime=expected_mime,
            source_media_key=source_media_key,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            selected_variant=selected_variant,
            provider_download_index=download_index,
        )
        assets.append(descriptor)
        internals.append(_InternalMedia(descriptor, orig_url=orig_url))
        if len(assets) > MAX_ASSETS_PER_POST:
            raise StatusBridgeError("X_TOO_MANY_ASSETS", "X post exceeds asset limit.")
    if not assets:
        raise StatusBridgeError("X_NO_SUPPORTED_MEDIA", "X post has no media.")
    user = status.get("user") if isinstance(status.get("user"), dict) else {}
    handle = _clean_text(user.get("screen_name"))
    resolved_canonical = canonical_url
    if handle is not None and not handle.startswith("i"):
        try:
            identity = accept_x_post_url(f"https://x.com/{handle}/status/{post_id}")
            resolved_canonical = identity.canonical_url
        except Exception:
            resolved_canonical = canonical_url
    return (
        XNormalizedInspection(
            post_id=post_id,
            canonical_url=resolved_canonical,
            post_text=_clean_text(status.get("full_text") or status.get("text")),
            posted_at_ms=_posted_at_ms(status),
            author_stable_id=_clean_text(user.get("id_str") or user.get("id")),
            author_handle=_clean_text(user.get("screen_name")),
            author_display_name=_clean_text(user.get("name")),
            assets=tuple(assets),
            extractor_version=PINNED_YTDLP_VERSION,
        ),
        tuple(internals),
    )


def _media_rows(status: dict[str, object]) -> list[dict[str, object]]:
    entities = status.get("extended_entities")
    if not isinstance(entities, dict):
        return []
    media = entities.get("media")
    if not isinstance(media, list):
        return []
    return [row for row in media if isinstance(row, dict)]


def _classify_media_row(
    row: dict[str, object],
) -> tuple[XMediaType, str, str, str | None, int | None, int | None, int | None, str] | None:
    source_media_key = _clean_text(row.get("id_str") or row.get("id"))
    if source_media_key is None:
        return None
    media_kind = str(row.get("type") or "").strip().lower()
    original = row.get("original_info") if isinstance(row.get("original_info"), dict) else {}
    width = _optional_int(original.get("width") or row.get("original_width") or row.get("width"))
    height = _optional_int(original.get("height") or row.get("original_height") or row.get("height"))
    if width is not None and (width < 0 or width > 100_000):
        raise StatusBridgeError("X_DIMENSIONS_TOO_LARGE", "X dimensions are invalid.")
    if height is not None and (height < 0 or height > 100_000):
        raise StatusBridgeError("X_DIMENSIONS_TOO_LARGE", "X dimensions are invalid.")
    if media_kind == "photo":
        variant, mime = _photo_variant(row)
        if variant is None or mime is None:
            return None
        media_url = row.get("media_url_https") or row.get("media_url")
        if not isinstance(media_url, str):
            return None
        orig_url = orig_photo_url(media_url, selected_variant=variant)
        return (
            XMediaType.IMAGE,
            mime,
            variant,
            orig_url,
            None,
            width,
            height,
            source_media_key,
        )
    if media_kind in {"video", "animated_gif"}:
        duration = _video_duration_seconds(row)
        if duration is not None and duration > MAX_VIDEO_DURATION_SECONDS:
            raise StatusBridgeError("X_DURATION_TOO_LONG", "X media exceeds duration limit.")
        if media_kind == "animated_gif":
            if _has_literal_gif(row):
                return (
                    XMediaType.ANIMATED_GIF,
                    "image/gif",
                    X_VARIANT_ANIMATED_GIF_LITERAL,
                    None,
                    duration,
                    width,
                    height,
                    source_media_key,
                )
            return (
                XMediaType.ANIMATED_GIF,
                "video/mp4",
                X_VARIANT_ANIMATED_GIF_MP4,
                None,
                duration,
                width,
                height,
                source_media_key,
            )
        return (
            XMediaType.VIDEO,
            "video/mp4",
            X_VARIANT_VIDEO_MP4,
            None,
            duration,
            width,
            height,
            source_media_key,
        )
    return None


def _photo_variant(row: dict[str, object]) -> tuple[str, str] | tuple[None, None]:
    media_url = str(row.get("media_url_https") or row.get("media_url") or "")
    parsed = urlsplit(media_url)
    fmt = None
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key == "format":
            fmt = value.lower()
    path = parsed.path.split(":", 1)[0].lower()
    if fmt == "webp" or path.endswith(".webp"):
        return None, None
    if fmt in {"png"} or path.endswith(".png"):
        return X_VARIANT_PHOTO_PNG, "image/png"
    if fmt in {"jpg", "jpeg", None} or path.endswith((".jpg", ".jpeg")):
        return X_VARIANT_PHOTO_JPEG, "image/jpeg"
    return None, None


def _has_literal_gif(row: dict[str, object]) -> bool:
    info = row.get("video_info")
    if not isinstance(info, dict):
        return False
    variants = info.get("variants")
    if not isinstance(variants, list):
        return False
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = str(variant.get("url") or "")
        content_type = str(variant.get("content_type") or "").lower()
        if content_type == "image/gif" or ".gif" in url.split("?", 1)[0].lower():
            return True
    return False


def _video_duration_seconds(row: dict[str, object]) -> int | None:
    info = row.get("video_info") if isinstance(row.get("video_info"), dict) else {}
    millis = info.get("duration_millis")
    if isinstance(millis, (int, float)) and not isinstance(millis, bool):
        return int(millis / 1000)
    duration = row.get("duration")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return int(duration)
    return None


def _match_internal(
    internals: tuple[_InternalMedia, ...],
    *,
    source_media_key: str,
    selected_variant: str,
    media_type: XMediaType,
) -> _InternalMedia | None:
    for item in internals:
        descriptor = item.descriptor
        if descriptor.source_media_key != source_media_key:
            continue
        if descriptor.media_type is not media_type:
            return None
        if descriptor.selected_variant != selected_variant:
            return None
        return item
    return None


def _posted_at_ms(status: dict[str, object]) -> int | None:
    timestamp = status.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        if timestamp > 10_000_000_000:
            return int(timestamp)
        return int(timestamp * 1000)
    created = status.get("created_at")
    if not isinstance(created, str) or not created:
        return None
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(created)
        return int(parsed.timestamp() * 1000)
    except Exception:
        return None


def _reject_forbidden_hosts(inspection: XNormalizedInspection) -> None:
    encoded = json.dumps(inspection_payload_without_check(inspection))
    if any(host in encoded for host in _FORBIDDEN_PAYLOAD_HOSTS):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")


def inspection_payload_without_check(inspection: XNormalizedInspection) -> dict[str, object]:
    return {
        "post_id": inspection.post_id,
        "canonical_url": inspection.canonical_url,
        "post_text": inspection.post_text,
        "posted_at_ms": inspection.posted_at_ms,
        "author_stable_id": inspection.author_stable_id,
        "author_handle": inspection.author_handle,
        "author_display_name": inspection.author_display_name,
        "extractor_version": inspection.extractor_version,
        "assets": [
            {
                "ordinal": asset.ordinal,
                "media_type": asset.media_type.value,
                "expected_mime": asset.expected_mime,
                "source_media_key": asset.source_media_key,
                "width": asset.width,
                "height": asset.height,
                "duration_seconds": asset.duration_seconds,
                "selected_variant": asset.selected_variant,
                "provider_download_index": asset.provider_download_index,
            }
            for asset in inspection.assets
        ],
    }


def _status_error_from_extractor(exc: BaseException) -> StatusBridgeError:
    text = str(exc).lower()
    if "429" in text or "rate" in text:
        return StatusBridgeError("X_RATE_LIMITED", "X extraction failed.")
    if "404" in text or "not found" in text or "no longer" in text:
        return StatusBridgeError("X_POST_DELETED", "X post is not publicly available.")
    if "403" in text or "protect" in text:
        return StatusBridgeError("X_POST_PROTECTED", "X post is not publicly available.")
    if "401" in text or "auth" in text or "login" in text:
        return StatusBridgeError("X_AUTHENTICATION_REQUIRED", "X post is not publicly available.")
    return StatusBridgeError("X_POST_UNAVAILABLE", "X post is not publicly available.")


def _default_resolver(host: str, port: int) -> list[tuple[str, int]]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[tuple[str, int]] = []
    for _family, _type, _proto, _canon, sockaddr in records:
        if not sockaddr:
            continue
        addresses.append((str(sockaddr[0]), int(sockaddr[1])))
    return addresses


def _resolve_global_address(host: str, *, resolver: PhotoResolver) -> str:
    try:
        records = resolver(host, 443)
    except OSError as exc:
        raise StatusBridgeError("X_DOWNLOAD_TIMEOUT", "X photo download timed out.") from exc
    for address, _port in records:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo host is not allowed.") from exc
        if parsed.is_global:
            return address
    raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo host is not allowed.")


def _default_transport(ip: str, host: str, request_target: str, timeout: float) -> PhotoHttpResult:
    context = ssl.create_default_context()
    sock = socket.create_connection((ip, 443), timeout=timeout)
    try:
        ssock = context.wrap_socket(sock, server_hostname=host)
        connection = http.client.HTTPSConnection(host, timeout=timeout, context=context)
        connection.sock = ssock
        connection.request(
            "GET",
            request_target,
            headers={
                "Host": host,
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(PHOTO_MAX_BYTES + 1)
        return PhotoHttpResult(status=response.status, headers=headers, body=body)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _stream_photo_to_destination(
    chunks: tuple[bytes, ...],
    *,
    destination: Path,
    partial: Path,
    expected_mime: str,
) -> str:
    _cleanup_paths(partial, destination)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd: int | None = None
    digest = hashlib.sha256()
    received = 0
    prefix = b""
    try:
        fd = os.open(partial, flags, 0o600)
        for chunk in chunks:
            if not chunk:
                continue
            received += len(chunk)
            if received > PHOTO_MAX_BYTES:
                raise StatusBridgeError("X_MEDIA_TOO_LARGE", "X photo exceeds size limit.")
            digest.update(chunk)
            if len(prefix) < 16:
                prefix += chunk[: 16 - len(prefix)]
                if len(prefix) >= 3:
                    _assert_magic(prefix, expected_mime)
            os.write(fd, chunk)
        if received <= 0:
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo response is invalid.")
        _assert_magic(prefix, expected_mime)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(partial, destination)
        return digest.hexdigest()
    except StatusBridgeError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_paths(partial, destination)
        raise
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_paths(partial, destination)
        raise StatusBridgeError("X_STAGING_FAILED", "X photo download failed.") from exc


def _assert_magic(prefix: bytes, expected_mime: str) -> None:
    if expected_mime == "image/jpeg" and not prefix.startswith(_JPEG_MAGIC):
        if prefix.startswith(b"RIFF") or prefix[8:12] == b"WEBP":
            raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo type is unsupported.")
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo bytes are invalid.")
    if expected_mime == "image/png" and not prefix.startswith(_PNG_MAGIC):
        raise StatusBridgeError("X_MEDIA_TYPE_UNSUPPORTED", "X photo bytes are invalid.")


def _accepted_content_types(variant: str) -> frozenset[str]:
    if variant == X_VARIANT_PHOTO_JPEG:
        return frozenset({"image/jpeg", "image/jpg"})
    if variant == X_VARIANT_PHOTO_PNG:
        return frozenset({"image/png"})
    return frozenset()


def _cleanup_paths(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            continue


def _clean_text(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:500] if cleaned else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    return value


def _require_text(value: object) -> str:
    cleaned = _clean_text(value)
    if cleaned is None:
        raise StatusBridgeError("X_EXTRACTOR_MALFORMED", "X extractor is malformed.")
    return cleaned


def _media_type_value(value: object) -> XMediaType | None:
    if not isinstance(value, str):
        return None
    try:
        return XMediaType(value)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
