"""Deterministic loopback acceptance demo for YouTube manual acquisition.

This test-only program never contacts YouTube.  It runs the real operator CLI
against a loopback Uvicorn server while a synthetic downloader supplies bounded
media bytes to the production acquisition, upload, quarantine, validation,
publication, and catalog services.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import insert
import uvicorn

from kronika.adapters.api.local_identity_api import (
    LocalIdentityMiddleware,
    configured_local_identity,
)
from kronika.adapters.api.youtube_operator_api import (
    YouTubeOperatorApiDependencies,
    create_youtube_operator_api_router,
)
from kronika.adapters.cli import youtube as youtube_cli
from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationEvidence,
)
from kronika.application.ports.youtube_downloader import (
    YouTubeDownloadError,
    YouTubeDownloadPlan,
    YouTubeDownloadResult,
    YouTubeInspection,
)
from kronika.application.upload_catalog import CatalogPublishedUpload
from kronika.application.upload_catalog_coordinator import (
    UploadCatalogCoordinator,
)
from kronika.application.upload_publication import PublishPendingUpload
from kronika.application.upload_publication_coordinator import (
    UploadPublicationCoordinator,
)
from kronika.application.upload_transport import (
    UploadSessionLockRegistry,
    UploadTransportLimits,
    UploadTransportService,
)
from kronika.application.upload_validation import ValidateReceivedUpload
from kronika.application.upload_validation_coordinator import (
    UploadValidationCoordinator,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionCoordinator,
    YouTubeAcquisitionService,
    automatic_analysis_allowed_for_upload,
    youtube_classification_for_upload,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import LibraryId
from kronika.domain.identity_access import (
    CAPABILITY_UPLOAD_MANAGE,
    CAPABILITY_YOUTUBE_ACQUIRE,
    build_identity_mapping,
    mapped_role_has_capability,
)
from kronika.domain.uploads import (
    UploadSessionId,
    UploadSessionState,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.filesystem.published_media_storage import (
    FilesystemPublishedMediaStorage,
)
from kronika.infrastructure.filesystem.quarantine_storage import (
    FilesystemQuarantineStorage,
)
from kronika.infrastructure.persistence.catalog_schema import (
    devices,
    libraries,
    media_metadata,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.library_repository import (
    SqliteLibraryRepository,
)
from kronika.infrastructure.persistence.migrations import (
    upgrade_database_to_head,
)
from kronika.infrastructure.persistence.upload_publication_repository import (
    SqliteUploadPublicationRepository,
)
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    SqliteYouTubeAcquisitionClaimRepository,
)
from kronika.infrastructure.youtube.staging import (
    ARTIFACT_FILENAME,
    FilesystemYouTubeStaging,
)

HOST = "127.0.0.1"
_LOCAL_OWNER_LOGIN = "kronika-operator@example.com"
DESTINATION_ID = LibraryId.from_string(
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
DEVICE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
VIDEO_NEW = "AbCdEf123_-"
VIDEO_MANUAL_DUPLICATE = "ZyXwVu987_-"
VIDEO_INTERRUPTED = "MnOpQr456_-"
VIDEO_FAILED = "HiJkLm321_-"
PAYLOAD_NEW = b"\x00\x00\x00\x18ftypmp42demo-new-youtube-media"
PAYLOAD_MANUAL = b"\x00\x00\x00\x18ftypmp42demo-existing-manual-media"
PAYLOAD_RECOVERED = b"\x00\x00\x00\x18ftypmp42demo-recovered-youtube-media"
RAW_FAILURE_SENTINEL = "raw-private-upstream-failure"


def _json_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class _SyntheticValidator:
    def validate(self, _reader: object) -> UploadMediaValidationEvidence:
        return UploadMediaValidationEvidence(
            UploadValidatedMediaKind.VIDEO,
            UploadValidatedFormat.MP4,
        )


@dataclass
class _Probe:
    inspect_calls: list[str]
    download_calls: list[str]
    analysis_notifications: list[tuple[str, str]]
    partial_ready: threading.Event
    resume_seen: bool = False
    provider_submissions: int = 0


class _FakeDownloader:
    version = "2026.7.4"

    def __init__(
        self,
        staging: FilesystemYouTubeStaging,
        probe: _Probe,
        *,
        interrupt_video: str | None = None,
    ) -> None:
        self._staging = staging
        self._probe = probe
        self._interrupt_video = interrupt_video

    async def attest_version(self) -> str:
        return self.version

    async def inspect(self, identity: object) -> YouTubeInspection:
        video_id = str(getattr(identity, "video_id"))
        self._probe.inspect_calls.append(video_id)
        payload = _payload_for(video_id)
        return YouTubeInspection(
            video_id=video_id,
            extractor_key="Youtube",
            title="Synthetic acceptance title",
            channel="Synthetic acceptance channel",
            channel_id="synthetic-acceptance-channel",
            source_date="2026-07-23",
            remote_filename="Synthetic advisory name.mp4",
            duration_seconds=3.0,
            downloader_version=self.version,
            extractor_version=self.version,
            plan=YouTubeDownloadPlan(
                video_format_id="18",
                audio_format_id=None,
                expected_size_bytes=len(payload),
                has_source_audio=True,
                split_streams=False,
            ),
        )

    async def download(
        self,
        identity: object,
        inspection: YouTubeInspection,
        *,
        staging_key: str,
    ) -> YouTubeDownloadResult:
        video_id = str(getattr(identity, "video_id"))
        assert inspection.video_id == video_id
        self._probe.download_calls.append(video_id)
        claim_directory = self._staging.prepare(staging_key)
        partial = claim_directory / "artifact.mp4.part"
        if video_id == VIDEO_FAILED:
            partial.write_bytes(b"synthetic-failed-partial")
            raise YouTubeDownloadError("DOWNLOAD_FAILED")
        if video_id == self._interrupt_video:
            partial.write_bytes(b"synthetic-resumable-partial")
            self._probe.partial_ready.set()
            await asyncio.Future()
        if partial.exists():
            self._probe.resume_seen = True
            partial.unlink()
        payload = _payload_for(video_id)
        (claim_directory / ARTIFACT_FILENAME).write_bytes(payload)
        return YouTubeDownloadResult(size_bytes=len(payload))


def _payload_for(video_id: str) -> bytes:
    if video_id == VIDEO_NEW:
        return PAYLOAD_NEW
    if video_id == VIDEO_MANUAL_DUPLICATE:
        return PAYLOAD_MANUAL
    if video_id == VIDEO_INTERRUPTED:
        return PAYLOAD_RECOVERED
    if video_id == VIDEO_FAILED:
        return b"\x00\x00\x00\x18ftypmp42unused-failure-media"
    raise AssertionError("unexpected synthetic video identity")


class _AnalysisProbe:
    def __init__(self, probe: _Probe) -> None:
        self._probe = probe

    def notify_cataloged(self, media_id: object, location_id: object) -> None:
        self._probe.analysis_notifications.append(
            (
                str(getattr(media_id, "to_string")()),
                str(getattr(location_id, "to_string")()),
            )
        )


@dataclass
class _Roots:
    database_path: Path
    quarantine: Path
    publication: Path
    staging: Path
    preview: Path


@dataclass
class _Environment:
    engine: object
    uploads: SqliteUploadSessionRepository
    publications: SqliteUploadPublicationRepository
    claims: SqliteYouTubeAcquisitionClaimRepository
    transport: UploadTransportService
    validation: UploadValidationCoordinator
    publication: UploadPublicationCoordinator
    catalog: UploadCatalogCoordinator
    acquisition: YouTubeAcquisitionCoordinator
    service: YouTubeAcquisitionService
    app: FastAPI
    loop: asyncio.AbstractEventLoop | None = None


def _initialize_roots(base: Path) -> _Roots:
    roots = _Roots(
        database_path=base / "database" / "catalog.sqlite3",
        quarantine=base / "quarantine",
        publication=base / "publication",
        staging=base / "youtube-acquisition",
        preview=base / "preview",
    )
    for root in (
        roots.quarantine,
        roots.publication,
        roots.staging,
        roots.preview,
    ):
        root.mkdir(parents=True)
    roots.staging.chmod(0o700)
    upgrade_database_to_head(
        KronikaSettings(
            database_path=roots.database_path,
            _env_file=None,
        )
    )
    engine = create_sqlite_engine(roots.database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(devices).values(
                    id=DEVICE_ID,
                    display_name="Synthetic acceptance device",
                )
            )
            connection.execute(
                insert(libraries).values(
                    id=DESTINATION_ID.to_string(),
                    device_id=DEVICE_ID,
                    display_name="Synthetic published originals",
                    path_flavor="posix",
                    root_path=str(roots.publication),
                )
            )
    finally:
        dispose_engine(engine)
    return roots


def _build_environment(
    roots: _Roots,
    probe: _Probe,
    *,
    interrupt_video: str | None,
) -> _Environment:
    engine = create_sqlite_engine(roots.database_path)
    uploads = SqliteUploadSessionRepository(engine)
    publications = SqliteUploadPublicationRepository(engine)
    claims = SqliteYouTubeAcquisitionClaimRepository(engine)
    library_repository = SqliteLibraryRepository(engine)
    quarantine = FilesystemQuarantineStorage(roots.quarantine)
    staging = FilesystemYouTubeStaging(
        roots.staging,
        forbidden_roots=(
            roots.database_path.parent,
            roots.quarantine,
            roots.publication,
            roots.preview,
        ),
    )
    published = FilesystemPublishedMediaStorage(
        DESTINATION_ID,
        roots.publication,
        forbidden_roots=(
            roots.database_path.parent,
            roots.quarantine,
            roots.staging,
            roots.preview,
        ),
    )
    locks = UploadSessionLockRegistry()
    transport = UploadTransportService(
        uploads,
        quarantine,
        library_repository,
        UploadTransportLimits(
            max_total_bytes=1_073_741_824,
            max_patch_bytes=8,
            session_ttl_seconds=86_400,
            min_free_space_reserve_bytes=0,
        ),
        quarantine_root=roots.quarantine,
        preview_cache_root=roots.preview,
        locks=locks,
    )
    validator = ValidateReceivedUpload(
        uploads,
        quarantine,
        _SyntheticValidator(),
        locks=locks,
    )
    cataloger = CatalogPublishedUpload(
        publications,
        classification_for_upload=lambda upload_id: (
            youtube_classification_for_upload(claims, upload_id)
        ),
    )
    catalog = UploadCatalogCoordinator(
        publications,
        cataloger,
        locks,
        analysis_notifier=_AnalysisProbe(probe),
        analysis_allowed_for_upload=lambda upload_id: (
            automatic_analysis_allowed_for_upload(claims, upload_id)
        ),
        retry_initial_delay_seconds=0,
        retry_max_delay_seconds=0,
    )
    publisher = PublishPendingUpload(
        publications,
        published,
        quarantine,
    )
    publication = UploadPublicationCoordinator(
        publications,
        publisher,
        locks,
        catalog_coordinator=catalog,
        retry_initial_delay_seconds=0,
        retry_max_delay_seconds=0,
    )
    validation = UploadValidationCoordinator(
        uploads,
        validator,
        locks,
        publication_coordinator=publication,
        discovery_retry_initial_delay_seconds=0,
        discovery_retry_max_delay_seconds=0,
    )
    local_owner_mapping = build_identity_mapping({_LOCAL_OWNER_LOGIN: "admin"})

    def _creator_manages_duplicates(login: str) -> bool:
        return mapped_role_has_capability(
            local_owner_mapping,
            login,
            CAPABILITY_UPLOAD_MANAGE,
        )

    acquisition = YouTubeAcquisitionCoordinator(
        claims,
        _FakeDownloader(
            staging,
            probe,
            interrupt_video=interrupt_video,
        ),
        staging,
        transport,
        uploads,
        publications,
        validation_coordinator=validation,
        publication_coordinator=publication,
        chunk_size_bytes=8,
        poll_interval_seconds=0.01,
        creator_manages_duplicates=_creator_manages_duplicates,
    )
    service = YouTubeAcquisitionService(
        claims,
        uploads,
        staging,
        notifier=acquisition,
    )
    environment = _Environment(
        engine=engine,
        uploads=uploads,
        publications=publications,
        claims=claims,
        transport=transport,
        validation=validation,
        publication=publication,
        catalog=catalog,
        acquisition=acquisition,
        service=service,
        app=FastAPI(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        environment.loop = asyncio.get_running_loop()
        await environment.catalog.start()
        await environment.publication.start()
        await environment.validation.start()
        await environment.acquisition.start()
        try:
            yield
        finally:
            await environment.acquisition.shutdown()
            await environment.validation.shutdown()
            await environment.publication.shutdown()
            await environment.catalog.shutdown()
            dispose_engine(environment.engine)  # type: ignore[arg-type]
            environment.loop = None

    app = FastAPI(lifespan=lifespan)
    app.include_router(
        create_youtube_operator_api_router(
            YouTubeOperatorApiDependencies(
                service=service,
                enabled=True,
            )
        )
    )
    local_settings = KronikaSettings(
        database_path=roots.database_path,
        host=HOST,
        port=8000,
        identity_map={_LOCAL_OWNER_LOGIN: "admin"},
        local_owner_login=_LOCAL_OWNER_LOGIN,
        _env_file=None,
    )
    local_identity = configured_local_identity(local_settings)
    if local_identity is None or not local_identity.has_capability(
        CAPABILITY_YOUTUBE_ACQUIRE
    ):
        raise RuntimeError("demo local owner is not configured")
    app.add_middleware(
        LocalIdentityMiddleware,
        identity=local_identity,
        origin=f"http://{local_settings.host}:{local_settings.port}",
    )
    environment.app = app
    return environment


class _LoopbackServer:
    def __init__(self, app: FastAPI) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((HOST, 0))
        self._socket.listen(128)
        self.port = int(self._socket.getsockname()[1])
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=self.port,
                access_log=False,
                log_level="critical",
            )
        )
        self._errors: list[BaseException] = []
        self._thread = threading.Thread(
            target=self._run,
            name="framenest-youtube-demo-loopback",
            daemon=True,
        )

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._socket])
        except BaseException as exc:
            self._errors.append(exc)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if self._errors:
                raise RuntimeError("loopback acceptance server failed") from self._errors[0]
            if not self._thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("loopback acceptance server did not start")
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)
        self._socket.close()
        if self._thread.is_alive():
            raise RuntimeError("loopback acceptance server did not stop")
        if self._errors:
            raise RuntimeError("loopback acceptance server failed") from self._errors[0]


def _run_cli(port: int, argv: list[str]) -> tuple[int, str, str]:
    previous_host = os.environ.get("FRAMENEST_HOST")
    previous_port = os.environ.get("FRAMENEST_PORT")
    os.environ["FRAMENEST_HOST"] = HOST
    os.environ["FRAMENEST_PORT"] = str(port)
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = youtube_cli.main(argv)
    finally:
        if previous_host is None:
            os.environ.pop("FRAMENEST_HOST", None)
        else:
            os.environ["FRAMENEST_HOST"] = previous_host
        if previous_port is None:
            os.environ.pop("FRAMENEST_PORT", None)
        else:
            os.environ["FRAMENEST_PORT"] = previous_port
    output = stdout.getvalue()
    error = stderr.getvalue()
    print(output, end="")
    if error:
        print(error, end="")
    return result, output, error


async def _create_manual_upload(
    environment: _Environment,
) -> UploadSessionId:
    snapshot = environment.transport.create_session(
        display_filename="manual-existing.mp4",
        declared_size_bytes=len(PAYLOAD_MANUAL),
    )
    upload_id = UploadSessionId.from_string(snapshot.id)
    offset = 0
    while offset < len(PAYLOAD_MANUAL):
        chunk = PAYLOAD_MANUAL[offset : offset + 8]

        async def body(value: bytes = chunk) -> AsyncIterator[bytes]:
            yield value

        snapshot = await environment.transport.receive_chunk(
            upload_id,
            upload_offset=offset,
            content_length=len(chunk),
            body=body(),
        )
        offset = snapshot.received_size_bytes
    await environment.transport.complete(upload_id)
    environment.validation.notify()
    return upload_id


def _wait_for_upload(
    environment: _Environment,
    upload_id: UploadSessionId,
) -> None:
    deadline = time.monotonic() + 10
    while True:
        upload = environment.uploads.get(upload_id)
        if upload is not None and upload.state is UploadSessionState.CATALOGED:
            return
        if time.monotonic() >= deadline:
            raise AssertionError("manual upload did not reach the catalog")
        time.sleep(0.01)


def _prepare_manual_item(environment: _Environment) -> tuple[str, str]:
    if environment.loop is None:
        raise AssertionError("loopback server lifecycle is not running")
    future = asyncio.run_coroutine_threadsafe(
        _create_manual_upload(environment),
        environment.loop,
    )
    upload_id = future.result(timeout=10)
    _wait_for_upload(environment, upload_id)
    candidate = environment.publications.get_candidate(upload_id)
    assert candidate is not None
    assert candidate.publication is not None
    assert candidate.publication.media_id is not None
    assert candidate.publication.media_location_id is not None
    media_id = candidate.publication.media_id.to_string()
    location_id = candidate.publication.media_location_id.to_string()
    with environment.engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            insert(media_metadata).values(
                media_id=media_id,
                display_title="Owner-preserved manual title",
                description=None,
                content_category="meme",
                acquisition_source="manual_upload",
                collection_key=None,
                processed_at_ms=None,
                created_at_ms=1,
                updated_at_ms=1,
            )
        )
    return media_id, location_id


def _claim_id_from_output(output: str) -> str:
    for line in output.splitlines():
        payload = json.loads(line)
        if payload.get("event") == "status":
            return str(payload["claim_id"])
    raise AssertionError("operator output did not contain a claim ID")


def _database_evidence(database_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        claims = [
            {
                "claim_id": row["id"],
                "video_id": row["youtube_video_id"],
                "state": row["state"],
                "media_id": row["media_id"],
                "location_id": row["media_location_id"],
                "retry_of_claim_id": row["retry_of_claim_id"],
                "resolved_claim_id": row["resolved_claim_id"],
                "cleanup_state": row["cleanup_state"],
            }
            for row in connection.execute(
                """
                SELECT id, youtube_video_id, state, media_id, media_location_id,
                       retry_of_claim_id, resolved_claim_id, cleanup_state
                FROM youtube_acquisition_claims
                ORDER BY created_at_ms, id
                """
            )
        ]
        metadata = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT display_title, content_category, acquisition_source
                FROM media_metadata
                WHERE display_title = 'Owner-preserved manual title'
                """
            )
        ]
        logical_media_count = int(
            connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()[0]
        )
        location_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM physical_media_locations"
            ).fetchone()[0]
        )
        return {
            "claims": claims,
            "claim_count": len(claims),
            "logical_media_count": logical_media_count,
            "location_count": location_count,
            "manual_metadata": metadata,
        }
    finally:
        connection.close()


