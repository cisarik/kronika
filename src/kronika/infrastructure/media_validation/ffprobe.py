"""Bounded validation for quarantined uploads."""

from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any

from PIL import Image, ImageFile, UnidentifiedImageError

from kronika.application.ports.quarantine_storage import QuarantineReader
from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationEvidence,
    UploadMediaValidationInfrastructureError,
    UploadMediaValidationInterruptedError,
    UploadMediaValidationRejectedError,
)
from kronika.application.upload_validation import (
    UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE,
    UPLOAD_VALIDATION_INTERNAL_ERROR,
    UPLOAD_VALIDATION_INVALID_MEDIA,
    UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT,
    UPLOAD_VALIDATION_OUTPUT_LIMIT,
    UPLOAD_VALIDATION_TIMEOUT,
    UPLOAD_VALIDATION_TOOL_UNAVAILABLE,
    UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE,
)
from kronika.domain.uploads import UploadValidatedFormat, UploadValidatedMediaKind
from kronika.infrastructure.media_analysis.process import (
    EXECUTABLE_NOT_FOUND_MESSAGE,
    PROCESS_OUTPUT_LIMIT_MESSAGE,
    PROCESS_TIMEOUT_MESSAGE,
    PROCESS_FAILED_MESSAGE,
    PROCESS_INTERRUPTED_MESSAGE,
    ProcessExecutionError,
    ProcessInterruptedError,
    ProcessRunner,
    SubprocessRunner,
)
from kronika.infrastructure.media_analysis.tools import (
    TOOL_NOT_AVAILABLE_MESSAGE,
    resolve_ffprobe,
)

UPLOAD_VALIDATION_PROBE_TIMEOUT_SECONDS = 10.0
UPLOAD_VALIDATION_PROBE_STDOUT_MAX_BYTES = 262_144
UPLOAD_VALIDATION_PROBE_STDERR_MAX_BYTES = 32_768
UPLOAD_VALIDATION_PREFIX_MAX_BYTES = 4096
UPLOAD_VALIDATION_PROBE_MAX_NESTING_DEPTH = 48
UPLOAD_VALIDATION_MAX_STREAMS = 16
UPLOAD_VALIDATION_MAX_DIMENSION = 8192
UPLOAD_VALIDATION_MAX_TOTAL_PIXELS = 33_177_600
UPLOAD_VALIDATION_MAX_DURATION_MS = 21_600_000
_UPLOAD_VALIDATION_MAX_DURATION_SECONDS = Decimal(UPLOAD_VALIDATION_MAX_DURATION_MS) / Decimal(
    1000
)
_UPLOAD_VALIDATION_MAX_NUMERIC_STRING_LENGTH = 32
_DECIMAL_SECONDS_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?$")

_GIF87A_SIGNATURE = b"GIF87a"
_GIF89A_SIGNATURE = b"GIF89a"
_JPEG_SOI_MARKER = b"\xff\xd8\xff"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_STILL_IMAGE_FORMATS = frozenset(
    {UploadValidatedFormat.JPEG, UploadValidatedFormat.PNG}
)
_MP4_ACCEPTED_MAJOR_BRANDS = frozenset(
    {
        "avc1",
        "cmfc",
        "dash",
        "iso2",
        "iso4",
        "iso5",
        "iso6",
        "isom",
        "mp41",
        "mp42",
    }
)
_MP4_SUPPORTING_COMPATIBLE_BRANDS = frozenset({"iso2", "iso4", "iso5", "iso6", "isom"})
_MP4_REJECTED_MAJOR_BRAND_PREFIXES = ("3gp", "3g2", "mj2")
_MP4_REJECTED_MAJOR_BRANDS = frozenset(
    {
        "qt  ",
        "M4A ",
        "M4B ",
        "M4P ",
        "M4R ",
        "M4A",
        "M4B",
        "M4P",
        "M4R",
    }
)
_MP4_ACCEPTED_CODECS = frozenset({"av1", "h264", "hevc", "mpeg4", "vp9"})


