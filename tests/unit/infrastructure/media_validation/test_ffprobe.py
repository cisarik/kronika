"""Unit tests for bounded upload media validation."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

import kronika.infrastructure.media_validation.ffprobe as ffprobe_module
from kronika.application.ports.quarantine_storage import QuarantineStateInconsistentError
from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationInfrastructureError,
    UploadMediaValidationInterruptedError,
    UploadMediaValidationRejectedError,
)
from kronika.application.upload_validation import (
    UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE,
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
    ProcessExecutionError,
    ProcessInterruptedError,
    ProcessRunResult,
)
from kronika.infrastructure.media_validation.ffprobe import BoundedUploadMediaValidator


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False

    @property
    def size_bytes(self) -> int:
        return len(self._payload)

    @property
    def file_descriptor(self) -> int:
        return 123

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def seek_start(self) -> None:
        self._offset = 0

    def verify_still_consistent(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Runner:
    def __init__(
        self,
        result: ProcessRunResult | None = None,
        error: ProcessExecutionError | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        *,
        executable: str,
        argv: Sequence[str],
        timeout_seconds: float,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
        pass_fds: Sequence[int] = (),
    ) -> ProcessRunResult:
        self.calls.append(
            {
                "executable": executable,
                "argv": tuple(argv),
                "pass_fds": tuple(pass_fds),
                "stdout_max_bytes": stdout_max_bytes,
                "stderr_max_bytes": stderr_max_bytes,
            }
        )
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _gif_bytes(signature: bytes = b"GIF89a", *, width: int = 1, height: int = 1) -> bytes:
    return signature + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x80"


def _mp4_bytes() -> bytes:
    return _mp4_bytes_with_ftyp("isom", ["mp42"])


def _mp4_bytes_with_ftyp(major_brand: str, compatible_brands: Sequence[str]) -> bytes:
    body = (
        major_brand.encode("ascii")
        + b"\x00\x00\x02\x00"
        + b"".join(brand.encode("ascii") for brand in compatible_brands)
    )
    return (len(body) + 8).to_bytes(4, "big") + b"ftyp" + body


def _probe_payload(
    *,
    format_name: str,
    codec: str,
    width: int = 1,
    height: int = 1,
    include_video: bool = True,
    extra_streams: int = 0,
    duration: str = "1.0",
) -> bytes:
    streams: list[dict[str, object]] = []
    if include_video:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": width,
                "height": height,
                "disposition": {"attached_pic": 0},
            }
        )
    streams.extend({"codec_type": "audio", "codec_name": "aac"} for _ in range(extra_streams))
    return json.dumps(
        {
            "streams": streams,
            "format": {"format_name": format_name, "duration": duration},
        }
    ).encode("utf-8")


def _probe_payload_with_extra_format(extra: object) -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1,
                    "height": 1,
                    "disposition": {"attached_pic": 0},
                }
            ],
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "1.0",
                "tags": extra,
            },
        }
    ).encode("utf-8")


def _nested_payload(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = {"nested": value}
    return value


def _validator(stdout: bytes) -> tuple[BoundedUploadMediaValidator, _Runner]:
    runner = _Runner(ProcessRunResult(returncode=0, stdout=stdout, stderr=b""))
    return BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe"), runner


def test_valid_gif89a_is_accepted_from_content_not_filename_or_mime() -> None:
    validator, runner = _validator(_probe_payload(format_name="gif", codec="gif"))

    evidence = validator.validate(_Reader(_gif_bytes()))

    assert evidence.media_kind is UploadValidatedMediaKind.ANIMATED_IMAGE
    assert evidence.media_format is UploadValidatedFormat.GIF
    assert runner.calls[0]["pass_fds"] == (123,)
    assert "/dev/fd/123" in runner.calls[0]["argv"]


def test_valid_gif87a_is_accepted() -> None:
    validator, _runner = _validator(_probe_payload(format_name="gif", codec="gif"))

    evidence = validator.validate(_Reader(_gif_bytes(b"GIF87a")))

    assert evidence.media_format is UploadValidatedFormat.GIF


def test_valid_mp4_with_visual_stream_is_accepted() -> None:
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="h264")
    )

    evidence = validator.validate(_Reader(_mp4_bytes()))

    assert evidence.media_kind is UploadValidatedMediaKind.VIDEO
    assert evidence.media_format is UploadValidatedFormat.MP4


@pytest.mark.parametrize(
    "major_brand",
    [
        "3gp4",
        "3g2a",
        "qt  ",
        "M4A ",
        "M4B ",
        "M4P ",
        "M4R ",
        "mj2s",
    ],
)
def test_excluded_mp4_family_major_brands_are_rejected_even_with_mp4_compatible_brands(
    major_brand: str,
) -> None:
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="h264")
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes_with_ftyp(major_brand, ["isom", "iso2"])))

    assert error.value.failure_code == UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE


def test_unknown_major_brand_is_not_promoted_by_compatible_mp4_brands() -> None:
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="h264")
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes_with_ftyp("zzzz", ["isom", "iso2", "mp42"])))

    assert error.value.failure_code == UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE


def test_audio_only_mp4_family_content_is_rejected_without_visual_stream() -> None:
    validator, _runner = _validator(
        _probe_payload(
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            codec="aac",
            include_video=False,
        )
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes_with_ftyp("mp42", ["isom", "iso2"])))

    assert error.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA


def test_contradictory_ftyp_and_probe_evidence_is_rejected() -> None:
    validator, _runner = _validator(_probe_payload(format_name="gif", codec="gif"))

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes_with_ftyp("mp42", ["isom"])))

    assert error.value.failure_code == UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE


@pytest.mark.parametrize(
    "payload",
    [
        b"\x00\x00\x00\x10ftyp",
        b"\x00\x00\x00\x13ftypmp42\x00\x00\x02\x00mp4",
        b"\x00\x00\x00\x14ftyp\xff\xff\xff\xff\x00\x00\x02\x00mp42",
    ],
)
def test_truncated_or_malformed_ftyp_is_rejected(payload: bytes) -> None:
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a,3gp,3g2,mj2", codec="h264")
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(payload))

    assert error.value.failure_code == UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize(
    ("payload", "stdout", "code"),
    [
        (b"not-media", _probe_payload(format_name="gif", codec="gif"), UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE),
        (_gif_bytes()[:8], _probe_payload(format_name="gif", codec="gif"), UPLOAD_VALIDATION_INVALID_MEDIA),
        (_gif_bytes(), _probe_payload(format_name="mov,mp4,m4a", codec="h264"), UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE),
        (_mp4_bytes(), _probe_payload(format_name="mov,mp4,m4a", codec="h264", include_video=False), UPLOAD_VALIDATION_INVALID_MEDIA),
        (_mp4_bytes(), _probe_payload(format_name="gif", codec="gif"), UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE),
        (_mp4_bytes(), _probe_payload(format_name="matroska,webm", codec="h264"), UPLOAD_VALIDATION_UNSUPPORTED_MEDIA_TYPE),
        (_mp4_bytes(), _probe_payload(format_name="mov,mp4,m4a", codec="h264", width=9000), UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT),
        (_mp4_bytes(), _probe_payload(format_name="mov,mp4,m4a", codec="h264", extra_streams=20), UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT),
        (_mp4_bytes(), _probe_payload(format_name="mov,mp4,m4a", codec="h264", duration="999999"), UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT),
        (_mp4_bytes(), b"{", UPLOAD_VALIDATION_INVALID_MEDIA),
    ],
)
def test_invalid_or_unsupported_media_is_rejected_with_sanitized_code(
    payload: bytes,
    stdout: bytes,
    code: str,
) -> None:
    validator, _runner = _validator(stdout)

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(payload))

    assert error.value.failure_code == code


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_numeric_constants_are_sanitized(constant: str) -> None:
    stdout = (
        b'{"streams":[{"codec_type":"video","codec_name":"h264","width":1,'
        b'"height":1,"disposition":{"attached_pic":0}}],"format":'
        + b'{"format_name":"mov,mp4,m4a","duration":'
        + constant.encode("ascii")
        + b"}}"
    )
    validator, _runner = _validator(stdout)

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes()))

    assert error.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA
    assert constant not in str(error.value)


@pytest.mark.parametrize("duration", ["1e1000000", "1e-1000000", "not-a-number"])
def test_malformed_or_extreme_duration_strings_are_sanitized(duration: str) -> None:
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a", codec="h264", duration=duration)
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes()))

    assert error.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA
    assert duration not in str(error.value)


def test_deep_probe_nesting_below_limit_is_accepted() -> None:
    validator, _runner = _validator(_probe_payload_with_extra_format(_nested_payload(4)))

    evidence = validator.validate(_Reader(_mp4_bytes()))

    assert evidence.media_format is UploadValidatedFormat.MP4


def test_deep_probe_nesting_above_limit_is_sanitized() -> None:
    validator, _runner = _validator(_probe_payload_with_extra_format(_nested_payload(60)))

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes()))

    assert error.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA


def test_probe_decoder_recursion_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_recursion(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("raw nested parser failure")

    monkeypatch.setattr(ffprobe_module.json, "loads", raise_recursion)
    validator, _runner = _validator(
        _probe_payload(format_name="mov,mp4,m4a", codec="h264")
    )

    with pytest.raises(UploadMediaValidationRejectedError) as error:
        validator.validate(_Reader(_mp4_bytes()))

    assert error.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA
    assert "raw nested parser failure" not in str(error.value)


@pytest.mark.parametrize(
    ("message", "error_type", "code"),
    [
        (PROCESS_TIMEOUT_MESSAGE, UploadMediaValidationRejectedError, UPLOAD_VALIDATION_TIMEOUT),
        (
            PROCESS_OUTPUT_LIMIT_MESSAGE,
            UploadMediaValidationRejectedError,
            UPLOAD_VALIDATION_OUTPUT_LIMIT,
        ),
        (
            EXECUTABLE_NOT_FOUND_MESSAGE,
            UploadMediaValidationInfrastructureError,
            UPLOAD_VALIDATION_TOOL_UNAVAILABLE,
        ),
    ],
)
def test_process_errors_are_sanitized(message: str, error_type: type[Exception], code: str) -> None:
    runner = _Runner(error=ProcessExecutionError(message))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")

    with pytest.raises(error_type) as error:
        validator.validate(_Reader(_mp4_bytes()))

    assert getattr(error.value, "failure_code") == code


def test_reader_io_error_is_sanitized_as_infrastructure_failure() -> None:
    class _BrokenReader(_Reader):
        def read(self, size: int) -> bytes:
            raise QuarantineStateInconsistentError("private/path")

    validator, _runner = _validator(_probe_payload(format_name="gif", codec="gif"))

    with pytest.raises(QuarantineStateInconsistentError):
        validator.validate(_BrokenReader(_gif_bytes()))


def _jpeg_bytes(*, size: tuple[int, int] = (2, 2)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, (12, 34, 56)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _png_bytes(*, size: tuple[int, int] = (2, 2)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_valid_jpeg_is_accepted_without_ffprobe() -> None:
    runner = _Runner(error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")

    evidence = validator.validate(_Reader(_jpeg_bytes()))

    assert evidence.media_kind is UploadValidatedMediaKind.IMAGE
    assert evidence.media_format is UploadValidatedFormat.JPEG
    assert runner.calls == []


def test_valid_png_is_accepted_without_ffprobe() -> None:
    runner = _Runner(error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")

    evidence = validator.validate(_Reader(_png_bytes()))

    assert evidence.media_kind is UploadValidatedMediaKind.IMAGE
    assert evidence.media_format is UploadValidatedFormat.PNG
    assert runner.calls == []


def test_truncated_jpeg_is_rejected() -> None:
    runner = _Runner(error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")
    payload = _jpeg_bytes()[:12]

    with pytest.raises(UploadMediaValidationRejectedError) as exc:
        validator.validate(_Reader(payload))

    assert exc.value.failure_code == UPLOAD_VALIDATION_INVALID_MEDIA


def test_png_bytes_with_jpeg_signature_prefix_mismatch_rejected() -> None:
    # PNG content renamed as JPEG signature path is impossible; mismatch uses JPEG SOI
    # followed by non-JPEG bytes.
    runner = _Runner(error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")
    payload = b"\xff\xd8\xff" + b"not-a-jpeg-body"

    with pytest.raises(UploadMediaValidationRejectedError) as exc:
        validator.validate(_Reader(payload))

    assert exc.value.failure_code in {
        UPLOAD_VALIDATION_INVALID_MEDIA,
        UPLOAD_VALIDATION_AMBIGUOUS_MEDIA_TYPE,
    }


def test_excessive_still_image_dimensions_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffprobe_module, "UPLOAD_VALIDATION_MAX_DIMENSION", 4)
    monkeypatch.setattr(ffprobe_module, "UPLOAD_VALIDATION_MAX_TOTAL_PIXELS", 16)
    runner = _Runner(error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE))
    validator = BoundedUploadMediaValidator(runner, ffprobe_executable="/usr/bin/ffprobe")

    with pytest.raises(UploadMediaValidationRejectedError) as exc:
        validator.validate(_Reader(_png_bytes(size=(8, 8))))

    assert exc.value.failure_code == UPLOAD_VALIDATION_MEDIA_POLICY_LIMIT


def test_ffprobe_interruption_is_recoverable_and_not_a_content_failure() -> None:
    class _InterruptRunner:
        def run(self, **_kwargs: object) -> ProcessRunResult:
            raise ProcessInterruptedError("External tool was interrupted.")

    validator = BoundedUploadMediaValidator(
        _InterruptRunner(),
        ffprobe_executable="/usr/bin/ffprobe",
    )
    with pytest.raises(UploadMediaValidationInterruptedError) as error:
        validator.validate(_Reader(_mp4_bytes()))

    raised = error.value
    assert type(raised) is UploadMediaValidationInterruptedError
    assert not isinstance(raised, ProcessInterruptedError)
    assert not isinstance(raised, UploadMediaValidationRejectedError)
    assert not isinstance(raised, UploadMediaValidationInfrastructureError)
    assert not hasattr(raised, "failure_code")
    assert str(raised) == "upload media validation interrupted"
    assert type(raised.__cause__) is ProcessInterruptedError
    assert not issubclass(
        UploadMediaValidationInterruptedError,
        ProcessInterruptedError,
    )
    assert not issubclass(
        ProcessInterruptedError,
        UploadMediaValidationInterruptedError,
    )