def main() -> int:
    probe = _Probe(
        inspect_calls=[],
        download_calls=[],
        analysis_notifications=[],
        partial_ready=threading.Event(),
    )
    with tempfile.TemporaryDirectory(
        prefix="framenest-youtube-fake-demo-"
    ) as temporary:
        roots = _initialize_roots(Path(temporary))
        first = _build_environment(
            roots,
            probe,
            interrupt_video=VIDEO_INTERRUPTED,
        )
        first_server = _LoopbackServer(first.app)
        first_server.start()

        new_exit, new_output, _ = _run_cli(
            first_server.port,
            [
                "ingest",
                f"https://youtu.be/{VIDEO_NEW}",
                "--yes",
                "--wait-timeout",
                "20",
            ],
        )
        assert new_exit == 0
        new_claim_id = _claim_id_from_output(new_output)

        repeat_exit, repeat_output, _ = _run_cli(
            first_server.port,
            [
                "ingest",
                f"https://m.youtube.com/shorts/{VIDEO_NEW}",
                "--yes",
                "--wait-timeout",
                "20",
            ],
        )
        assert repeat_exit == 0
        repeated_claim_id = _claim_id_from_output(repeat_output)
        assert repeated_claim_id != new_claim_id
        assert probe.inspect_calls.count(VIDEO_NEW) == 1
        assert probe.download_calls.count(VIDEO_NEW) == 1

        manual_media_id, manual_location_id = _prepare_manual_item(first)
        manual_analysis_notifications = tuple(probe.analysis_notifications)
        assert manual_analysis_notifications == (
            (manual_media_id, manual_location_id),
        )
        duplicate_exit, duplicate_output, _ = _run_cli(
            first_server.port,
            [
                "ingest",
                f"https://www.youtube.com/watch?v={VIDEO_MANUAL_DUPLICATE}",
                "--yes",
                "--wait-timeout",
                "20",
            ],
        )
        assert duplicate_exit == 0
        assert '"result":"reused"' in duplicate_output
        assert f'"media_id":"{manual_media_id}"' in duplicate_output
        assert probe.analysis_notifications == list(
            manual_analysis_notifications
        )

        interrupted_exit, interrupted_output, _ = _run_cli(
            first_server.port,
            [
                "ingest",
                f"https://youtu.be/{VIDEO_INTERRUPTED}",
                "--yes",
                "--wait-timeout",
                "0.2",
            ],
        )
        assert interrupted_exit == 124
        interrupted_claim_id = _claim_id_from_output(interrupted_output)
        assert probe.partial_ready.wait(timeout=5)
        first_server.stop()

        second = _build_environment(
            roots,
            probe,
            interrupt_video=None,
        )
        second_server = _LoopbackServer(second.app)
        second_server.start()
        recovered_exit, recovered_output, _ = _run_cli(
            second_server.port,
            [
                "status",
                interrupted_claim_id,
                "--wait",
                "--wait-timeout",
                "20",
            ],
        )
        assert recovered_exit == 0
        assert '"event":"success"' in recovered_output
        assert probe.resume_seen is True

        failed_exit, failed_output, failed_error = _run_cli(
            second_server.port,
            [
                "ingest",
                f"https://youtu.be/{VIDEO_FAILED}",
                "--yes",
                "--wait-timeout",
                "20",
            ],
        )
        assert failed_exit == 4
        assert '"failure_code":"DOWNLOAD_FAILED"' in failed_output
        assert '"error_code":"YOUTUBE_ACQUISITION_FAILED"' in failed_error
        assert RAW_FAILURE_SENTINEL not in failed_output + failed_error
        second_server.stop()

        evidence = _database_evidence(roots.database_path)
        assert evidence["logical_media_count"] == 3
        assert evidence["location_count"] == 3
        assert evidence["claim_count"] == 5
        assert evidence["manual_metadata"] == [
            (
                "Owner-preserved manual title",
                "meme",
                "manual_upload",
            )
        ]
        claims = evidence["claims"]
        assert isinstance(claims, list)
        assert all(
            row["cleanup_state"] == "complete"
            for row in claims
            if isinstance(row, dict)
            and row["state"]
            in {"cataloged", "duplicate_resolved", "failed"}
        )
        assert list(roots.staging.iterdir()) == []
        assert probe.provider_submissions == 0
        assert probe.analysis_notifications == list(
            manual_analysis_notifications
        )
        print(
            _json_line(
                {
                    "event": "acceptance",
                    "status": "pass",
                    "new_claim_id": new_claim_id,
                    "repeated_claim_id": repeated_claim_id,
                    "recovered_claim_id": interrupted_claim_id,
                    "logical_media_count": evidence[
                        "logical_media_count"
                    ],
                    "location_count": evidence["location_count"],
                    "claim_count": evidence["claim_count"],
                    "staging_residue_count": 0,
                    "provider_submission_count": probe.provider_submissions,
                    "youtube_analysis_notification_count": 0,
                    "resume_seen": probe.resume_seen,
                }
            )
        )
        print(
            _json_line(
                {
                    "event": "provenance",
                    "claims": evidence["claims"],
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
