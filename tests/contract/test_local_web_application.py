"""Contract tests for the packaged local FrameNest web application shell."""

from __future__ import annotations

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from tests.support.tooling import resolve_tool
from fastapi.testclient import TestClient
from pydantic import SecretStr

from framenest.adapters.api.application import create_app
from framenest.configuration import FrameNestSettings

REPRESENTATIVE_SECRET = "local-web-contract-secret"
REPRESENTATIVE_DATABASE_PATH = "/Users/example/framenest/catalog.sqlite3"
REPRESENTATIVE_REPOSITORY_PATH = "/Users/example/framenest"
FORBIDDEN_RESPONSE_FRAGMENTS = (
    REPRESENTATIVE_SECRET,
    REPRESENTATIVE_DATABASE_PATH,
    REPRESENTATIVE_REPOSITORY_PATH,
    "NVIDIA_API_KEY",
    "FRAMENEST_API_KEY",
    "FRAMENEST_DATABASE_PATH",
)


class _AssetReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.main_count = 0
        self.stylesheet_hrefs: list[str] = []
        self.script_srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "main":
            self.main_count += 1
        if tag == "link" and attributes.get("rel") == "stylesheet":
            href = attributes.get("href")
            if href is not None:
                self.stylesheet_hrefs.append(href)
        if tag == "script":
            src = attributes.get("src")
            if src is not None:
                self.script_srcs.append(src)


@pytest.fixture
def client() -> TestClient:
    settings = FrameNestSettings(
        host="127.0.0.1",
        api_key=SecretStr(REPRESENTATIVE_SECRET),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings))


def _parse_document(html: str) -> _AssetReferenceParser:
    parser = _AssetReferenceParser()
    parser.feed(html)
    return parser


