"""Deterministic local browser acceptance server for requester-private X.

Starts a loopback FrameNest server with the fake X extractor injected and the
full upload/validation/publication pipeline configured so a submitted X claim
can progress to cataloged media. Identity is injected from an optional
``X-Test-Identity`` header (default ``alice`` = ordinary requester) so the
requester cockpit, privacy and admin scenarios can be driven from one browser.

The included still-image asset is a SYNTHETIC normalized-image application-path
fixture (exercising FrameNest's generic image lifecycle/category/handoff), NOT
evidence that the real pinned ``yt-dlp`` TwitterIE adapter acquires X still
photos — that capability is intentionally deferred.

Usage:
    python x_browser_server.py <db> <fixtures> <staging> <quarantine> <publish> <port>

Ephemeral. Never contacts X. Uses repository-owned generated media fixtures.
"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import subprocess
import sys
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

DB, FIXTURES, STAGING, QUARANTINE, PUBLISH, PORT_TEXT = sys.argv[1:7]
PORT = int(PORT_TEXT)

FIXTURES = pathlib.Path(FIXTURES)
STAGING = pathlib.Path(STAGING)
QUARANTINE = pathlib.Path(QUARANTINE)
PUBLISH = pathlib.Path(PUBLISH)

for directory in (FIXTURES, STAGING, QUARANTINE, PUBLISH):
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)


def _ffmpeg_available() -> bool:
    return subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0


def _generate_fixtures() -> None:
    mp4 = FIXTURES / "lal.mp4"
    if not mp4.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=blue:s=320x180:d=2", "-pix_fmt", "yuv420p",
                str(mp4),
            ],
            check=True, capture_output=True,
        )
    jpg = FIXTURES / "lal.jpg"
    if not jpg.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=green:s=200x150", "-frames:v", "1",
                str(jpg),
            ],
            check=True, capture_output=True,
        )


_generate_fixtures()

MULTI_INSPECTIONS = {"multi", "partial"}


class BrowserFakeExtractor:
    """Deterministic fake X extractor that writes valid local media to staging."""

    def __init__(self, *, scenario: str = "multi", fail_second: bool = False) -> None:
        self.scenario = scenario
        self.fail_second = fail_second

    def attest_version(self) -> str | None:
        return "browser-fake-2026.07.04"

    def inspect(self, *, post_id: str, submitted_url: str):
        from kronika.domain.x_acquisition import (
            XMediaType,
            XNormalizedAssetDescriptor,
            XNormalizedInspection,
        )

        if self.scenario == "partial":
            return XNormalizedInspection(
                post_id=post_id,
                canonical_url=None,
                post_text="A partial clip with an image",
                posted_at_ms=1_700_000_000_000,
                author_stable_id=str(uuid.uuid4()),
                author_handle="author",
                author_display_name="Author Name",
                assets=(
                    XNormalizedAssetDescriptor(
                        ordinal=0, media_type=XMediaType.VIDEO,
                        expected_mime="video/mp4", source_media_key="vid-0",
                        width=320, height=180, duration_seconds=2,
                    ),
                    XNormalizedAssetDescriptor(
                        ordinal=1, media_type=XMediaType.IMAGE,
                        expected_mime="image/jpeg", source_media_key="img-1",
                        width=200, height=150,
                    ),
                ),
                extractor_version="browser-fake-2026.07.04",
            )
        return XNormalizedInspection(
            post_id=post_id,
            canonical_url=None,
            post_text="A genuinely funny clip with an image",
            posted_at_ms=1_700_000_000_000,
            author_stable_id=str(uuid.uuid4()),
            author_handle="author",
            author_display_name="Author Name",
            assets=(
                XNormalizedAssetDescriptor(
                    ordinal=0, media_type=XMediaType.VIDEO,
                    expected_mime="video/mp4", source_media_key="vid-0",
                    width=320, height=180, duration_seconds=2,
                ),
                XNormalizedAssetDescriptor(
                    ordinal=1, media_type=XMediaType.IMAGE,
                    expected_mime="image/jpeg", source_media_key="img-1",
                    width=200, height=150,
                ),
            ),
            extractor_version="browser-fake-2026.07.04",
        )

    def download(
        self,
        *,
        post_id: str,
        ordinal: int,
        media_type: str,
        expected_mime: str,
        source_media_key: str | None,
        selected_variant: str | None = None,
        stage_key: str,
        submitted_url: str,
        staging: object,
    ) -> None:
        from kronika.application.ports.x_extractor import XAssetAcquisition

        if self.scenario == "partial" and ordinal == 0:
            from kronika.application.ports.x_extractor import XExtractionError

            raise XExtractionError(
                "X_DOWNLOAD_TIMEOUT", "fake extractor video retrieval timed out"
            )
        directory = staging.prepare(stage_key)
        payload = (
            (FIXTURES / "lal.mp4").read_bytes()
            if media_type in {"video", "animated_gif"}
            else (FIXTURES / "lal.jpg").read_bytes()
        )
        artifact = pathlib.Path(directory) / "artifact.bin"
        artifact.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return XAssetAcquisition(size_bytes=len(payload), sha256=digest)


# ------------------------------------------------------------------ wire app
from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID, SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    ROLE_ADMIN,
    ROLE_USER,
    IdentityContext,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DEV = "66666666-6666-4666-8666-666666666666"
LIB = "55555555-5555-4555-8555-555555555555"

scenario = "multi"
fail_second = False
if len(sys.argv) > 7:
    scenario = sys.argv[7]

settings_pre = KronikaSettings(database_path=DB, _env_file=None)
upgrade_database_to_head(settings_pre)

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("INSERT INTO devices (id, display_name) VALUES (?, 'dev')", (DEV,))
    conn.execute(
        "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
        "VALUES (?, ?, 'publish', 'posix', ?)",
        (LIB, DEV, str(PUBLISH)),
    )
    conn.commit()
finally:
    conn.close()

settings = KronikaSettings(
    database_path=DB,
    upload_quarantine_root=str(QUARANTINE),
    upload_publication_library_id=LIB,
    x_acquisition_root=str(STAGING),
    host="127.0.0.1",
    port=PORT,
    automatic_media_analysis_enabled=False,
    _env_file=None,
)

fake = BrowserFakeExtractor(scenario=scenario, fail_second=fail_second)
app = create_app(settings=settings, x_extractor=fake)

from starlette.middleware.base import BaseHTTPMiddleware


class BrowserIdentityInjector(BaseHTTPMiddleware):
    def __init__(self, inner):
        super().__init__(inner)
        self.inner = inner

    async def dispatch(self, request, call_next):
        requested = request.headers.get("X-Test-Identity", "alice").strip().lower()
        if requested == "admin":
            identity = IdentityContext(
                login="admin@example.com",
                login_key="admin@example.com",
                display_name="Browser Admin",
                role=ROLE_ADMIN,
                capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN],
                provenance="tailscale-serve",
            )
        else:
            login = f"{requested}@example.com" if "@" not in requested else requested
            identity = IdentityContext(
                login=login,
                login_key=login,
                display_name=requested,
                role=ROLE_USER,
                capabilities=CAPABILITIES_BY_ROLE[ROLE_USER],
                provenance="tailscale-serve",
            )
        request.scope[SCOPE_IDENTITY] = identity
        request.scope[SCOPE_AUDIT_EVENT_ID] = str(uuid.uuid4())
        return await call_next(request)




async def identity_me(request: Request) -> JSONResponse:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return JSONResponse(
        content={
            "login": identity.login,
            "display_name": identity.display_name,
            "role": identity.role,
            "capabilities": sorted(identity.capabilities),
            "provenance": identity.provenance,
        }
    )


_router = APIRouter()
_router.add_api_route("/api/identity/me", identity_me, methods=["GET"])
app.include_router(_router)

app = BrowserIdentityInjector(app)

import uvicorn

print(f"READY {PORT} scenario={scenario}", flush=True)
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error", access_log=False)
