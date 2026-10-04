"""Contract tests for the portable media sidecar v1 domain codec."""

from __future__ import annotations

import json
from typing import Any

import pytest

from kronika.domain.identities import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import MediaKind, MediaRelativePath
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.domain.media_metadata import (
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaCollectionKey,
    MediaDescription,
    MediaDisplayTitle,
    PROCESSED_COLLECTION_KEY,
)
from kronika.domain.media_sidecar import (
    MAX_SIDECAR_BYTES,
    SIDECAR_FORMAT,
    SIDECAR_SCHEMA_VERSION,
    FrameNestMediaSidecarError,
    SidecarDocument,
    SidecarLocation,
    SidecarProcessedState,
    SidecarTagDefinition,
    decode_media_sidecar,
    encode_media_sidecar,
)

MEDIA_ID_TEXT = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
LIBRARY_ID_TEXT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PRIVATE_PATH_MARKER = "/home/private/secret.mp4"
PAYLOAD_MARKER = "PAYLOAD_MARKER_9f3a"

MINIMAL_CANONICAL_BYTES = (
    b'{"acquisition_source":"unknown","content_category":"general",'
    b'"created_at_ms":null,"creator_attribution_kind":null,'
    b'"creator_display_name":null,"creator_handle":null,'
    b'"creator_stable_id":null,"description":null,"display_title":null,'
    b'"format":"framenest-media-sidecar","genre_keys":[],'
    b'"location":{"library_id":"'
    + LIBRARY_ID_TEXT.encode("ascii")
    + b'","location_id":"'
    + LOCATION_ID_TEXT.encode("ascii")
    + b'","relative_path":"clip.mp4"},"media_id":"'
    + MEDIA_ID_TEXT.encode("ascii")
    + b'","media_kind":"video","processed":null,"schema_version":1,'
    b'"tag_definitions":[],"tag_keys":[],"updated_at_ms":null}\n'
)

UNICODE_MOVIE_BYTES = (
    b'{"acquisition_source":"manual_upload","content_category":"movie",'
    b'"created_at_ms":100,"creator_attribution_kind":"youtube_channel",'
    b'"creator_display_name":"Example Channel",'
    b'"creator_handle":"examplehandle","creator_stable_id":"UC123",'
    b'"description":"Unicode description \xc5\xbd\xc3\xa1nr\\nand \xf0\x9f\x8e\xac",'
    b'"display_title":"\xc5\xbd\xc3\xa1nr: \xc3\x89l\xc3\xa9gie",'
    b'"format":"framenest-media-sidecar","genre_keys":["drama","sci-fi"],'
    b'"location":{"library_id":"'
    + LIBRARY_ID_TEXT.encode("ascii")
    + b'","location_id":"'
    + LOCATION_ID_TEXT.encode("ascii")
    + b'","relative_path":"movies/\xc3\xa9l\xc3\xa9gie.mp4"},"media_id":"'
    + MEDIA_ID_TEXT.encode("ascii")
    + b'","media_kind":"video","processed":{"collection_key":"processed",'
    b'"processed_at_ms":500},"schema_version":1,"tag_definitions":['
    b'{"display_name":"Math","key":"mathematics"},'
    b'{"display_name":"Kompresia","key":"compression"}],'
    b'"tag_keys":["mathematics","compression"],"updated_at_ms":200}\n'
)

ROOT_FIELDS = (
    "format",
    "schema_version",
    "media_id",
    "media_kind",
    "display_title",
    "description",
    "tag_keys",
    "tag_definitions",
    "content_category",
    "acquisition_source",
    "genre_keys",
    "creator_attribution_kind",
    "creator_display_name",
    "creator_handle",
    "creator_stable_id",
    "processed",
    "created_at_ms",
    "updated_at_ms",
    "location",
)


def _media_id() -> MediaId:
    return MediaId.from_string(MEDIA_ID_TEXT)


def _location(*, relative_path: str = "clip.mp4") -> SidecarLocation:
    return SidecarLocation(
        location_id=MediaLocationId.from_string(LOCATION_ID_TEXT),
        library_id=LibraryId.from_string(LIBRARY_ID_TEXT),
        relative_path=MediaRelativePath(relative_path),
    )