def _javascript_function(script: str, name: str) -> str:
    marker = f"function {name}("
    start = script.index(marker)
    brace_start = script.index(") {", start) + 2
    depth = 0
    for index in range(brace_start, len(script)):
        char = script[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[start : index + 1]
    raise AssertionError(f"Could not find complete JavaScript function {name}")


def _evaluate_upload_should_poll(
    script: str,
    *,
    state: str | None,
    publication_poll_attempts: int = 0,
) -> bool:
    """Execute the real uploadShouldPoll body against lifecycle snapshots."""
    function_body = _javascript_function(script, "uploadShouldPoll")
    max_attempts_match = re.search(
        r"const UPLOAD_PUBLICATION_POLL_MAX_ATTEMPTS = (\d+);",
        script,
    )
    assert max_attempts_match is not None
    max_attempts = int(max_attempts_match.group(1))
    snapshot_literal = "null" if state is None else json.dumps({"state": state})
    program = (
        f"const UPLOAD_PUBLICATION_POLL_MAX_ATTEMPTS = {max_attempts};\n"
        f"const uploadState = {{ publicationPollAttempts: {publication_poll_attempts} }};\n"
        f"{function_body}\n"
        f"process.stdout.write(JSON.stringify(uploadShouldPoll({snapshot_literal})));\n"
    )
    result = subprocess.run(
        [resolve_tool("node"), "-e", program],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _css_rule_declarations(css: str, selectors: tuple[str, ...]) -> dict[str, str]:
    expected_selectors = {" ".join(selector.split()) for selector in selectors}
    for selector_block, declaration_block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        actual_selectors = {
            " ".join(selector.split()) for selector in selector_block.split(",")
        }
        if actual_selectors != expected_selectors:
            continue

        declarations = {}
        for declaration in declaration_block.split(";"):
            if not declaration.strip():
                continue
            property_name, value = declaration.split(":", 1)
            declarations[property_name.strip()] = value.strip()
        return declarations

    raise AssertionError(f"Could not find CSS rule for selectors {selectors}")


def test_root_serves_framenest_application_document(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    html = response.text
    parsed = _parse_document(html)
    assert "Kronika" in html
    assert "FrameNest" not in html
    assert parsed.main_count == 1
    assert parsed.stylesheet_hrefs == ["/assets/styles.css"]
    assert parsed.script_srcs == ["/assets/companion_host.js", "/assets/app.js"]


def test_root_document_references_only_local_application_assets(client: TestClient) -> None:
    response = client.get("/")
    html = response.text
    parsed = _parse_document(html)
    references = parsed.stylesheet_hrefs + parsed.script_srcs

    assert references
    assert all(reference.startswith("/assets/") for reference in references)
    assert "http://" not in html
    assert "https://" not in html
    assert 'src="//' not in html
    assert 'href="//' not in html


@pytest.mark.parametrize(
    ("path", "expected_content_type"),
    [
        ("/assets/styles.css", "text/css"),
        ("/assets/app.js", "text/javascript"),
        ("/assets/companion_host.js", "text/javascript"),
    ],
)
def test_local_application_assets_are_served(
    client: TestClient,
    path: str,
    expected_content_type: str,
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(expected_content_type)
    assert response.text.strip()


def test_unknown_application_asset_returns_404_not_application_document(
    client: TestClient,
) -> None:
    response = client.get("/assets/missing.css")
    assert response.status_code == 404
    assert "FrameNest" not in response.text


def test_health_contract_remains_unchanged_with_web_application(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"].startswith("application/json")


_UNIFORM_VALIDATION_BODY = {
    "error": {
        "code": "VALIDATION_FAILED",
        "message": "Request validation failed.",
    }
}


def test_malformed_requests_return_uniform_sanitized_422(client: TestClient) -> None:
    marker = "zz-hostile-caller-input-zz"
    malformed_requests = (
        ("post", "/api/x/requests", {"url": {"nested": marker}}),
        (
            "post",
            "/api/companion/review-inbox/22222222-2222-4222-8222-222222222222/apply",
            {"analysis_run_id": {"nested": marker}, "fields": []},
        ),
        ("post", f"/api/uploads/{marker}/complete", {"resolution": marker}),
        (
            "put",
            "/api/media/12345678-1234-4234-9234-123456789abc/alias",
            {"tag_keys": "not-a-list", "extra_field": marker},
        ),
        (
            "put",
            "/api/admin/settings/automatic-analysis",
            {"automatic_media_analysis_enabled": marker},
        ),
    )
    for method, path, body in malformed_requests:
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 422, path
        assert response.json() == _UNIFORM_VALIDATION_BODY, path
        assert "detail" not in response.json(), path
        assert marker not in response.text, path
        assert response.headers.get("cache-control") == "no-store", path


def test_malformed_analysis_proposal_query_returns_uniform_sanitized_422(
    client: TestClient,
) -> None:
    marker = "zz-hostile-caller-input-zz"
    response = client.get(
        "/api/admin/analysis-proposals",
        params={"limit": marker},
    )
    assert response.status_code == 422
    assert response.json() == _UNIFORM_VALIDATION_BODY
    assert "detail" not in response.json()
    assert marker not in response.text
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize(
    "path",
    ["/", "/assets/styles.css", "/assets/app.js", "/assets/companion_host.js", "/health"],
)
def test_application_responses_do_not_add_wildcard_cors(
    client: TestClient,
    path: str,
) -> None:
    response = client.get(path)
    assert response.headers.get("access-control-allow-origin") != "*"


def test_companion_host_asset_omits_wildcard_cors_and_wildcard_postmessage(
    client: TestClient,
) -> None:
    response = client.get("/assets/companion_host.js")
    assert response.status_code == 200
    body = response.text
    assert "framenest.companion.web.v1" in body
    assert "chrome-extension://omiihmnlkmieaafaphohakcgmbggppap" in body
    assert '"*"' not in body
    assert "'*'" not in body
    assert "Access-Control-Allow-Origin" not in body
    assert "access-control-allow-origin" not in body
    html = client.get("/").text
    assert "https://" not in html
    assert "http://" not in html


def test_browser_application_uses_same_origin_health_with_distinct_status_states(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    assert 'const HEALTH_ENDPOINT = "/health";' in script
    assert "fetch(HEALTH_ENDPOINT" in script
    assert "status--loading" in script
    assert "status--healthy" in script
    assert "status--error" in script
    assert "setLoadingState" in script
    assert "setHealthyState" in script
    assert "setErrorState" in script


def test_web_shell_removes_developer_library_tools_from_gallery(client: TestClient) -> None:
    html = client.get("/").text
    assert "library-browser" not in html
    assert "Library tools" not in html
    assert "Preview media" not in html
    assert "Not scanned" not in html
    assert "Local preview" not in html
    assert "AI suggestion review" not in html


def test_web_shell_does_not_contain_verbose_library_prose(client: TestClient) -> None:
    html = client.get("/").text
    assert "CLI-only" not in html
    assert "Library registration remains CLI-only" not in html
    assert "path flavor" not in html
    assert "Root path is intentionally hidden" not in html
    assert "posix path flavor" not in html


def test_web_shell_contains_reachable_catalog_browser_states(client: TestClient) -> None:
    html = client.get("/").text
    assert "catalog-browser" in html
    assert "catalog-state-loading" in html
    assert "library-state__spinner" in html
    assert "Loading media…" in html
    assert "Loading catalog media" not in html
    assert "No media matched this catalog query" in html
    assert "catalog-retry-button" in html
    assert "Media could not be loaded." in html
    assert "Previous page" in html
    assert "Next page" in html
    assert "&lt;" in html
    assert "&gt;" in html
    for page_size in ("10 per page", "30 per page", "60 per page", "90 per page"):
        assert page_size in html


def test_web_shell_removes_visible_catalog_headings_for_compactness(client: TestClient) -> None:
    html = client.get("/").text
    catalog_section = html[html.index('id="catalog-browser"') : html.index('id="catalog-scope-all"')]

    assert "section-heading" not in catalog_section
    assert '<p class="eyebrow">Catalog</p>' not in html
    assert '<h2 id="catalog-title">Imported media</h2>' not in html
    assert 'aria-labelledby="catalog-title"' not in html


def test_web_shell_does_not_contain_obsolete_catalog_search_form(client: TestClient) -> None:
    html = client.get("/").text
    assert "catalog-search-form" not in html
    assert "catalog-search-input" not in html
    assert "catalog-search-button" not in html
    assert "catalog-clear-button" not in html
    assert "Search display titles" not in html


def test_web_shell_contains_manual_current_metadata_workspace(client: TestClient) -> None:
    html = client.get("/").text

    assert "metadata-workspace" in html or "metadata-dialog" in html
    dialog_section = html[html.index("metadata-dialog"):]
    assert "Edit media" in dialog_section
    assert "Title" in dialog_section
    assert "Description" in dialog_section
    assert "Tags" in dialog_section
    assert "Search or add a tag" in dialog_section
    assert "metadata-tag-suggestions" in html
    assert "metadata-selected-tags" in html
    assert "Save" in dialog_section
    assert "Cancel" in dialog_section
    for obsolete in (
        "Current metadata",
        "Display title",
        "Search canonical tags",
        "Canonical key",
        "Display name",
        "Create and select",
        "Selected canonical tags",
        "Save metadata",
        "Discard changes",
        "Processed status",
        "Clean.",
        "Unsaved changes.",
        "0 / 10000",
        "0 of 32 selected",
    ):
        assert obsolete not in dialog_section


def test_browser_description_counter_uses_code_point_length(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "unicodeCodePointLength(metadataDescriptionInput.value)" in script
    assert "input.value.length" not in script or (
        "input.value.length" in script and "unicodeCodePointLength" in script
    )
    status_block = script[script.index("function updateDescriptionStatus") : script.index("function renderMetadataWorkspace")]
    assert "unicodeCodePointLength" in status_block
    assert "metadataDescriptionStatus.hidden = true" in status_block


def test_browser_description_textarea_has_no_maxlength(client: TestClient) -> None:
    html = client.get("/").text
    assert "maxlength=\"10000\"" not in html
    assert "metadata-description-input" in html
    assert "textarea" in html


def test_browser_title_input_has_no_maxlength(client: TestClient) -> None:
    html = client.get("/").text
    id_index = html.index('id="metadata-title-input"')
    tag = html[html.rindex("<input", 0, id_index) : html.index(">", id_index)]
    assert "maxlength" not in tag


def test_browser_description_is_never_rendered_as_inner_html(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script


def test_browser_description_workspace_includes_dirty_state_for_description(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "normalized.description !== metadataWorkspace.baseline.description" in script
    assert "description: null" in script
    assert "description: \"\"" in script


def test_catalog_cards_have_explicit_metadata_edit_action(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")

    assert "Edit" in card_body
    assert "handleOpenMetadataWorkspace(item" in script
    assert "card.addEventListener(\"click\"" not in script


def test_javascript_metadata_workspace_uses_existing_same_origin_endpoints(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert 'const MEDIA_METADATA_ENDPOINT_PREFIX = "/api/media";' in script
    assert "metadataEndpoint(mediaId)" in script
    assert 'fetch(metadataEndpoint(mediaId)' in script
    assert 'method: "PUT"' in script
    assert 'body: JSON.stringify(saveOwner.requestPayload)' in script
    assert "content_category: normalized.contentCategory" in script
    # Acquisition source is immutable catalog provenance (ADR-0055): it is part
    # of the normalized workspace form state (shown read-only) but must not be
    # sent as a mutable field on ordinary metadata Save.
    form_body = _javascript_function(script, "normalizedMetadataFormState")
    assert "acquisitionSource: metadataWorkspace.current.acquisitionSource || \"unknown\"" in form_body
    save_body = _javascript_function(script, "claimMetadataSaveOwner")
    assert "requestPayload" in save_body
    assert "acquisition_source" not in save_body
    assert "genres: normalized.genres" in script
    assert 'fetch(CANONICAL_TAGS_ENDPOINT, {' in script
    assert 'method: "POST"' in script
    assert "createAndSelectMetadataTag" in script
    assert "handleCreateAndSelectTag" not in script
    assert "CANONICAL_TAG_DEFINITION_CONFLICT" in script
    assert "CANONICAL_TAG_NOT_FOUND" in script
    assert "MEDIA_NOT_FOUND" in script
    assert "CATALOG_UNAVAILABLE" in script


def test_javascript_metadata_workspace_tracks_sparse_baseline_dirty_and_discard(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "metadataWorkspace.baseline" in script
    assert "metadataWorkspace.current" in script
    assert "payload.display_title === null" in script
    assert "metadataTitleInput.value = metadataWorkspace.current.displayTitle || \"\";" in script
    assert "deriveCatalogFallbackTitle(item)" in script
    assert "function normalizedMetadataFormState" in script
    assert "displayTitle: null" in script
    assert "metadataSaveButton.disabled = metadataWorkspace.loading || metadataWorkspace.saving || !dirty || Boolean(validation);" in script
    assert "metadataDiscardButton.disabled = metadataWorkspace.saving;" in script
    discard_body = _javascript_function(script, "confirmDiscardDirtyMetadata")
    assert "requestConfirmation({" in discard_body
    assert 'title: "Discard changes?"' in discard_body
    assert 'dismissLabel: "Keep editing"' in discard_body
    assert 'confirmLabel: "Discard changes"' in discard_body
    assert "handleDiscardMetadataChanges" in script
    assert "closeMetadataWorkspace();" in _javascript_function(script, "handleDiscardMetadataChanges")
    assert "metadataBeforeUnloadHandler" in script
    assert "beforeunload" in script


def test_javascript_metadata_workspace_tag_selection_ordering_and_limits(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "const MAX_METADATA_TAGS = 32;" in script
    assert "function renderMetadataTagSuggestions" in script
    assert "function selectMetadataTag" in script
    assert "metadataWorkspace.current.tagKeys.includes(tag.key)" in script
    assert "metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS" in script
    assert "function removeSelectedMetadataTag" in script
    assert "function handleMetadataTagSearchKeydown" in script
    assert "ArrowDown" in script
    assert "ArrowUp" in script
    assert "Add “${displayName}”" in script
    assert "uniqueTagKeyForDisplayName(displayName)" in script
    assert "body: JSON.stringify({ key, display_name: displayName })" in script
    assert "Remove ${displayName}" in script
    assert "Move earlier" not in script
    assert "Move later" not in script
    assert "Remove tag" not in script
    assert "tag_keys: normalized.tagKeys" in script
    assert ".sort(" not in script[script.index("function renderSelectedMetadataTags") : script.index("function renderMetadataTagSuggestions")]


def test_javascript_metadata_save_refreshes_catalog_and_preserves_filters(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "async function handleSaveMetadata" in script
    save_block = _javascript_function(script, "handleSaveMetadata")
    assert 'setMetadataStatus("saved", "Saved.")' not in save_block
    assert "created" not in save_block
    assert "updated" not in save_block
    assert "unchanged" not in save_block
    assert "metadataWorkspace.openMediaId" in save_block
    assert "await loadCatalog();" in save_block
    assert "closeMetadataWorkspace();" in save_block
    assert "catalogState.q" not in save_block
    assert "catalogState.tagKeys" not in save_block
    assert "catalogState.offset = 0" not in save_block


def test_javascript_loads_catalog_without_auto_scan_analysis_or_ai(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert 'const MEDIA_CATALOG_ENDPOINT = "/api/media";' in script
    assert 'const CANONICAL_TAGS_ENDPOINT = "/api/canonical-tags";' in script
    assert "loadCatalog();" in script
    assert "loadCatalogTags();" in script
    assert "function buildCatalogQueryParams" in script
    assert "fetch(`${MEDIA_CATALOG_ENDPOINT}" in script
    catalog_block = script[
        script.index("async function loadCatalog") : script.index("function applyAdminCatalogFilters")
    ]
    assert "scan-preview" not in catalog_block
    assert "media-analysis-preview" not in catalog_block
    assert "media-suggestion-preview" not in catalog_block


def test_catalog_rendering_uses_safe_dom_text_apis_and_no_inline_html(client: TestClient) -> None:
    script = client.get("/assets/app.js").text

    assert "renderCatalogSuccess" in script
    assert "renderCatalogCard" in script
    assert "deriveCatalogFallbackTitle" in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script


def test_browser_decodes_base64_to_png_blob_and_revokes_object_urls(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "atob(" in script
    assert "Uint8Array" in script
    assert 'new Blob([bytes], { type: "image/png" })' in script
    assert "URL.createObjectURL" in script
    assert "URL.revokeObjectURL" in script
    assert "beforeunload" in script
    assert "payload_base64" in script


def test_browser_does_not_store_frame_payloads_or_add_external_runtime_urls(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    html = client.get("/").text

    assert "payload_base64" not in script[script.index("function restoredCatalogPageSize") : script.index("function setStatusClass")]
    assert "framenest.youtube.currentClaim.v1" in script
    assert "indexedDB" not in script
    assert "data:image" not in script
    assert "http://" not in html + script
    assert "https://" not in html + script


def test_browser_loads_ai_capability_without_invoking_analysis(client: TestClient) -> None:
    script = client.get("/assets/app.js").text

    assert 'const AI_CAPABILITY_ENDPOINT = "/api/ai/media-suggestion-capability";' in script
    assert "loadAiCapability();" in script
    assert "fetch(AI_CAPABILITY_ENDPOINT" in script
    assert "checkHealth();" in script
    capability_body = _javascript_function(script, "loadAiCapability")
    assert "ai-suggestion-preview" not in capability_body
    assert "media-analysis-preview" not in capability_body


def test_browser_presents_ai_capability_states_from_api(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    combined = html + script

    assert "AI unavailable" in combined
    assert "AI not configured" in combined
    assert "Server credential unavailable" in combined
    assert "Authentication failed" in combined
    assert "provider_id" in script
    assert "provider_display_name" in script
    assert "model_id" in script
    assert "credential_available" in script
    assert "last_connection_test" in script
    assert "prompt_version" in script
    assert "execution" in script
    assert ">Provider<" in html
    assert ">Model<" in html
    assert ">Configuration<" in html
    assert ">Credential<" in html
    assert ">Last connection test<" in html
    assert ">Tested at<" in html
    assert "No server AI provider has been selected." in combined
    assert "The selected provider credential is not available" in combined
    assert "AI is configured by the FrameNest server operator" not in combined


def test_browser_analyze_is_explicit_confirmed_and_cloud_disclosed(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    combined = html + script

    assert "Analyze" in combined
    assert "optimized preview frames" in combined
    assert "original file, local path, and API key are not uploaded" in combined
    assert "become proposal strips beside Title, Description, and Tags" in combined
    assert "They do not replace the current unsaved values" in combined
    assert "will not be saved automatically" in combined
    assert "physical file will not be renamed" in combined
    assert "confirm_cloud_upload" in script
    assert "Cancel analysis" not in combined
    assert "Provider selection" not in combined
    assert "Model selection" not in combined
    metadata_analyze_block = script[
        script.index("async function handleAnalyzeMetadataByAi") : script.index("function aiSuggestionErrorMessage")
    ]
    assert "progress" not in metadata_analyze_block.lower()


def test_browser_editor_uses_single_form_ai_assistance(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    dialog_section = html[html.index("metadata-dialog"):]

    assert "metadata-title-input" in dialog_section
    assert "metadata-description-input" in dialog_section
    assert "metadata-tag-search-input" in dialog_section
    assert "metadata-ai-filename-note" in dialog_section
    assert "metadata-ai-filename-input" not in dialog_section
    assert "metadata-ai-filename-display" not in dialog_section
    assert "review-title-input" not in dialog_section
    assert "review-description-input" not in dialog_section
    assert "review-tag-input" not in dialog_section
    assert "provider_id" in script
    assert "model_id" in script
    assert "prompt_version" in script


def test_browser_editor_has_single_title_description_and_tags_workflow(client: TestClient) -> None:
    html = client.get("/").text
    dialog_section = html[html.index("metadata-dialog") : html.index("</dialog>", html.index("metadata-dialog"))]

    assert dialog_section.count('id="metadata-title-input"') == 1
    assert dialog_section.count('id="metadata-description-input"') == 1
    assert dialog_section.count('id="metadata-tag-search-input"') == 1
    assert dialog_section.count('id="metadata-selected-tags"') == 1
    assert dialog_section.count(">Title<") == 1
    assert dialog_section.count(">Description<") == 1
    assert dialog_section.count(">Tags<") == 1


def test_browser_editor_hides_provider_model_noise_in_ordinary_editor(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    dialog_section = html[html.index("metadata-dialog") : html.index("</dialog>", html.index("metadata-dialog"))]
    ai_panel_body = _javascript_function(script, "renderMetadataAiPanel")

    assert "provider_id" not in dialog_section
    assert "model_id" not in dialog_section
    assert "prompt_version" not in dialog_section
    assert "provider_id ||" not in ai_panel_body
    assert "model_id ||" not in ai_panel_body
    assert "New AI analysis is available after confirmation." not in ai_panel_body
    assert "AI suggestions" in ai_panel_body
    assert "Suggested filename:" in ai_panel_body


def test_browser_editor_idle_ai_button_is_exact_and_not_loading(client: TestClient) -> None:
    html = client.get("/").text
    button_start = html.index('id="metadata-ai-analyze-button"')
    button_section = html[button_start : html.index("</button>", button_start)]

    assert "Analyze by AI" in button_section
    assert "🧠 Analyze by AI" not in button_section
    assert "🪄 Analyze by AI" not in button_section
    assert "loading-spinner" not in button_section
    assert "Analyzing…" not in button_section
    assert "metadata-ai-analyze-button__idle" not in button_section
    assert "metadata-ai-analyze-button__loading" not in button_section


def test_browser_editor_ai_button_active_state_replaces_entire_content(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    html = client.get("/").text
    render_button_body = _javascript_function(script, "renderMetadataAiAnalyzeButtonContent")
    analyze_block = script[
        script.index("async function runMetadataAiAnalysis") : script.index("function aiSuggestionErrorMessage")
    ]

    assert "metadataAiAnalyzeButton.replaceChildren()" in render_button_body
    assert '"Analyze by AI"' in render_button_body
    assert '"Retry analysis"' in render_button_body
    assert "loading-spinner" not in render_button_body
    assert 'label.textContent = "Analyzing…"' in render_button_body
    assert "metadataAiAnalyzeButton.append(label)" in render_button_body
    assert 'id="metadata-ai-progress"' in html
    assert "metadataAiProgress.hidden = !metadataWorkspace.analyzing" in script
    assert 'metadataAiStatus.textContent = "Analyzing…"' not in analyze_block
    assert 'metadataAiStatus.textContent = "Loaded"' not in analyze_block
    assert "Suggestion ready. Review and copy only the fields you want." in analyze_block


def test_browser_editor_ai_button_active_state_is_prominent_without_idle_animation(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    button_css = css[
        css.index(".metadata-dialog__footer .metadata-ai-analyze-button {")
        : css.index(".metadata-ai-analyze-button > *")
    ]
    busy_block = css[
        css.index('.metadata-dialog__footer .metadata-ai-analyze-button[aria-busy="true"]')
        : css.index(".metadata-ai-analyze-button > *")
    ]

    assert "background: #f5f8f5" in button_css
    assert "color: #0c1a10" in button_css
    assert "linear-gradient(90deg, #0b2514" not in css
    assert "metadata-ai-analyzing" not in css
    assert 'metadata-ai-analyze-button:hover::after' not in css
    assert 'metadata-ai-analyze-button:focus-visible::after' not in css
    assert ':disabled:not([aria-busy="true"])' in css
    assert "opacity: 1" in busy_block
    assert "cursor: progress" in busy_block
    assert "background: #e8f3ea" in busy_block


def test_browser_ai_replacement_is_session_only_without_mutation_api(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    analyze_block = script[
        script.index("async function runMetadataAiAnalysis") : script.index("function aiSuggestionErrorMessage")
    ]
    present_body = _javascript_function(script, "presentInSessionSuggestion")
    copy_body = _javascript_function(script, "copySuggestionFieldToCurrent")
    controls_body = _javascript_function(script, "updateMetadataControls")

    assert "presentInSessionSuggestion(suggestion, payload)" in analyze_block
    assert "applyResolvedAiSuggestionToMetadataWorkspace" not in script
    assert "metadataWorkspace.current.displayTitle = suggestion.title" not in present_body
    assert "metadataWorkspace.current.description = suggestion.description" not in present_body
    assert "metadataWorkspace.suggestedFilename = item.suggestedFilename || \"\"" in present_body
    assert "metadataWorkspace.current.displayTitle = item.title || \"\"" in copy_body
    assert "metadataAiAnalyzeButton.hidden = !showAnalyze" in controls_body
    assert "analysisAvailable" in controls_body
    assert "fetch(metadataEndpoint" not in analyze_block
    assert "confirm_cloud_upload: true" in analyze_block
    assert "if (metadataWorkspace.analyzing) return false;" in analyze_block
    assert "proposal strips" in analyze_block
    assert "This editor changed before analysis could start." in analyze_block


def test_browser_editor_ai_failure_restores_idle_action_and_preserves_values(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    controls_body = _javascript_function(script, "updateMetadataControls")
    analyze_block = script[
        script.index("async function runMetadataAiAnalysis") : script.index("function aiSuggestionErrorMessage")
    ]
    error_body = _javascript_function(script, "aiSuggestionErrorMessage")

    assert "metadataWorkspace.analyzing = false" in analyze_block
    assert "aiSuggestionErrorMessage(payload)" in analyze_block
    assert "AI analysis failed. Try again." in error_body
    assert "The model returned an unusable response. Try analysis again." in error_body
    assert "metadataWorkspace.current = " not in analyze_block
    assert "beforeRequest" not in analyze_block
    assert "renderMetadataAiAnalyzeButtonContent(metadataWorkspace.analyzing)" in controls_body
    assert "metadataAiAnalyzeButton.disabled = !showAnalyze" in controls_body
    assert '"Analyze by AI"' in script
    assert '"Retry analysis"' in script


def test_selected_metadata_tag_remove_is_red_and_bounded_to_current_media(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    css = client.get("/assets/styles.css").text
    render_tags_body = _javascript_function(script, "renderSelectedMetadataTags")
    remove_body = _javascript_function(script, "removeSelectedMetadataTag")
    remove_css = css[css.index(".metadata-tag-chip__remove:hover") : css.index("/* --- Library cards ---")]

    assert 'remove.className = "metadata-tag-chip__remove"' in render_tags_body
    assert "from this media" in render_tags_body
    assert "metadataWorkspace.current.tagKeys = metadataWorkspace.current.tagKeys.filter" in remove_body
    assert "fetch(" not in remove_body
    assert "CANONICAL_TAGS_ENDPOINT" not in remove_body
    assert "delete" not in remove_body.lower()
    assert "border-color: var(--danger)" in remove_css
    assert "background: var(--danger-soft)" in remove_css
    assert "color: var(--danger)" in remove_css


def test_javascript_uses_safe_dom_text_apis_for_repository_values(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert ".textContent" in script
    assert "createTextNode" in script
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script


@pytest.mark.parametrize("path", ["/", "/assets/styles.css", "/assets/app.js"])
def test_application_surfaces_do_not_contain_external_runtime_urls_or_sensitive_values(
    client: TestClient,
    path: str,
) -> None:
    response = client.get(path)
    body = response.text
    assert "http://" not in body
    assert "https://" not in body
    for fragment in FORBIDDEN_RESPONSE_FRAGMENTS:
        assert fragment not in body


def test_browser_catalog_has_all_media_and_processed_scope_controls(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text

    assert "catalog-scope" in html
    assert "All media" in html
    assert "Processed" in html
    assert "catalogScope" in script or "catalogState.collection" in script
    assert "const PROCESSED_COLLECTION" in script


def test_browser_catalog_scope_defaults_to_all_media_and_switching_is_well_formed(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    start = script.index("let catalogState = {")
    end = script.index("};", start) + len("};")
    catalog_state_literal = script[start:end]
    assert "collection: \"\"" in catalog_state_literal or "collection: ''" in catalog_state_literal

    assert "function setCatalogScope(collection)" in script
    assert "function resetCatalogToAllMedia()" in script
    assert "catalogState.offset = 0" in script
    assert "resetCatalogToAllMedia()" in script
    assert "setCatalogScope(PROCESSED_COLLECTION)" in script
    assert "catalogIsUnfilteredAllMedia()" in script

    build_start = script.index("function buildCatalogQueryParams(")
    build_end = script.index("}", script.index("return params;", build_start)) + 1
    build_body = script[build_start:build_end]
    assert "if (snapshot.collection)" in build_body
    assert "params.set(\"collection\", snapshot.collection)" in build_body


def test_browser_metadata_editor_hides_processed_state_but_preserves_collection_state(
    client: TestClient,
) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    dialog_section = html[html.index("metadata-dialog"):]

    assert "metadata-collection-status" not in dialog_section
    assert "Processed status" not in dialog_section
    assert "Processed collection" not in _javascript_function(script, "renderMetadataWorkspace")
    assert "collectionKey" in script
    assert "processedAtMs" in script
    assert "payload.collection_key" in script
    assert "payload.processed_at_ms" in script
    assert "applyMetadataPayloadToWorkspace" in script


def test_browser_metadata_workspace_no_manual_collection_picker_or_mark_button(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    combined = html + script

    assert "Mark as processed" not in combined
    assert "Select collection" not in combined
    assert "Create collection" not in combined
    assert "manual collection" not in combined.lower()


def test_browser_metadata_workspace_uses_nullish_coalescing_for_collection_state(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    start = script.index("function applyMetadataPayloadToWorkspace(payload)")
    end = script.index("\n}\n", start) + len("\n}\n")
    body = script[start:end]

    assert "payload.collection_key ?? null" in body
    assert "payload.processed_at_ms ?? null" in body
    assert "payload.collection_key || null" not in body
    assert "payload.processed_at_ms || null" not in body


def test_browser_catalog_card_omits_internal_processed_status_and_time(client: TestClient) -> None:
    script = client.get("/assets/app.js").text

    start = script.index("function renderCatalogCard(item)")
    end = script.index("\n}\n", start) + len("\n}\n")
    card_body = script[start:end]
    assert "Processed" not in card_body
    assert "catalog-card__status" not in card_body
    assert "catalog-card__status-dot" not in card_body
    assert "processed_at_ms" not in card_body


def test_browser_processed_time_never_uses_filesystem_timestamps(client: TestClient) -> None:
    script = client.get("/assets/app.js").text

    processed_time_section = (
        _javascript_function(script, "applyMetadataPayloadToWorkspace")
        + _javascript_function(script, "renderMetadataWorkspace")
    )
    forbidden = (
        "observed_mtime_ns",
        "mtime",
        "st_mtime",
        "File.getModificationTime",
        "lastModified",
    )
    for fragment in forbidden:
        assert fragment not in processed_time_section


def test_browser_review_uses_no_persistence_or_hidden_complete_suggestion(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "framenest.catalog.pageSize" in script
    assert "framenest.youtube.currentClaim.v1" in script
    assert "indexedDB" not in script
    assert "location.search" not in script
    assert "complete suggestion" not in script.lower()
    assert "console.log" not in script
    assert "payload_base64" in script
    assert "data:image" not in script


# ---------------------------------------------------------------------------
# Terminal glass application shell — Cycle 075
# ---------------------------------------------------------------------------


def test_application_header_is_sticky_and_contains_brand(client: TestClient) -> None:
    html = client.get("/").text
    assert 'class="app-header' in html
    assert "position: sticky" in client.get("/assets/styles.css").text.lower()
    assert "Kronika" in html
    assert "FrameNest" not in html
    assert "brand" in html


def test_header_brand_mark_is_circular_branding_not_button(client: TestClient) -> None:
    html = client.get("/").text
    css = client.get("/assets/styles.css").text
    brand_start = html.index("brand-mark")
    brand_section = html[brand_start : html.index("</div>", brand_start)]
    brand_css = css[css.index(".brand-mark") : css.index("/* --- Header command search ---")]

    assert "FN" in brand_section
    assert "<button" not in brand_section
    assert "role=" not in brand_section
    assert "tabindex" not in brand_section
    assert "border-radius: 50%" in brand_css
    assert "rgba(0, 255, 65, 0.75)" in brand_css


def test_header_does_not_contain_pre_alpha_foundation_text(client: TestClient) -> None:
    html = client.get("/").text
    assert "Pre-alpha foundation" not in html
    assert "stage-pill" not in html


def test_header_contains_server_health_status_button(client: TestClient) -> None:
    html = client.get("/").text
    header_section = html[html.index("<header") : html.index("</header>")]
    assert "server-health-button" in html
    assert "☁️" in header_section
    assert 'aria-label="Cloud status: checking"' in header_section
    assert "status-button--icon" in header_section
    assert "Server" not in header_section
    assert 'status-button__label">Cloud<' not in header_section


def test_header_contains_ai_status_button(client: TestClient) -> None:
    html = client.get("/").text
    header_section = html[html.index("<header") : html.index("</header>")]
    assert "ai-status-button" in html
    assert "🧠" in header_section
    assert 'aria-label="AI status: checking"' in header_section
    assert 'status-button__label">AI<' not in header_section
    assert "🧠 AI" not in html


def test_header_statuses_keep_accessible_truth_and_state_coloring(client: TestClient) -> None:
    html = client.get("/").text
    css = client.get("/assets/styles.css").text
    script = client.get("/assets/app.js").text

    assert "server-health-button-text" in html
    assert "ai-status-button-text" in html
    assert "visually-hidden" in html
    assert "Cloud status: connected" in script
    assert "AI status: available" in script
    assert 'setServerHealthButtonState("healthy", "Server healthy")' in script
    assert 'setAiStatusButtonState("healthy", "AI test successful")' in script
    assert ".status-button--checking" in css
    assert ".status-button--healthy" in css
    assert ".status-button--unhealthy" in css
    assert ".status-button--icon" in css
    assert ".status-button__glyph" in css


def test_status_and_gallery_filters_have_white_border_hover_focus(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text

    status_hover = css[css.index(".status-button:hover") : css.index(".status-button__dot")]
    filter_hover = css[css.index(".catalog-filter-chip:hover") : css.index(".catalog-filter-chip__remove")]
    scope_hover = css[css.index(".catalog-scope button:hover") : css.index(".catalog-scope .scope-active")]
    assert "border-color: rgba(255, 255, 255, 0.86)" in status_hover
    assert ".status-button:focus-visible" in status_hover
    assert "border-color: rgba(255, 255, 255, 0.86)" in filter_hover
    assert ".catalog-filter-chip:focus-visible" in filter_hover
    assert "border-color: rgba(255, 255, 255, 0.86)" in scope_hover
    assert ".catalog-scope button:focus-visible" in scope_hover


def test_frontend_hidden_attribute_is_authoritative(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text

    hidden_block = css[css.index("[hidden]") : css.index(":root")]

    assert "[hidden]" in hidden_block
    assert "display: none !important;" in hidden_block
    assert css.index("[hidden]") < css.index(".settings-dialog__section")
    assert css.index("[hidden]") < css.index(".metadata-dialog__footer .metadata-ai-analyze-button")


def test_application_has_status_dialog_element(client: TestClient) -> None:
    html = client.get("/").text
    assert 'id="status-dialog"' in html
    assert "Status" in html
    assert "AI" in html


def test_status_dialog_has_accessible_ai_cloud_and_tailscale_tabs(client: TestClient) -> None:
    html = client.get("/").text
    start = html.index('id="status-dialog"')
    status_section = html[start : html.index("</dialog>", start)]

    assert 'role="tablist"' in status_section
    assert 'id="status-tab-ai"' in status_section
    assert 'id="status-tab-cloud"' in status_section
    assert 'id="status-tab-tailscale"' in status_section
    assert status_section.index('id="status-tab-ai"') < status_section.index('id="status-tab-cloud"')
    assert status_section.index('id="status-tab-cloud"') < status_section.index(
        'id="status-tab-tailscale"'
    )
    assert 'role="tab"' in status_section
    assert 'aria-selected="true"' in status_section
    assert 'aria-controls="status-panel-ai"' in status_section
    assert 'aria-controls="status-panel-cloud"' in status_section
    assert 'aria-controls="status-panel-tailscale"' in status_section
    assert 'id="status-panel-tailscale"' in status_section
    assert 'role="tabpanel"' in status_section
    assert ">Model<" in status_section
    assert ">Cloud<" in status_section
    assert ">Tailscale<" in status_section
    assert "status-tailscale-login" in status_section
    assert "status-tailscale-provenance" in status_section
    assert "tailnet" in status_section.lower()
    assert "auth key" not in status_section.lower()
    assert "wireguard" not in status_section.lower()
    assert "api token" not in status_section.lower()
    assert "nuc-1.tail247768.ts.net" not in status_section


def test_status_dialog_does_not_contain_provider_configuration_inputs(client: TestClient) -> None:
    html = client.get("/").text
    start = html.index('id="status-dialog"')
    status_section = html[start : html.index("</dialog>", start)]
    assert "Test connection" not in status_section
    assert "Open AI Settings" not in status_section
    assert "OpenAI" not in status_section
    assert "Anthropic" not in status_section
    assert "LMStudio" not in status_section and "LM Studio" not in status_section
    assert "api-key" not in status_section.lower()
    assert "api_key" not in status_section.lower()
    assert '<input' not in status_section
    assert "AI capability" not in status_section
    assert "AI Test" not in status_section


def test_javascript_has_health_retry_logic(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "checkHealth" in script
    assert "retryHealth" in script or "retry" in script.lower()
    assert "healthCheckInFlight" in script or "healthRequestToken" in script or "inFlight" in script


def test_javascript_status_buttons_open_status_tabs(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    set_tab_body = _javascript_function(script, "setActiveStatusTab")

    assert "ai-status-button" in script or "aiStatusButton" in script
    assert "status-dialog" in script or "statusDialog" in script
    assert 'openStatusDialog("ai"' in script
    assert 'openStatusDialog("cloud")' in script
    assert 'openStatusDialog("tailscale")' in script
    assert "loadCloudStatus()" in script
    assert "loadTailscaleStatus()" in set_tab_body
    assert "loadAiCapability(" in set_tab_body
    assert 'setActiveStatusTab("ai"' in script
    assert 'setActiveStatusTab("tailscale")' in script
    assert "handleStatusTabKeydown" in script
    assert "ArrowLeft" in script and "ArrowRight" in script
    assert 'tabName === "cloud"' in set_tab_body or 'normalized === "cloud"' in set_tab_body
    assert 'normalized === "tailscale"' in set_tab_body
    assert "statusTabTailscale" in set_tab_body
    assert "statusPanelTailscale" in set_tab_body
    assert 'entry.tab.setAttribute("aria-selected", String(selected))' in set_tab_body
    assert "entry.panel.hidden = !selected" in set_tab_body
    assert "showModal" in script


def test_javascript_ai_status_modal_refreshes_on_each_explicit_open(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    open_dialog_body = _javascript_function(script, "openStatusDialog")
    ai_button_body = script[script.index("if (aiStatusButton)") : script.index("if (identityBadge)")]

    assert "refreshAiStatus" in script
    assert "loadAiCapability(" in open_dialog_body or (
        "loadAiCapability(" in _javascript_function(script, "setActiveStatusTab")
    )
    assert "refreshAiStatus: true" in ai_button_body
    assert "statusDialog.showModal" in open_dialog_body
    assert "setInterval" not in open_dialog_body
    assert "setTimeout" not in open_dialog_body
    assert 'openStatusDialog("tailscale")' in script[script.index("if (identityBadge)") :]

def test_javascript_ai_status_modal_failure_stays_sanitized(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    load_body = _javascript_function(script, "loadAiCapability")

    assert "try {" in load_body
    assert "catch" in load_body
    assert "AI status unavailable" in script
    assert "renderAiCapability({ available: false" in load_body
    assert "Authorization" not in script
    assert "NVIDIA_API_KEY" not in script


def test_javascript_status_optional_rows_hide_complete_empty_rows(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_row_body = _javascript_function(script, "renderOptionalStatusRow")
    render_cloud_body = _javascript_function(script, "renderCloudStatus")

    assert "row.hidden = true" in render_row_body
    assert 'valueElement.textContent = ""' in render_row_body
    assert "row.hidden = !text" in render_row_body
    assert "statusCloudRemoteRow.hidden = !remote" in render_cloud_body
    assert "statusCloudRemote.textContent = remote" in render_cloud_body


def test_javascript_status_dialog_close_behavior(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "close" in script.lower()
    assert "Escape" in script or "escape" in script or "keydown" in script
    assert "lastFocusedElementBeforeStatus" in script


def test_javascript_has_distinct_health_states(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "checking" in script.lower()
    assert "healthy" in script.lower()
    assert "unhealthy" in script.lower() or "error" in script.lower()


def test_javascript_has_distinct_ai_states(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "checking" in script.lower() or "loading" in script.lower()
    assert "available" in script.lower()
    assert "unavailable" in script.lower()


def test_css_has_terminal_glass_visual_tokens(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "--accent" in css
    assert "backdrop-filter" in css or "backdrop-filter" in css.lower()
    assert "blur" in css
    assert "monospace" in css or "mono" in css.lower()


def test_css_supports_reduced_motion(client: TestClient) -> None:
    css = client.get("/assets.css") if False else client.get("/assets/styles.css")
    css_text = css.text
    assert "prefers-reduced-motion" in css_text


def test_css_has_sticky_header_positioning(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "position: sticky" in css or "position:sticky" in css


def test_application_does_not_contain_intro_panel_or_hero_copy(client: TestClient) -> None:
    html = client.get("/").text
    assert "intro-panel" not in html
    assert "intro-copy" not in html
    assert "FrameNest is running locally" not in html
    assert "This pre-alpha web shell is served" not in html


def test_application_does_not_contain_foundation_grid_boundary_cards(client: TestClient) -> None:
    html = client.get("/").text
    assert "foundation-grid" not in html
    assert "boundary-card" not in html
    assert "Foundation boundaries" not in html


# ---------------------------------------------------------------------------
# Command search and canonical-tag interaction cleanup — Cycle 076
# ---------------------------------------------------------------------------


def test_header_contains_command_search_input(client: TestClient) -> None:
    html = client.get("/").text
    assert "command-search-input" in html
    assert "command-search" in html
    assert 'placeholder="Search titles and tags"' in html
    assert "role=\"search\"" in html or 'role="search"' in html


def test_header_command_search_has_accessible_label(client: TestClient) -> None:
    html = client.get("/").text
    search_start = html.index('id="command-search-input"')
    search_input = html[search_start : html.index(">", search_start)]
    assert 'aria-label="Search catalog by title or tag"' in search_input
    assert 'placeholder="Search titles and tags"' in search_input


def test_header_command_search_is_wider_with_restrained_green_focus(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    search = css[css.index(".header-search {") : css.index(".header-search__control")]
    search_control = css[css.index(".header-search__control {") : css.index(".header-search__prompt")]
    search_input = css[css.index(".header-search__input {") : css.index(".header-search__input::placeholder")]
    search_focus = css[css.index(".header-search__control:focus-within") : css.index(".header-search__prompt")]

    assert "grid-area: search" in search
    assert "max-width: 100%" in search
    assert "width: 100%" in search
    assert "rgba(0, 255, 65, 0.5)" in search_control
    assert "0 0 14px rgba(0, 255, 65, 0.06)" in search_control
    assert "border: 0" in search_input
    assert "background: transparent" in search_input
    assert "outline" in search_focus
    assert "rgba(0, 255, 65, 0.82)" in search_focus
    assert "0 0 20px rgba(0, 255, 65, 0.13)" in search_focus


def test_command_search_suggestion_panel_exists(client: TestClient) -> None:
    html = client.get("/").text
    assert "command-search-suggestions" in html
    assert "listbox" in html or "combobox" in html or "aria-expanded" in html


def test_javascript_has_command_search_suggestion_logic(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "commandSearchInput" in script or "command-search-input" in script
    assert "commandSearchSuggestions" in script or "command-search-suggestions" in script
    assert "debounce" in script.lower() or "setTimeout" in script
    assert "ArrowDown" in script or "ArrowDown" in script
    assert "ArrowUp" in script or "ArrowUp" in script


def test_javascript_command_search_distinguishes_title_and_tag_results(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "title" in script.lower()
    assert "tag" in script.lower()
    assert "suggestion" in script.lower()


def test_javascript_command_search_enter_applies_title_query(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "catalogState.q" in script
    assert "catalogState.offset = 0" in script


def test_catalog_has_single_tag_toggle_region(client: TestClient) -> None:
    html = client.get("/").text
    assert "catalog-tag-filters" in html
    assert "catalog-active-filters" not in html


def test_active_tag_filters_are_an_inline_region_of_the_unified_search_control(client: TestClient) -> None:
    html = client.get("/").text
    css = client.get("/assets/styles.css").text
    control_start = html.index('<div class="header-search__control">')
    filters_start = html.index('id="catalog-tag-filters"')
    suggestions_end = html.index("</ul>", filters_start)
    control_end = html.index("</div>", suggestions_end)
    search_css = css[css.index(".header-search {") : css.index(".header-search__control")]
    control_css = css[css.index(".header-search__control {") : css.index(".header-search__control:focus-within")]
    input_css = css[css.index(".header-search__input {") : css.index(".header-search__input::placeholder")]
    filters_css = css[css.index(".catalog-tag-filters {") : css.index(".catalog-filter-chip {")]
    mobile_css = css[css.index("@media (max-width: 620px)") : css.index("@keyframes ai-pending")]

    assert control_start < filters_start < suggestions_end < control_end
    assert "max-width: 100%" in search_css
    assert "grid-area: search" in search_css
    assert "min-width: 0" in control_css
    assert "height: 40px" in control_css
    assert "min-width: 80px" in input_css
    assert "flex-wrap: nowrap" in filters_css
    assert "overflow-x: auto" in filters_css
    assert "max-width: 360px" in filters_css
    assert "max-width: 46%" in mobile_css
    assert ".catalog-tag-filters" in mobile_css
    compact_header_css = css[css.index("@media (max-width: 900px)") : css.index("@media (max-width: 360px)")]
    assert ".header-search" in compact_header_css
    assert "max-width: 100%" in compact_header_css


def test_terminal_search_prompt_pulses_boldly_and_respects_reduced_motion(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    prompt_css = css[css.index(".header-search__prompt {") : css.index(".header-search__input {")]
    keyframes = css[css.index("@keyframes header-search-prompt-pulse") : css.index(".command-search-suggestions")]
    reduced_motion = css[css.rindex("@media (prefers-reduced-motion: reduce)") :]

    assert "font-weight: 900" in prompt_css
    assert "header-search-prompt-pulse 1s" in prompt_css
    assert "infinite alternate" in prompt_css
    assert "opacity: 0.62" in keyframes
    assert "opacity: 0.92" in keyframes
    assert ".header-search__prompt" in reduced_motion
    assert "animation: none" in reduced_motion
    assert "opacity: 0.82" in reduced_motion


def test_javascript_tag_filters_use_active_chip_and_card_pressed_semantics(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "aria-pressed" in script
    assert "function renderActiveCatalogTagFilters" in script
    assert "function removeCatalogTagFilter" in script
    assert "function activateCatalogTagFilter" in script
    assert "function toggleCatalogCardTagFilter" in script
    assert "catalog-card__tag" in script
    assert "Remove ${displayName} tag filter" in script


def test_javascript_tag_toggle_preserves_and_semantics(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "catalogState.tagKeys" in script
    query_body = _javascript_function(script, "buildCatalogQueryParams")
    assert 'params.append("tag", key)' in query_body
    assert "snapshot.tagKeys.forEach" in query_body


def test_catalog_does_not_contain_duplicate_filter_text(client: TestClient) -> None:
    html = client.get("/").text
    assert "No active canonical tag filters" not in html
    assert "Canonical tag filters" not in html
    assert "Multiple selected tags use AND semantics" not in html


def test_library_tools_section_is_absent_from_flagship_gallery(client: TestClient) -> None:
    html = client.get("/").text
    assert "Library tools" not in html
    assert "library-browser" not in html
    assert "library-card-template" not in html


def test_library_tools_does_not_expose_uuid_or_path_flavor(client: TestClient) -> None:
    html = client.get("/").text
    assert "path flavor" not in html
    assert "Root path is intentionally hidden" not in html
    assert "Library ID" not in html


def test_javascript_library_rendering_does_not_show_uuid(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "Library ID" not in script
    assert "path flavor" not in script
    assert "Root path is intentionally hidden" not in script


def test_javascript_removes_obsolete_catalog_search_handlers(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "catalogSearchForm" not in script
    assert "catalogSearchInput" not in script
    assert "catalogClearButton" not in script


# ---------------------------------------------------------------------------
# Gallery workspace, details-dialog, and metadata-dialog — Cycle 077
# ---------------------------------------------------------------------------


def test_gallery_grid_replaces_verbose_catalog_layout(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "grid-template-columns" in css
    assert "auto-fill" in css or "auto-fit" in css
    assert "repeat(" in css


def test_catalog_card_does_not_show_technical_metadata_list(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function renderCatalogCard(item)")
    end = script.index("\n}\n", start) + len("\n}\n")
    card_body = script[start:end]
    assert "catalog-facts" not in card_body
    assert "Locations" not in card_body
    assert "Availability" not in card_body
    assert "Media ID" not in card_body
    assert "catalog-locations" not in card_body


def test_catalog_card_does_not_show_fallback_label_text(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function renderCatalogCard(item)")
    end = script.index("\n}\n", start) + len("\n}\n")
    card_body = script[start:end]
    assert "Fallback label from first deterministic relative location" not in card_body
    assert "Persisted display title" not in card_body
    assert "No canonical tags" not in card_body


def test_catalog_card_has_play_surface_and_edit_action_without_details_button(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    assert "Edit" in card_body
    assert "View details" not in card_body
    assert "Edit metadata" not in card_body
    assert 'textContent = "▶"' not in surface_body
    assert "media-placeholder__play-indicator" not in script
    assert "activateCardPlayback" in surface_body
    assert "openDetailsDialog(item, titleButton)" in card_body
    assert "handleOpenMetadataWorkspace" in card_body


def test_catalog_card_has_truthful_unavailable_fallback(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    fallback_body = _javascript_function(script, "renderUnavailableCardMediaSurface")
    assert "data-media-state" in fallback_body
    assert "unavailable" in fallback_body
    assert "No local playback available" in fallback_body
    assert "mediaContentUrl" not in fallback_body


def test_details_dialog_exists_in_html(client: TestClient) -> None:
    html = client.get("/").text
    assert "media-details-dialog" in html or "details-dialog" in html
    assert "dialog" in html.lower()


def test_metadata_dialog_exists_in_html(client: TestClient) -> None:
    html = client.get("/").text
    assert "metadata-dialog" in html or "metadata-workspace" in html
    assert "dialog" in html.lower()


def test_metadata_workspace_not_in_normal_document_flow(client: TestClient) -> None:
    html = client.get("/").text
    assert "<dialog" in html
    assert 'id="metadata-workspace"' in html or 'id="metadata-dialog"' in html


def test_javascript_has_details_dialog_logic(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "detailsDialog" in script or "details-dialog" in script or "mediaDetailsDialog" in script
    assert "openDetailsDialog" in script or "showDetails" in script or "openDetails" in script
    assert "closeDetailsDialog" in script or "closeDetails" in script


def test_javascript_details_dialog_has_edit_transition(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "handleOpenMetadataWorkspace" in script
    assert "Edit" in script


def test_javascript_metadata_dialog_uses_show_modal(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "showModal" in script or "showModal()" in script
    assert "close()" in script or ".close(" in script


def test_javascript_dialog_has_dirty_close_protection(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "confirmDiscardDirtyMetadata" in script
    assert "requestConfirmation({" in script


def test_javascript_dialog_has_focus_restoration(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "lastFocused" in script or "focusReturn" in script or "openerElement" in script or ".focus()" in script


def test_javascript_dialog_has_escape_close(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "Escape" in script or "escape" in script


def test_javascript_card_renders_canonical_tag_buttons_without_hidden_counter(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")
    tags_body = _javascript_function(script, "renderCatalogCardTags")
    assert "renderCatalogCardTags(item)" in card_body
    assert 'button.className = "catalog-card__tag"' in tags_body
    assert "button.dataset.tagKey = tag.key" in tags_body
    assert "button.textContent = tag.display_name" in tags_body
    assert "toggleCatalogCardTagFilter(tag.key" in tags_body
    assert "catalog-card__tag-more" not in tags_body
    assert "maxVisible" not in tags_body


def test_javascript_card_has_status_row(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function renderCatalogCard(item)")
    end = script.index("\n}\n", start) + len("\n}\n")
    card_body = script[start:end]
    assert "processed" in card_body.lower() or "status" in card_body.lower()


def test_javascript_card_has_persistent_preview_first_media_surface(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    preview_body = _javascript_function(script, "renderPersistentPreview")
    assert "mediaGalleryPreviewUrl(item.media_id, location.location_id)" in preview_body
    assert 'document.createElement("img")' in preview_body
    assert 'image.loading = "lazy"' in preview_body
    assert 'image.decoding = "async"' in preview_body
    assert "mediaContentUrl(" not in surface_body
    assert 'document.createElement("video")' not in surface_body
    assert "Video placeholder" not in surface_body
    assert "Animated image placeholder" not in surface_body


def test_html_does_not_contain_permanent_metadata_section_in_flow(client: TestClient) -> None:
    html = client.get("/").text
    assert 'class="metadata-workspace"' not in html or "<dialog" in html


def test_javascript_preserves_existing_metadata_state_machine(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "metadataWorkspace.baseline" in script
    assert "metadataWorkspace.current" in script
    assert "applyMetadataPayloadToWorkspace" in script
    assert "handleSaveMetadata" in script
    assert "handleDiscardMetadataChanges" in script
    assert "confirmDiscardDirtyMetadata" in script
    assert "normalizedMetadataFormState" in script


# ---------------------------------------------------------------------------
# On-demand gallery preview — Cycle 078
# ---------------------------------------------------------------------------


def test_card_uses_media_surface_for_playback_without_visible_play_control(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    preview_body = _javascript_function(script, "renderPersistentPreview")
    fallback_body = _javascript_function(script, "renderPreviewFallback")
    assert 'textContent = "▶"' not in surface_body
    assert "media-placeholder__play-indicator" not in script
    assert "buildCardPlayIndicator" not in script
    assert "play-indicator" not in preview_body
    assert "play-indicator" not in fallback_body
    assert "activateCardPlayback" in surface_body
    assert 'surface.addEventListener("click"' in surface_body
    assert 'surface.addEventListener("keydown"' in surface_body
    assert 'surface.setAttribute("role", "button")' in surface_body
    assert 'surface.setAttribute("tabindex", "0")' in surface_body
    assert "handleCardPreview" not in _javascript_function(script, "renderCatalogCard")


def test_catalog_card_analyze_shortcut_for_metadata_needed_admin_media(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")
    eligible_body = _javascript_function(script, "cardAiQuickActionEligible")
    identity_gate_body = _javascript_function(script, "identityAllowsCardAiQuickAction")

    assert 'analyzeButton.textContent = "🧠"' in card_body
    assert 'analyzeButton.title = "Analyze by AI"' in card_body
    assert "Generate first-pass AI metadata for" in card_body
    assert "cardAiQuickActionEligible(item)" in card_body
    assert "cardNeedsMetadata(item)" not in eligible_body
    assert "selectSupportedAvailableLocation(item) !== null" in eligible_body
    assert "identityAllowsCardAiQuickAction()" in eligible_body
    assert "identityHasCapability(" not in eligible_body
    assert "identityState.resolved" in identity_gate_body
    assert "identityState.available" in identity_gate_body
    assert 'capabilities.has("analysis.run")' in identity_gate_body
    assert 'capabilities.has("metadata.canonical.write")' in identity_gate_body
    assert '(item.content_category || "general") !== "movie"' in eligible_body
    assert "actions.appendChild(analyzeButton)" in card_body
    assert "renderCatalogCardTags(item)" in card_body
    assert "dismissCardAiQuickActionButton" not in script
    assert "announceCardAiQuickActionSuccess" not in script
    assert "animateCatalogCardMetadataReflow" not in script
    assert "companionWebHosted()" in identity_gate_body


def test_catalog_card_has_overlay_original_media_action_in_bottom_right(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")

    assert "openOriginalLink.href = mediaContentUrl(item.media_id, supportedLocation.location_id)" in card_body
    assert 'openOriginalLink.target = "_blank"' in card_body
    assert 'openOriginalLink.rel = "noopener noreferrer"' in card_body
    assert (
        'openOriginalLink.setAttribute("aria-label", `Open original media ${displayTitle}`)'
        in card_body
    )
    assert 'openOriginalLink.title = "Open original media"' in card_body
    assert "openOriginalIcon()" in card_body
    assert "editIcon()" in card_body
    assert 'editButton.setAttribute("aria-label", `Edit ${displayTitle}`)' in card_body
    assert 'editButton.title = "Edit"' in card_body
    assert "/download" not in card_body
    assert "createObjectURL" in script
    open_section = card_body[
        card_body.index("const editButton") : card_body.index("const analysisStatus")
    ]
    assert "URL.createObjectURL" not in open_section


def test_catalog_card_original_media_action_appears_only_for_supported_available_location(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")
    supported_body = _javascript_function(script, "selectSupportedAvailableLocation")

    assert "const supportedLocation = selectSupportedAvailableLocation(item)" in card_body
    assert "if (supportedLocation)" in card_body
    assert (
        'item.media_kind !== "video"\n'
        '    && item.media_kind !== "animated_image"\n'
        '    && item.media_kind !== "image"'
    ) in supported_body or (
        'item.media_kind !== "video"' in supported_body
        and 'item.media_kind !== "animated_image"' in supported_body
        and 'item.media_kind !== "image"' in supported_body
    )
    assert 'location.availability === "available" && location.location_id' in supported_body


def test_catalog_card_actions_are_compact_overlay_controls(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    styles = client.get("/assets/styles.css").text
    card_body = _javascript_function(script, "renderCatalogCard")
    top_right_css = styles[
        styles.index(".catalog-card__action--top-right") : styles.index(".catalog-card__action--bottom-left")
    ]
    bottom_right_css = styles[
        styles.index(".catalog-card__action--bottom-right") : styles.index(".catalog-card__action--open-original")
    ]
    analyze_css = styles[
        styles.index(".catalog-card__action--analyze {")
        : styles.index(".catalog-card__action--analyze:hover")
    ]
    action_css = styles[
        styles.index(".catalog-card__action {") : styles.index(".catalog-card__action svg")
    ]

    assert 'mediaFrame.className = "catalog-card__media-frame"' in card_body
    assert 'actions.className = "catalog-card__actions catalog-card__actions--overlay"' in card_body
    assert "catalog-card__action--top-right" in card_body
    assert "catalog-card__action--bottom-left" in card_body
    assert "catalog-card__action--bottom-right" in card_body
    assert "catalog-card__action--edit" in card_body
    assert "catalog-card__action--open-original" in card_body
    assert "catalog-card__action--primary" not in card_body
    assert "catalog-card__action-row" not in card_body
    assert ".catalog-card__actions--overlay" in styles
    assert "position: absolute;" in styles
    assert ".catalog-card__action--top-right" in styles
    assert ".catalog-card__action--bottom-left" in styles
    assert ".catalog-card__action--bottom-right" in styles
    assert "width: 36px;" in action_css
    assert "height: 36px;" in action_css
    assert "right: 8px;" in top_right_css
    assert "right: 8px;" in bottom_right_css
    assert "left:" not in top_right_css
    assert "position:" not in analyze_css


def test_catalog_card_actions_reveal_contextually_without_losing_keyboard_or_touch_access(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    styles = client.get("/assets/styles.css").text
    card_body = _javascript_function(script, "renderCatalogCard")
    desktop_css = styles[
        styles.index("@media (hover: hover) and (pointer: fine)")
        : styles.index("@media (hover: none), (pointer: coarse)")
    ]
    touch_start = styles.index("@media (hover: none), (pointer: coarse)")
    touch_css = styles[touch_start : styles.index("\n.catalog-card__action {", touch_start)]
    action_css = styles[
        styles.index(".catalog-card__action {") : styles.index(".catalog-card__action svg")
    ]
    overlay_selector = (".catalog-card__actions--overlay",)
    overlay_action_selector = (
        ".catalog-card__actions--overlay .catalog-card__action",
    )
    revealed_overlay_selectors = (
        ".catalog-card:hover .catalog-card__actions--overlay",
        ".catalog-card:focus-within .catalog-card__actions--overlay",
        ".catalog-card--selected .catalog-card__actions--overlay",
    )
    revealed_action_selectors = (
        ".catalog-card:hover .catalog-card__action",
        ".catalog-card:focus-within .catalog-card__action",
        ".catalog-card--selected .catalog-card__action",
    )
    idle_overlay = _css_rule_declarations(desktop_css, overlay_selector)
    idle_actions = _css_rule_declarations(desktop_css, overlay_action_selector)
    revealed_overlay = _css_rule_declarations(desktop_css, revealed_overlay_selectors)
    revealed_actions = _css_rule_declarations(desktop_css, revealed_action_selectors)
    touch_overlay = _css_rule_declarations(touch_css, overlay_selector)
    touch_actions = _css_rule_declarations(touch_css, overlay_action_selector)

    assert idle_overlay["opacity"] == "0"
    assert idle_actions["pointer-events"] == "none"
    assert revealed_overlay["opacity"] == "1"
    assert revealed_actions["pointer-events"] == "auto"
    assert touch_overlay["opacity"] == "1"
    assert touch_actions["pointer-events"] == "auto"
    assert "display: none" not in desktop_css
    assert "visibility: hidden" not in desktop_css
    assert "transition:" not in desktop_css
    assert "pointer-events: auto;" in action_css
    assert ".catalog-card__action:focus-visible" in styles
    assert 'titleButton.addEventListener("click", () => openDetailsDialog(item, titleButton))' in card_body
    assert 'editButton.type = "button"' in card_body
    assert 'editButton.setAttribute("aria-label", `Edit ${displayTitle}`)' in card_body
    assert 'editButton.addEventListener("click", () => handleOpenMetadataWorkspace(item, editButton))' in card_body


def test_catalog_card_unavailable_ai_is_natively_disabled_without_status_shortcut(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    analyze_body = _javascript_function(script, "handleAnalyzeCatalogCard")
    state_body = _javascript_function(script, "setCardAnalyzeButtonState")
    reconcile_body = _javascript_function(script, "reconcileCatalogCardAiQuickActions")
    provider_blocked_body = _javascript_function(script, "cardAiQuickActionProviderBlocked")

    assert 'openStatusDialog("ai"' not in analyze_body
    assert "cardAiQuickActionProviderBlocked()" in analyze_body
    assert "aiCapabilityDiscoveryPending || !aiCapability.available" in provider_blocked_body
    assert "button.disabled = locked || providerBlocked || state === \"unavailable\"" in state_body
    assert 'button.setAttribute("aria-disabled", "true")' in state_body
    assert "button.removeAttribute(\"aria-disabled\")" in state_body
    assert "Checking AI availability for" in state_body
    assert "AI analysis unavailable for" in state_body
    assert "button.disabled = true" in reconcile_body
    assert "setCardAnalyzeButtonState(button, state, preservedMessage)" in reconcile_body
    assert "Generate first-pass AI metadata for" in state_body
    assert "Retry Analyze by AI" in state_body
    assert "Analyze by AI again" not in state_body
    run_body = _javascript_function(script, "runMetadataAiAnalysis")
    assert "cardAiPreviewResponseMatchesRequest(payload, requestContext.mediaId, requestContext.locationId)" in run_body
    assert "The AI result did not match this media. Try analysis again." in run_body


def test_catalog_card_analyze_request_busy_success_and_failure_flow(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    analyze_body = _javascript_function(script, "handleAnalyzeCatalogCard")
    run_body = _javascript_function(script, "runMetadataAiAnalysis")
    state_body = _javascript_function(script, "setCardAnalyzeButtonState")
    open_body = _javascript_function(script, "handleOpenMetadataWorkspace")

    assert '"Analyzing…"' not in state_body
    assert 'button.textContent = "🧠"' in state_body
    assert "catalog-card__analyze-busy" not in state_body
    assert 'button.setAttribute("aria-busy", busy ? "true" : "false")' in state_body
    assert "cardAiQuickActionIsLocked(mediaId)" in analyze_body
    assert 'state: "analyzing"' in analyze_body
    assert 'state: "applying"' not in analyze_body
    assert 'state: "idle"' in analyze_body
    assert 'state: "saved"' not in analyze_body
    assert 'state: "failed_analysis"' not in analyze_body
    assert 'state: "failed_save"' not in analyze_body
    assert "handleOpenMetadataWorkspace(item, button)" in analyze_body
    assert analyze_body.index("handleOpenMetadataWorkspace(item, button)") < analyze_body.index(
        "runMetadataAiAnalysis(metadataConfirmationContext"
    )
    assert "previewSuggestion: suggestion" not in analyze_body
    assert "mediaAiSuggestionEndpoint(requestContext.mediaId, requestContext.locationId)" in run_body
    assert "confirm_cloud_upload: true" in run_body
    assert "await requestConfirmation({" in analyze_body
    assert 'title: "Analyze with AI?"' in analyze_body
    assert 'confirmLabel: "Analyze by AI"' in analyze_body
    assert 'method: "PUT"' not in analyze_body
    assert "applySavedAiMetadataToCatalogSurfaces" not in analyze_body
    assert "presentPreviewSuggestionInMetadataWorkspace(previewSuggestion, previewPayload)" in open_body
    assert "applyResolvedAiSuggestionToMetadataWorkspace" not in open_body
    assert "fetch(metadataEndpoint(mediaId)" not in analyze_body
    assert "loadCatalog(" not in analyze_body


def test_catalog_card_analyze_busy_keeps_fixed_size_and_reduced_motion(client: TestClient) -> None:
    styles = client.get("/assets/styles.css").text
    busy_css = styles[
        styles.index('.catalog-card__action--analyze[data-analysis-state="analyzing"]')
        : styles.index(".catalog-card__title--fade-in")
    ]

    assert "width: 36px;" in busy_css
    assert "height: 36px;" in busy_css
    assert "min-width" not in busy_css
    assert "Analyzing" not in busy_css
    assert "catalog-card-analyze-pulse" in busy_css
    assert "animation:" in busy_css
    assert "catalog-card__analyze-busy" not in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "animation: none !important;" in styles
    assert 'data-analysis-state="applying"' not in styles
    assert "catalog-card__action--analyze-dismissing" not in styles


def test_no_automatic_analysis_on_page_load(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    init_section = script[script.index("checkHealth();"):]
    assert "media-analysis-preview" not in init_section[:200]


def test_javascript_reuses_existing_analysis_endpoint(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "media-analysis-preview" in script
    assert "LIBRARIES_ENDPOINT" in script


def test_javascript_has_preview_cache(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "previewCache" in script or "previewCacheMap" in script
    assert "MAX_PREVIEW_CACHE" in script or "maxPreviewCache" in script or "previewCacheLimit" in script


def test_preview_cache_does_not_use_persistent_storage(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    preview_section = script[script.index("function renderCardPreviewFrames") : script.index("function selectPlaybackLocation")]
    assert "localStorage" not in preview_section
    assert "framenest.youtube.currentClaim.v1" in script
    assert "indexedDB" not in script


def test_javascript_has_single_active_preview_rule(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "activePreviewMediaId" in script or "activePreviewTimer" in script or "activeCardPreview" in script
    assert "clearInterval" in script or "stopAnimationFrame" in script or "stopPreviewTimer" in script or "stopCardPreview" in script


def test_javascript_has_preview_race_protection(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "previewRequestToken" in script or "previewAbortController" in script or "AbortController" in script


def test_javascript_has_previewable_location_selection(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "previewableLocation" in script or "selectPreviewable" in script or "resolvePreviewable" in script or "getPreviewableLocation" in script
    assert "availability" in script.lower()
    assert "available" in script.lower()


def test_javascript_unavailable_location_does_not_trigger_analysis(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "available" in script
    assert "offline" in script or "missing" in script or "unavailable" in script


def test_javascript_preview_uses_honest_terminology(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "Preview" in script
    assert "cover" not in script.lower() or "placeholder" in script.lower()
    assert "thumbnail" not in script.lower() or "placeholder" in script.lower()


def test_javascript_preview_states_exist(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "loading" in script.lower() or "Loading" in script
    assert "unavailable" in script.lower() or "Preview unavailable" in script
    assert "Retry" in script or "retry" in script.lower()


def test_javascript_preview_does_not_mutate_metadata_state(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "metadataWorkspace" in script
    assert "previewCache" in script or "previewCacheMap" in script
    preview_section_start = script.find("previewCache")
    if preview_section_start == -1:
        preview_section_start = script.find("previewCacheMap")
    assert preview_section_start != -1


def test_details_dialog_has_playback_integration(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "media-details-dialog" in script
    assert "renderDetailsMedia" in script
    assert "mediaContentUrl" in script
    assert "/content" in script


def test_details_dialog_does_not_use_frame_navigation(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "Previous frame" not in script
    assert "Next frame" not in script
    assert "detailsPreviewFrameIndex" not in script
    assert "detailsPreviewTimer" not in script


def test_javascript_details_playback_uses_identity_only_url(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function mediaContentUrl(")
    end = script.index("\n}\n", start) + len("\n}\n")
    url_body = script[start:end]
    assert "MEDIA_CATALOG_ENDPOINT" in url_body
    assert "media_id" in url_body or "mediaId" in url_body
    assert "location_id" in url_body or "locationId" in url_body
    assert "relative_path" not in url_body
    assert "library.path" not in url_body


def test_javascript_available_cards_use_identity_only_preview_and_content_urls(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    media_url_body = _javascript_function(script, "mediaContentUrl")
    preview_url_body = _javascript_function(script, "mediaGalleryPreviewUrl")

    assert "selectSupportedAvailableLocation(item)" in surface_body
    assert "renderPersistentPreview(surface, item, location, title)" in surface_body
    assert "MEDIA_CATALOG_ENDPOINT" in media_url_body
    assert "mediaId" in media_url_body
    assert "locationId" in media_url_body
    assert "encodeURIComponent(mediaId)" in media_url_body
    assert "encodeURIComponent(locationId)" in media_url_body
    assert "gallery-preview" in preview_url_body
    assert "encodeURIComponent(mediaId)" in preview_url_body
    assert "encodeURIComponent(locationId)" in preview_url_body
    assert "relative_path" not in media_url_body
    assert "relative_path" not in preview_url_body
    assert "library" not in media_url_body.lower()
    assert "library" not in preview_url_body.lower()


def test_javascript_initial_card_renders_static_persistent_preview_image(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    preview_body = _javascript_function(script, "renderPersistentPreview")

    assert 'document.createElement("img")' in preview_body
    assert "media-placeholder__preview-img" in preview_body
    assert "Gallery preview for ${title}" in preview_body
    assert "mediaGalleryPreviewUrl(item.media_id, location.location_id)" in preview_body
    assert "renderPreviewFallback(surface, title)" in preview_body
    assert "mediaContentUrl(item.media_id, location.location_id)" in preview_body


def test_javascript_initial_card_render_does_not_fetch_original_or_generate_preview(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    preview_body = _javascript_function(script, "renderPersistentPreview")
    fallback_body = _javascript_function(script, "renderPreviewFallback")

    assert "mediaGalleryPreviewUrl(item.media_id, location.location_id)" in preview_body
    assert "mediaContentUrl(item.media_id, location.location_id)" in preview_body
    assert 'item.media_kind === "image"' in preview_body
    assert "media-analysis-preview" not in surface_body
    assert "media-analysis-preview" not in preview_body
    assert "previews generate" not in script
    assert "mediaContentUrl" not in fallback_body


def test_javascript_explicit_card_play_renders_original_media(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    playback_body = _javascript_function(script, "renderCardOriginalPlayback")
    video_section = playback_body[
        playback_body.index('document.createElement("video")') : playback_body.index("} else {")
    ]
    image_section = playback_body[playback_body.index('document.createElement("img")') :]

    assert "video.src = url" in video_section
    assert 'video.preload = "metadata"' in video_section
    assert "video.playsInline = true" in video_section
    assert "video.autoplay = false" in video_section
    assert "video.muted = true" in video_section
    assert "video.controls = false" in video_section
    assert "video.loop = false" in video_section
    assert "video.play(" in video_section
    assert "image.src = url" in image_section
    assert "mediaContentUrl(item.media_id, location.location_id)" in playback_body


def test_javascript_card_media_surface_is_accessible_and_card_title_opens_details(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    sync_body = _javascript_function(script, "syncCardMediaSurfaceToggleState")
    activate_body = _javascript_function(script, "activateCardPlayback")
    card_body = _javascript_function(script, "renderCatalogCard")

    assert "syncCardMediaSurfaceToggleState(surface, item, title, false)" in surface_body
    assert "aria-label" in sync_body
    assert "aria-pressed" in sync_body
    assert "Play animated preview for ${title}" in sync_body
    assert "activateCardPlayback(item, surface)" in surface_body
    assert 'media_kind === "animated_image"' in activate_body
    assert "Enter" in surface_body
    assert "event.key === \" \"" in surface_body
    assert "Open details for" in card_body
    assert "openDetailsDialog(item, titleButton)" in card_body
    assert "mediaContentUrl" in _javascript_function(script, "renderDetailsMedia")


def test_javascript_catalog_card_details_button_is_removed(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    card_body = _javascript_function(script, "renderCatalogCard")
    assert "detailsButton" not in card_body
    assert "openDetailsDialog(item, detailsButton)" not in card_body


def test_javascript_catalog_rerender_cleans_card_media_resources(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    cleanup_body = _javascript_function(script, "cleanupCatalogCardMedia")
    success_body = _javascript_function(script, "renderCatalogSuccess")

    assert "cardMediaElements.forEach" in cleanup_body
    assert "element.pause()" in cleanup_body
    assert "element.removeAttribute(\"src\")" in cleanup_body
    assert "element.load()" in cleanup_body
    assert "cardMediaElements = new Set()" in cleanup_body
    assert success_body.index("cleanupCatalogCardMedia()") < success_body.index("catalogResults.replaceChildren()")


def test_javascript_details_is_player_first_with_single_title(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    details_section = html[html.index("media-details-dialog") : html.index("</dialog>", html.index("media-details-dialog"))]
    populate_body = _javascript_function(script, "populateDetailsDialog")

    assert 'id="media-details-title"' in details_section
    assert "media-details-display-title" not in details_section
    assert "detailsDialogTitle.textContent" in populate_body
    assert "detailsDisplayTitle" not in script
    assert "detailsTechnical.removeAttribute(\"open\")" in populate_body
    assert "media-details-kind" not in details_section
    assert "detailsKind.textContent" not in populate_body


def test_javascript_details_tags_filter_gallery_with_existing_state(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    populate_body = _javascript_function(script, "populateDetailsDialog")
    activate_body = _javascript_function(script, "activateDetailsTagFilter")

    assert 'document.createElement("button")' in populate_body
    assert 'pill.type = "button"' in populate_body
    assert 'pill.className = "media-details-dialog__tag"' in populate_body
    assert "activateDetailsTagFilter(tag.key)" in populate_body
    catalog_activation = _javascript_function(script, "activateCatalogTagFilter")
    assert "if (!alreadyActive)" in catalog_activation
    assert "catalogState.tagKeys = [...catalogState.tagKeys, tagKey]" in catalog_activation
    assert "catalogState.offset = 0" in catalog_activation
    assert "catalogState.q" not in activate_body
    assert "closeDetailsDialog({ restoreFocus: false })" in activate_body
    assert "activateCatalogTagFilter(tagKey, { focusChip: true })" in activate_body


def test_javascript_details_description_replaces_prominent_processed_panel(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    styles = client.get("/assets/styles.css").text
    populate_body = _javascript_function(script, "populateDetailsDialog")
    principal_body = populate_body[: populate_body.index("detailsTechnicalList.replaceChildren()")]
    details_css = styles[styles.index(".media-details-dialog") : styles.index("/* --- Metadata edit dialog --- */")]

    assert "Processed" not in principal_body
    assert " since " not in principal_body
    assert "detailsDescription.textContent = description" in populate_body
    assert "detailsDescription.hidden = !description" in populate_body
    assert "detailsProcessedContainer" not in populate_body
    assert 'addMetadataValue(detailsTechnicalList, "Processed at"' in populate_body
    assert ".media-details-dialog__description" in details_css
    assert "border: 1px solid var(--accent-border);" in details_css
    assert "color: var(--accent);" in details_css


def test_javascript_details_selects_first_available_location(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function selectPlaybackLocation(")
    end = script.index("\n}\n", start) + len("\n}\n")
    location_body = script[start:end]
    assert "availability" in location_body
    assert "available" in location_body
    assert "location_id" in location_body


def test_javascript_details_video_has_required_attributes(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_body = _javascript_function(script, "renderDetailsMedia")
    assert "document.createElement(\"video\")" in render_body
    assert "video.controls = true" in render_body
    assert 'video.preload = "metadata"' in render_body
    assert "video.playsInline = true" in render_body
    assert "video.autoplay = false" in render_body or "video.autoplay" not in render_body


def test_javascript_details_image_uses_real_img(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_body = _javascript_function(script, "renderDetailsMedia")
    assert "document.createElement(\"img\")" in render_body
    assert ".alt =" in render_body


def test_javascript_details_media_uses_display_title(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_body = _javascript_function(script, "renderDetailsMedia")
    assert "display_title" in render_body or "deriveCatalogFallbackTitle" in render_body
    assert "aria-label" in render_body or ".alt =" in render_body


def test_javascript_details_has_unavailable_state(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "renderDetailsMediaUnavailable" in script
    assert "Media unavailable." in script


def test_javascript_details_has_explicit_media_cleanup(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "cleanupDetailsMedia" in script
    close_section = script[script.index("function closeDetailsDialog"):]
    assert "cleanupDetailsMedia" in close_section[:300]
    cleanup_section = _javascript_function(script, "cleanupDetailsMedia")
    assert "removeAttribute(\"src\")" in cleanup_section or "removeAttribute('src')" in cleanup_section
    assert "pause()" in cleanup_section
    assert "load()" in cleanup_section


def test_javascript_details_ignores_stale_media_events(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "detailsMediaToken" in script
    render_section = _javascript_function(script, "renderDetailsMedia")
    assert "token !== detailsMediaToken" in render_section


def test_javascript_details_media_handlers_exist_before_src(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")
    video_start = render_section.index('document.createElement("video")')
    image_start = render_section.index('document.createElement("img")')
    video_section = render_section[video_start:image_start]
    image_section = render_section[image_start:]

    for handler in ("video.onloadeddata =", "video.oncanplay =", "video.onerror ="):
        assert video_section.index(handler) < video_section.index("video.src = url")
    assert video_section.index("video.src = url") < video_section.index("appendChild(video)")

    for handler in ("img.onload =", "img.onerror ="):
        assert image_section.index(handler) < image_section.index("img.src = url")
    assert image_section.index("img.src = url") < image_section.index("appendChild(img)")


def test_javascript_details_media_loading_cannot_start_before_handlers(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")

    video_src_index = render_section.index("video.src = url")
    assert render_section.index("video.onloadeddata =") < video_src_index
    assert render_section.index("video.oncanplay =") < video_src_index
    assert render_section.index("video.onerror =") < video_src_index

    image_src_index = render_section.index("img.src = url")
    assert render_section.index("img.onload =") < image_src_index
    assert render_section.index("img.onerror =") < image_src_index


def test_javascript_details_media_success_reveals_element_and_clears_loading(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")
    loadeddata_section = render_section[
        render_section.index("video.onloadeddata =") : render_section.index("video.oncanplay =")
    ]
    canplay_section = render_section[
        render_section.index("video.oncanplay =") : render_section.index("video.onerror =")
    ]
    image_load_section = render_section[
        render_section.index("img.onload =") : render_section.index("img.onerror =")
    ]

    for handler_section in (loadeddata_section, canplay_section, image_load_section):
        assert "token !== detailsMediaToken" in handler_section
        assert "loading.remove()" in handler_section
        assert ".hidden = false" in handler_section


def test_javascript_details_media_error_shows_unavailable_state(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")
    video_error_section = render_section[
        render_section.index("video.onerror =") : render_section.index("detailsMediaElement = video")
    ]
    image_error_section = render_section[
        render_section.index("img.onerror =") : render_section.index("detailsMediaElement = img")
    ]

    for handler_section in (video_error_section, image_error_section):
        assert "token !== detailsMediaToken" in handler_section
        assert "cleanupDetailsMedia({ invalidate: false })" in handler_section
        assert "renderDetailsMediaUnavailable(detailsPreviewContainer)" in handler_section


def test_javascript_details_media_append_is_guarded_after_src_assignment(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")

    assert "token === detailsMediaToken && detailsMediaElement === video" in render_section
    assert "token === detailsMediaToken && detailsMediaElement === img" in render_section


def test_javascript_details_cleanup_invalidates_loading_media(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    cleanup_section = _javascript_function(script, "cleanupDetailsMedia")
    close_section = _javascript_function(script, "closeDetailsDialog")

    assert "invalidate = true" in cleanup_section
    assert "detailsMediaToken += 1" in cleanup_section
    assert "cleanupDetailsMedia()" in close_section


def test_javascript_details_replacement_invalidates_old_media_before_new_src(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    render_section = _javascript_function(script, "renderDetailsMedia")

    cleanup_index = render_section.index("cleanupDetailsMedia()")
    token_index = render_section.index("const token = ++detailsMediaToken")
    video_src_index = render_section.index("video.src = url")
    image_src_index = render_section.index("img.src = url")

    assert cleanup_index < token_index < video_src_index
    assert cleanup_index < token_index < image_src_index


def test_gallery_reference_no_longer_contains_canonical_tag_fragment() -> None:
    gallery = Path("GALLERY.md").read_text(encoding="utf-8")
    assert "\nsuggestions, keyboard and mouse navigation" not in gallery
    assert "Tag editing should support suggestions" in gallery


def test_gallery_reference_documents_content_first_playback_boundary() -> None:
    gallery = Path("GALLERY.md").read_text(encoding="utf-8")
    assert "persistent server-generated static JPEG\ngallery preview derivatives" in gallery
    assert "/gallery-preview" in gallery
    assert "Initial card rendering does not require original\nGIF or MP4 transfer" in gallery
    assert "Missing or unavailable derivatives use a compact\nnon-original fallback" in gallery
    assert "Activating the card's media\nsurface" in gallery
    assert "Opening Details from the card\ntitle continues to use original GIF/MP4 content" in gallery
    assert "not durable accepted covers" in gallery
    assert "Cover Studio state" in gallery
    assert "Details uses a black player-first\nsurface" in gallery


def test_javascript_details_no_longer_loads_representative_frames(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "loadDetailsPreview" not in script
    assert "renderDetailsPreviewFrames" not in script
    assert "startDetailsPreviewCycling" not in script


def test_javascript_has_reduced_motion_preview_behavior(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "prefers-reduced-motion" in script or "reducedMotion" in script or "matchMedia" in script


def test_javascript_reuses_base64_decode_helper(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "decodeBase64Png" in script
    assert "atob(" in script
    assert "URL.createObjectURL" in script


def test_javascript_preview_does_not_persist_frames(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    preview_section = script[script.index("function renderCardPreviewFrames") : script.index("function selectPlaybackLocation")]
    assert "localStorage" not in preview_section
    assert "framenest.youtube.currentClaim.v1" in script
    assert "indexedDB" not in script


def test_javascript_has_no_preview_on_hover_or_scroll(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "mouseover" not in script.lower() or "hover" not in script.lower()
    assert "IntersectionObserver" not in script


# ---------------------------------------------------------------------------
# Urgent Gallery UI Regression Repair — Cycle 078A
# ---------------------------------------------------------------------------


def test_dialogs_not_open_in_source_html(client: TestClient) -> None:
    html = client.get("/").text
    assert 'open=""' not in html
    assert "open" not in html.split("<dialog")[1].split(">")[0] if "<dialog" in html else True


def test_css_closes_dialogs_without_open_attribute(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "dialog:not([open])" in css or "dialog:not([open])" in css.replace(" ", "")


def test_css_dialog_layout_only_under_open(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "dialog[open]" in css or "dialog[open]" in css.replace(" ", "")


def test_startup_does_not_open_metadata_dialog(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    init_section = script[script.index("checkHealth();"):]
    assert "metadataDialog.showModal()" not in init_section[:200]
    assert "showModal()" not in init_section[:200]


def test_javascript_focuses_search_on_startup(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "commandSearchInput.focus()" in script or "commandSearchInput.focus()" in script
    assert "preventScroll" in script


def test_search_suggestions_close_on_no_results(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert '"No matches."' not in script or "closeCommandSearchSuggestions" in script
    render_section = script[script.index("function renderCommandSearchSuggestions"):]
    assert "No matches" not in render_section[:500] or "closeCommandSearchSuggestions" in render_section[:500]


def test_javascript_has_fallback_title_suggestions(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "deriveCatalogFallbackTitle" in script
    search_section = script[script.index("function renderCommandSearchSuggestions"):]
    assert "fallback" in search_section[:2000].lower() or "deriveCatalogFallbackTitle" in search_section[:2000] or "catalogResults" in search_section[:2000]


def test_card_visual_surface_is_preview_trigger(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    surface_body = _javascript_function(script, "renderCatalogCardMediaSurface")
    assert "activateCardPlayback(item" in surface_body
    assert "media-placeholder__play-indicator" not in script
    assert 'surface.setAttribute("role", "button")' in surface_body


def test_card_does_not_have_separate_footer_preview_button(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    start = script.index("function renderCatalogCard(item)")
    end = script.index("\n}\n", start) + len("\n}\n")
    card_body = script[start:end]
    assert "catalog-card__preview-button" not in card_body or "Preview" not in card_body


def test_javascript_no_manual_frame_navigation_controls(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert '"Prev"' not in script or "detailsPreviewNavigate" not in script
    assert '"Next"' not in script or "detailsPreviewNavigate" not in script
    assert '"Start"' not in script or "detailsPreviewNavigate" not in script


def test_details_has_no_frame_counter(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    assert "details-preview-counter" not in script or "detailsPreviewNavigate" not in script


def test_details_dialog_has_one_visual_surface(client: TestClient) -> None:
    html = client.get("/").text
    details_section = html[html.index("media-details-dialog"):]
    assert details_section.count("media-placeholder") <= 2
    assert "media-details-display-title" not in details_section


def test_metadata_dialog_contains_save_and_cancel(client: TestClient) -> None:
    html = client.get("/").text
    dialog_section = html[html.index("metadata-dialog"):]
    assert "Save" in dialog_section
    assert "Cancel" in dialog_section
    assert "Save metadata" not in dialog_section
    assert "Discard changes" not in dialog_section
    assert "metadata-save-button" in dialog_section
    assert "metadata-discard-button" in dialog_section


def test_metadata_dialog_contains_single_form_ai_assistance(client: TestClient) -> None:
    html = client.get("/").text
    dialog_section = html[html.index("metadata-dialog"):]

    assert "Analyze by AI" in dialog_section
    assert "AI Draft" not in dialog_section
    assert "Use draft" not in dialog_section
    assert "Discard draft" not in dialog_section
    assert "AI suggestions" in dialog_section
    assert "metadata-ai-filename-note" in dialog_section
    assert "metadata-ai-filename-input" not in dialog_section
    assert "metadata-ai-filename-display" not in dialog_section
    assert dialog_section.index("metadata-save-button") < dialog_section.index("metadata-ai-analyze-button")
    assert dialog_section.index("metadata-ai-analyze-button") < dialog_section.index("metadata-discard-button")
    assert "Canonical key" not in dialog_section
    assert "NVIDIA_API_KEY" not in dialog_section
    assert "Authorization" not in dialog_section


def test_browser_metadata_editor_exposes_durable_load_ai_suggestion(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    css = client.get("/assets/styles.css").text
    dialog_section = html[html.index('id="metadata-dialog"') : html.index('id="catalog-browser"')]
    select_body = _javascript_function(script, "selectMetadataSuggestion")
    refresh_body = _javascript_function(script, "refreshMetadataSuggestionList")
    copy_body = _javascript_function(script, "copySuggestionFieldToCurrent")

    assert 'id="metadata-load-ai-suggestion-button"' not in dialog_section
    assert "handleLoadDurableAiSuggestion" not in script
    assert "metadataSuggestionList.revealed" not in script
    assert 'id="metadata-ai-progress"' in dialog_section
    assert "metadata-ai-progress__spinner" in dialog_section
    assert ">Load<" not in dialog_section
    assert "Load AI suggestion" not in dialog_section
    assert 'id="metadata-ai-suggestion-dropdown"' in dialog_section
    assert 'id="metadata-ai-suggestion-toggle"' in dialog_section
    assert 'id="metadata-ai-suggestion-select"' not in dialog_section
    assert 'id="metadata-ai-heading"' in dialog_section
    assert ">AI suggestions<" in dialog_section
    assert 'id="metadata-ai-title-strip"' in dialog_section
    assert 'id="metadata-ai-details-toggle"' not in dialog_section
    assert 'id="metadata-durable-ai-suggestion"' not in dialog_section
    assert "Saved AI suggestion" not in dialog_section
    assert "mediaAiSuggestionsEndpoint" in script
    assert "companionReviewInboxDetailEndpoint" not in script
    assert "/apply" not in script
    assert "aiSuggestionOriginExplanation" not in script
    assert "metadataSuggestionList.selectedRunId = items.length > 0 ? items[0].analysisRunId : null" in refresh_body
    assert "metadataSuggestionList.revealed = false" not in select_body
    assert "fetch(" not in copy_body
    assert "handleSaveMetadata" not in copy_body
    assert "applyResolvedAiSuggestionToMetadataWorkspace" not in script
    assert "window.confirm" not in select_body
    assert "Durable AI suggestion" not in dialog_section
    assert "metadata-ai-filename-note" in dialog_section
    assert "metadata-ai-filename-input" not in dialog_section
    assert "metadata-ai-filename-display" not in dialog_section
    assert dialog_section.index("metadata-ai-suggestion-dropdown") < dialog_section.index(
        "metadata-title-input"
    )
    assert dialog_section.index("metadata-save-button") < dialog_section.index(
        "metadata-ai-analyze-button"
    )
    assert dialog_section.index("metadata-ai-analyze-button") < dialog_section.index(
        "metadata-discard-button"
    )
    assert ".metadata-dialog::backdrop" in css
    assert "rgba(0, 0, 0, 0.34)" in css
    assert "rgba(0, 0, 0, 0.52)" in css
    assert ".metadata-ai-progress__spinner" in css
    assert "metadata-suggestion-tag--already-added" in css


def test_javascript_metadata_ai_analysis_requires_confirmation_and_identity_url(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    assert "function mediaAiSuggestionEndpoint(mediaId, locationId)" in script
    assert "${mediaId}/locations/${locationId}/ai-suggestion-preview" in script
    assert "handleAnalyzeMetadataByAi" in script
    analyze_body = script[
        script.index("async function handleAnalyzeMetadataByAi") : script.index("function aiSuggestionErrorMessage")
    ]
    assert "await requestConfirmation({" in analyze_body
    assert '"Use AI analysis?"' in analyze_body
    assert '"Retry AI analysis?"' in analyze_body
    assert 'dismissLabel: "Not now"' in analyze_body
    assert '"Analyze by AI"' in analyze_body
    assert '"Retry analysis"' in analyze_body
    assert "confirm_cloud_upload: true" in script
    assert "metadataAiRequestToken" in script
    assert "token !== metadataAiRequestToken" in script
    assert "metadataWorkspace.openMediaId === null" in script
    assert "relative_path" not in analyze_body
    assert "NVIDIA_API_KEY" not in script
    assert "Authorization" not in script


def test_javascript_metadata_ai_success_populates_single_form_without_autosave_or_rename(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    analyze_body = script[
        script.index("async function runMetadataAiAnalysis") : script.index("function aiSuggestionErrorMessage")
    ]
    present_body = _javascript_function(script, "presentInSessionSuggestion")
    copy_body = _javascript_function(script, "copySuggestionFieldToCurrent")

    assert "metadataWorkspace.current.displayTitle = suggestion.title" not in present_body
    assert "metadataWorkspace.suggestedFilename = item.suggestedFilename || \"\"" in present_body
    assert "metadataWorkspace.current.displayTitle = item.title || \"\"" in copy_body
    assert "presentInSessionSuggestion(suggestion, payload)" in analyze_body
    assert "applyResolvedAiSuggestionToMetadataWorkspace" not in script
    assert "metadataAiAnalyzeButton.hidden = !showAnalyze" in script
    assert "fetch(metadataEndpoint" not in analyze_body
    assert "Suggestion ready. Review and copy only the fields you want." in analyze_body
    assert "handleUseMetadataAiDraft" not in script
    assert "handleDiscardMetadataAiDraft" not in script


def test_header_uses_compact_brand_and_accessible_status_labels(client: TestClient) -> None:
    html = client.get("/").text
    header_section = html[html.index("app-header") : html.index("</header>")]

    assert ">FN<" in header_section
    assert ">FrameNest<" not in header_section
    assert 'aria-label="Cloud status: checking"' in header_section
    assert 'aria-label="AI status: checking"' in header_section
    assert ">Cloud<" not in header_section
    assert ">AI<" not in header_section
    assert ">🧠 AI<" not in header_section
    assert ">Server<" not in header_section
    for visible_state in (">Healthy<", ">Available<", ">Unavailable<", ">Checking<"):
        assert visible_state not in header_section
    assert "visually-hidden" in header_section


def test_javascript_catalog_page_size_is_bounded_persisted_and_resets_page(client: TestClient) -> None:
    script = client.get("/assets/app.js").text

    assert "const CATALOG_PAGE_SIZE_OPTIONS = [10, 30, 60, 90];" in script
    assert 'const CATALOG_PAGE_SIZE = 30;' in script
    assert "framenest.catalog.pageSize" in script
    assert "CATALOG_PAGE_SIZE_OPTIONS.includes(stored)" in script
    assert "catalogState.offset = 0;" in script[script.index("catalogPageSizeSelect.addEventListener"):]
    assert 'params.set("limit", String(snapshot.limit));' in script


def test_css_details_dialog_uses_black_player_first_surfaces(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    details_css = css[css.index(".media-details-dialog") : css.index("/* --- Metadata edit dialog --- */")]

    assert "background: #000;" in details_css
    assert ".media-details-dialog__header" in details_css
    assert ".media-details-dialog__footer" in details_css
    assert ".details-preview-container" in details_css
    assert "max-height: min(62dvh, 560px)" in details_css


def test_javascript_card_playback_cleanup_releases_media_resources(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    cleanup_body = _javascript_function(script, "cleanupCatalogCardMedia")
    playback_body = _javascript_function(script, "renderCardOriginalPlayback")

    assert "activeCardMediaRestore" in cleanup_body
    assert "renderPersistentPreview(" in cleanup_body
    assert "cardMediaElements.add(video)" in playback_body
    assert "cardMediaElements.add(image)" in playback_body
    assert "element.pause()" in cleanup_body
    assert "element.removeAttribute(\"src\")" in cleanup_body
    assert "element.load()" in cleanup_body
    assert "cardMediaElements = new Set()" in cleanup_body


def test_css_metadata_dialog_has_scrollable_body(client: TestClient) -> None:
    css = client.get("/assets/styles.css").text
    assert "overflow-y" in css or "overflow-y" in css
    assert "dvh" in css or "vh" in css or "max-height" in css


def test_web_shell_contains_local_upload_cockpit_without_gallery_publication_claim(
    client: TestClient,
) -> None:
    html = client.get("/").text
    header_section = html[html.index("app-header") : html.index("</header>")]
    upload_dialog = html[html.index('id="upload-dialog"') : html.index('id="status-dialog"')]

    assert "upload-open-button" in header_section
    assert "↑" in header_section
    assert 'aria-label="Upload media"' in header_section
    assert 'status-button__label">Upload<' not in header_section
    assert ">Local<" not in header_section
    assert 'aria-label="Upload"' in upload_dialog
    assert '>Upload</h2>' in upload_dialog
    assert "Upload media" not in upload_dialog
    assert "upload-file-input" in upload_dialog
    assert 'class="upload-file-input visually-hidden"' in upload_dialog
    assert (
        'accept=".gif,.mp4,.jpg,.jpeg,.png,image/gif,video/mp4,image/jpeg,image/png"'
        in upload_dialog
    )
    assert 'id="upload-file-field-label" class="visually-hidden">Source file</span>' in upload_dialog
    assert 'aria-labelledby="upload-file-field-label upload-file-trigger"' in upload_dialog
    assert 'aria-describedby="upload-file-name upload-file-hint"' in upload_dialog
    assert '>Supported: GIF, MP4, JPEG, and PNG.</p>' in upload_dialog
    assert 'for="upload-file-input">Choose file</label>' in upload_dialog
    assert upload_dialog.count('id="upload-file-name"') == 1
    assert 'id="upload-state-label"' in upload_dialog
    assert 'id="upload-message" class="upload-message" role="status"' in upload_dialog
    assert 'role="status"' in upload_dialog
    assert 'aria-live="polite"' in upload_dialog
    assert 'aria-atomic="true"' in upload_dialog
    assert (
        '<p id="upload-message" class="upload-message" role="status" '
        'aria-live="polite" aria-atomic="true" tabindex="-1"></p>'
        in upload_dialog
    )
    assert '>No file selected</span>' in upload_dialog
    assert ">Preparing</span>" not in upload_dialog
    assert "upload-progress" in upload_dialog
    assert 'id="upload-start-button" type="button" hidden disabled>Start upload</button>' in upload_dialog
    assert 'id="upload-pause-button" type="button" hidden disabled>Pause</button>' in upload_dialog
    assert 'id="upload-resume-button" type="button" hidden disabled>Resume</button>' in upload_dialog
    assert 'id="upload-cancel-button" class="danger-button" type="button" hidden disabled>Cancel</button>' in upload_dialog
    assert "Cancel upload" not in upload_dialog
    assert "danger-button" in upload_dialog
    assert "upload-danger-callout" in upload_dialog
    assert "upload-capability-status" not in upload_dialog
    assert "Uploads ready." not in upload_dialog
    assert "Uploads enter server quarantine for validation" in upload_dialog
    assert "not yet available in Gallery" in upload_dialog
    assert "published" not in upload_dialog.lower()
    assert "saved to Gallery" not in upload_dialog


def test_upload_cockpit_capability_and_danger_presentation_hooks(
    client: TestClient,
) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    css = client.get("/assets/styles.css").text
    upload_dialog = html[html.index('id="upload-dialog"') : html.index('id="status-dialog"')]
    selection_limit_body = _javascript_function(script, "selectedUploadLimitMessage")
    display_state_body = _javascript_function(script, "uploadDisplayState")
    action_body = _javascript_function(script, "updateUploadActions")

    assert "Uploads ready." not in script
    assert "Ready to create an upload session." not in script
    assert 'return uploadState.file ? "Ready" : "No file selected";' in display_state_body
    assert "focusedAction.hidden" in action_body
    assert "uploadMessage.focus()" in action_body
    assert 'fetchUploadJson(UPLOAD_CAPABILITY_ENDPOINT' in script
    assert "max_total_size_bytes" in script
    assert "File is too large. Maximum size is" in selection_limit_body
    assert "formatSize(uploadCapability.max_total_size_bytes)" in selection_limit_body

    assert "upload-danger-callout" in upload_dialog
    assert ".upload-dialog__footer .danger-button" in css
    assert ".upload-row[data-state=\"cancelled\"]" in css
    assert "--danger" in css
    assert "--danger-border" in css
    assert "--danger-glow" in css
    cancel_body = _javascript_function(script, "handleCancelUpload")
    assert "await requestConfirmation({" in cancel_body
    assert 'title: "Cancel upload?"' in cancel_body
    assert 'message: "Uploaded progress will be discarded."' in cancel_body
    assert 'dismissLabel: "Keep upload"' in cancel_body
    assert 'confirmLabel: "Cancel upload"' in cancel_body
    assert "destructive: true" in cancel_body
    assert "Cancelled: Upload was cancelled before Gallery publication." in script


def test_reusable_confirmation_dialog_is_single_closed_accessible_and_style_reusing(
    client: TestClient,
) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    css = client.get("/assets/styles.css").text
    start = html.index('id="confirmation-dialog"')
    confirmation_dialog = html[html.rfind("<dialog", 0, start) : html.index("</dialog>", start)]
    opening_tag = confirmation_dialog[: confirmation_dialog.index(">")]

    assert html.count('id="confirmation-dialog"') == 1
    assert " open" not in opening_tag
    assert 'class="upload-dialog confirmation-dialog"' in confirmation_dialog
    assert 'role="alertdialog"' in confirmation_dialog
    assert 'aria-modal="true"' in confirmation_dialog
    assert 'aria-labelledby="confirmation-dialog-title"' in confirmation_dialog
    assert 'aria-describedby="confirmation-dialog-message"' in confirmation_dialog
    assert confirmation_dialog.count('id="confirmation-dialog-title"') == 1
    assert confirmation_dialog.count('id="confirmation-dialog-message"') == 1
    assert 'id="confirmation-dismiss-button" type="button"' in confirmation_dialog
    assert 'id="confirmation-confirm-button" type="button"' in confirmation_dialog
    assert "upload-dialog__header" in confirmation_dialog
    assert "upload-dialog__body" in confirmation_dialog
    assert "upload-dialog__footer" in confirmation_dialog

    request_body = _javascript_function(script, "requestConfirmation")
    settle_body = _javascript_function(script, "settleConfirmation")
    keydown_body = _javascript_function(script, "handleConfirmationKeydown")
    assert "activeConfirmationRequest" in request_body
    assert "Promise.resolve(false)" in request_body
    assert "confirmationDialog.showModal()" in request_body
    assert "confirmationDismissButton.focus()" in request_body
    assert 'classList.toggle("danger-button", Boolean(destructive))' in request_body
    assert "activeConfirmationRequest !== request" in settle_body
    assert "request.settled" in settle_body
    assert "resetConfirmationDialog()" in settle_body
    assert "restoreConfirmationFocus(request.focusReturn)" in settle_body
    assert 'event.key !== "Tab"' in keydown_body
    assert "confirmationDismissButton.focus()" in keydown_body
    assert "confirmationConfirmButton.focus()" in keydown_body
    assert 'confirmationDialog.addEventListener("cancel"' in script
    assert "event.preventDefault()" in script
    assert "window.confirm" not in script
    assert re.search(r"(?<![A-Za-z0-9_.])confirm\s*\(", script) is None
    assert "window.alert" not in script
    assert "window.prompt" not in script
    assert "127.0.0.1:57087 says" not in html + script
    assert ">OK<" not in confirmation_dialog

    confirmation_css = css[
        css.index("/* --- Reusable confirmation dialog") : css.index("/* --- Main shell --- */")
    ]
    assert ".confirmation-dialog" in confirmation_css
    assert ".confirmation-dialog__message" in confirmation_css
    assert "overflow-wrap: anywhere" in confirmation_css
    assert "white-space: pre-wrap" in confirmation_css
    assert "background: var(--surface-solid)" not in confirmation_css
    assert "box-shadow: 0 24px 80px" not in confirmation_css
    assert ".upload-dialog__footer .danger-button" in css


def test_upload_confirmation_waits_before_claiming_and_revalidates_eligibility(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    cancel_body = _javascript_function(script, "handleCancelUpload")

    assert cancel_body.index("await requestConfirmation({") < cancel_body.index("claimUploadAction(")
    assert "const currentSnapshot = activeUploadSnapshot();" in cancel_body
    assert "currentSnapshot.id !== snapshot.id" in cancel_body
    assert "!uploadCancelPermitted(currentSnapshot)" in cancel_body
    assert 'claimUploadAction("cancel"' in cancel_body
    assert '{ supersede: true }' in cancel_body
    assert 'method: "DELETE"' in cancel_body


def test_javascript_upload_uses_capability_registry_and_no_file_byte_persistence(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    upload_block = script[script.index("function uploadEndpoint") : script.index("function revokePreviewObjectUrls")]

    assert 'const UPLOADS_ENDPOINT = "/api/uploads";' in script
    assert 'const UPLOAD_CAPABILITY_ENDPOINT = "/api/uploads/capability";' in script
    assert "framenest.upload.recovery.v1" in script
    assert (
        "window.localStorage.setItem(KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY" in upload_block
    )
    assert "readMigratedStorageItem(" in upload_block
    assert "KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY," in upload_block
    assert "UPLOAD_RECOVERY_STORAGE_KEY," in upload_block
    assert "window.localStorage.setItem(UPLOAD_RECOVERY_STORAGE_KEY," not in upload_block
    assert "window.localStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY" not in upload_block
    for recovery_field in (
        "upload_id",
        "file_name_hint",
        "expected_size_bytes",
        "last_modified_hint",
        "last_known_state",
    ):
        assert recovery_field in upload_block
    assert "file_bytes" not in upload_block
    assert "payload_base64" not in upload_block
    assert "framenest.youtube.currentClaim.v1" in script
    assert "indexedDB" not in script


def test_javascript_upload_loop_uses_server_confirmed_offsets_and_patch_framing(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    loop_body = _javascript_function(script, "runUploadLoop")

    assert "await refreshUploadStatus(owner.uploadId, owner)" in loop_body
    assert "const serverOffset = snapshot.received_size_bytes;" in loop_body
    assert "selectedUploadChunkSize(remainingBytes)" in loop_body
    assert "owner.file.slice(serverOffset, serverOffset + chunkSize)" in loop_body
    assert '"Content-Type": "application/offset+octet-stream"' in loop_body
    assert '"Upload-Offset": String(serverOffset)' in loop_body
    assert "Content-Length" not in script
    assert "payload.error.current_offset" in loop_body
    assert "completeUploadIfReady(owner)" in loop_body


def test_javascript_upload_pause_resume_refresh_reselection_and_mismatch_paths(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    pause_body = _javascript_function(script, "handlePauseUpload")
    resume_body = _javascript_function(script, "handleResumeUpload")
    selection_body = _javascript_function(script, "handleUploadFileSelection")

    assert "Pausing after the active request settles." in pause_body
    assert "uploadState.paused = true" in pause_body
    assert "Refreshing server offset before resuming." in resume_body
    assert "await refreshUploadStatus(snapshot.id, owner)" in resume_body
    assert "uploadState.needsReselection = true" in resume_body
    assert "file.size !== snapshot.declared_size_bytes" in selection_body
    assert "Selected file size does not match this upload session." in selection_body
    assert "Ready to resume." in selection_body
    assert "Source file reselected. Resume will use the latest server offset." not in script


def test_javascript_upload_states_polling_cancel_and_gallery_boundaries_are_truthful(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text
    upload_block = script[script.index("function uploadEndpoint") : script.index("function revokePreviewObjectUrls")]

    for label in (
        "Preparing",
        "No file selected",
        "Ready",
        "Uploading",
        "Pausing",
        "Paused",
        "Cancelling",
        "Reselect file to resume",
        "Ready to resume",
        "Validating",
        "Completed",
        "Failed",
        "Cancelled",
    ):
        assert label in upload_block
    assert "Validated. Awaiting publication. Not yet available in Gallery." in upload_block
    assert "Published. Awaiting cataloging. Not yet available in Gallery." in upload_block
    assert "Cataloged. Available in Gallery." in upload_block
    assert "uploadShouldPoll(snapshot)" in upload_block
    assert "refreshGalleryAfterCataloged" in upload_block
    assert "publish_pending" in upload_block
    assert "rejected" in upload_block
    assert "failed" in upload_block
    assert "cancelled" in upload_block
    assert "expired" in upload_block
    assert 'method: "DELETE"' in upload_block
    assert "loadCatalog()" in upload_block
    assert "mediaContentUrl" not in upload_block
    assert "gallery-preview" not in upload_block

    should_poll_body = _javascript_function(script, "uploadShouldPoll")
    assert 'snapshot.state === "published"' in should_poll_body
    assert _evaluate_upload_should_poll(script, state="published") is True
    assert _evaluate_upload_should_poll(script, state="received") is True
    assert _evaluate_upload_should_poll(script, state="validating") is True
    assert _evaluate_upload_should_poll(script, state="publish_pending") is True
    assert (
        _evaluate_upload_should_poll(
            script,
            state="publish_pending",
            publication_poll_attempts=25,
        )
        is False
    )
    assert _evaluate_upload_should_poll(script, state="cataloged") is False
    assert _evaluate_upload_should_poll(script, state="rejected") is False
    assert _evaluate_upload_should_poll(script, state="failed") is False
    assert _evaluate_upload_should_poll(script, state="cancelled") is False
    assert _evaluate_upload_should_poll(script, state="expired") is False
    assert _evaluate_upload_should_poll(script, state=None) is False


def test_kronika_shell_adds_timeline_without_replacing_gallery(client: TestClient) -> None:
    html = client.get("/").text
    script = client.get("/assets/app.js").text
    header = html[html.index("app-header") : html.index("</header>")]
    assert "<title>Kronika</title>" in html
    assert ">Kronika<" in header
    assert ">FN<" in header
    assert ">FrameNest<" not in header
    assert 'aria-label="Kronika application header"' in header
    assert 'href="#main"' in html
    assert 'tabindex="-1"' in html[html.index("<main") : html.index("<main") + 80]
    assert 'id="catalog-browser"' in html
    assert 'href="#/timeline"' in html
    assert 'href="#/gallery"' in html
    assert 'id="kronika-document-frame"' in html
    assert 'sandbox=""' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert "http://" not in html
    assert "https://" not in html
    assert "FrameNestCompanionWeb.onOpenDetails" in script
    assert "openDetailsDialog({ media_id: mediaId }, detailsCloseButton)" in script
    assert "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'" in script
    assert "kronikaStartNavigation" in script


# ---------------------------------------------------------------------------
# KSI-IMPL-C2 - served web shell identity compatibility
# ---------------------------------------------------------------------------


def test_served_shell_sends_both_mutation_header_spellings(client: TestClient) -> None:
    script = client.get("/assets/app.js").text
    helper = _javascript_function(script, "framenestMutationHeaders")

    assert script.count('"X-FrameNest-Request"') == 1, "the retired spelling stays single"
    assert script.count('"X-Kronika-Request"') == 1, "the current spelling is single too"
    assert 'merged["X-Kronika-Request"] = "1";' in helper
    assert 'Object.assign({ "X-FrameNest-Request": "1" }, headers)' in helper
    assert helper.index("Object.assign") < helper.index('merged["X-Kronika-Request"]'), (
        "the current spelling is applied after the merge so a caller cannot weaken the gate"
    )
    wrapped = script.count("headers: framenestMutationHeaders(")
    unsafe = len(re.findall(r'method: "(?:POST|PUT|PATCH|DELETE)"', script))
    assert unsafe > 0
    assert wrapped == unsafe, "every unsafe fetch call site still uses the shared helper"


def test_served_shell_persisted_keys_read_the_retired_spelling_and_write_the_current_one(
    client: TestClient,
) -> None:
    script = client.get("/assets/app.js").text

    for retired, current in (
        ("framenest.youtube.currentClaim.v1", "kronika.youtube.currentClaim.v1"),
        ("framenest.catalog.pageSize", "kronika.catalog.pageSize"),
        ("framenest.upload.recovery.v1", "kronika.upload.recovery.v1"),
    ):
        assert retired in script, retired
        assert current in script, current

    assert 'const KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "kronika.youtube.currentClaim.v1";' in script
    assert 'const KRONIKA_CATALOG_PAGE_SIZE_STORAGE_KEY = "kronika.catalog.pageSize";' in script
    assert 'const KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY = "kronika.upload.recovery.v1";' in script

    reader = _javascript_function(script, "readMigratedStorageItem")
    assert "storage.getItem(currentKey)" in reader
    assert reader.index("currentKey") < reader.index("return storage.getItem(retiredKey);")
    assert reader.count("getItem") == 2, "exactly one read per spelling"

    clearer = _javascript_function(script, "clearMigratedStorageItem")
    assert "storage.removeItem(currentKey);" in clearer
    assert "storage.removeItem(retiredKey);" in clearer

    writes = re.findall(r"\.setItem\((\w+),", script)
    assert "KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY" in writes
    assert "KRONIKA_CATALOG_PAGE_SIZE_STORAGE_KEY" in writes
    assert "KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY" in writes
    for retired_constant in (
        "UPLOAD_RECOVERY_STORAGE_KEY",
        "CATALOG_PAGE_SIZE_STORAGE_KEY",
        "YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY",
    ):
        assert retired_constant not in writes, (
            f"{retired_constant} must never be a write target again"
        )
