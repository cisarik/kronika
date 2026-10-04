"""First-class content classification, acquisition source, and movie genres.

Technical media kind remains independent in ``domain.media.MediaKind``.
"""

from __future__ import annotations

from enum import StrEnum


class ContentCategory(StrEnum):
    """Canonical user-editable content category.

    Orthogonal to technical media kind and acquisition source.
    """

    GENERAL = "general"
    MEME = "meme"
    MOVIE = "movie"
    YOUTUBE = "youtube"


class AcquisitionSource(StrEnum):
    """How the media entered the catalog, orthogonal to content category.

    ``youtube_manual_claim`` is durable catalog provenance set at acquisition
    or upload creation. Ordinary metadata Save must not change it
    (ADR-0055). Immutable source evidence for controlled ingestion also lives
    in the durable acquisition claim defined by ADR-0046.
    """

    UNKNOWN = "unknown"
    MANUAL_UPLOAD = "manual_upload"
    LIBRARY_SCAN = "library_scan"
    YOUTUBE_MANUAL_CLAIM = "youtube_manual_claim"
    X_MANUAL_CLAIM = "x_manual_claim"


class CreatorAttributionKind(StrEnum):
    """Platform-scoped creator attribution kind.

    Absence of attribution is represented by NULL, never a stored ``none``.
    ``x_author`` is schema-forward-compatible; X acquisition is not implemented.
    """

    YOUTUBE_CHANNEL = "youtube_channel"
    X_AUTHOR = "x_author"


CONTENT_CATEGORY_DISPLAY_NAMES: dict[ContentCategory, str] = {
    ContentCategory.GENERAL: "General",
    ContentCategory.MEME: "Meme",
    ContentCategory.MOVIE: "Movie",
    ContentCategory.YOUTUBE: "YouTube",
}


class MovieGenre(StrEnum):
    """Bounded canonical movie genres, separate from ordinary tags."""

    DRAMA = "drama"
    COMEDY = "comedy"
    SCI_FI = "sci-fi"
    THRILLER = "thriller"
    HORROR = "horror"
    ACTION = "action"
    ADVENTURE = "adventure"
    DOCUMENTARY = "documentary"
    ANIMATION = "animation"
    FAMILY = "family"
    ROMANCE = "romance"
    CRIME = "crime"
    FANTASY = "fantasy"
    MYSTERY = "mystery"


MOVIE_GENRE_DISPLAY_NAMES: dict[MovieGenre, str] = {
    MovieGenre.DRAMA: "Drama",
    MovieGenre.COMEDY: "Comedy",
    MovieGenre.SCI_FI: "Sci-Fi",
    MovieGenre.THRILLER: "Thriller",
    MovieGenre.HORROR: "Horror",
    MovieGenre.ACTION: "Action",
    MovieGenre.ADVENTURE: "Adventure",
    MovieGenre.DOCUMENTARY: "Documentary",
    MovieGenre.ANIMATION: "Animation",
    MovieGenre.FAMILY: "Family",
    MovieGenre.ROMANCE: "Romance",
    MovieGenre.CRIME: "Crime",
    MovieGenre.FANTASY: "Fantasy",
    MovieGenre.MYSTERY: "Mystery",
}

DEFAULT_CONTENT_CATEGORY = ContentCategory.GENERAL
DEFAULT_ACQUISITION_SOURCE = AcquisitionSource.UNKNOWN
MAX_MEDIA_GENRES = 8


class AnalysisProfile(StrEnum):
    """Explicit analysis profile controlling derivative and reasoning policy."""

    GENERIC_MEDIA = "generic_media"
    MOVIE_IDENTIFICATION = "movie_identification"


GENERIC_MEDIA_ANALYSIS_DEFINITION = "automatic_post_catalog"
MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION = "movie_identification"

ANALYSIS_PROFILE_BY_DEFINITION: dict[str, AnalysisProfile] = {
    GENERIC_MEDIA_ANALYSIS_DEFINITION: AnalysisProfile.GENERIC_MEDIA,
    MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION: AnalysisProfile.MOVIE_IDENTIFICATION,
}

MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION = "framenest-movie-identification-result-v1"
MOVIE_IDENTIFICATION_PROMPT_VERSION = "framenest-movie-identification-prompt-v2"

# Bounded reasoning budget for movie identification on integrate.api.nvidia.com.
# Official Thinking-mode sampling uses temperature/top_p (no instruct-mode top_k).
# Canonical budget control is top-level reasoning_budget; max_tokens must leave
# room for the final structured answer after the reasoning phase.
MOVIE_IDENTIFICATION_REASONING_BUDGET = 2048
MOVIE_IDENTIFICATION_MAX_TOKENS = 4096
MOVIE_IDENTIFICATION_TEMPERATURE = 0.6
MOVIE_IDENTIFICATION_TOP_P = 0.95

CONTACT_SHEET_REQUESTED_FRAME_COUNT = 5
CONTACT_SHEET_DERIVATIVE_STRATEGY = "bounded_contact_sheet_jpeg_v1"
GENERIC_DERIVATIVE_STRATEGY = "representative_frames_jpeg_v1"


class MovieIdentificationStatus(StrEnum):
    """Structured identification outcome; unknown is explicitly allowed."""

    IDENTIFIED = "identified"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


class IdentificationConfidence(StrEnum):
    """Discrete confidence contract for movie identification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"