@dataclass(frozen=True, slots=True)
class _SignatureEvidence:
    media_format: UploadValidatedFormat
    width: int | None = None
    height: int | None = None
    mp4_major_brand: str | None = None
    mp4_compatible_brands: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _ProbeEvidence:
    media_format: UploadValidatedFormat
    media_kind: UploadValidatedMediaKind
    width: int
    height: int
    duration_ms: int | None
    video_codec: str


class BoundedUploadMediaValidator:
    """Validate GIF/MP4/JPEG/PNG content through prefix evidence and bounded probes."""

    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        ffprobe_executable: str | None = None,
    ) -> None:
        self._runner = runner or SubprocessRunner()
        self._ffprobe_executable = ffprobe_executable

    def validate(self, reader: QuarantineReader) -> UploadMediaValidationEvidence:
        signature = _detect_signature(reader)
        if signature.media_format in _STILL_IMAGE_FORMATS:
            return _validate_still_image(reader, signature)
        probe = self._probe(reader)
        _ensure_signature_and_probe_agree(signature, probe)
        return UploadMediaValidationEvidence(
            media_kind=probe.media_kind,
            media_format=probe.media_format,
        )

    def _probe(self, reader: QuarantineReader) -> _ProbeEvidence:
        try:
            executable = self._ffprobe_executable
            if executable is None:
                executable, _version = resolve_ffprobe(self._runner)
            result = self._runner.run(
                executable=executable,
                argv=[
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    "-i",
                    _fd_path(reader.file_descriptor),
                ],
                timeout_seconds=UPLOAD_VALIDATION_PROBE_TIMEOUT_SECONDS,
                stdout_max_bytes=UPLOAD_VALIDATION_PROBE_STDOUT_MAX_BYTES,
                stderr_max_bytes=UPLOAD_VALIDATION_PROBE_STDERR_MAX_BYTES,
                pass_fds=(reader.file_descriptor,),
            )
        except ProcessInterruptedError as exc:
            raise UploadMediaValidationInterruptedError() from exc
        except ProcessExecutionError as exc:
            _raise_process_error(exc)
        if result.returncode != 0:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        try:
            payload = _decode_probe_payload(result.stdout)
        except UploadMediaValidationRejectedError:
            raise
        except Exception:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA) from None
        if not isinstance(payload, dict):
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        return _parse_probe_payload(payload)


def _detect_signature(reader: QuarantineReader) -> _SignatureEvidence:
    reader.seek_start()
    prefix = reader.read(UPLOAD_VALIDATION_PREFIX_MAX_BYTES)
    reader.seek_start()
    if prefix.startswith((_GIF87A_SIGNATURE, _GIF89A_SIGNATURE)):
        if len(prefix) < 10:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        width = int.from_bytes(prefix[6:8], "little")
        height = int.from_bytes(prefix[8:10], "little")
        if width <= 0 or height <= 0:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        _validate_dimensions(width, height)
        return _SignatureEvidence(UploadValidatedFormat.GIF, width=width, height=height)
    if prefix.startswith(_JPEG_SOI_MARKER):
        return _SignatureEvidence(UploadValidatedFormat.JPEG)
    if prefix.startswith(_PNG_SIGNATURE):
        return _SignatureEvidence(UploadValidatedFormat.PNG)
    ftyp = _mp4_ftyp_evidence(prefix)
    if ftyp is not None:
        major_brand, compatible_brands = ftyp
        return _SignatureEvidence(
            UploadValidatedFormat.MP4,
            mp4_major_brand=major_brand,
            mp4_compatible_brands=compatible_brands,
        )
    raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE)


