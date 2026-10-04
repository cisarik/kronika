"""Application port for local movie-identification preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.application.movie_identification import LocalMovieHints
from kronika.domain import LibraryRoot
from kronika.application.media_analysis import MediaRelativePath


@dataclass(frozen=True, slots=True)
class PreparedMovieIdentification:
    """Locally prepared contact-sheet evidence for movie identification."""

    basename: str
    contact_sheet: object
    hints: LocalMovieHints
    warnings: tuple[str, ...]


class MovieIdentificationPreparer(Protocol):
    """Prepare one bounded contact-sheet derivative without provider access."""

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedMovieIdentification:
        """Extract frames and compose one contact sheet under the library root."""
