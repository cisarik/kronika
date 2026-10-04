"""SQLAlchemy Core table definitions for FrameNest catalog schema."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from kronika.infrastructure.persistence.upload_schema import define_upload_sessions_table

metadata = MetaData()

media_byte_identities = Table(
    "media_byte_identities",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("checksum_algorithm", Text(), nullable=False),
    Column("size_bytes", Integer(), nullable=False),
    Column("checksum_hex", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_media_byte_identities_id_length"),
    CheckConstraint(
        "checksum_algorithm = 'sha256'",
        name="ck_media_byte_identities_algorithm_sha256",
    ),
    CheckConstraint("size_bytes > 0", name="ck_media_byte_identities_size_positive"),
    CheckConstraint(
        "length(checksum_hex) = 64 "
        "AND checksum_hex = lower(checksum_hex) "
        "AND checksum_hex NOT GLOB '*[^0-9a-f]*'",
        name="ck_media_byte_identities_checksum_hex",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_media_byte_identities_created_at_ms_non_negative",
    ),
    UniqueConstraint(
        "checksum_algorithm",
        "size_bytes",
        "checksum_hex",
        name="uq_media_byte_identities_tuple",
    ),
)

devices = Table(
    "devices",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("display_name", Text(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_devices_id_length"),
    CheckConstraint(
        "length(display_name) >= 1 AND length(display_name) <= 120",
        name="ck_devices_display_name_length",
    ),
)

libraries = Table(
    "libraries",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column(
        "device_id",
        Text(),
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("display_name", Text(), nullable=False),
    Column("path_flavor", Text(), nullable=False),
    Column("root_path", Text(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_libraries_id_length"),
    CheckConstraint("length(device_id) = 36", name="ck_libraries_device_id_length"),
    CheckConstraint(
        "length(display_name) >= 1 AND length(display_name) <= 120",
        name="ck_libraries_display_name_length",
    ),
    CheckConstraint(
        "path_flavor IN ('posix', 'windows')",
        name="ck_libraries_path_flavor",
    ),
    CheckConstraint(
        "length(root_path) >= 1 AND length(root_path) <= 4096",
        name="ck_libraries_root_path_length",
    ),
    UniqueConstraint(
        "device_id",
        "path_flavor",
        "root_path",
        name="uq_libraries_device_root",
    ),
)

logical_media = Table(
    "logical_media",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("media_kind", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_logical_media_id_length"),
    CheckConstraint(
        "media_kind IN ('video', 'animated_image', 'image')",
        name="ck_logical_media_kind",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_logical_media_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_logical_media_updated_at_ms_non_negative",
    ),
)

media_content_publications = Table(
    "media_content_publications",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="CASCADE",
            name="fk_media_content_publications_media_id",
        ),
        primary_key=True,
        nullable=False,
    ),
    Column("published_at_ms", Integer(), nullable=False),
    Column("publication_origin", Text(), nullable=False),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_media_content_publications_media_id_length",
    ),
    CheckConstraint(
        "published_at_ms >= 0",
        name="ck_media_content_publications_published_at_non_negative",
    ),
    CheckConstraint(
        "publication_origin IN "
        "('legacy_backfill', 'admin_explicit', 'companion_review')",
        name="ck_media_content_publications_origin",
    ),
    Index(
        "ix_media_content_publications_published_at",
        "published_at_ms",
        "media_id",
    ),
)

media_covers = Table(
    "media_covers",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="CASCADE",
            name="fk_media_covers_media_id",
        ),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "source_location_id",
        Text(),
        ForeignKey(
            "physical_media_locations.id",
            ondelete="SET NULL",
            name="fk_media_covers_source_location_id",
        ),
        nullable=True,
    ),
    Column("source_reference", Text(), nullable=False),
    Column("source_kind", Text(), nullable=False),
    Column("source_timestamp_ms", Integer(), nullable=False),
    Column("source_size_bytes", Integer(), nullable=False),
    Column("source_mtime_ns", Integer(), nullable=True),
    Column("source_duration_ms", Integer(), nullable=True),
    Column("source_observation_version", Text(), nullable=False),
    Column("source_observation_digest", Text(), nullable=False),
    Column("artifact_profile", Text(), nullable=False),
    Column("artifact_media_type", Text(), nullable=False),
    Column("artifact_digest", Text(), nullable=False),
    Column("artifact_width", Integer(), nullable=False),
    Column("artifact_height", Integer(), nullable=False),
    Column("artifact_byte_size", Integer(), nullable=False),
    Column("revision", Integer(), nullable=False),
    Column("accepted_at_ms", Integer(), nullable=False),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_media_covers_media_id_length",
    ),
    CheckConstraint(
        "source_location_id IS NULL OR length(source_location_id) = 36",
        name="ck_media_covers_source_location_id_length",
    ),
    CheckConstraint(
        "substr(source_reference, 1, 9) = 'location:' "
        "AND length(source_reference) = 45",
        name="ck_media_covers_source_reference_shape",
    ),
    CheckConstraint(
        "source_kind IN ('gif', 'mp4', 'image')",
        name="ck_media_covers_source_kind",
    ),
    CheckConstraint(
        "source_timestamp_ms >= 0",
        name="ck_media_covers_source_timestamp_non_negative",
    ),
    CheckConstraint(
        "source_size_bytes > 0",
        name="ck_media_covers_source_size_positive",
    ),
    CheckConstraint(
        "source_mtime_ns IS NULL OR source_mtime_ns >= 0",
        name="ck_media_covers_source_mtime_non_negative",
    ),
    CheckConstraint(
        "source_duration_ms IS NULL OR source_duration_ms >= 0",
        name="ck_media_covers_source_duration_non_negative",
    ),
    CheckConstraint(
        "source_observation_version = 'cover-source-observation-v1'",
        name="ck_media_covers_source_observation_version",
    ),
    CheckConstraint(
        "length(source_observation_digest) = 64 "
        "AND source_observation_digest = lower(source_observation_digest) "
        "AND source_observation_digest NOT GLOB '*[^0-9a-f]*'",
        name="ck_media_covers_source_observation_digest",
    ),
    CheckConstraint(
        "artifact_profile = 'durable-cover-jpeg-v1'",
        name="ck_media_covers_artifact_profile",
    ),
    CheckConstraint(
        "artifact_media_type = 'image/jpeg'",
        name="ck_media_covers_artifact_media_type",
    ),
    CheckConstraint(
        "length(artifact_digest) = 64 "
        "AND artifact_digest = lower(artifact_digest) "
        "AND artifact_digest NOT GLOB '*[^0-9a-f]*'",
        name="ck_media_covers_artifact_digest",
    ),
    CheckConstraint(
        "artifact_width > 0",
        name="ck_media_covers_artifact_width_positive",
    ),
    CheckConstraint(
        "artifact_height > 0",
        name="ck_media_covers_artifact_height_positive",
    ),
    CheckConstraint(
        "artifact_byte_size > 0",
        name="ck_media_covers_artifact_byte_size_positive",
    ),
    CheckConstraint(
        "revision >= 1",
        name="ck_media_covers_revision_positive",
    ),
    CheckConstraint(
        "accepted_at_ms >= 0",
        name="ck_media_covers_accepted_at_non_negative",
    ),
    Index(
        "ix_media_covers_source_location_id",
        "source_location_id",
    ),
)

physical_media_locations = Table(
    "physical_media_locations",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column(
        "media_id",
        Text(),
        ForeignKey("logical_media.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "library_id",
        Text(),
        ForeignKey("libraries.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("relative_path", Text(), nullable=False),
    Column("availability", Text(), nullable=False),
    Column("observed_size_bytes", Integer(), nullable=True),
    Column("observed_mtime_ns", Integer(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_physical_media_locations_id_length"),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_physical_media_locations_media_id_length",
    ),
    CheckConstraint(
        "length(library_id) = 36",
        name="ck_physical_media_locations_library_id_length",
    ),
    CheckConstraint(
        "length(relative_path) >= 1 AND length(relative_path) <= 4096",
        name="ck_physical_media_locations_relative_path_length",
    ),
    CheckConstraint(
        "availability IN ('available', 'offline', 'missing', 'unverified', 'archived')",
        name="ck_physical_media_locations_availability",
    ),
    CheckConstraint(
        "observed_size_bytes IS NULL OR observed_size_bytes >= 0",
        name="ck_physical_media_locations_observed_size_non_negative",
    ),
    CheckConstraint(
        "observed_mtime_ns IS NULL OR observed_mtime_ns >= 0",
        name="ck_physical_media_locations_observed_mtime_non_negative",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_physical_media_locations_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_physical_media_locations_updated_at_ms_non_negative",
    ),
    UniqueConstraint(
        "library_id",
        "relative_path",
        name="uq_physical_media_locations_library_path",
    ),
    Index("ix_physical_media_locations_media_id", "media_id"),
)

canonical_tags = Table(
    "canonical_tags",
    metadata,
    Column("key", Text(), primary_key=True, nullable=False),
    Column("display_name", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    CheckConstraint(
        "length(key) >= 1 AND length(key) <= 64",
        name="ck_canonical_tags_key_length",
    ),
    CheckConstraint("key = lower(key)", name="ck_canonical_tags_key_lowercase"),
    CheckConstraint(
        "key GLOB '[a-z]*' "
        "AND key NOT GLOB '*[^a-z0-9-]*' "
        "AND key NOT LIKE '%--%' "
        "AND substr(key, length(key), 1) != '-'",
        name="ck_canonical_tags_key_slug",
    ),
    CheckConstraint(
        "length(display_name) >= 1 AND length(display_name) <= 80",
        name="ck_canonical_tags_display_name_length",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_canonical_tags_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_canonical_tags_updated_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= created_at_ms",
        name="ck_canonical_tags_updated_not_before_created",
    ),
)

media_metadata = Table(
    "media_metadata",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey("logical_media.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    ),
    Column("display_title", Text(), nullable=True),
    Column("description", Text(), nullable=True),
    Column("content_category", Text(), nullable=False, server_default="general"),
    Column("acquisition_source", Text(), nullable=False, server_default="unknown"),
    Column("creator_attribution_kind", Text(), nullable=True),
    Column("creator_stable_id", Text(), nullable=True),
    Column("creator_handle", Text(), nullable=True),
    Column("creator_display_name", Text(), nullable=True),
    Column("collection_key", Text(), nullable=True),
    Column("processed_at_ms", Integer(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    CheckConstraint("length(media_id) = 36", name="ck_media_metadata_media_id_length"),
    CheckConstraint(
        "content_category IN ('general', 'meme', 'movie', 'youtube')",
        name="ck_media_metadata_content_category",
    ),
    CheckConstraint(
        "acquisition_source IN ("
        "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim', "
        "'x_manual_claim')",
        name="ck_media_metadata_acquisition_source",
    ),
    CheckConstraint(
        "("
        "creator_attribution_kind IS NULL "
        "AND creator_stable_id IS NULL "
        "AND creator_handle IS NULL "
        "AND creator_display_name IS NULL"
        ") OR ("
        "creator_attribution_kind IS NOT NULL "
        "AND creator_attribution_kind IN ('youtube_channel', 'x_author') "
        "AND ("
        "creator_stable_id IS NOT NULL "
        "OR creator_handle IS NOT NULL "
        "OR creator_display_name IS NOT NULL"
        ") "
        "AND (creator_stable_id IS NULL OR ("
        "length(creator_stable_id) >= 1 AND length(creator_stable_id) <= 128"
        ")) "
        "AND (creator_handle IS NULL OR ("
        "length(creator_handle) >= 1 AND length(creator_handle) <= 64 "
        "AND creator_handle = lower(creator_handle) "
        "AND substr(creator_handle, 1, 1) != '@'"
        ")) "
        "AND (creator_display_name IS NULL OR ("
        "length(creator_display_name) >= 1 AND length(creator_display_name) <= 200"
        "))"
        ")",
        name="ck_media_metadata_creator_attribution",
    ),
    CheckConstraint(
        "collection_key IS NULL OR collection_key = 'processed'",
        name="ck_media_metadata_collection_key_valid",
    ),
    CheckConstraint(
        "(collection_key IS NULL AND processed_at_ms IS NULL) "
        "OR (collection_key = 'processed' AND processed_at_ms IS NOT NULL)",
        name="ck_media_metadata_collection_paired",
    ),
    CheckConstraint(
        "processed_at_ms IS NULL OR processed_at_ms >= 0",
        name="ck_media_metadata_processed_at_ms_non_negative",
    ),
    CheckConstraint(
        "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
        name="ck_media_metadata_title_length",
    ),
    CheckConstraint(
        "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
        name="ck_media_metadata_description_length",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_media_metadata_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_media_metadata_updated_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= created_at_ms",
        name="ck_media_metadata_updated_not_before_created",
    ),
    Index(
        "ix_media_metadata_collection",
        "collection_key",
        "processed_at_ms",
        "media_id",
    ),
    Index("ix_media_metadata_content_category", "content_category", "media_id"),
    Index("ix_media_metadata_acquisition_source", "acquisition_source", "media_id"),
    Index(
        "ix_media_metadata_creator_stable",
        "creator_attribution_kind",
        "creator_stable_id",
        "media_id",
    ),
    Index(
        "ix_media_metadata_creator_handle",
        "creator_attribution_kind",
        "creator_handle",
        "media_id",
    ),
)

media_genres = Table(
    "media_genres",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey("media_metadata.media_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("genre_key", Text(), nullable=False),
    Column("position", Integer(), nullable=False),
    CheckConstraint("length(media_id) = 36", name="ck_media_genres_media_id_length"),
    CheckConstraint(
        "genre_key IN ("
        "'drama', 'comedy', 'sci-fi', 'thriller', 'horror', 'action', 'adventure', "
        "'documentary', 'animation', 'family', 'romance', 'crime', 'fantasy', 'mystery')",
        name="ck_media_genres_genre_key",
    ),
    CheckConstraint(
        "position >= 0 AND position < 8",
        name="ck_media_genres_position_range",
    ),
    PrimaryKeyConstraint("media_id", "genre_key", name="pk_media_genres"),
    UniqueConstraint("media_id", "position", name="uq_media_genres_media_position"),
    Index("ix_media_genres_genre_key", "genre_key"),
)

media_canonical_tags = Table(
    "media_canonical_tags",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey("media_metadata.media_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "tag_key",
        Text(),
        ForeignKey("canonical_tags.key", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("position", Integer(), nullable=False),
    CheckConstraint("length(media_id) = 36", name="ck_media_canonical_tags_media_id_length"),
    CheckConstraint(
        "length(tag_key) >= 1 AND length(tag_key) <= 64",
        name="ck_media_canonical_tags_tag_key_length",
    ),
    CheckConstraint(
        "position >= 0 AND position < 32",
        name="ck_media_canonical_tags_position_range",
    ),
    PrimaryKeyConstraint("media_id", "tag_key", name="pk_media_canonical_tags"),
    UniqueConstraint("media_id", "position", name="uq_media_canonical_tags_media_position"),
)

upload_sessions = define_upload_sessions_table(metadata)

upload_publications = Table(
    "upload_publications",
    metadata,
    Column(
        "upload_id",
        Text(),
        ForeignKey("upload_sessions.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    ),
    Column("publication_id", Text(), nullable=False),
    Column(
        "destination_id",
        Text(),
        ForeignKey("libraries.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("relative_target", Text(), nullable=False),
    Column(
        "byte_identity_id",
        Text(),
        ForeignKey("media_byte_identities.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("expected_size_bytes", Integer(), nullable=False),
    Column("checksum_algorithm", Text(), nullable=False),
    Column("checksum_hex", Text(), nullable=False),
    Column("validated_media_kind", Text(), nullable=False),
    Column("validated_format", Text(), nullable=False),
    Column("state", Text(), nullable=False),
    Column("cleanup_state", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    Column("verified_at_ms", Integer(), nullable=True),
    Column("cleanup_completed_at_ms", Integer(), nullable=True),
    Column("version", Integer(), nullable=False),
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="RESTRICT",
            name="fk_upload_publications_media_id",
        ),
        nullable=True,
    ),
    Column(
        "media_location_id",
        Text(),
        ForeignKey(
            "physical_media_locations.id",
            ondelete="RESTRICT",
            name="fk_upload_publications_media_location_id",
        ),
        nullable=True,
    ),
    CheckConstraint(
        "length(upload_id) = 36",
        name="ck_upload_publications_upload_id_length",
    ),
    CheckConstraint(
        "length(publication_id) = 36",
        name="ck_upload_publications_publication_id_length",
    ),
    CheckConstraint(
        "length(destination_id) = 36",
        name="ck_upload_publications_destination_id_length",
    ),
    CheckConstraint(
        "length(byte_identity_id) = 36",
        name="ck_upload_publications_byte_identity_id_length",
    ),
    CheckConstraint(
        "length(relative_target) = 36 "
        "AND relative_target = lower(relative_target) "
        "AND substr(relative_target, 1, 32) NOT GLOB '*[^0-9a-f]*' "
        "AND relative_target NOT GLOB '*/*' "
        "AND relative_target NOT GLOB '*\\*' "
        "AND substr(relative_target, 1, 32) = replace(publication_id, '-', '') "
        "AND ((validated_format = 'gif' AND substr(relative_target, 33) = '.gif') "
        "OR (validated_format = 'mp4' AND substr(relative_target, 33) = '.mp4') "
        "OR (validated_format = 'jpg' AND substr(relative_target, 33) = '.jpg') "
        "OR (validated_format = 'png' AND substr(relative_target, 33) = '.png'))",
        name="ck_upload_publications_relative_target_opaque",
    ),
    CheckConstraint(
        "expected_size_bytes > 0",
        name="ck_upload_publications_expected_size_positive",
    ),
    CheckConstraint(
        "checksum_algorithm = 'sha256'",
        name="ck_upload_publications_checksum_algorithm",
    ),
    CheckConstraint(
        "length(checksum_hex) = 64 "
        "AND checksum_hex = lower(checksum_hex) "
        "AND checksum_hex NOT GLOB '*[^0-9a-f]*'",
        name="ck_upload_publications_checksum_hex",
    ),
    CheckConstraint(
        "(validated_media_kind = 'animated_image' AND validated_format = 'gif') "
        "OR (validated_media_kind = 'video' AND validated_format = 'mp4') "
        "OR (validated_media_kind = 'image' AND validated_format = 'jpg') "
        "OR (validated_media_kind = 'image' AND validated_format = 'png')",
        name="ck_upload_publications_validation_evidence_pair",
    ),
    CheckConstraint(
        "state IN ('reserved', 'verified')",
        name="ck_upload_publications_state",
    ),
    CheckConstraint(
        "cleanup_state IN ('pending', 'complete')",
        name="ck_upload_publications_cleanup_state",
    ),
    CheckConstraint(
        "(state = 'reserved' AND cleanup_state = 'pending' "
        "AND verified_at_ms IS NULL AND cleanup_completed_at_ms IS NULL) "
        "OR (state = 'verified' AND verified_at_ms IS NOT NULL "
        "AND ((cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
        "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)))",
        name="ck_upload_publications_progress",
    ),
    CheckConstraint(
        "created_at_ms >= 0 AND updated_at_ms >= created_at_ms",
        name="ck_upload_publications_timestamps",
    ),
    CheckConstraint(
        "verified_at_ms IS NULL OR verified_at_ms >= created_at_ms",
        name="ck_upload_publications_verified_at_ms",
    ),
    CheckConstraint(
        "cleanup_completed_at_ms IS NULL "
        "OR cleanup_completed_at_ms >= verified_at_ms",
        name="ck_upload_publications_cleanup_completed_at_ms",
    ),
    CheckConstraint(
        "version >= 0",
        name="ck_upload_publications_version_non_negative",
    ),
    CheckConstraint(
        "(media_id IS NULL AND media_location_id IS NULL) "
        "OR (media_id IS NOT NULL AND media_location_id IS NOT NULL)",
        name="ck_upload_publications_catalog_linkage",
    ),
    CheckConstraint(
        "media_id IS NULL OR length(media_id) = 36",
        name="ck_upload_publications_media_id_length",
    ),
    CheckConstraint(
        "media_location_id IS NULL OR length(media_location_id) = 36",
        name="ck_upload_publications_media_location_id_length",
    ),
    UniqueConstraint(
        "publication_id",
        name="uq_upload_publications_publication_id",
    ),
    UniqueConstraint(
        "destination_id",
        "relative_target",
        name="uq_upload_publications_destination_target",
    ),
    UniqueConstraint(
        "media_id",
        name="uq_upload_publications_media_id",
    ),
    UniqueConstraint(
        "media_location_id",
        name="uq_upload_publications_media_location_id",
    ),
    Index(
        "ix_upload_publications_state_cleanup",
        "state",
        "cleanup_state",
        "updated_at_ms",
        "upload_id",
    ),
    Index("ix_upload_publications_byte_identity_id", "byte_identity_id"),
)

youtube_acquisition_claims = Table(
    "youtube_acquisition_claims",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("state", Text(), nullable=False),
    Column("acquisition_source", Text(), nullable=False),
    Column("submitted_url", Text(), nullable=False),
    Column("canonical_url", Text(), nullable=False),
    Column("youtube_video_id", Text(), nullable=False),
    Column("extractor_key", Text(), nullable=False),
    Column(
        "retry_of_claim_id",
        Text(),
        ForeignKey(
            "youtube_acquisition_claims.id",
            ondelete="RESTRICT",
            name="fk_youtube_claims_retry_of_claim_id",
        ),
        nullable=True,
    ),
    Column(
        "resolved_claim_id",
        Text(),
        ForeignKey(
            "youtube_acquisition_claims.id",
            ondelete="RESTRICT",
            name="fk_youtube_claims_resolved_claim_id",
        ),
        nullable=True,
    ),
    Column(
        "upload_id",
        Text(),
        ForeignKey(
            "upload_sessions.id",
            ondelete="RESTRICT",
            name="fk_youtube_claims_upload_id",
        ),
        nullable=True,
    ),
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="RESTRICT",
            name="fk_youtube_claims_media_id",
        ),
        nullable=True,
    ),
    Column(
        "media_location_id",
        Text(),
        ForeignKey(
            "physical_media_locations.id",
            ondelete="RESTRICT",
            name="fk_youtube_claims_media_location_id",
        ),
        nullable=True,
    ),
    Column("confirmation_method", Text(), nullable=False),
    Column("confirmed_at_ms", Integer(), nullable=False),
    Column("upstream_title", Text(), nullable=True),
    Column("upstream_channel", Text(), nullable=True),
    Column("upstream_channel_id", Text(), nullable=True),
    Column("upstream_source_date", Text(), nullable=True),
    Column("downloader_name", Text(), nullable=True),
    Column("downloader_version", Text(), nullable=True),
    Column("extractor_version", Text(), nullable=True),
    Column("selected_video_format_id", Text(), nullable=True),
    Column("selected_audio_format_id", Text(), nullable=True),
    Column("remote_filename", Text(), nullable=True),
    Column("generated_filename", Text(), nullable=False),
    Column("staging_key", Text(), nullable=False),
    Column("downloaded_size_bytes", Integer(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    Column("downloaded_at_ms", Integer(), nullable=True),
    Column("completed_at_ms", Integer(), nullable=True),
    Column("catalog_removed_at_ms", Integer(), nullable=True),
    Column("failure_stage", Text(), nullable=True),
    Column("failure_code", Text(), nullable=True),
    Column("cleanup_state", Text(), nullable=False),
    Column("cleanup_completed_at_ms", Integer(), nullable=True),
    Column("version", Integer(), nullable=False),
    Column("created_by_login_key", Text(), nullable=True),
    CheckConstraint("length(id) = 36", name="ck_youtube_claims_id_length"),
    CheckConstraint(
        "created_by_login_key IS NULL OR ("
        "length(created_by_login_key) >= 1 "
        "AND length(created_by_login_key) <= 254 "
        "AND created_by_login_key = lower(created_by_login_key) "
        "AND instr(created_by_login_key, ' ') = 0 "
        "AND instr(created_by_login_key, char(9)) = 0 "
        "AND instr(created_by_login_key, char(10)) = 0 "
        "AND instr(created_by_login_key, char(13)) = 0)",
        name="ck_youtube_claims_created_by_login_key",
    ),
    CheckConstraint(
        "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
        "'downloaded', 'handoff', 'handed_off', 'duplicate_resolved', "
        "'cataloged', 'catalog_removed', 'failed')",
        name="ck_youtube_claims_state",
    ),
    CheckConstraint(
        "acquisition_source = 'youtube_manual_claim'",
        name="ck_youtube_claims_acquisition_source",
    ),
    CheckConstraint(
        "length(submitted_url) >= 1 AND length(submitted_url) <= 2048",
        name="ck_youtube_claims_submitted_url_length",
    ),
    CheckConstraint(
        "canonical_url = 'https://www.youtube.com/watch?v=' || youtube_video_id",
        name="ck_youtube_claims_canonical_identity",
    ),
    CheckConstraint(
        "length(youtube_video_id) = 11 "
        "AND youtube_video_id NOT GLOB '*[^A-Za-z0-9_-]*'",
        name="ck_youtube_claims_video_id",
    ),
    CheckConstraint(
        "extractor_key = 'Youtube'",
        name="ck_youtube_claims_extractor_key",
    ),
    CheckConstraint(
        "retry_of_claim_id IS NULL OR "
        "(length(retry_of_claim_id) = 36 AND retry_of_claim_id != id)",
        name="ck_youtube_claims_retry_lineage",
    ),
    CheckConstraint(
        "resolved_claim_id IS NULL OR "
        "(length(resolved_claim_id) = 36 AND resolved_claim_id != id)",
        name="ck_youtube_claims_resolution_lineage",
    ),
    CheckConstraint(
        "upload_id IS NULL OR length(upload_id) = 36",
        name="ck_youtube_claims_upload_id_length",
    ),
    CheckConstraint(
        "(media_id IS NULL AND media_location_id IS NULL) "
        "OR (length(media_id) = 36 AND length(media_location_id) = 36)",
        name="ck_youtube_claims_catalog_linkage_pair",
    ),
    CheckConstraint(
        "confirmation_method IN ('interactive', 'yes_flag')",
        name="ck_youtube_claims_confirmation_method",
    ),
    CheckConstraint(
        "confirmed_at_ms >= created_at_ms",
        name="ck_youtube_claims_confirmed_at",
    ),
    CheckConstraint(
        "upstream_title IS NULL OR "
        "(length(upstream_title) >= 1 AND length(upstream_title) <= 500)",
        name="ck_youtube_claims_upstream_title_length",
    ),
    CheckConstraint(
        "upstream_channel IS NULL OR "
        "(length(upstream_channel) >= 1 AND length(upstream_channel) <= 200)",
        name="ck_youtube_claims_upstream_channel_length",
    ),
    CheckConstraint(
        "upstream_channel_id IS NULL OR "
        "(length(upstream_channel_id) >= 1 AND length(upstream_channel_id) <= 128)",
        name="ck_youtube_claims_upstream_channel_id_length",
    ),
    CheckConstraint(
        "upstream_source_date IS NULL OR "
        "(length(upstream_source_date) = 10 "
        "AND upstream_source_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
        name="ck_youtube_claims_upstream_source_date",
    ),
    CheckConstraint(
        "downloader_name IS NULL OR "
        "(length(downloader_name) >= 1 AND length(downloader_name) <= 120)",
        name="ck_youtube_claims_downloader_name_length",
    ),
    CheckConstraint(
        "downloader_version IS NULL OR "
        "(length(downloader_version) >= 1 AND length(downloader_version) <= 120)",
        name="ck_youtube_claims_downloader_version_length",
    ),
    CheckConstraint(
        "extractor_version IS NULL OR "
        "(length(extractor_version) >= 1 AND length(extractor_version) <= 120)",
        name="ck_youtube_claims_extractor_version_length",
    ),
    CheckConstraint(
        "selected_video_format_id IS NULL OR "
        "(length(selected_video_format_id) >= 1 "
        "AND length(selected_video_format_id) <= 120)",
        name="ck_youtube_claims_video_format_id_length",
    ),
    CheckConstraint(
        "selected_audio_format_id IS NULL OR "
        "(length(selected_audio_format_id) >= 1 "
        "AND length(selected_audio_format_id) <= 120)",
        name="ck_youtube_claims_audio_format_id_length",
    ),
    CheckConstraint(
        "remote_filename IS NULL OR "
        "(length(remote_filename) >= 1 AND length(remote_filename) <= 500)",
        name="ck_youtube_claims_remote_filename_length",
    ),
    CheckConstraint(
        "generated_filename = 'youtube-' || youtube_video_id || '.mp4'",
        name="ck_youtube_claims_generated_filename",
    ),
    CheckConstraint(
        "length(staging_key) = 32 AND staging_key = lower(staging_key) "
        "AND staging_key NOT GLOB '*[^0-9a-f]*'",
        name="ck_youtube_claims_staging_key",
    ),
    CheckConstraint(
        "downloaded_size_bytes IS NULL OR downloaded_size_bytes > 0",
        name="ck_youtube_claims_downloaded_size",
    ),
    CheckConstraint(
        "created_at_ms >= 0 AND updated_at_ms >= created_at_ms",
        name="ck_youtube_claims_timestamps",
    ),
    CheckConstraint(
        "downloaded_at_ms IS NULL OR downloaded_at_ms >= created_at_ms",
        name="ck_youtube_claims_downloaded_at",
    ),
    CheckConstraint(
        "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
        name="ck_youtube_claims_completed_at",
    ),
    CheckConstraint(
        "(failure_stage IS NULL AND failure_code IS NULL) OR ("
        "failure_stage IN ('configuration', 'inspection', 'download', 'staging', "
        "'handoff', 'downstream', 'cleanup', 'internal') "
        "AND length(failure_code) >= 1 AND length(failure_code) <= 80 "
        "AND failure_code NOT GLOB '*[^A-Z0-9_]*')",
        name="ck_youtube_claims_failure_pair",
    ),
    CheckConstraint(
        "cleanup_state IN ('pending', 'complete') "
        "AND ((cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
        "OR (cleanup_state = 'complete' "
        "AND cleanup_completed_at_ms IS NOT NULL))",
        name="ck_youtube_claims_cleanup_pair",
    ),
    CheckConstraint(
        "version >= 0",
        name="ck_youtube_claims_version",
    ),
    CheckConstraint(
        "(state IN ('downloaded', 'handoff', 'handed_off', 'cataloged') "
        "AND downloaded_size_bytes IS NOT NULL AND downloaded_at_ms IS NOT NULL) "
        "OR state NOT IN ('downloaded', 'handoff', 'handed_off', 'cataloged')",
        name="ck_youtube_claims_download_payload",
    ),
    CheckConstraint(
        "(state IN ('handed_off', 'cataloged') AND upload_id IS NOT NULL) "
        "OR state NOT IN ('handed_off', 'cataloged')",
        name="ck_youtube_claims_handoff_linkage",
    ),
    CheckConstraint(
        "(state = 'cataloged' AND media_id IS NOT NULL "
        "AND media_location_id IS NOT NULL "
        "AND completed_at_ms IS NOT NULL AND failure_code IS NULL "
        "AND catalog_removed_at_ms IS NULL) "
        "OR (state = 'duplicate_resolved' "
        "AND media_id IS NOT NULL AND media_location_id IS NOT NULL "
        "AND completed_at_ms IS NOT NULL AND failure_code IS NULL "
        "AND catalog_removed_at_ms IS NULL) "
        "OR (state = 'catalog_removed' AND media_id IS NULL "
        "AND media_location_id IS NULL AND completed_at_ms IS NOT NULL "
        "AND catalog_removed_at_ms IS NOT NULL "
        "AND catalog_removed_at_ms >= completed_at_ms "
        "AND failure_code IS NULL) "
        "OR (state = 'failed' AND failure_code IS NOT NULL "
        "AND completed_at_ms IS NOT NULL AND catalog_removed_at_ms IS NULL) "
        "OR (state NOT IN ('cataloged', 'duplicate_resolved', 'catalog_removed', "
        "'failed') "
        "AND completed_at_ms IS NULL AND failure_code IS NULL "
        "AND catalog_removed_at_ms IS NULL)",
        name="ck_youtube_claims_terminal_payload",
    ),
    UniqueConstraint("staging_key", name="uq_youtube_claims_staging_key"),
    UniqueConstraint("upload_id", name="uq_youtube_claims_upload_id"),
    Index(
        "uq_youtube_claims_active_source_admin",
        "extractor_key",
        "youtube_video_id",
        unique=True,
        sqlite_where=text(
            "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
            "'downloaded', 'handoff', 'handed_off') "
            "AND created_by_login_key IS NULL"
        ),
    ),
    Index(
        "uq_youtube_claims_active_source_requester",
        "extractor_key",
        "youtube_video_id",
        "created_by_login_key",
        unique=True,
        sqlite_where=text(
            "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
            "'downloaded', 'handoff', 'handed_off') "
            "AND created_by_login_key IS NOT NULL"
        ),
    ),
    Index(
        "ix_youtube_claims_source_identity",
        "extractor_key",
        "youtube_video_id",
        "created_at_ms",
    ),
    Index("ix_youtube_claims_state", "state", "updated_at_ms", "id"),
    Index("ix_youtube_claims_retry_of", "retry_of_claim_id"),
    Index("ix_youtube_claims_resolved_claim", "resolved_claim_id"),
    Index("ix_youtube_claims_media", "media_id", "media_location_id"),
    Index("ix_youtube_claims_created_by_login_key", "created_by_login_key"),
    Index(
        "ix_youtube_claims_owner_updated",
        "created_by_login_key",
        "updated_at_ms",
        "id",
    ),
    Index(
        "ix_youtube_claims_media_requester_live",
        "media_id",
        "created_by_login_key",
    ),
)

x_post_claims = Table(
    "x_post_claims",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("state", Text(), nullable=False),
    Column("acquisition_source", Text(), nullable=False),
    Column("submitted_url", Text(), nullable=False),
    Column("canonical_url", Text(), nullable=False),
    Column("x_post_id", Text(), nullable=False),
    Column("extractor_key", Text(), nullable=False),
    Column("created_by_login_key", Text(), nullable=True),
    Column(
        "retry_of_claim_id",
        Text(),
        ForeignKey("x_post_claims.id", ondelete="RESTRICT",
                   name="fk_x_post_claims_retry_of_claim_id"),
        nullable=True,
    ),
    Column(
        "resolved_claim_id",
        Text(),
        ForeignKey("x_post_claims.id", ondelete="RESTRICT",
                   name="fk_x_post_claims_resolved_claim_id"),
        nullable=True,
    ),
    Column("source_author_stable_id", Text(), nullable=True),
    Column("source_author_handle", Text(), nullable=True),
    Column("source_author_display_name", Text(), nullable=True),
    Column("source_post_text", Text(), nullable=True),
    Column("source_posted_at_ms", Integer(), nullable=True),
    Column("title", Text(), nullable=True),
    Column("extractor_version", Text(), nullable=True),
    Column("discovered_asset_count", Integer(), nullable=False),
    Column("success_count", Integer(), nullable=False),
    Column("failure_count", Integer(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    Column("completed_at_ms", Integer(), nullable=True),
    Column("catalog_removed_at_ms", Integer(), nullable=True),
    Column("failure_stage", Text(), nullable=True),
    Column("failure_code", Text(), nullable=True),
    Column("cleanup_state", Text(), nullable=False),
    Column("cleanup_completed_at_ms", Integer(), nullable=True),
    Column("requested_content_category", Text(), nullable=True),
    Column("version", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_x_post_claims_id_length"),
    CheckConstraint(
        "created_by_login_key IS NULL OR ("
        "length(created_by_login_key) >= 1 "
        "AND length(created_by_login_key) <= 254 "
        "AND created_by_login_key = lower(created_by_login_key) "
        "AND instr(created_by_login_key, ' ') = 0 "
        "AND instr(created_by_login_key, char(9)) = 0 "
        "AND instr(created_by_login_key, char(10)) = 0 "
        "AND instr(created_by_login_key, char(13)) = 0)",
        name="ck_x_post_claims_created_by_login_key",
    ),
    CheckConstraint(
        "state IN ('submitted', 'queued', 'extracting', 'acquiring', "
        "'handing_off', 'completed', 'completed_partial', 'failed', "
        "'duplicate_resolved', 'catalog_removed')",
        name="ck_x_post_claims_state",
    ),
    CheckConstraint(
        "acquisition_source = 'x_manual_claim'",
        name="ck_x_post_claims_acquisition_source",
    ),
    CheckConstraint(
        "length(submitted_url) >= 1 AND length(submitted_url) <= 2048",
        name="ck_x_post_claims_submitted_url_length",
    ),
    CheckConstraint(
        "length(canonical_url) >= 1 AND length(canonical_url) <= 2048",
        name="ck_x_post_claims_canonical_url_length",
    ),
    CheckConstraint(
        "length(x_post_id) >= 1 AND length(x_post_id) <= 19 "
        "AND x_post_id NOT GLOB '*[^0-9]*'",
        name="ck_x_post_claims_post_id",
    ),
    CheckConstraint(
        "extractor_key = 'X'",
        name="ck_x_post_claims_extractor_key",
    ),
    CheckConstraint(
        "source_author_handle IS NULL OR ("
        "length(source_author_handle) >= 1 "
        "AND length(source_author_handle) <= 64 "
        "AND source_author_handle = lower(source_author_handle) "
        "AND substr(source_author_handle, 1, 1) != '@')",
        name="ck_x_post_claims_author_handle",
    ),
    CheckConstraint(
        "source_posted_at_ms IS NULL OR source_posted_at_ms >= 0",
        name="ck_x_post_claims_source_posted_at",
    ),
    CheckConstraint(
        "discovered_asset_count >= 0 AND discovered_asset_count <= 4",
        name="ck_x_post_claims_discovered_count",
    ),
    CheckConstraint(
        "success_count >= 0 AND success_count <= 4 "
        "AND failure_count >= 0 AND failure_count <= 4",
        name="ck_x_post_claims_outcome_counts",
    ),
    CheckConstraint(
        "success_count + failure_count <= discovered_asset_count",
        name="ck_x_post_claims_outcome_bounded",
    ),
    CheckConstraint(
        "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
        name="ck_x_post_claims_completed_at",
    ),
    CheckConstraint(
        "catalog_removed_at_ms IS NULL OR completed_at_ms IS NULL "
        "OR catalog_removed_at_ms >= completed_at_ms",
        name="ck_x_post_claims_removed_at",
    ),
    CheckConstraint(
        "(failure_stage IS NULL AND failure_code IS NULL) "
        "OR (failure_stage IS NOT NULL AND failure_code IS NOT NULL)",
        name="ck_x_post_claims_failure_pair",
    ),
    CheckConstraint(
        "failure_stage IS NULL OR failure_stage IN ("
        "'configuration', 'extraction', 'acquisition', 'staging', "
        "'handoff', 'downstream', 'cleanup', 'internal')",
        name="ck_x_post_claims_failure_stage",
    ),
    CheckConstraint(
        "cleanup_state IN ('pending', 'complete')",
        name="ck_x_post_claims_cleanup_state",
    ),
    CheckConstraint(
        "(cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
        "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)",
        name="ck_x_post_claims_cleanup_pair",
    ),
    CheckConstraint(
        "requested_content_category IS NULL OR "
        "requested_content_category IN ('general', 'meme', 'movie', 'youtube')",
        name="ck_x_post_claims_requested_content_category",
    ),
    Index(
        "uq_x_post_claims_active_requester",
        "x_post_id",
        "created_by_login_key",
        unique=True,
        sqlite_where=text(
            "state IN ('submitted', 'queued', 'extracting', 'acquiring', "
            "'handing_off') AND created_by_login_key IS NOT NULL"
        ),
    ),
    Index(
        "ix_x_post_claims_state",
        "state",
        "updated_at_ms",
        "id",
    ),
    Index("ix_x_post_claims_post_id", "x_post_id", "created_at_ms"),
    Index("ix_x_post_claims_retry_of", "retry_of_claim_id"),
    Index("ix_x_post_claims_resolved_claim", "resolved_claim_id"),
    Index("ix_x_post_claims_created_by_login_key", "created_by_login_key"),
    Index(
        "ix_x_post_claims_owner_updated",
        "created_by_login_key",
        "updated_at_ms",
        "id",
    ),
)

x_assets = Table(
    "x_assets",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column(
        "claim_id",
        Text(),
        ForeignKey("x_post_claims.id", ondelete="RESTRICT",
                   name="fk_x_assets_claim_id"),
        nullable=False,
    ),
    Column("ordinal", Integer(), nullable=False),
    Column("media_type", Text(), nullable=False),
    Column("expected_mime", Text(), nullable=False),
    Column("source_media_key", Text(), nullable=True),
    Column("width", Integer(), nullable=True),
    Column("height", Integer(), nullable=True),
    Column("duration_seconds", Integer(), nullable=True),
    Column("selected_variant", Text(), nullable=True),
    Column("state", Text(), nullable=False),
    Column("stage_key", Text(), nullable=False),
    Column("acquired_bytes", Integer(), nullable=True),
    Column("acquired_sha256", Text(), nullable=True),
    Column(
        "media_id",
        Text(),
        ForeignKey("logical_media.id", ondelete="RESTRICT",
                   name="fk_x_assets_media_id"),
        nullable=True,
    ),
    Column(
        "media_location_id",
        Text(),
        ForeignKey("physical_media_locations.id", ondelete="RESTRICT",
                   name="fk_x_assets_media_location_id"),
        nullable=True,
    ),
    Column("upload_asset_key", Text(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    Column("completed_at_ms", Integer(), nullable=True),
    Column("failure_stage", Text(), nullable=True),
    Column("failure_code", Text(), nullable=True),
    Column("cleanup_state", Text(), nullable=False),
    Column("cleanup_completed_at_ms", Integer(), nullable=True),
    Column("version", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_x_assets_id_length"),
    CheckConstraint("length(claim_id) = 36", name="ck_x_assets_claim_id_length"),
    CheckConstraint(
        "ordinal >= 0 AND ordinal <= 3",
        name="ck_x_assets_ordinal",
    ),
    CheckConstraint(
        "media_type IN ('video', 'animated_gif', 'image')",
        name="ck_x_assets_media_type",
    ),
    CheckConstraint(
        "length(expected_mime) >= 1 AND length(expected_mime) <= 120",
        name="ck_x_assets_mime_length",
    ),
    CheckConstraint(
        "width IS NULL OR width >= 0",
        name="ck_x_assets_width",
    ),
    CheckConstraint(
        "height IS NULL OR height >= 0",
        name="ck_x_assets_height",
    ),
    CheckConstraint(
        "duration_seconds IS NULL OR duration_seconds <= 300",
        name="ck_x_assets_duration",
    ),
    CheckConstraint(
        "state IN ('pending', 'extracted', 'acquiring', 'staged', "
        "'handing_off', 'cataloged', 'failed')",
        name="ck_x_assets_state",
    ),
    CheckConstraint(
        "length(stage_key) = 32 "
        "AND stage_key NOT GLOB '*[^0-9a-f]*'",
        name="ck_x_assets_stage_key",
    ),
    CheckConstraint(
        "acquired_bytes IS NULL OR acquired_bytes > 0",
        name="ck_x_assets_acquired_bytes",
    ),
    CheckConstraint(
        "acquired_sha256 IS NULL OR ("
        "length(acquired_sha256) = 64 "
        "AND acquired_sha256 = lower(acquired_sha256) "
        "AND acquired_sha256 NOT GLOB '*[^0-9a-f]*')",
        name="ck_x_assets_sha256",
    ),
    CheckConstraint(
        "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
        name="ck_x_assets_completed_at",
    ),
    CheckConstraint(
        "(failure_stage IS NULL AND failure_code IS NULL) "
        "OR (failure_stage IS NOT NULL AND failure_code IS NOT NULL)",
        name="ck_x_assets_failure_pair",
    ),
    CheckConstraint(
        "cleanup_state IN ('pending', 'complete')",
        name="ck_x_assets_cleanup_state",
    ),
    CheckConstraint(
        "(cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
        "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)",
        name="ck_x_assets_cleanup_pair",
    ),
    UniqueConstraint("stage_key", name="uq_x_assets_stage_key"),
    UniqueConstraint("claim_id", "ordinal", name="uq_x_assets_claim_ordinal"),
    Index("ix_x_assets_claim_id", "claim_id", "ordinal"),
    Index("ix_x_assets_state", "state", "updated_at_ms", "id"),
    Index("ix_x_assets_media", "media_id", "media_location_id"),
)

_LOGIN_KEY_SQL = (
    "length(login_key) >= 1 AND length(login_key) <= 254 "
    "AND login_key = lower(login_key) "
    "AND instr(login_key, ' ') = 0 "
    "AND instr(login_key, char(9)) = 0 "
    "AND instr(login_key, char(10)) = 0 "
    "AND instr(login_key, char(13)) = 0"
)

media_user_aliases = Table(
    "media_user_aliases",
    metadata,
    Column("media_id", Text(), nullable=False),
    Column("login_key", Text(), nullable=False),
    Column("display_title", Text(), nullable=True),
    Column("description", Text(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint("media_id", "login_key", name="pk_media_user_aliases"),
    ForeignKeyConstraint(
        ["media_id"],
        ["logical_media.id"],
        name="fk_media_user_aliases_media_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint("length(media_id) = 36", name="ck_media_user_aliases_media_id_length"),
    CheckConstraint(_LOGIN_KEY_SQL, name="ck_media_user_aliases_login_key"),
    CheckConstraint(
        "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
        name="ck_media_user_aliases_title_length",
    ),
    CheckConstraint(
        "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
        name="ck_media_user_aliases_description_length",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_media_user_aliases_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_media_user_aliases_updated_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= created_at_ms",
        name="ck_media_user_aliases_updated_not_before_created",
    ),
    Index("ix_media_user_aliases_login_key", "login_key", "updated_at_ms"),
)

media_user_alias_tags = Table(
    "media_user_alias_tags",
    metadata,
    Column("media_id", Text(), nullable=False),
    Column("login_key", Text(), nullable=False),
    Column("tag_key", Text(), nullable=False),
    Column("position", Integer(), nullable=False),
    PrimaryKeyConstraint(
        "media_id", "login_key", "tag_key", name="pk_media_user_alias_tags"
    ),
    ForeignKeyConstraint(
        ["media_id", "login_key"],
        ["media_user_aliases.media_id", "media_user_aliases.login_key"],
        name="fk_media_user_alias_tags_alias",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tag_key"],
        ["canonical_tags.key"],
        name="fk_media_user_alias_tags_tag_key",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "media_id",
        "login_key",
        "position",
        name="uq_media_user_alias_tags_position",
    ),
    CheckConstraint(
        "length(media_id) = 36", name="ck_media_user_alias_tags_media_id_length"
    ),
    CheckConstraint(_LOGIN_KEY_SQL, name="ck_media_user_alias_tags_login_key"),
    CheckConstraint(
        "length(tag_key) >= 1 AND length(tag_key) <= 64",
        name="ck_media_user_alias_tags_tag_key_length",
    ),
    CheckConstraint(
        "position >= 0 AND position < 32",
        name="ck_media_user_alias_tags_position_range",
    ),
)

x_claim_pending_aliases = Table(
    "x_claim_pending_aliases",
    metadata,
    Column("claim_id", Text(), nullable=False),
    Column("login_key", Text(), nullable=False),
    Column("display_title", Text(), nullable=True),
    Column("description", Text(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("updated_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint("claim_id", name="pk_x_claim_pending_aliases"),
    ForeignKeyConstraint(
        ["claim_id"],
        ["x_post_claims.id"],
        name="fk_x_claim_pending_aliases_claim_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "length(claim_id) = 36", name="ck_x_claim_pending_aliases_claim_id_length"
    ),
    CheckConstraint(_LOGIN_KEY_SQL, name="ck_x_claim_pending_aliases_login_key"),
    CheckConstraint(
        "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
        name="ck_x_claim_pending_aliases_title_length",
    ),
    CheckConstraint(
        "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
        name="ck_x_claim_pending_aliases_description_length",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_x_claim_pending_aliases_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= 0",
        name="ck_x_claim_pending_aliases_updated_at_ms_non_negative",
    ),
    CheckConstraint(
        "updated_at_ms >= created_at_ms",
        name="ck_x_claim_pending_aliases_updated_not_before_created",
    ),
)

x_claim_pending_alias_tags = Table(
    "x_claim_pending_alias_tags",
    metadata,
    Column("claim_id", Text(), nullable=False),
    Column("tag_key", Text(), nullable=False),
    Column("position", Integer(), nullable=False),
    PrimaryKeyConstraint(
        "claim_id", "tag_key", name="pk_x_claim_pending_alias_tags"
    ),
    ForeignKeyConstraint(
        ["claim_id"],
        ["x_claim_pending_aliases.claim_id"],
        name="fk_x_claim_pending_alias_tags_pending",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tag_key"],
        ["canonical_tags.key"],
        name="fk_x_claim_pending_alias_tags_tag_key",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "claim_id", "position", name="uq_x_claim_pending_alias_tags_position"
    ),
    CheckConstraint(
        "length(claim_id) = 36",
        name="ck_x_claim_pending_alias_tags_claim_id_length",
    ),
    CheckConstraint(
        "length(tag_key) >= 1 AND length(tag_key) <= 64",
        name="ck_x_claim_pending_alias_tags_tag_key_length",
    ),
    CheckConstraint(
        "position >= 0 AND position < 32",
        name="ck_x_claim_pending_alias_tags_position_range",
    ),
)

media_analysis_runs = Table(
    "media_analysis_runs",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="RESTRICT",
            name="fk_media_analysis_runs_media_id",
        ),
        nullable=False,
    ),
    Column(
        "media_location_id",
        Text(),
        ForeignKey(
            "physical_media_locations.id",
            ondelete="RESTRICT",
            name="fk_media_analysis_runs_media_location_id",
        ),
        nullable=False,
    ),
    Column("analysis_definition", Text(), nullable=False),
    Column("state", Text(), nullable=False),
    Column("attempt_count", Integer(), nullable=False),
    Column("provider_id", Text(), nullable=True),
    Column("model_id", Text(), nullable=True),
    Column("prompt_version", Text(), nullable=True),
    Column("result_schema_version", Text(), nullable=True),
    Column("result_json", Text(), nullable=True),
    Column("error_code", Text(), nullable=True),
    Column("error_message", Text(), nullable=True),
    Column("analysis_profile", Text(), nullable=True),
    Column("reasoning_enabled", Integer(), nullable=True),
    Column("derivative_strategy", Text(), nullable=True),
    Column("derivative_count", Integer(), nullable=True),
    Column("provider_submission_occurred", Integer(), nullable=True),
    Column(
        "supersedes_run_id",
        Text(),
        ForeignKey(
            "media_analysis_runs.id",
            ondelete="RESTRICT",
            name="fk_media_analysis_runs_supersedes_run_id",
        ),
        nullable=True,
    ),
    Column("created_at_ms", Integer(), nullable=False),
    Column("started_at_ms", Integer(), nullable=True),
    Column("completed_at_ms", Integer(), nullable=True),
    Column("version", Integer(), nullable=False),
    CheckConstraint("length(id) = 36", name="ck_media_analysis_runs_id_length"),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_media_analysis_runs_media_id_length",
    ),
    CheckConstraint(
        "length(media_location_id) = 36",
        name="ck_media_analysis_runs_media_location_id_length",
    ),
    CheckConstraint(
        "length(analysis_definition) >= 1 AND length(analysis_definition) <= 64",
        name="ck_media_analysis_runs_definition_length",
    ),
    CheckConstraint(
        "state IN ('pending', 'analyzing', 'analyzed', 'failed')",
        name="ck_media_analysis_runs_state",
    ),
    CheckConstraint(
        "attempt_count >= 0 AND attempt_count <= 100",
        name="ck_media_analysis_runs_attempt_count",
    ),
    CheckConstraint(
        "analysis_profile IS NULL OR analysis_profile IN "
        "('generic_media', 'movie_identification')",
        name="ck_media_analysis_runs_analysis_profile",
    ),
    CheckConstraint(
        "reasoning_enabled IS NULL OR reasoning_enabled IN (0, 1)",
        name="ck_media_analysis_runs_reasoning_enabled",
    ),
    CheckConstraint(
        "provider_submission_occurred IS NULL OR provider_submission_occurred IN (0, 1)",
        name="ck_media_analysis_runs_provider_submission",
    ),
    CheckConstraint(
        "derivative_count IS NULL OR (derivative_count >= 0 AND derivative_count <= 16)",
        name="ck_media_analysis_runs_derivative_count",
    ),
    CheckConstraint(
        "supersedes_run_id IS NULL OR ("
        "length(supersedes_run_id) = 36 AND supersedes_run_id != id"
        ")",
        name="ck_media_analysis_runs_supersedes_run_id",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_media_analysis_runs_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "started_at_ms IS NULL OR started_at_ms >= 0",
        name="ck_media_analysis_runs_started_at_ms_non_negative",
    ),
    CheckConstraint(
        "completed_at_ms IS NULL OR completed_at_ms >= 0",
        name="ck_media_analysis_runs_completed_at_ms_non_negative",
    ),
    CheckConstraint(
        "version >= 1",
        name="ck_media_analysis_runs_version_positive",
    ),
    CheckConstraint(
        "("
        "state = 'pending' AND started_at_ms IS NULL AND completed_at_ms IS NULL "
        "AND result_json IS NULL AND result_schema_version IS NULL "
        "AND error_code IS NULL AND error_message IS NULL"
        ") OR ("
        "state = 'analyzing' AND started_at_ms IS NOT NULL AND completed_at_ms IS NULL "
        "AND result_json IS NULL AND result_schema_version IS NULL "
        "AND error_code IS NULL AND error_message IS NULL "
        "AND attempt_count >= 1"
        ") OR ("
        "state = 'analyzed' AND started_at_ms IS NOT NULL AND completed_at_ms IS NOT NULL "
        "AND result_json IS NOT NULL AND result_schema_version IS NOT NULL "
        "AND error_code IS NULL AND error_message IS NULL "
        "AND provider_id IS NOT NULL AND model_id IS NOT NULL "
        "AND prompt_version IS NOT NULL AND attempt_count >= 1"
        ") OR ("
        "state = 'failed' AND started_at_ms IS NOT NULL AND completed_at_ms IS NOT NULL "
        "AND result_json IS NULL AND result_schema_version IS NULL "
        "AND error_code IS NOT NULL AND error_message IS NOT NULL "
        "AND attempt_count >= 1"
        ")",
        name="ck_media_analysis_runs_state_payload",
    ),
    Index(
        "ix_media_analysis_runs_unfinished",
        "state",
        "created_at_ms",
        "id",
    ),
    Index(
        "uq_media_analysis_runs_active_media_definition",
        "media_id",
        "analysis_definition",
        unique=True,
        sqlite_where=text("state IN ('pending', 'analyzing')"),
    ),
    Index(
        "ix_companion_review_successful_inbox",
        "analysis_definition",
        "state",
        "analysis_profile",
        "completed_at_ms",
        "id",
        "media_id",
    ),
    Index(
        "ix_companion_review_per_media_history",
        "media_id",
        "analysis_definition",
        "state",
        "analysis_profile",
        "completed_at_ms",
        "id",
    ),
)

_COMPANION_REVIEW_LOGIN_KEY_SQL = (
    "length({column}) >= 1 AND length({column}) <= 254 "
    "AND {column} = lower({column}) "
    "AND instr({column}, ' ') = 0 "
    "AND instr({column}, char(9)) = 0 "
    "AND instr({column}, char(10)) = 0 "
    "AND instr({column}, char(13)) = 0"
)

companion_review_open_states = Table(
    "companion_review_open_states",
    metadata,
    Column("actor_login_key", Text(), nullable=False),
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="CASCADE",
            name="fk_companion_review_open_states_media_id",
        ),
        nullable=False,
    ),
    Column(
        "opened_run_id",
        Text(),
        ForeignKey(
            "media_analysis_runs.id",
            ondelete="CASCADE",
            name="fk_companion_review_open_states_opened_run_id",
        ),
        nullable=False,
    ),
    Column("opened_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint(
        "actor_login_key",
        "media_id",
        name="pk_companion_review_open_states",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_companion_review_open_states_media_id_length",
    ),
    CheckConstraint(
        "length(opened_run_id) = 36",
        name="ck_companion_review_open_states_opened_run_id_length",
    ),
    CheckConstraint(
        _COMPANION_REVIEW_LOGIN_KEY_SQL.format(column="actor_login_key"),
        name="ck_companion_review_open_states_actor_login_key",
    ),
    CheckConstraint(
        "opened_at_ms >= 0",
        name="ck_companion_review_open_states_opened_at_ms_non_negative",
    ),
    Index(
        "ix_companion_review_open_states_opened_run_id",
        "opened_run_id",
    ),
)

companion_review_field_sources = Table(
    "companion_review_field_sources",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="CASCADE",
            name="fk_companion_review_field_sources_media_id",
        ),
        nullable=False,
    ),
    Column("field_name", Text(), nullable=False),
    Column(
        "analysis_run_id",
        Text(),
        ForeignKey(
            "media_analysis_runs.id",
            ondelete="CASCADE",
            name="fk_companion_review_field_sources_analysis_run_id",
        ),
        nullable=False,
    ),
    Column("applied_by_login_key", Text(), nullable=False),
    Column("applied_at_ms", Integer(), nullable=False),
    Column("value_digest", Text(), nullable=False),
    PrimaryKeyConstraint(
        "media_id",
        "field_name",
        name="pk_companion_review_field_sources",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_companion_review_field_sources_media_id_length",
    ),
    CheckConstraint(
        "length(analysis_run_id) = 36",
        name="ck_companion_review_field_sources_analysis_run_id_length",
    ),
    CheckConstraint(
        "field_name IN ('display_title', 'tags', 'description')",
        name="ck_companion_review_field_sources_field_name",
    ),
    CheckConstraint(
        _COMPANION_REVIEW_LOGIN_KEY_SQL.format(column="applied_by_login_key"),
        name="ck_companion_review_field_sources_applied_by_login_key",
    ),
    CheckConstraint(
        "applied_at_ms >= 0",
        name="ck_companion_review_field_sources_applied_at_ms_non_negative",
    ),
    CheckConstraint(
        "length(value_digest) = 64 "
        "AND value_digest = lower(value_digest) "
        "AND value_digest NOT GLOB '*[^0-9a-f]*'",
        name="ck_companion_review_field_sources_value_digest",
    ),
    Index(
        "ix_companion_review_field_sources_analysis_run_id",
        "analysis_run_id",
    ),
)

companion_review_tag_sources = Table(
    "companion_review_tag_sources",
    metadata,
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "media_metadata.media_id",
            ondelete="CASCADE",
            name="fk_companion_review_tag_sources_media_id",
        ),
        nullable=False,
    ),
    Column(
        "tag_key",
        Text(),
        ForeignKey(
            "canonical_tags.key",
            ondelete="RESTRICT",
            name="fk_companion_review_tag_sources_tag_key",
        ),
        nullable=False,
    ),
    Column(
        "analysis_run_id",
        Text(),
        ForeignKey(
            "media_analysis_runs.id",
            ondelete="CASCADE",
            name="fk_companion_review_tag_sources_analysis_run_id",
        ),
        nullable=False,
    ),
    Column("applied_by_login_key", Text(), nullable=False),
    Column("applied_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint(
        "media_id",
        "tag_key",
        name="pk_companion_review_tag_sources",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_companion_review_tag_sources_media_id_length",
    ),
    CheckConstraint(
        "length(tag_key) >= 1 AND length(tag_key) <= 64",
        name="ck_companion_review_tag_sources_tag_key_length",
    ),
    CheckConstraint(
        "tag_key = lower(tag_key)",
        name="ck_companion_review_tag_sources_tag_key_lowercase",
    ),
    CheckConstraint(
        "tag_key GLOB '[a-z]*' "
        "AND tag_key NOT GLOB '*[^a-z0-9-]*' "
        "AND tag_key NOT LIKE '%--%' "
        "AND substr(tag_key, length(tag_key), 1) != '-'",
        name="ck_companion_review_tag_sources_tag_key_slug",
    ),
    CheckConstraint(
        "length(analysis_run_id) = 36",
        name="ck_companion_review_tag_sources_analysis_run_id_length",
    ),
    CheckConstraint(
        _COMPANION_REVIEW_LOGIN_KEY_SQL.format(column="applied_by_login_key"),
        name="ck_companion_review_tag_sources_applied_by_login_key",
    ),
    CheckConstraint(
        "applied_at_ms >= 0",
        name="ck_companion_review_tag_sources_applied_at_ms_non_negative",
    ),
    Index(
        "ix_companion_review_tag_sources_analysis_run_id",
        "analysis_run_id",
    ),
)

security_audit_events = Table(
    "security_audit_events",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("occurred_at_ms", Integer(), nullable=False),
    Column("request_id", Text(), nullable=False),
    Column("actor_login", Text(), nullable=False),
    Column("actor_key", Text(), nullable=False),
    Column("identity_provenance", Text(), nullable=False),
    Column("role", Text(), nullable=False),
    Column("capability", Text(), nullable=False),
    Column("action", Text(), nullable=False),
    Column("target_type", Text(), nullable=False),
    Column("target_id", Text(), nullable=True),
    Column("outcome", Text(), nullable=False),
    Column("http_status", Integer(), nullable=True),
    CheckConstraint("length(id) = 36", name="ck_security_audit_events_id_length"),
    CheckConstraint(
        "occurred_at_ms >= 0",
        name="ck_security_audit_events_occurred_at_non_negative",
    ),
    CheckConstraint(
        "length(request_id) >= 1 AND length(request_id) <= 64",
        name="ck_security_audit_events_request_id_length",
    ),
    CheckConstraint(
        "length(actor_login) >= 1 AND length(actor_login) <= 254",
        name="ck_security_audit_events_actor_login_length",
    ),
    CheckConstraint(
        "length(actor_key) >= 1 AND length(actor_key) <= 254",
        name="ck_security_audit_events_actor_key_length",
    ),
    CheckConstraint(
        "length(identity_provenance) >= 1 AND length(identity_provenance) <= 32",
        name="ck_security_audit_events_provenance_length",
    ),
    CheckConstraint(
        "length(role) >= 1 AND length(role) <= 16",
        name="ck_security_audit_events_role_length",
    ),
    CheckConstraint(
        "length(capability) >= 1 AND length(capability) <= 64",
        name="ck_security_audit_events_capability_length",
    ),
    CheckConstraint(
        "length(action) >= 1 AND length(action) <= 64",
        name="ck_security_audit_events_action_length",
    ),
    CheckConstraint(
        "length(target_type) >= 1 AND length(target_type) <= 64",
        name="ck_security_audit_events_target_type_length",
    ),
    CheckConstraint(
        "target_id IS NULL OR "
        "(length(target_id) >= 1 AND length(target_id) <= 128)",
        name="ck_security_audit_events_target_id_length",
    ),
    CheckConstraint(
        "outcome IN ('allowed', 'denied')",
        name="ck_security_audit_events_outcome",
    ),
    CheckConstraint(
        "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
        name="ck_security_audit_events_http_status",
    ),
    Index("ix_security_audit_events_occurred_at", "occurred_at_ms", "id"),
    Index("ix_security_audit_events_actor_key", "actor_key", "occurred_at_ms"),
    Index("ix_security_audit_events_capability", "capability", "occurred_at_ms"),
)

media_catalog_removal_receipts = Table(
    "media_catalog_removal_receipts",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column("occurred_at_ms", Integer(), nullable=False),
    Column("request_id", Text(), nullable=False),
    Column("actor_key", Text(), nullable=False),
    Column("media_id", Text(), nullable=False),
    Column("display_title_snapshot", Text(), nullable=True),
    Column("acquisition_source", Text(), nullable=False),
    Column("storage_class", Text(), nullable=False),
    Column("was_published", Integer(), nullable=False),
    Column("published_at_ms", Integer(), nullable=True),
    Column("consequence_fingerprint", Text(), nullable=False),
    Column("catalog_outcome", Text(), nullable=False),
    Column("original_bytes_policy", Text(), nullable=False),
    Column("original_bytes_outcome", Text(), nullable=False),
    Column("youtube_claims_transitioned", Integer(), nullable=False),
    Column("upload_publications_detached", Integer(), nullable=False),
    Column("analysis_run_count", Integer(), nullable=False),
    Column("provider_submission_count", Integer(), nullable=False),
    Column("cover_artifact_digest", Text(), nullable=True),
    Column("preview_location_ids_json", Text(), nullable=True),
    Column("cover_cleanup_state", Text(), nullable=False),
    Column("preview_cleanup_state", Text(), nullable=False),
    Column("cleanup_updated_at_ms", Integer(), nullable=True),
    CheckConstraint("length(id) = 36", name="ck_catalog_removal_receipts_id_length"),
    CheckConstraint(
        "occurred_at_ms >= 0",
        name="ck_catalog_removal_receipts_occurred_non_negative",
    ),
    CheckConstraint(
        "length(request_id) >= 1 AND length(request_id) <= 64",
        name="ck_catalog_removal_receipts_request_id_length",
    ),
    CheckConstraint(
        "length(actor_key) >= 1 AND length(actor_key) <= 254",
        name="ck_catalog_removal_receipts_actor_key_length",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_catalog_removal_receipts_media_id_length",
    ),
    CheckConstraint(
        "display_title_snapshot IS NULL OR ("
        "length(display_title_snapshot) >= 1 "
        "AND length(display_title_snapshot) <= 240)",
        name="ck_catalog_removal_receipts_title_length",
    ),
    CheckConstraint(
        "acquisition_source IN ("
        "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim', "
        "'x_manual_claim')",
        name="ck_catalog_removal_receipts_acquisition_source",
    ),
    CheckConstraint(
        "storage_class IN ("
        "'operator_managed', 'server_managed_upload', 'unknown')",
        name="ck_catalog_removal_receipts_storage_class",
    ),
    CheckConstraint(
        "was_published IN (0, 1)",
        name="ck_catalog_removal_receipts_was_published",
    ),
    CheckConstraint(
        "published_at_ms IS NULL OR published_at_ms >= 0",
        name="ck_catalog_removal_receipts_published_at",
    ),
    CheckConstraint(
        "length(consequence_fingerprint) = 64 "
        "AND consequence_fingerprint = lower(consequence_fingerprint) "
        "AND consequence_fingerprint NOT GLOB '*[^0-9a-f]*'",
        name="ck_catalog_removal_receipts_fingerprint",
    ),
    CheckConstraint(
        "catalog_outcome = 'removed'",
        name="ck_catalog_removal_receipts_catalog_outcome",
    ),
    CheckConstraint(
        "original_bytes_policy = 'retain_all'",
        name="ck_catalog_removal_receipts_bytes_policy",
    ),
    CheckConstraint(
        "original_bytes_outcome IN ("
        "'retained_operator_managed', 'retained_server_managed', "
        "'retained_already_missing', 'retained_unknown')",
        name="ck_catalog_removal_receipts_bytes_outcome",
    ),
    CheckConstraint(
        "youtube_claims_transitioned >= 0",
        name="ck_catalog_removal_receipts_youtube_count",
    ),
    CheckConstraint(
        "upload_publications_detached >= 0",
        name="ck_catalog_removal_receipts_upload_count",
    ),
    CheckConstraint(
        "analysis_run_count >= 0",
        name="ck_catalog_removal_receipts_analysis_count",
    ),
    CheckConstraint(
        "provider_submission_count >= 0",
        name="ck_catalog_removal_receipts_provider_count",
    ),
    CheckConstraint(
        "cover_artifact_digest IS NULL OR ("
        "length(cover_artifact_digest) = 64 "
        "AND cover_artifact_digest = lower(cover_artifact_digest) "
        "AND cover_artifact_digest NOT GLOB '*[^0-9a-f]*')",
        name="ck_catalog_removal_receipts_cover_digest",
    ),
    CheckConstraint(
        "cover_cleanup_state IN ('none', 'pending', 'complete', 'failed')",
        name="ck_catalog_removal_receipts_cover_cleanup",
    ),
    CheckConstraint(
        "preview_cleanup_state IN ('none', 'pending', 'complete', 'failed')",
        name="ck_catalog_removal_receipts_preview_cleanup",
    ),
    CheckConstraint(
        "cleanup_updated_at_ms IS NULL OR cleanup_updated_at_ms >= occurred_at_ms",
        name="ck_catalog_removal_receipts_cleanup_updated",
    ),
    Index(
        "ix_catalog_removal_receipts_occurred",
        "occurred_at_ms",
        "id",
    ),
    Index(
        "ix_catalog_removal_receipts_media_id",
        "media_id",
        "occurred_at_ms",
    ),
)

media_analysis_proposals = Table(
    "media_analysis_proposals",
    metadata,
    Column("id", Text(), primary_key=True, nullable=False),
    Column(
        "media_id",
        Text(),
        ForeignKey(
            "logical_media.id",
            ondelete="CASCADE",
            name="fk_media_analysis_proposals_media_id",
        ),
        nullable=False,
    ),
    Column("proposed_by_login_key", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("status", Text(), nullable=False),
    CheckConstraint(
        "length(id) = 36",
        name="ck_media_analysis_proposals_id_length",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_media_analysis_proposals_media_id_length",
    ),
    CheckConstraint(
        _COMPANION_REVIEW_LOGIN_KEY_SQL.format(column="proposed_by_login_key"),
        name="ck_media_analysis_proposals_proposed_by_login_key",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_media_analysis_proposals_created_at_ms_non_negative",
    ),
    CheckConstraint(
        "status IN ('open', 'dismissed', 'completed')",
        name="ck_media_analysis_proposals_status",
    ),
    Index(
        "ix_media_analysis_proposals_created_at",
        "created_at_ms",
        "id",
    ),
    Index(
        "ix_media_analysis_proposals_status_created",
        "status",
        "created_at_ms",
        "id",
    ),
)

_KRONIKA_LOGIN_SQL = _COMPANION_REVIEW_LOGIN_KEY_SQL
_KRONIKA_NOT_UUID = (
    "NOT ({column} GLOB "
    "'[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]')"
)


def _kronika_bytes(column: str, low: int, high: int) -> str:
    return (
        f"length(CAST({column} AS BLOB)) >= {low} "
        f"AND length(CAST({column} AS BLOB)) <= {high}"
    )


kronika_documents = Table(
    "kronika_documents",
    metadata,
    Column("id", Text(), nullable=False),
    Column("operation_id", Text(), nullable=False),
    Column("kind", Text(), nullable=False),
    Column("question_text", Text(), nullable=False),
    Column("answer_text", Text(), nullable=False),
    Column("citations_json", Text(), nullable=False),
    Column("completion_evidence_json", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("completed_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint("id", name="pk_kronika_documents"),
    UniqueConstraint("operation_id", name="uq_kronika_documents_operation_id"),
    UniqueConstraint(
        "id",
        "operation_id",
        "kind",
        name="uq_kronika_documents_id_operation_kind",
    ),
    CheckConstraint("length(id) = 36", name="ck_kronika_documents_id_length"),
    CheckConstraint(
        f"{_kronika_bytes('operation_id', 1, 128)} AND {_KRONIKA_NOT_UUID.format(column='operation_id')}",
        name="ck_kronika_documents_operation_id",
    ),
    CheckConstraint("kind IN ('search', 'research')", name="ck_kronika_documents_kind"),
    CheckConstraint(
        f"{_kronika_bytes('question_text', 1, 16384)} AND length(trim(question_text)) > 0",
        name="ck_kronika_documents_question_text",
    ),
    CheckConstraint(
        f"{_kronika_bytes('answer_text', 1, 2097152)} AND length(trim(answer_text)) > 0",
        name="ck_kronika_documents_answer_text",
    ),
    CheckConstraint(
        "length(citations_json) >= 2",
        name="ck_kronika_documents_citations_json",
    ),
    CheckConstraint(
        "length(completion_evidence_json) >= 2",
        name="ck_kronika_documents_completion_evidence_json",
    ),
    CheckConstraint("created_at_ms >= 0", name="ck_kronika_documents_created_at_ms"),
    CheckConstraint(
        "completed_at_ms >= created_at_ms",
        name="ck_kronika_documents_completed_at_ms",
    ),
)

kronika_records = Table(
    "kronika_records",
    metadata,
    Column("id", Text(), nullable=False),
    Column("kind", Text(), nullable=False),
    Column("owner_login_key", Text(), nullable=False),
    Column("visibility", Text(), nullable=False, server_default="private"),
    Column("media_id", Text(), nullable=True),
    Column("document_id", Text(), nullable=True),
    Column("final_operation_id", Text(), nullable=True),
    Column("created_at_ms", Integer(), nullable=False),
    Column("completed_at_ms", Integer(), nullable=True),
    Column("timeline_entered_at_ms", Integer(), nullable=True),
    Column("version", Integer(), nullable=False, server_default="1"),
    Column("latest_successful_analysis_run_id", Text(), nullable=True),
    Column("approved_analysis_run_id", Text(), nullable=True),
    Column("approved_by_login_key", Text(), nullable=True),
    Column("approved_at_ms", Integer(), nullable=True),
    Column("approved_record_version", Integer(), nullable=True),
    Column("approved_projection_json", Text(), nullable=True),
    PrimaryKeyConstraint("id", name="pk_kronika_records"),
    UniqueConstraint("media_id", name="uq_kronika_records_media_id"),
    UniqueConstraint("document_id", name="uq_kronika_records_document_id"),
    UniqueConstraint(
        "final_operation_id", name="uq_kronika_records_final_operation_id"
    ),
    ForeignKeyConstraint(
        ["media_id"],
        ["logical_media.id"],
        name="fk_kronika_records_media_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["document_id", "final_operation_id", "kind"],
        [
            "kronika_documents.id",
            "kronika_documents.operation_id",
            "kronika_documents.kind",
        ],
        name="fk_kronika_records_document",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["latest_successful_analysis_run_id"],
        ["media_analysis_runs.id"],
        name="fk_kronika_records_latest_analysis_run_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["approved_analysis_run_id"],
        ["media_analysis_runs.id"],
        name="fk_kronika_records_approved_analysis_run_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint("length(id) = 36", name="ck_kronika_records_id_length"),
    CheckConstraint(
        "kind IN ('media', 'search', 'research')",
        name="ck_kronika_records_kind",
    ),
    CheckConstraint(
        _KRONIKA_LOGIN_SQL.format(column="owner_login_key"),
        name="ck_kronika_records_owner_login_key",
    ),
    CheckConstraint(
        "visibility IN ('private', 'family')",
        name="ck_kronika_records_visibility",
    ),
    CheckConstraint(
        "("
        "kind = 'media' AND media_id IS NOT NULL AND length(media_id) = 36 "
        "AND document_id IS NULL AND final_operation_id IS NULL"
        ") OR ("
        "kind IN ('search', 'research') AND media_id IS NULL "
        "AND document_id IS NOT NULL AND length(document_id) = 36 "
        "AND final_operation_id IS NOT NULL "
        f"AND {_kronika_bytes('final_operation_id', 1, 128)} "
        f"AND {_KRONIKA_NOT_UUID.format(column='final_operation_id')} "
        "AND completed_at_ms IS NOT NULL "
        "AND latest_successful_analysis_run_id IS NULL "
        "AND approved_analysis_run_id IS NULL"
        ")",
        name="ck_kronika_records_shape",
    ),
    CheckConstraint("created_at_ms >= 0", name="ck_kronika_records_created_at_ms"),
    CheckConstraint(
        "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
        name="ck_kronika_records_completed_at_ms",
    ),
    CheckConstraint(
        "timeline_entered_at_ms IS NULL OR ("
        "completed_at_ms IS NOT NULL AND timeline_entered_at_ms >= completed_at_ms"
        ")",
        name="ck_kronika_records_timeline_entered_at_ms",
    ),
    CheckConstraint("version >= 1", name="ck_kronika_records_version"),
    CheckConstraint(
        "latest_successful_analysis_run_id IS NULL "
        "OR length(latest_successful_analysis_run_id) = 36",
        name="ck_kronika_records_latest_analysis_length",
    ),
    CheckConstraint(
        "approved_analysis_run_id IS NULL OR length(approved_analysis_run_id) = 36",
        name="ck_kronika_records_approved_analysis_length",
    ),
    CheckConstraint(
        "("
        "approved_by_login_key IS NULL AND approved_at_ms IS NULL "
        "AND approved_record_version IS NULL AND approved_projection_json IS NULL"
        ") OR ("
        "approved_by_login_key IS NOT NULL "
        f"AND {_KRONIKA_LOGIN_SQL.format(column='approved_by_login_key')} "
        "AND approved_at_ms IS NOT NULL AND approved_record_version IS NOT NULL "
        "AND approved_projection_json IS NOT NULL "
        "AND completed_at_ms IS NOT NULL AND timeline_entered_at_ms IS NOT NULL "
        "AND approved_at_ms >= completed_at_ms "
        "AND approved_at_ms >= timeline_entered_at_ms "
        "AND approved_record_version >= 1 AND approved_record_version <= version "
        "AND ("
        "(kind = 'media' AND approved_analysis_run_id IS NOT NULL) "
        "OR (kind IN ('search', 'research') AND approved_analysis_run_id IS NULL)"
        ")"
        ")",
        name="ck_kronika_records_approval",
    ),
    CheckConstraint(
        "visibility = 'private' OR ("
        "visibility = 'family' AND approved_by_login_key IS NOT NULL"
        ")",
        name="ck_kronika_records_family_requires_approval",
    ),
)

Index(
    "ix_kronika_records_owner_history",
    kronika_records.c.owner_login_key,
    kronika_records.c.created_at_ms.desc(),
    kronika_records.c.id.asc(),
)
Index(
    "ix_kronika_records_admin_history",
    kronika_records.c.created_at_ms.desc(),
    kronika_records.c.id.asc(),
)
Index(
    "ix_kronika_records_timeline",
    kronika_records.c.visibility,
    kronika_records.c.timeline_entered_at_ms.desc(),
    kronika_records.c.id.asc(),
)
Index(
    "ix_kronika_records_latest_analysis_run_id",
    kronika_records.c.latest_successful_analysis_run_id,
)
Index(
    "ix_kronika_records_approved_analysis_run_id",
    kronika_records.c.approved_analysis_run_id,
)

kronika_approved_media = Table(
    "kronika_approved_media",
    metadata,
    Column("record_id", Text(), nullable=False),
    Column("media_id", Text(), nullable=False),
    Column("approval_version", Integer(), nullable=False),
    Column("content_category", Text(), nullable=False),
    Column("acquisition_source", Text(), nullable=False),
    Column("display_title", Text(), nullable=False),
    Column("description", Text(), nullable=False),
    Column("creator_attribution_kind", Text(), nullable=True),
    Column("creator_stable_id", Text(), nullable=True),
    Column("creator_handle", Text(), nullable=True),
    Column("creator_display_name", Text(), nullable=True),
    Column("cover_artifact_digest", Text(), nullable=True),
    PrimaryKeyConstraint("record_id", name="pk_kronika_approved_media"),
    UniqueConstraint("media_id", name="uq_kronika_approved_media_media_id"),
    ForeignKeyConstraint(
        ["record_id"],
        ["kronika_records.id"],
        name="fk_kronika_approved_media_record_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["media_id"],
        ["logical_media.id"],
        name="fk_kronika_approved_media_media_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "length(record_id) = 36",
        name="ck_kronika_approved_media_record_id_length",
    ),
    CheckConstraint(
        "length(media_id) = 36",
        name="ck_kronika_approved_media_media_id_length",
    ),
    CheckConstraint(
        "approval_version >= 1",
        name="ck_kronika_approved_media_approval_version",
    ),
    CheckConstraint(
        "content_category IN ('general', 'meme', 'movie', 'youtube')",
        name="ck_kronika_approved_media_content_category",
    ),
    CheckConstraint(
        "length(display_title) >= 1 AND length(display_title) <= 240",
        name="ck_kronika_approved_media_title",
    ),
    CheckConstraint(
        "length(description) >= 1 AND length(description) <= 10000",
        name="ck_kronika_approved_media_description",
    ),
    CheckConstraint(
        "cover_artifact_digest IS NULL OR ("
        "length(cover_artifact_digest) = 64 "
        "AND cover_artifact_digest = lower(cover_artifact_digest) "
        "AND cover_artifact_digest NOT GLOB '*[^0-9a-f]*')",
        name="ck_kronika_approved_media_cover_digest",
    ),
)

kronika_approved_media_tags = Table(
    "kronika_approved_media_tags",
    metadata,
    Column("record_id", Text(), nullable=False),
    Column("tag_key", Text(), nullable=False),
    Column("display_name", Text(), nullable=False),
    Column("position", Integer(), nullable=False),
    PrimaryKeyConstraint("record_id", "tag_key", name="pk_kronika_approved_media_tags"),
    ForeignKeyConstraint(
        ["record_id"],
        ["kronika_records.id"],
        name="fk_kronika_approved_media_tags_record_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "length(record_id) = 36",
        name="ck_kronika_approved_media_tags_record_id",
    ),
    CheckConstraint(
        "length(tag_key) >= 1 AND length(tag_key) <= 64",
        name="ck_kronika_approved_media_tags_key",
    ),
    CheckConstraint(
        "length(display_name) >= 1 AND length(display_name) <= 80",
        name="ck_kronika_approved_media_tags_display_name",
    ),
    CheckConstraint(
        "position >= 0 AND position < 32",
        name="ck_kronika_approved_media_tags_position",
    ),
    Index(
        "ix_kronika_approved_media_tags_record_position",
        "record_id",
        "position",
    ),
)

kronika_approved_media_locations = Table(
    "kronika_approved_media_locations",
    metadata,
    Column("record_id", Text(), nullable=False),
    Column("location_id", Text(), nullable=False),
    Column("library_id", Text(), nullable=False),
    Column("relative_path", Text(), nullable=False),
    Column("availability", Text(), nullable=False),
    Column("observed_size_bytes", Integer(), nullable=True),
    Column("observed_mtime_ns", Integer(), nullable=True),
    PrimaryKeyConstraint(
        "record_id",
        "location_id",
        name="pk_kronika_approved_media_locations",
    ),
    ForeignKeyConstraint(
        ["record_id"],
        ["kronika_records.id"],
        name="fk_kronika_approved_media_locations_record_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["location_id"],
        ["physical_media_locations.id"],
        name="fk_kronika_approved_media_locations_location_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "length(record_id) = 36 AND length(location_id) = 36 AND length(library_id) = 36",
        name="ck_kronika_approved_media_locations_ids",
    ),
    CheckConstraint(
        "length(relative_path) >= 1 AND length(relative_path) <= 4096",
        name="ck_kronika_approved_media_locations_path",
    ),
    CheckConstraint(
        "availability IN ('available', 'offline', 'missing', 'unverified', 'archived')",
        name="ck_kronika_approved_media_locations_availability",
    ),
)

_RESEARCH_LIFECYCLE_STATES = (
    "'admitted', 'submitting', 'running', 'validating', 'saved', "
    "'refused', 'failed', 'incomplete', 'cancel_requested', 'cancelled', "
    "'timeout', 'submission_unknown'"
)
_RESEARCH_CLEANUP_STATES = "'not_required', 'pending', 'deleted', 'failed', 'unknown'"
_RESEARCH_ACCOUNTING_STATES = "'reserved', 'reconciled', 'unknown'"
_RESEARCH_LOWER_HEX_64 = (
    "length({column}) = 64 "
    "AND {column} = lower({column}) "
    "AND {column} NOT GLOB '*[^0-9a-f]*'"
)

research_requests = Table(
    "research_requests",
    metadata,
    Column("operation_id", Text(), nullable=False),
    Column("owner_login_key", Text(), nullable=False),
    Column("client_request_id", Text(), nullable=False),
    Column("request_fingerprint", Text(), nullable=False),
    Column("kind", Text(), nullable=False),
    Column("prompt_text", Text(), nullable=False),
    Column("prompt_utf8_bytes", Integer(), nullable=False),
    Column("lifecycle_state", Text(), nullable=False),
    Column("provider_id", Text(), nullable=False),
    Column("model_id", Text(), nullable=False),
    Column("configuration_version", Text(), nullable=False),
    Column("reasoning_effort", Text(), nullable=False),
    Column("max_tool_calls", Integer(), nullable=False),
    Column("max_output_tokens", Integer(), nullable=False),
    Column("deadline_seconds", Integer(), nullable=False),
    Column("reservation_micro_usd", Integer(), nullable=False),
    Column("tool_allowlist_json", Text(), nullable=False),
    Column("background", Integer(), nullable=False),
    Column("prompt_max_utf8_bytes", Integer(), nullable=False),
    Column("answer_max_utf8_bytes", Integer(), nullable=False),
    Column("citation_count_max", Integer(), nullable=False),
    Column("remote_handle_json", Text(), nullable=True),
    Column("checkpoint_json", Text(), nullable=True),
    Column("checkpoint_sha256", Text(), nullable=True),
    Column("record_id", Text(), nullable=True),
    Column("error_code", Text(), nullable=True),
    Column("cancel_requested_at_ms", Integer(), nullable=True),
    Column("cancellation_confirmed_at_ms", Integer(), nullable=True),
    Column("cleanup_state", Text(), nullable=False),
    Column("accounting_state", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("admitted_at_ms", Integer(), nullable=False),
    Column("submitted_at_ms", Integer(), nullable=True),
    Column("finished_at_ms", Integer(), nullable=True),
    Column("updated_at_ms", Integer(), nullable=False),
    PrimaryKeyConstraint("operation_id", name="pk_research_requests"),
    UniqueConstraint("record_id", name="uq_research_requests_record_id"),
    UniqueConstraint(
        "owner_login_key",
        "client_request_id",
        name="uq_research_requests_client",
    ),
    ForeignKeyConstraint(
        ["record_id"],
        ["kronika_records.id"],
        name="fk_research_requests_record_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        f"{_kronika_bytes('operation_id', 1, 128)} AND {_KRONIKA_NOT_UUID.format(column='operation_id')}",
        name="ck_research_requests_operation_id",
    ),
    CheckConstraint(
        _KRONIKA_LOGIN_SQL.format(column="owner_login_key"),
        name="ck_research_requests_owner_login_key",
    ),
    CheckConstraint(
        _kronika_bytes("client_request_id", 1, 128),
        name="ck_research_requests_client_request_id",
    ),
    CheckConstraint(
        _RESEARCH_LOWER_HEX_64.format(column="request_fingerprint"),
        name="ck_research_requests_fingerprint",
    ),
    CheckConstraint(
        "kind IN ('search', 'research')",
        name="ck_research_requests_kind",
    ),
    CheckConstraint(
        f"{_kronika_bytes('prompt_text', 1, 16384)} AND length(trim(prompt_text)) > 0",
        name="ck_research_requests_prompt_text",
    ),
    CheckConstraint(
        "prompt_utf8_bytes >= 1 AND prompt_utf8_bytes <= 16384 "
        "AND prompt_utf8_bytes = length(CAST(prompt_text AS BLOB))",
        name="ck_research_requests_prompt_bytes",
    ),
    CheckConstraint(
        f"lifecycle_state IN ({_RESEARCH_LIFECYCLE_STATES})",
        name="ck_research_requests_lifecycle_state",
    ),
    CheckConstraint(
        _kronika_bytes("provider_id", 1, 64),
        name="ck_research_requests_provider_id",
    ),
    CheckConstraint(
        _kronika_bytes("model_id", 1, 128),
        name="ck_research_requests_model_id",
    ),
    CheckConstraint(
        _kronika_bytes("configuration_version", 1, 16),
        name="ck_research_requests_configuration_version",
    ),
    CheckConstraint(
        "reasoning_effort IN ('low', 'high')",
        name="ck_research_requests_reasoning_effort",
    ),
    CheckConstraint(
        "max_tool_calls >= 1 AND max_tool_calls <= 20",
        name="ck_research_requests_max_tool_calls",
    ),
    CheckConstraint(
        "max_output_tokens >= 1 AND max_output_tokens <= 32768",
        name="ck_research_requests_max_output_tokens",
    ),
    CheckConstraint(
        "deadline_seconds >= 1 AND deadline_seconds <= 1800",
        name="ck_research_requests_deadline_seconds",
    ),
    CheckConstraint(
        "reservation_micro_usd >= 1 AND reservation_micro_usd <= 5000000",
        name="ck_research_requests_reservation",
    ),
    CheckConstraint(
        "tool_allowlist_json IN ('[\"web_search\"]')",
        name="ck_research_requests_tool_allowlist",
    ),
    CheckConstraint(
        "background IN (0, 1)",
        name="ck_research_requests_background",
    ),
    CheckConstraint(
        "prompt_max_utf8_bytes >= 1 AND prompt_max_utf8_bytes <= 16384",
        name="ck_research_requests_prompt_max",
    ),
    CheckConstraint(
        "answer_max_utf8_bytes >= 1 AND answer_max_utf8_bytes <= 2097152",
        name="ck_research_requests_answer_max",
    ),
    CheckConstraint(
        "citation_count_max >= 1 AND citation_count_max <= 200",
        name="ck_research_requests_citation_max",
    ),
    CheckConstraint(
        "remote_handle_json IS NULL OR "
        "(length(remote_handle_json) >= 2 AND length(remote_handle_json) <= 65536)",
        name="ck_research_requests_remote_handle",
    ),
    CheckConstraint(
        "checkpoint_json IS NULL OR "
        "(length(checkpoint_json) >= 2 AND length(checkpoint_json) <= 2097152)",
        name="ck_research_requests_checkpoint_json",
    ),
    CheckConstraint(
        "checkpoint_sha256 IS NULL OR ("
        f"{_RESEARCH_LOWER_HEX_64.format(column='checkpoint_sha256')})",
        name="ck_research_requests_checkpoint_sha256",
    ),
    CheckConstraint(
        "record_id IS NULL OR length(record_id) = 36",
        name="ck_research_requests_record_id",
    ),
    CheckConstraint(
        f"error_code IS NULL OR {_kronika_bytes('error_code', 1, 64)}",
        name="ck_research_requests_error_code",
    ),
    CheckConstraint(
        "cancel_requested_at_ms IS NULL OR cancel_requested_at_ms >= 0",
        name="ck_research_requests_cancel_requested_at_ms",
    ),
    CheckConstraint(
        "cancellation_confirmed_at_ms IS NULL OR ("
        "cancel_requested_at_ms IS NOT NULL "
        "AND cancellation_confirmed_at_ms >= cancel_requested_at_ms)",
        name="ck_research_requests_cancellation_confirmed_at_ms",
    ),
    CheckConstraint(
        f"cleanup_state IN ({_RESEARCH_CLEANUP_STATES})",
        name="ck_research_requests_cleanup_state",
    ),
    CheckConstraint(
        f"accounting_state IN ({_RESEARCH_ACCOUNTING_STATES})",
        name="ck_research_requests_accounting_state",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_research_requests_created_at_ms",
    ),
    CheckConstraint(
        "admitted_at_ms >= created_at_ms",
        name="ck_research_requests_admitted_at_ms",
    ),
    CheckConstraint(
        "submitted_at_ms IS NULL OR submitted_at_ms >= admitted_at_ms",
        name="ck_research_requests_submitted_at_ms",
    ),
    CheckConstraint(
        "finished_at_ms IS NULL OR finished_at_ms >= admitted_at_ms",
        name="ck_research_requests_finished_at_ms",
    ),
    CheckConstraint(
        "updated_at_ms >= created_at_ms",
        name="ck_research_requests_updated_at_ms",
    ),
)
Index(
    "ix_research_requests_state",
    research_requests.c.lifecycle_state,
    research_requests.c.updated_at_ms,
)
Index(
    "ix_research_requests_owner_history",
    research_requests.c.owner_login_key,
    research_requests.c.created_at_ms.desc(),
    research_requests.c.operation_id.asc(),
)

research_active_slot = Table(
    "research_active_slot",
    metadata,
    Column("id", Integer(), nullable=False),
    Column("operation_id", Text(), nullable=True),
    Column("held_since_ms", Integer(), nullable=True),
    PrimaryKeyConstraint("id", name="pk_research_active_slot"),
    ForeignKeyConstraint(
        ["operation_id"],
        ["research_requests.operation_id"],
        name="fk_research_active_slot_operation_id",
        ondelete="RESTRICT",
    ),
    CheckConstraint("id = 1", name="ck_research_active_slot_single_row"),
    CheckConstraint(
        "(operation_id IS NULL AND held_since_ms IS NULL) OR ("
        "operation_id IS NOT NULL AND held_since_ms IS NOT NULL "
        "AND held_since_ms >= 0)",
        name="ck_research_active_slot_shape",
    ),
)

research_operations = Table(
    "research_operations",
    metadata,
    Column("operation_row_id", Text(), nullable=False),
    Column("request_id", Text(), nullable=False),
    Column("parent_operation_id", Text(), nullable=True),
    Column("purpose", Text(), nullable=False),
    Column("operation", Text(), nullable=False),
    Column("attempt_number", Integer(), nullable=False),
    Column("started_at_ms", Integer(), nullable=False),
    Column("finished_at_ms", Integer(), nullable=True),
    Column("terminal_classification", Text(), nullable=True),
    Column("remote_handle_reference", Text(), nullable=True),
    Column("transport_outcome", Text(), nullable=True),
    Column("reserved_usd_micros", Integer(), nullable=True),
    Column("input_tokens", Integer(), nullable=True),
    Column("cached_input_tokens", Integer(), nullable=True),
    Column("output_tokens", Integer(), nullable=True),
    Column("reasoning_tokens_as_subset", Integer(), nullable=True),
    Column("web_tool_calls", Integer(), nullable=True),
    Column("calculated_cost_usd_micros", Integer(), nullable=True),
    Column("accounting_state", Text(), nullable=False),
    Column("cleanup_state", Text(), nullable=True),
    Column("cancellation_state", Text(), nullable=True),
    PrimaryKeyConstraint("operation_row_id", name="pk_research_operations"),
    ForeignKeyConstraint(
        ["request_id"],
        ["research_requests.operation_id"],
        name="fk_research_operations_request_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["parent_operation_id"],
        ["research_operations.operation_row_id"],
        name="fk_research_operations_parent",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "length(operation_row_id) = 36",
        name="ck_research_operations_row_id",
    ),
    CheckConstraint(
        "parent_operation_id IS NULL OR length(parent_operation_id) = 36",
        name="ck_research_operations_parent_id",
    ),
    CheckConstraint(
        "purpose IN ('search', 'research', 'synthetic_acceptance')",
        name="ck_research_operations_purpose",
    ),
    CheckConstraint(
        "operation IN ('create', 'poll', 'cancel', 'delete')",
        name="ck_research_operations_operation",
    ),
    CheckConstraint(
        "attempt_number >= 1",
        name="ck_research_operations_attempt_number",
    ),
    CheckConstraint(
        "started_at_ms >= 0",
        name="ck_research_operations_started_at_ms",
    ),
    CheckConstraint(
        "finished_at_ms IS NULL OR finished_at_ms >= started_at_ms",
        name="ck_research_operations_finished_at_ms",
    ),
    CheckConstraint(
        f"terminal_classification IS NULL OR "
        f"{_kronika_bytes('terminal_classification', 1, 64)}",
        name="ck_research_operations_terminal_classification",
    ),
    CheckConstraint(
        f"remote_handle_reference IS NULL OR "
        f"{_kronika_bytes('remote_handle_reference', 1, 256)}",
        name="ck_research_operations_remote_handle_reference",
    ),
    CheckConstraint(
        f"transport_outcome IS NULL OR {_kronika_bytes('transport_outcome', 1, 64)}",
        name="ck_research_operations_transport_outcome",
    ),
    CheckConstraint(
        "reserved_usd_micros IS NULL OR "
        "(reserved_usd_micros >= 0 AND reserved_usd_micros <= 5000000)",
        name="ck_research_operations_reserved",
    ),
    CheckConstraint(
        "input_tokens IS NULL OR input_tokens >= 0",
        name="ck_research_operations_input_tokens",
    ),
    CheckConstraint(
        "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
        name="ck_research_operations_cached_input_tokens",
    ),
    CheckConstraint(
        "output_tokens IS NULL OR output_tokens >= 0",
        name="ck_research_operations_output_tokens",
    ),
    CheckConstraint(
        "reasoning_tokens_as_subset IS NULL OR reasoning_tokens_as_subset >= 0",
        name="ck_research_operations_reasoning_tokens",
    ),
    CheckConstraint(
        "web_tool_calls IS NULL OR web_tool_calls >= 0",
        name="ck_research_operations_web_tool_calls",
    ),
    CheckConstraint(
        "calculated_cost_usd_micros IS NULL OR calculated_cost_usd_micros >= 0",
        name="ck_research_operations_calculated_cost",
    ),
    CheckConstraint(
        "cached_input_tokens IS NULL OR input_tokens IS NULL "
        "OR cached_input_tokens <= input_tokens",
        name="ck_research_operations_cached_subset",
    ),
    CheckConstraint(
        "reasoning_tokens_as_subset IS NULL OR ("
        "output_tokens IS NOT NULL "
        "AND reasoning_tokens_as_subset <= output_tokens)",
        name="ck_research_operations_reasoning_subset",
    ),
    CheckConstraint(
        f"accounting_state IN ({_RESEARCH_ACCOUNTING_STATES})",
        name="ck_research_operations_accounting_state",
    ),
    CheckConstraint(
        f"cleanup_state IS NULL OR {_kronika_bytes('cleanup_state', 1, 32)}",
        name="ck_research_operations_cleanup_state",
    ),
    CheckConstraint(
        f"cancellation_state IS NULL OR {_kronika_bytes('cancellation_state', 1, 32)}",
        name="ck_research_operations_cancellation_state",
    ),
)
Index(
    "ix_research_operations_request",
    research_operations.c.request_id,
    research_operations.c.started_at_ms,
)

research_budget_holds = Table(
    "research_budget_holds",
    metadata,
    Column("operation_id", Text(), nullable=False),
    Column("day_key", Text(), nullable=False),
    Column("month_key", Text(), nullable=False),
    Column("reserved_usd_micros", Integer(), nullable=False),
    Column("accounted_usd_micros", Integer(), nullable=True),
    Column("state", Text(), nullable=False),
    Column("created_at_ms", Integer(), nullable=False),
    Column("reconciled_at_ms", Integer(), nullable=True),
    PrimaryKeyConstraint("operation_id", name="pk_research_budget_holds"),
    ForeignKeyConstraint(
        ["operation_id"],
        ["research_requests.operation_id"],
        name="fk_research_budget_holds_request",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "day_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'",
        name="ck_research_budget_holds_day_key",
    ),
    CheckConstraint(
        "month_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'",
        name="ck_research_budget_holds_month_key",
    ),
    CheckConstraint(
        "reserved_usd_micros >= 1 AND reserved_usd_micros <= 5000000",
        name="ck_research_budget_holds_reserved",
    ),
    CheckConstraint(
        "accounted_usd_micros IS NULL OR accounted_usd_micros >= 0",
        name="ck_research_budget_holds_accounted",
    ),
    CheckConstraint(
        f"state IN ({_RESEARCH_ACCOUNTING_STATES})",
        name="ck_research_budget_holds_state",
    ),
    CheckConstraint(
        "(state = 'reserved' AND accounted_usd_micros IS NULL "
        "AND reconciled_at_ms IS NULL) OR ("
        "state IN ('reconciled', 'unknown') "
        "AND accounted_usd_micros IS NOT NULL AND reconciled_at_ms IS NOT NULL)",
        name="ck_research_budget_holds_shape",
    ),
    CheckConstraint(
        "created_at_ms >= 0",
        name="ck_research_budget_holds_created_at_ms",
    ),
    CheckConstraint(
        "reconciled_at_ms IS NULL OR reconciled_at_ms >= created_at_ms",
        name="ck_research_budget_holds_reconciled_at_ms",
    ),
)
Index(
    "ix_research_budget_holds_day",
    research_budget_holds.c.day_key,
    research_budget_holds.c.state,
)
Index(
    "ix_research_budget_holds_month",
    research_budget_holds.c.month_key,
    research_budget_holds.c.state,
)