def _validate_still_image(
    reader: QuarantineReader,
    signature: _SignatureEvidence,
) -> UploadMediaValidationEvidence:
    reader.seek_start()
    payload = bytearray()
    while True:
        chunk = reader.read(1024 * 1024)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > reader.size_bytes:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    if len(payload) != reader.size_bytes or not payload:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    previous_max_pixels = Image.MAX_IMAGE_PIXELS
    previous_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
    Image.MAX_IMAGE_PIXELS = UPLOAD_VALIDATION_MAX_TOTAL_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(io.BytesIO(bytes(payload))) as source:
            source.load()
            format_name = source.format
            if signature.media_format is UploadValidatedFormat.JPEG:
                if format_name != "JPEG":
                    raise UploadMediaValidationRejectedError(
                        UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE
                    )
                media_format = UploadValidatedFormat.JPEG
            elif signature.media_format is UploadValidatedFormat.PNG:
                if format_name != "PNG":
                    raise UploadMediaValidationRejectedError(
                        UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE
                    )
                media_format = UploadValidatedFormat.PNG
            else:
                raise UploadMediaValidationRejectedError(
                    UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE
                )
            width, height = source.size
            if width <= 0 or height <= 0:
                raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
            _validate_dimensions(width, height)
    except UploadMediaValidationRejectedError:
        raise
    except Image.DecompressionBombError:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT) from None
    except (OSError, UnidentifiedImageError, ValueError, TypeError, SyntaxError):
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA) from None
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_pixels
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated
        reader.seek_start()
    return UploadMediaValidationEvidence(
        media_kind=UploadValidatedMediaKind.IMAGE,
        media_format=media_format,
    )


def _mp4_ftyp_evidence(prefix: bytes) -> tuple[str, frozenset[str]] | None:
    if len(prefix) < 16:
        return None
    box_size = int.from_bytes(prefix[0:4], "big")
    box_type = prefix[4:8]
    if box_type != b"ftyp" or box_size < 16 or box_size > len(prefix):
        return None
    body = prefix[8:box_size]
    if len(body) < 8 or len(body) % 4 != 0:
        return None
    try:
        major_brand = body[0:4].decode("ascii")
        compatible_brands = frozenset(
            body[index : index + 4].decode("ascii") for index in range(8, len(body), 4)
        )
    except UnicodeDecodeError:
        return None
    if _is_rejected_mp4_major_brand(major_brand):
        return None
    if major_brand not in _MP4_ACCEPTED_MAJOR_BRANDS:
        return None
    if compatible_brands and not (
        compatible_brands & (_MP4_ACCEPTED_MAJOR_BRANDS | _MP4_SUPPORTING_COMPATIBLE_BRANDS)
    ):
        return None
    return major_brand, compatible_brands


def _is_rejected_mp4_major_brand(brand: str) -> bool:
    return brand in _MP4_REJECTED_MAJOR_BRANDS or brand.startswith(
        _MP4_REJECTED_MAJOR_BRAND_PREFIXES
    )


def _decode_probe_payload(stdout: bytes) -> object:
    try:
        text = stdout.decode("utf-8")
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
        )
        _validate_probe_nesting(payload)
        return payload
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
        TypeError,
    ):
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA) from None


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-standard JSON numeric constant")


def _validate_probe_nesting(payload: object) -> None:
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > UPLOAD_VALIDATION_PROBE_MAX_NESTING_DEPTH:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)


def _parse_probe_payload(payload: dict[str, Any]) -> _ProbeEvidence:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    if len(streams) > UPLOAD_VALIDATION_MAX_STREAMS:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
    stream_dicts = [stream for stream in streams if isinstance(stream, dict)]
    if len(stream_dicts) != len(streams):
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    visual_streams = [
        stream
        for stream in stream_dicts
        if stream.get("codec_type") == "video" and not _is_attached_picture(stream)
    ]
    if not visual_streams:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    if len(visual_streams) > 1:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
    stream = visual_streams[0]
    width = _positive_int(stream.get("width"))
    height = _positive_int(stream.get("height"))
    if width is None or height is None:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    _validate_dimensions(width, height)
    codec = stream.get("codec_name")
    if not isinstance(codec, str) or not codec:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    duration_ms = _duration_ms(stream.get("duration"))
    format_block = payload.get("format")
    format_names: tuple[str, ...] = ()
    if isinstance(format_block, dict):
        if duration_ms is None:
            duration_ms = _duration_ms(format_block.get("duration"))
        format_names = _format_names(format_block.get("format_name"))
    _validate_duration(duration_ms)
    return _probe_from_formats(
        format_names=format_names,
        width=width,
        height=height,
        duration_ms=duration_ms,
        video_codec=codec.lower(),
    )