def _minimal_document(**overrides: Any) -> SidecarDocument:
    values: dict[str, Any] = {
        "media_id": _media_id(),
        "media_kind": MediaKind.VIDEO,
        "display_title": None,
        "description": None,
        "tag_keys": (),
        "tag_definitions": (),
        "content_category": ContentCategory.GENERAL,
        "acquisition_source": AcquisitionSource.UNKNOWN,
        "genre_keys": (),
        "creator_attribution_kind": None,
        "creator_stable_id": None,
        "creator_handle": None,
        "creator_display_name": None,
        "processed": None,
        "created_at_ms": None,
        "updated_at_ms": None,
        "location": _location(),
    }
    values.update(overrides)
    return SidecarDocument(**values)


def _unicode_movie_document() -> SidecarDocument:
    return _minimal_document(
        display_title=MediaDisplayTitle("Žánr: Élégie"),
        description=MediaDescription("Unicode description Žánr\nand 🎬"),
        tag_keys=(CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
        tag_definitions=(
            SidecarTagDefinition(
                key=CanonicalTagKey("mathematics"),
                display_name=CanonicalTagDisplayName("Math"),
            ),
            SidecarTagDefinition(
                key=CanonicalTagKey("compression"),
                display_name=CanonicalTagDisplayName("Kompresia"),
            ),
        ),
        content_category=ContentCategory.MOVIE,
        acquisition_source=AcquisitionSource.MANUAL_UPLOAD,
        genre_keys=(MovieGenre.DRAMA, MovieGenre.SCI_FI),
        creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
        creator_stable_id="UC123",
        creator_handle="examplehandle",
        creator_display_name="Example Channel",
        processed=SidecarProcessedState(
            collection_key=MediaCollectionKey(PROCESSED_COLLECTION_KEY),
            processed_at_ms=500,
        ),
        created_at_ms=100,
        updated_at_ms=200,
        location=_location(relative_path="movies/élégie.mp4"),
    )


def _object_from_canonical() -> dict[str, Any]:
    payload = json.loads(MINIMAL_CANONICAL_BYTES)
    assert isinstance(payload, dict)
    return payload


def _encode_object(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _expect_error(payload: object, *, error_code: str) -> FrameNestMediaSidecarError:
    with pytest.raises(FrameNestMediaSidecarError) as exc_info:
        decode_media_sidecar(payload)  # type: ignore[arg-type]
    error = exc_info.value
    assert error.error_code == error_code
    message = str(error)
    assert PAYLOAD_MARKER not in message
    assert PRIVATE_PATH_MARKER not in message
    assert "\\x" not in message
    return error


def test_public_identity_constants() -> None:
    assert SIDECAR_FORMAT == "framenest-media-sidecar"
    assert SIDECAR_SCHEMA_VERSION == 1
    assert MAX_SIDECAR_BYTES == 256 * 1024


def test_exact_canonical_minimal_byte_fixture() -> None:
    encoded = encode_media_sidecar(_minimal_document())
    assert encoded == MINIMAL_CANONICAL_BYTES
    assert encoded.endswith(b"\n")
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert encoded.count(b"\n") == 1
    decoded = decode_media_sidecar(MINIMAL_CANONICAL_BYTES)
    assert decoded == _minimal_document()
    assert decoded.format == SIDECAR_FORMAT
    assert decoded.schema_version == SIDECAR_SCHEMA_VERSION


def test_fully_populated_unicode_movie_fixture() -> None:
    document = _unicode_movie_document()
    encoded = encode_media_sidecar(document)
    assert encoded == UNICODE_MOVIE_BYTES
    decoded = decode_media_sidecar(UNICODE_MOVIE_BYTES)
    assert decoded == document
    assert decoded.display_title is not None
    assert decoded.display_title.value == "Žánr: Élégie"
    assert decoded.description is not None
    assert "🎬" in decoded.description.value
    assert decoded.location.relative_path.value == "movies/élégie.mp4"


@pytest.mark.parametrize("kind", list(MediaKind))
def test_all_supported_media_kinds_roundtrip(kind: MediaKind) -> None:
    document = _minimal_document(media_kind=kind)
    encoded = encode_media_sidecar(document)
    decoded = decode_media_sidecar(encoded)
    assert decoded.media_kind is kind
    assert decoded == document


def test_empty_and_populated_optional_states() -> None:
    empty = _minimal_document()
    assert empty.tag_keys == ()
    assert empty.tag_definitions == ()
    assert empty.genre_keys == ()
    assert empty.creator_attribution_kind is None
    assert empty.processed is None

    populated = _unicode_movie_document()
    assert [key.value for key in populated.tag_keys] == ["mathematics", "compression"]
    assert populated.genre_keys == (MovieGenre.DRAMA, MovieGenre.SCI_FI)
    assert populated.creator_attribution_kind is CreatorAttributionKind.YOUTUBE_CHANNEL
    assert populated.processed is not None
    assert populated.processed.collection_key.value == "processed"
    assert populated.processed.processed_at_ms == 500


def test_deterministic_repeated_encode() -> None:
    document = _unicode_movie_document()
    first = encode_media_sidecar(document)
    second = encode_media_sidecar(document)
    assert first == second == UNICODE_MOVIE_BYTES


def test_encode_decode_equality() -> None:
    document = _unicode_movie_document()
    assert decode_media_sidecar(encode_media_sidecar(document)) == document


def test_decode_encode_canonicalizes_whitespace_and_key_order() -> None:
    pretty = json.dumps(_object_from_canonical(), indent=2, sort_keys=False)
    scrambled = (
        '{"updated_at_ms":null,"tag_keys":[],"tag_definitions":[],'
        '"schema_version":1,"processed":null,"media_kind":"video",'
        f'"media_id":"{MEDIA_ID_TEXT}","location":{{"relative_path":"clip.mp4",'
        f'"location_id":"{LOCATION_ID_TEXT}","library_id":"{LIBRARY_ID_TEXT}"}},'
        '"genre_keys":[],"format":"framenest-media-sidecar","display_title":null,'
        '"description":null,"creator_stable_id":null,"creator_handle":null,'
        '"creator_display_name":null,"creator_attribution_kind":null,'
        '"created_at_ms":null,"content_category":"general",'
        '"acquisition_source":"unknown"}'
    )
    assert encode_media_sidecar(decode_media_sidecar(pretty.encode("utf-8"))) == MINIMAL_CANONICAL_BYTES
    assert encode_media_sidecar(decode_media_sidecar(scrambled.encode("utf-8"))) == MINIMAL_CANONICAL_BYTES


def test_trailing_lf_and_no_bom() -> None:
    encoded = encode_media_sidecar(_minimal_document())
    assert encoded.endswith(b"\n")
    assert encoded[-1] == 0x0A
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert encoded.decode("utf-8")[0] == "{"


def test_order_preservation_for_tags_definitions_and_genres() -> None:
    document = _minimal_document(
        content_category=ContentCategory.MOVIE,
        tag_keys=(CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
        tag_definitions=(
            SidecarTagDefinition(
                key=CanonicalTagKey("compression"),
                display_name=CanonicalTagDisplayName("Kompresia"),
            ),
            SidecarTagDefinition(
                key=CanonicalTagKey("mathematics"),
                display_name=CanonicalTagDisplayName("Math"),
            ),
        ),
        genre_keys=(MovieGenre.SCI_FI, MovieGenre.HORROR, MovieGenre.DRAMA),
    )
    decoded = decode_media_sidecar(encode_media_sidecar(document))
    assert [key.value for key in decoded.tag_keys] == ["compression", "mathematics"]
    assert [item.key.value for item in decoded.tag_definitions] == [
        "compression",
        "mathematics",
    ]
    assert decoded.genre_keys == (MovieGenre.SCI_FI, MovieGenre.HORROR, MovieGenre.DRAMA)


def test_nullable_timestamp_pair_both_null_and_both_present() -> None:
    assert _minimal_document().created_at_ms is None
    assert _minimal_document().updated_at_ms is None
    with_times = _minimal_document(created_at_ms=0, updated_at_ms=0)
    assert decode_media_sidecar(encode_media_sidecar(with_times)) == with_times
    ordered = _minimal_document(created_at_ms=10, updated_at_ms=10)
    assert decode_media_sidecar(encode_media_sidecar(ordered)) == ordered


def test_rejects_invalid_utf8_and_bom() -> None:
    bom_error = _expect_error(b"\xef\xbb\xbf" + MINIMAL_CANONICAL_BYTES, error_code="SIDECAR_MALFORMED")
    utf8_error = _expect_error(b"\xff\xfe{" + PAYLOAD_MARKER.encode("ascii"), error_code="SIDECAR_MALFORMED")
    assert PAYLOAD_MARKER not in str(bom_error)
    assert PAYLOAD_MARKER not in str(utf8_error)


def test_rejects_empty_oversize_non_object_and_multiple_values() -> None:
    _expect_error(b"", error_code="SIDECAR_MALFORMED")
    _expect_error(b"   \n", error_code="SIDECAR_MALFORMED")
    _expect_error(b"x" * (MAX_SIDECAR_BYTES + 1), error_code="SIDECAR_MALFORMED")
    _expect_error(b"[]\n", error_code="SIDECAR_MALFORMED")
    _expect_error(b"1\n", error_code="SIDECAR_MALFORMED")
    _expect_error(b"true\n", error_code="SIDECAR_MALFORMED")
    _expect_error(b'"sidecar"\n', error_code="SIDECAR_MALFORMED")
    _expect_error(MINIMAL_CANONICAL_BYTES.rstrip() + b"{}\n", error_code="SIDECAR_MALFORMED")
    _expect_error("not-bytes", error_code="SIDECAR_MALFORMED")
    _expect_error(bytearray(MINIMAL_CANONICAL_BYTES), error_code="SIDECAR_MALFORMED")


def test_rejects_duplicate_keys_at_root_and_nested_levels() -> None:
    root_duplicate = MINIMAL_CANONICAL_BYTES.rstrip()[:-1] + b',"format":"other"}\n'
    nested_duplicate = MINIMAL_CANONICAL_BYTES.replace(
        b'"library_id":"' + LIBRARY_ID_TEXT.encode("ascii") + b'"',
        b'"library_id":"'
        + LIBRARY_ID_TEXT.encode("ascii")
        + b'","library_id":"'
        + LIBRARY_ID_TEXT.encode("ascii")
        + b'"',
        1,
    )
    _expect_error(root_duplicate, error_code="SIDECAR_MALFORMED")
    _expect_error(nested_duplicate, error_code="SIDECAR_MALFORMED")


def test_rejects_missing_and_unknown_root_and_nested_fields() -> None:
    for field in ROOT_FIELDS:
        payload = _object_from_canonical()
        del payload[field]
        _expect_error(_encode_object(payload), error_code="SIDECAR_MALFORMED")

    extra = _object_from_canonical()
    extra["unexpected"] = None
    _expect_error(_encode_object(extra), error_code="SIDECAR_MALFORMED")

    location = _object_from_canonical()
    assert isinstance(location["location"], dict)
    del location["location"]["relative_path"]
    _expect_error(_encode_object(location), error_code="SIDECAR_MALFORMED")

    location_extra = _object_from_canonical()
    assert isinstance(location_extra["location"], dict)
    location_extra["location"]["host_path"] = PRIVATE_PATH_MARKER
    error = _expect_error(_encode_object(location_extra), error_code="SIDECAR_MALFORMED")
    assert PRIVATE_PATH_MARKER not in str(error)

    processed = _object_from_canonical()
    processed["processed"] = {"collection_key": "processed", "processed_at_ms": 1, "extra": 1}
    _expect_error(_encode_object(processed), error_code="SIDECAR_MALFORMED")

    definition = _object_from_canonical()
    definition["tag_keys"] = ["mathematics"]
    definition["tag_definitions"] = [{"key": "mathematics"}]
    _expect_error(_encode_object(definition), error_code="SIDECAR_MALFORMED")


def test_unsupported_format_and_schema_version() -> None:
    wrong_format = _object_from_canonical()
    wrong_format["format"] = "other-sidecar"
    error = _expect_error(_encode_object(wrong_format), error_code="SIDECAR_UNSUPPORTED")
    assert "SIDECAR_UNSUPPORTED" == error.error_code

    wrong_version = _object_from_canonical()
    wrong_version["schema_version"] = 2
    _expect_error(_encode_object(wrong_version), error_code="SIDECAR_UNSUPPORTED")

    future = _object_from_canonical()
    future["schema_version"] = 2
    future["future_field"] = True
    _expect_error(_encode_object(future), error_code="SIDECAR_UNSUPPORTED")

    typed_wrong = _object_from_canonical()
    typed_wrong["format"] = 1
    _expect_error(_encode_object(typed_wrong), error_code="SIDECAR_MALFORMED")

    float_version = _object_from_canonical()
    float_version["schema_version"] = 1.0
    _expect_error(_encode_object(float_version), error_code="SIDECAR_MALFORMED")


def test_explicit_rejection_of_sidecar_written_at_ms() -> None:
    payload = _object_from_canonical()
    payload["sidecar_written_at_ms"] = 123
    _expect_error(_encode_object(payload), error_code="SIDECAR_MALFORMED")


def test_rejects_invalid_uuids_enums_paths_tags_genres_creator_processed_timestamps() -> None:
    invalid_uuid = _object_from_canonical()
    invalid_uuid["media_id"] = "12345678-1234-4234-9234-123456789ABC"
    _expect_error(_encode_object(invalid_uuid), error_code="SIDECAR_MALFORMED")

    uuidv1 = _object_from_canonical()
    uuidv1["media_id"] = "a8098c1a-f86e-11da-bd1a-00112444be1e"
    _expect_error(_encode_object(uuidv1), error_code="SIDECAR_MALFORMED")

    kind = _object_from_canonical()
    kind["media_kind"] = "gif"
    _expect_error(_encode_object(kind), error_code="SIDECAR_MALFORMED")

    category = _object_from_canonical()
    category["content_category"] = "tiktok"
    _expect_error(_encode_object(category), error_code="SIDECAR_MALFORMED")

    source = _object_from_canonical()
    source["acquisition_source"] = "nfo"
    _expect_error(_encode_object(source), error_code="SIDECAR_MALFORMED")

    relative = _object_from_canonical()
    assert isinstance(relative["location"], dict)
    relative["location"]["relative_path"] = PRIVATE_PATH_MARKER.lstrip("/")
    relative["location"]["relative_path"] = "../secret.mp4"
    error = _expect_error(_encode_object(relative), error_code="SIDECAR_MALFORMED")
    assert "../secret.mp4" not in str(error)
    assert PRIVATE_PATH_MARKER not in str(error)

    absolute = _object_from_canonical()
    assert isinstance(absolute["location"], dict)
    absolute["location"]["relative_path"] = PRIVATE_PATH_MARKER
    abs_error = _expect_error(_encode_object(absolute), error_code="SIDECAR_MALFORMED")
    assert PRIVATE_PATH_MARKER not in str(abs_error)

    tag_key = _object_from_canonical()
    tag_key["tag_keys"] = ["Math"]
    tag_key["tag_definitions"] = [{"key": "Math", "display_name": "Math"}]
    _expect_error(_encode_object(tag_key), error_code="SIDECAR_MALFORMED")

    genres = _object_from_canonical()
    genres["genre_keys"] = ["drama"]
    _expect_error(_encode_object(genres), error_code="SIDECAR_MALFORMED")

    duplicate_genres = _object_from_canonical()
    duplicate_genres["content_category"] = "movie"
    duplicate_genres["genre_keys"] = ["drama", "drama"]
    _expect_error(_encode_object(duplicate_genres), error_code="SIDECAR_MALFORMED")

    unknown_genre = _object_from_canonical()
    unknown_genre["content_category"] = "movie"
    unknown_genre["genre_keys"] = ["noir"]
    _expect_error(_encode_object(unknown_genre), error_code="SIDECAR_MALFORMED")

    creator = _object_from_canonical()
    creator["creator_stable_id"] = "UC123"
    _expect_error(_encode_object(creator), error_code="SIDECAR_MALFORMED")

    empty_creator = _object_from_canonical()
    empty_creator["creator_attribution_kind"] = "youtube_channel"
    _expect_error(_encode_object(empty_creator), error_code="SIDECAR_MALFORMED")

    processed_key = _object_from_canonical()
    processed_key["processed"] = {"collection_key": "favorites", "processed_at_ms": 1}
    _expect_error(_encode_object(processed_key), error_code="SIDECAR_MALFORMED")

    processed_null_time = _object_from_canonical()
    processed_null_time["processed"] = {"collection_key": "processed", "processed_at_ms": None}
    _expect_error(_encode_object(processed_null_time), error_code="SIDECAR_MALFORMED")

    one_timestamp = _object_from_canonical()
    one_timestamp["created_at_ms"] = 1
    _expect_error(_encode_object(one_timestamp), error_code="SIDECAR_MALFORMED")

    reversed_time = _object_from_canonical()
    reversed_time["created_at_ms"] = 20
    reversed_time["updated_at_ms"] = 10
    _expect_error(_encode_object(reversed_time), error_code="SIDECAR_MALFORMED")

    negative = _object_from_canonical()
    negative["created_at_ms"] = -1
    negative["updated_at_ms"] = 0
    _expect_error(_encode_object(negative), error_code="SIDECAR_MALFORMED")


def test_rejects_bool_as_int() -> None:
    created = _object_from_canonical()
    created["created_at_ms"] = True
    created["updated_at_ms"] = True
    _expect_error(_encode_object(created), error_code="SIDECAR_MALFORMED")

    processed = _object_from_canonical()
    processed["processed"] = {"collection_key": "processed", "processed_at_ms": False}
    _expect_error(_encode_object(processed), error_code="SIDECAR_MALFORMED")

    version = _object_from_canonical()
    version["schema_version"] = True
    _expect_error(_encode_object(version), error_code="SIDECAR_MALFORMED")


def test_rejects_nan_and_infinities() -> None:
    _expect_error(
        MINIMAL_CANONICAL_BYTES.replace(b'"created_at_ms":null', b'"created_at_ms":NaN'),
        error_code="SIDECAR_MALFORMED",
    )
    _expect_error(
        MINIMAL_CANONICAL_BYTES.replace(b'"updated_at_ms":null', b'"updated_at_ms":Infinity'),
        error_code="SIDECAR_MALFORMED",
    )
    _expect_error(
        MINIMAL_CANONICAL_BYTES.replace(b'"created_at_ms":null', b'"created_at_ms":-Infinity'),
        error_code="SIDECAR_MALFORMED",
    )


def test_rejects_definition_key_mismatch_duplicate_and_wrong_order() -> None:
    mismatch = _object_from_canonical()
    mismatch["tag_keys"] = ["mathematics"]
    mismatch["tag_definitions"] = [{"key": "compression", "display_name": "Kompresia"}]
    _expect_error(_encode_object(mismatch), error_code="SIDECAR_MALFORMED")

    extra_definition = _object_from_canonical()
    extra_definition["tag_keys"] = ["mathematics"]
    extra_definition["tag_definitions"] = [
        {"key": "mathematics", "display_name": "Math"},
        {"key": "compression", "display_name": "Kompresia"},
    ]
    _expect_error(_encode_object(extra_definition), error_code="SIDECAR_MALFORMED")

    missing_definition = _object_from_canonical()
    missing_definition["tag_keys"] = ["mathematics", "compression"]
    missing_definition["tag_definitions"] = [{"key": "mathematics", "display_name": "Math"}]
    _expect_error(_encode_object(missing_definition), error_code="SIDECAR_MALFORMED")

    wrong_order = _object_from_canonical()
    wrong_order["tag_keys"] = ["mathematics", "compression"]
    wrong_order["tag_definitions"] = [
        {"key": "compression", "display_name": "Kompresia"},
        {"key": "mathematics", "display_name": "Math"},
    ]
    _expect_error(_encode_object(wrong_order), error_code="SIDECAR_MALFORMED")

    duplicate_keys = _object_from_canonical()
    duplicate_keys["tag_keys"] = ["mathematics", "mathematics"]
    duplicate_keys["tag_definitions"] = [
        {"key": "mathematics", "display_name": "Math"},
        {"key": "mathematics", "display_name": "Math"},
    ]
    _expect_error(_encode_object(duplicate_keys), error_code="SIDECAR_MALFORMED")

    duplicate_definition_only = _object_from_canonical()
    duplicate_definition_only["tag_keys"] = ["mathematics", "compression"]
    duplicate_definition_only["tag_definitions"] = [
        {"key": "mathematics", "display_name": "Math"},
        {"key": "mathematics", "display_name": "Math Again"},
    ]
    _expect_error(_encode_object(duplicate_definition_only), error_code="SIDECAR_MALFORMED")


def test_sanitized_errors_omit_payload_and_private_paths() -> None:
    payload = (
        b'{"format":"'
        + PAYLOAD_MARKER.encode("ascii")
        + b'","path":"'
        + PRIVATE_PATH_MARKER.encode("ascii")
        + b'"}\n'
    )
    error = _expect_error(payload, error_code="SIDECAR_UNSUPPORTED")
    assert PAYLOAD_MARKER not in str(error)
    assert PRIVATE_PATH_MARKER not in str(error)
    assert error.error_code in {"SIDECAR_MALFORMED", "SIDECAR_UNSUPPORTED"}

    construction_error = None
    with pytest.raises(FrameNestMediaSidecarError) as exc_info:
        _minimal_document(
            location=_location(relative_path="clip.mp4"),
            tag_keys=(CanonicalTagKey("mathematics"),),
            tag_definitions=(),
        )
    construction_error = exc_info.value
    assert construction_error.error_code == "SIDECAR_MALFORMED"
    assert PAYLOAD_MARKER not in str(construction_error)


def test_closed_root_fields_are_always_emitted() -> None:
    encoded = encode_media_sidecar(_minimal_document())
    payload = json.loads(encoded)
    assert set(payload) == set(ROOT_FIELDS)
    for field in ROOT_FIELDS:
        assert field in payload
