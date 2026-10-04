"""Unit tests for provider-neutral media suggestion application values and service."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaRelativePath,
    PNG_SIGNATURE,
    PreparedAnalysisResult,
    RepresentativeFrame,
    TechnicalMetadata,
    build_representative_frame,
    REQUESTED_FRAME_COUNT,
)
from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    ImportedMediaSuggestionPreviewResult,
    MediaSuggestion,
    MediaSuggestionNotFoundError,
    MediaSuggestionPreparationUnavailableError,
    MediaSuggestionPreviewResult,
    MediaSuggestionProviderFailedError,
    MediaSuggestionRequest,
    PreviewImportedMediaSuggestion,
    PreviewMediaSuggestion,
    PROMPT_VERSION,
    build_suggestion_request,
    validate_suggested_filename,
)
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot, MediaId, MediaLocationId
from kronika.domain.media import LogicalMedia, MediaKind, MediaLocation, MediaLocationAvailability
from kronika.domain.media import MediaRelativePath as DomainMediaRelativePath

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_SUGGESTION = REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_suggestion.py"
MEDIA_ANALYSIS_API = REPOSITORY_ROOT / "src" / "kronika" / "adapters" / "api" / "media_analysis_api.py"
_VALID_PNG = PNG_SIGNATURE + b"png"
MEDIA_ID = MediaId.from_string("99999999-8888-4777-9666-555555555555")
LOCATION_ID = MediaLocationId.from_string("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")


def _sample_metadata() -> TechnicalMetadata:
    return TechnicalMetadata(
        duration_ms=1000,
        width=64,
        height=48,
        video_codec="h264",
        container_formats=("mp4",),
        has_audio=False,
    )


def _sample_frame() -> RepresentativeFrame:
    return build_representative_frame(timestamp_ms=0, payload=_VALID_PNG)


def _sample_prepared() -> PreparedAnalysisResult:
    return PreparedAnalysisResult(
        relative_path=MediaRelativePath("clips/sample.mp4"),
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=_sample_metadata(),
        representative_frames=(_sample_frame(),),
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=(),
        ffprobe_version="ffprobe version 8.1.2",
        ffmpeg_version="ffmpeg version 8.1.2",
    )


def _sample_suggestion(*, filename: str = "evening-clip.mp4") -> MediaSuggestion:
    return MediaSuggestion(
        title="Evening clip",
        description="A short evening scene with warm light.",
        collection="Home",
        tags=("evening", "clip"),
        suggested_filename=filename,
        confidence=0.72,
        evidence=("Warm light is visible in the frame.",),
        uncertainties=("Exact location is unknown.",),
        provider_id="nvidia-nim",
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        prompt_version=PROMPT_VERSION,
    )


class _FakeLibraryRepository:
    def __init__(self, library: Library | None) -> None:
        self._library = library
        self.write_calls = 0

    def add(self, library: Library) -> None:
        self.write_calls += 1

    def get(self, library_id: LibraryId) -> Library | None:
        if self._library is None or self._library.id != library_id:
            return None
        return self._library

    def list_all(self) -> tuple[Library, ...]:
        return () if self._library is None else (self._library,)


class _FakeMediaRepository:
    def __init__(self, media: LogicalMedia | None, location: MediaLocation | None) -> None:
        self._media = media
        self._location = location
        self.write_calls = 0

    def add_media(self, media: LogicalMedia) -> None:
        self.write_calls += 1

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        if self._media is not None and self._media.id == media_id:
            return self._media
        return None

    def list_media(self) -> tuple[LogicalMedia, ...]:
        return ()

    def add_location(self, location: MediaLocation) -> None:
        self.write_calls += 1

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        self.write_calls += 1

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        if self._location is not None and self._location.id == location_id:
            return self._location
        return None

    def get_location_by_library_path(
        self,
        library_id: LibraryId,
        relative_path: DomainMediaRelativePath,
    ) -> MediaLocation | None:
        return None

    def list_locations_for_media(self, media_id: MediaId) -> tuple[MediaLocation, ...]:
        return ()


class _FakePreparer:
    def __init__(self, prepared: PreparedAnalysisResult) -> None:
        self.prepared = prepared
        self.calls = 0

    def prepare(self, root: LibraryRoot, relative_path: MediaRelativePath) -> PreparedAnalysisResult:
        self.calls += 1
        return self.prepared


class _FakeProvider:
    def __init__(self, suggestion: MediaSuggestion) -> None:
        self.suggestion = suggestion
        self.calls = 0
        self.last_request: MediaSuggestionRequest | None = None

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        self.calls += 1
        self.last_request = request
        return self.suggestion


class _FakeJoin:
    def __init__(self) -> None:
        self.calls: list[tuple[MediaId, MediaLocationId, MediaSuggestion]] = []

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
        suggestion: MediaSuggestion,
    ) -> None:
        self.calls.append((media_id, location_id, suggestion))


def _sample_library() -> Library:
    return Library(
        id=LibraryId.from_string("12345678-1234-4234-9234-123456789abc"),
        device_id=DeviceId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef"),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


def _sample_media(kind: MediaKind = MediaKind.VIDEO) -> LogicalMedia:
    return LogicalMedia(id=MEDIA_ID, kind=kind, created_at_ms=10, updated_at_ms=10)


def _sample_location(
    *,
    media_id: MediaId = MEDIA_ID,
    relative_path: str = "clips/sample.mp4",
    availability: MediaLocationAvailability = MediaLocationAvailability.AVAILABLE,
) -> MediaLocation:
    return MediaLocation(
        id=LOCATION_ID,
        media_id=media_id,
        library_id=_sample_library().id,
        relative_path=DomainMediaRelativePath(relative_path),
        availability=availability,
        observed_size_bytes=100,
        observed_mtime_ns=200,
        created_at_ms=10,
        updated_at_ms=10,
    )


def test_request_uses_basename_without_absolute_path() -> None:
    prepared = _sample_prepared()
    request = build_suggestion_request(prepared)
    assert request.basename == "sample.mp4"
    assert "/" not in request.basename
    assert request.prompt_version == "framenest-media-suggestion-v4"


def test_prompt_version_is_v4() -> None:
    assert PROMPT_VERSION == "framenest-media-suggestion-v4"


def test_hidden_segments_rejected_for_suggestion_paths() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        MediaRelativePath(".hidden/clip.mp4")


def test_suggestion_validates_bounds_and_unique_tags() -> None:
    with pytest.raises(FrameNestMediaSuggestionError):
        MediaSuggestion(
            title="x" * 121,
            description="Valid description",
            collection="Home",
            tags=("a",),
            suggested_filename="clip.mp4",
            confidence=0.5,
            evidence=("evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="model",
            prompt_version=PROMPT_VERSION,
        )
    with pytest.raises(FrameNestMediaSuggestionError):
        MediaSuggestion(
            title="Title",
            description="Valid description",
            collection="Home",
            tags=("Evening", "evening"),
            suggested_filename="clip.mp4",
            confidence=0.5,
            evidence=("evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="model",
            prompt_version=PROMPT_VERSION,
        )


def test_suggestion_rejects_zero_or_six_tags() -> None:
    with pytest.raises(FrameNestMediaSuggestionError):
        MediaSuggestion(
            title="Title",
            description="Valid description",
            collection="Home",
            tags=(),
            suggested_filename="clip.mp4",
            confidence=0.5,
            evidence=("evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="model",
            prompt_version=PROMPT_VERSION,
        )
    with pytest.raises(FrameNestMediaSuggestionError):
        MediaSuggestion(
            title="Title",
            description="Valid description",
            collection="Home",
            tags=("one", "two", "three", "four", "five", "six"),
            suggested_filename="clip.mp4",
            confidence=0.5,
            evidence=("evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="model",
            prompt_version=PROMPT_VERSION,
        )


def test_suggestion_accepts_five_tags() -> None:
    suggestion = MediaSuggestion(
        title="Title",
        description="Valid description",
        collection="Home",
        tags=("one", "two", "three", "four", "five"),
        suggested_filename="clip.mp4",
        confidence=0.5,
        evidence=("evidence",),
        uncertainties=(),
        provider_id="nvidia-nim",
        model_id="model",
        prompt_version=PROMPT_VERSION,
    )
    assert suggestion.tags == ("one", "two", "three", "four", "five")


@pytest.mark.parametrize("filename", ["CON.mp4", "bad/name.mp4", "clip.mov", "clip.mp4 "])
def test_validate_suggested_filename_rejects_unsafe_names(filename: str) -> None:
    with pytest.raises(FrameNestMediaSuggestionError):
        validate_suggested_filename(filename, required_extension=".mp4")


def test_validate_suggested_filename_preserves_extension() -> None:
    assert validate_suggested_filename("evening-clip.mp4", required_extension=".mp4") == "evening-clip.mp4"


def test_preview_service_prepares_once_and_invokes_provider_once() -> None:
    library = _sample_library()
    prepared = _sample_prepared()
    suggestion = _sample_suggestion()
    repository = _FakeLibraryRepository(library)
    preparer = _FakePreparer(prepared)
    provider = _FakeProvider(suggestion)
    service = PreviewMediaSuggestion(repository, preparer, provider)

    result = service.execute(library.id, MediaRelativePath("clips/sample.mp4"))

    assert isinstance(result, MediaSuggestionPreviewResult)
    assert preparer.calls == 1
    assert provider.calls == 1
    assert provider.last_request is not None
    assert provider.last_request.basename == "sample.mp4"
    assert result.suggestion.provider_id == "nvidia-nim"
    assert repository.write_calls == 0


def test_imported_preview_service_uses_media_location_identity_without_paths() -> None:
    library = _sample_library()
    media_repository = _FakeMediaRepository(_sample_media(), _sample_location())
    library_repository = _FakeLibraryRepository(library)
    preparer = _FakePreparer(_sample_prepared())
    provider = _FakeProvider(_sample_suggestion())
    service = PreviewImportedMediaSuggestion(media_repository, library_repository, preparer, provider)

    result = service.execute(MEDIA_ID, LOCATION_ID)

    assert isinstance(result, ImportedMediaSuggestionPreviewResult)
    assert result.media_id == MEDIA_ID
    assert result.location_id == LOCATION_ID
    assert preparer.calls == 1
    assert provider.calls == 1
    assert provider.last_request is not None
    assert provider.last_request.basename == "sample.mp4"
    assert "/" not in provider.last_request.basename
    assert media_repository.write_calls == 0
    assert library_repository.write_calls == 0


def test_imported_preview_invokes_join_once_without_second_provider_call() -> None:
    join = _FakeJoin()
    provider = _FakeProvider(_sample_suggestion())
    service = PreviewImportedMediaSuggestion(
        _FakeMediaRepository(_sample_media(), _sample_location()),
        _FakeLibraryRepository(_sample_library()),
        _FakePreparer(_sample_prepared()),
        provider,
        join,
    )

    result = service.execute(MEDIA_ID, LOCATION_ID)

    assert provider.calls == 1
    assert join.calls == [(MEDIA_ID, LOCATION_ID, result.suggestion)]


def test_imported_preview_does_not_join_when_provider_fails() -> None:
    join = _FakeJoin()

    class _FailingProvider:
        calls = 0

        def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
            del request
            self.calls += 1
            raise MediaSuggestionProviderFailedError("provider failed")

    provider = _FailingProvider()
    service = PreviewImportedMediaSuggestion(
        _FakeMediaRepository(_sample_media(), _sample_location()),
        _FakeLibraryRepository(_sample_library()),
        _FakePreparer(_sample_prepared()),
        provider,
        join,
    )

    with pytest.raises(MediaSuggestionProviderFailedError):
        service.execute(MEDIA_ID, LOCATION_ID)
    assert provider.calls == 1
    assert join.calls == []


def test_imported_preview_service_rejects_mismatched_location() -> None:
    service = PreviewImportedMediaSuggestion(
        _FakeMediaRepository(_sample_media(), _sample_location(media_id=MediaId.new())),
        _FakeLibraryRepository(_sample_library()),
        _FakePreparer(_sample_prepared()),
        _FakeProvider(_sample_suggestion()),
    )

    with pytest.raises(MediaSuggestionNotFoundError):
        service.execute(MEDIA_ID, LOCATION_ID)


def test_imported_preview_service_rejects_unavailable_location_before_preparation() -> None:
    preparer = _FakePreparer(_sample_prepared())
    provider = _FakeProvider(_sample_suggestion())
    service = PreviewImportedMediaSuggestion(
        _FakeMediaRepository(
            _sample_media(),
            _sample_location(availability=MediaLocationAvailability.MISSING),
        ),
        _FakeLibraryRepository(_sample_library()),
        preparer,
        provider,
    )

    with pytest.raises(MediaSuggestionPreparationUnavailableError):
        service.execute(MEDIA_ID, LOCATION_ID)
    assert preparer.calls == 0
    assert provider.calls == 0


def test_imported_preview_service_rejects_unsupported_kind_extension_pair() -> None:
    preparer = _FakePreparer(_sample_prepared())
    provider = _FakeProvider(_sample_suggestion())
    service = PreviewImportedMediaSuggestion(
        _FakeMediaRepository(_sample_media(MediaKind.VIDEO), _sample_location(relative_path="clips/sample.gif")),
        _FakeLibraryRepository(_sample_library()),
        preparer,
        provider,
    )

    with pytest.raises(FrameNestMediaSuggestionError):
        service.execute(MEDIA_ID, LOCATION_ID)
    assert preparer.calls == 0
    assert provider.calls == 0


def test_preview_service_raises_not_found_for_missing_library() -> None:
    service = PreviewMediaSuggestion(
        _FakeLibraryRepository(None),
        _FakePreparer(_sample_prepared()),
        _FakeProvider(_sample_suggestion()),
    )
    with pytest.raises(MediaSuggestionNotFoundError):
        service.execute(LibraryId.new(), MediaRelativePath("clip.mp4"))


def test_preview_service_propagates_provider_failures() -> None:
    class _FailingProvider:
        def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
            raise MediaSuggestionProviderFailedError("provider failed")

    service = PreviewMediaSuggestion(
        _FakeLibraryRepository(_sample_library()),
        _FakePreparer(_sample_prepared()),
        _FailingProvider(),
    )
    with pytest.raises(MediaSuggestionProviderFailedError):
        service.execute(
            LibraryId.from_string("12345678-1234-4234-9234-123456789abc"),
            MediaRelativePath("clips/sample.mp4"),
        )


def test_application_suggestion_module_imports_no_infrastructure() -> None:
    tree = ast.parse(APPLICATION_SUGGESTION.read_text(encoding="utf-8"), filename=str(APPLICATION_SUGGESTION))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        if module.startswith("kronika.infrastructure"):
            violations.append(module)
    assert violations == []


def test_pillow_imports_are_confined_outside_application_and_domain() -> None:
    source_roots = [
        REPOSITORY_ROOT / "src" / "kronika" / "application",
        REPOSITORY_ROOT / "src" / "kronika" / "domain",
    ]
    violations: list[str] = []
    for path in source_roots:
        for source_file in sorted(path.rglob("*.py")):
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                if any(module == "PIL" or module.startswith("PIL.") for module in modules):
                    violations.append(str(source_file.relative_to(REPOSITORY_ROOT)))
    assert violations == []


def test_media_analysis_api_remains_png_for_local_preview() -> None:
    source = MEDIA_ANALYSIS_API.read_text(encoding="utf-8")

    assert "payload_base64" in source
    assert "mime_type=frame.mime_type" in source
    assert "image/jpeg" not in source
    assert "Pillow" not in source
    assert "PIL" not in source