def _probe_from_formats(
    *,
    format_names: tuple[str, ...],
    width: int,
    height: int,
    duration_ms: int | None,
    video_codec: str,
) -> _ProbeEvidence:
    if "gif" in format_names:
        if len(format_names) != 1 or video_codec != "gif":
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE)
        return _ProbeEvidence(
            media_format=UploadValidatedFormat.GIF,
            media_kind=UploadValidatedMediaKind.ANIMATED_IMAGE,
            width=width,
            height=height,
            duration_ms=duration_ms,
            video_codec=video_codec,
        )
    if "mp4" in format_names:
        if {"gif", "matroska", "webm"} & set(format_names):
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE)
        if video_codec not in _MP4_ACCEPTED_CODECS:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
        return _ProbeEvidence(
            media_format=UploadValidatedFormat.MP4,
            media_kind=UploadValidatedMediaKind.VIDEO,
            width=width,
            height=height,
            duration_ms=duration_ms,
            video_codec=video_codec,
        )
    raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE)


def _ensure_signature_and_probe_agree(
    signature: _SignatureEvidence,
    probe: _ProbeEvidence,
) -> None:
    if signature.media_format is not probe.media_format:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE)
    if signature.width is not None and signature.width != probe.width:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE)
    if signature.height is not None and signature.height != probe.height:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE)


def _is_attached_picture(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return False
    return disposition.get("attached_pic") in (1, True, "1")


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        if (
            not value.isdecimal()
            or len(value) > _UPLOAD_VALIDATION_MAX_NUMERIC_STRING_LENGTH
        ):
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        if value > float(_UPLOAD_VALIDATION_MAX_DURATION_SECONDS):
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
        return int(value * 1000)
    if isinstance(value, str):
        try:
            seconds = _bounded_decimal_seconds(value)
        except (DecimalException, ValueError):
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA) from None
        if seconds < 0 or not seconds.is_finite():
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
        if seconds > _UPLOAD_VALIDATION_MAX_DURATION_SECONDS:
            raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
        return int(seconds * 1000)
    raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)


def _bounded_decimal_seconds(value: str) -> Decimal:
    if (
        len(value) > _UPLOAD_VALIDATION_MAX_NUMERIC_STRING_LENGTH
        or not _DECIMAL_SECONDS_PATTERN.fullmatch(value)
    ):
        raise ValueError("unsupported numeric string")
    seconds = Decimal(value)
    if not seconds.is_finite():
        raise InvalidOperation
    return seconds


def _format_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    names = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    if not names:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)
    if any(len(name) > 64 for name in names):
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
    return names


def _validate_dimensions(width: int, height: int) -> None:
    if width > UPLOAD_VALIDATION_MAX_DIMENSION or height > UPLOAD_VALIDATION_MAX_DIMENSION:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)
    if width * height > UPLOAD_VALIDATION_MAX_TOTAL_PIXELS:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)


def _validate_duration(duration_ms: int | None) -> None:
    if duration_ms is None:
        return
    if duration_ms > UPLOAD_VALIDATION_MAX_DURATION_MS:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT)


def _raise_process_error(exc: ProcessExecutionError) -> None:
    message = str(exc)
    if message == PROCESS_INTERRUPTED_MESSAGE:
        raise UploadMediaValidationInterruptedError() from None
    if message in {EXECUTABLE_NOT_FOUND_MESSAGE, TOOL_NOT_AVAILABLE_MESSAGE}:
        raise UploadMediaValidationInfrastructureError(
            UPLOAD_VALIDATION_TOOL_UNAVAILABLE
        ) from None
    if message == PROCESS_TIMEOUT_MESSAGE:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_TIMEOUT) from None
    if message == PROCESS_OUTPUT_LIMIT_MESSAGE:
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_OUTPUT_LIMIT) from None
    if message == PROCESS_FAILED_MESSAGE:
        raise UploadMediaValidationInfrastructureError(
            UPLOAD_VALIDATION_INTERNAL_ERROR
        ) from None
    raise UploadMediaValidationInfrastructureError(UPLOAD_VALIDATION_INTERNAL_ERROR) from None


def _fd_path(fd: int) -> str:
    return f"/dev/fd/{fd}"
