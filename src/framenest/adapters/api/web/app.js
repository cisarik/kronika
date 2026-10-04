"use strict";

const HEALTH_ENDPOINT = "/health";
const LIBRARIES_ENDPOINT = "/api/libraries";
const MEDIA_CATALOG_ENDPOINT = "/api/media";
const ADMIN_MEDIA_ENDPOINT = "/api/admin/media";
const WORKSPACE_MEDIA_ENDPOINT = "/api/workspace/media";
const ANALYSIS_PROPOSALS_ENDPOINT = "/api/admin/analysis-proposals";
const ADMIN_MEDIA_ALIASES_PATH = "/aliases";
const MEDIA_METADATA_ENDPOINT_PREFIX = "/api/media";
const CANONICAL_TAGS_ENDPOINT = "/api/canonical-tags";
const AI_CAPABILITY_ENDPOINT = "/api/ai/media-suggestion-capability";
const AUTOMATIC_ANALYSIS_CAPABILITY_ENDPOINT = "/api/ai/automatic-analysis-capability";
const CLOUD_STATUS_ENDPOINT = "/api/status/cloud";
const AUDIENCE_ENDPOINT = "/api/audience/me";
const UPLOADS_ENDPOINT = "/api/uploads";
const UPLOAD_CAPABILITY_ENDPOINT = "/api/uploads/capability";
const YOUTUBE_CLAIMS_ENDPOINT = "/api/admin/youtube/claims";
const YOUTUBE_REQUESTS_ENDPOINT = "/api/youtube/requests";
const YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "framenest.youtube.currentClaim.v1";
const CATALOG_PAGE_SIZE_OPTIONS = [10, 30, 60, 90];
const CATALOG_PAGE_SIZE_STORAGE_KEY = "framenest.catalog.pageSize";
const UPLOAD_RECOVERY_STORAGE_KEY = "framenest.upload.recovery.v1";
// Persisted browser keys read the retired spelling only when the current name is
// absent, are written only under the current name, and never rewrite or migrate
// the retired entry away. The explicit consume paths are the one exception and
// remove both spellings, because a consumed retired entry left behind would be
// resurrected by the fallback read on the next load.
const KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "kronika.youtube.currentClaim.v1";
const KRONIKA_CATALOG_PAGE_SIZE_STORAGE_KEY = "kronika.catalog.pageSize";
const KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY = "kronika.upload.recovery.v1";
const CATALOG_PAGE_SIZE = 30;
const ADMIN_MEDIA_PAGE_SIZE = 24;
const WORKSPACE_MEDIA_PAGE_SIZE = 24;
const ANALYSIS_PROPOSALS_PAGE_SIZE = 24;
const ADMIN_ANALYSIS_BATCH_MAX_ITEMS = 10;
const DEFAULT_UPLOAD_CHUNK_BYTES = 1024 * 1024;
const UPLOAD_POLL_INTERVAL_MS = 1200;
const UPLOAD_POLL_RETRY_MAX_MS = 10000;
const YOUTUBE_CLAIM_POLL_INTERVAL_MS = 1000;
const YOUTUBE_CLAIM_POLL_RETRY_MAX_MS = 10000;
const UPLOAD_PUBLICATION_POLL_MAX_ATTEMPTS = 25;
const AUTOMATIC_ANALYSIS_POLL_INTERVAL_MS = 1500;
const AUTOMATIC_ANALYSIS_POLL_MAX_ATTEMPTS = 40;
const AUTOMATIC_ANALYSIS_TERMINAL_STATES = new Set(["analyzed", "failed", "not_requested"]);
const UPLOAD_PUBLIC_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UPLOAD_KNOWN_STATES = new Set([
  "created",
  "receiving",
  "received",
  "validating",
  "duplicate_pending",
  "publish_pending",
  "published",
  "cataloged",
  "rejected",
  "failed",
  "cancelled",
  "expired",
]);
const UPLOAD_RECOVERY_CLEANUP_STATES = new Set([
  "published",
  "cataloged",
  "rejected",
  "failed",
  "cancelled",
  "expired",
]);
const MAX_METADATA_TITLE_CODE_POINTS = 240;
const MAX_METADATA_DESCRIPTION_CODE_POINTS = 10000;
const MAX_METADATA_TAGS = 32;
const MAX_METADATA_GENRES = 8;
const TAG_KEY_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const PROCESSED_COLLECTION = "processed";
const SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

let previewObjectUrls = [];
let catalogRequestToken = 0;
let catalogRequestOwner = null;
let metadataRequestToken = 0;
let canonicalTagDefinitions = [];
let canonicalTagsLoaded = false;
const MAX_PREVIEW_CACHE = 12;
const PREVIEW_FRAME_INTERVAL_MS = 1200;
let previewCacheMap = new Map();
let previewRequestToken = 0;
let activePreviewMediaId = null;
let activePreviewTimer = null;
let cardMediaElements = new Set();
let activeCardMediaSurface = null;
let activeCardMediaRestore = null;
/** Browser-session video resume positions keyed by stable logical media_id. */
let videoPlaybackPositionByMediaId = new Map();
const VIDEO_PLAYBACK_END_EPSILON_SECONDS = 0.35;
let detailsMediaToken = 0;
let detailsMediaElement = null;
const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let metadataBeforeUnloadAttached = false;
let metadataAiRequestToken = 0;
let metadataDurableAnalysisToken = 0;
let metadataSaveRequestToken = 0;
let metadataSaveOwner = null;
let metadataWorkspaceRevision = 0;
let aiCapabilityRevision = 0;
const cardAiQuickActionByMediaId = new Map();
const CARD_AI_QUICK_ACTION_LOCKED = new Set(["confirming", "analyzing"]);
let catalogState = {
  q: "",
  tagKeys: [],
  collection: "",
  contentCategory: "",
  acquisitionSource: "",
  creatorAttributionKind: "",
  creatorStableId: "",
  creatorHandle: "",
  limit: CATALOG_PAGE_SIZE,
  offset: 0,
  total: 0,
};
let adminCatalogRequestToken = 0;
let adminPublicationRequestToken = 0;
let adminCatalogState = {
  q: "",
  publication: "unpublished",
  readiness: "all",
  analysis: "all",
  contributor: "",
  limit: ADMIN_MEDIA_PAGE_SIZE,
  offset: 0,
  total: 0,
  requestOwner: null,
  items: [],
  publishOwners: new Map(),
  removalOwners: new Map(),
  pendingCleanupReceiptId: null,
  actionStatusByMediaId: new Map(),
  loading: false,
  error: false,
};
let workspaceMediaRequestToken = 0;
let workspaceMediaState = {
  limit: WORKSPACE_MEDIA_PAGE_SIZE,
  offset: 0,
  total: 0,
  requestOwner: null,
  items: [],
  loading: false,
  error: false,
};
let analysisProposalRequestToken = 0;
let analysisProposalState = {
  limit: ANALYSIS_PROPOSALS_PAGE_SIZE,
  offset: 0,
  total: 0,
  requestOwner: null,
  items: [],
  loading: false,
  error: false,
};
let uploadCapability = {
  uploads_enabled: false,
  max_total_size_bytes: 0,
  max_chunk_size_bytes: DEFAULT_UPLOAD_CHUNK_BYTES,
  session_ttl_seconds: 0,
};
let uploadState = {
  generation: 0,
  uploadId: null,
  file: null,
  fileNameHint: "",
  expectedSizeBytes: 0,
  lastModifiedHint: null,
  snapshot: null,
  actionOwner: null,
  preparing: false,
  running: false,
  paused: false,
  needsReselection: false,
  completing: false,
  uploadLoopOwner: null,
  completionOwner: null,
  pollOwner: null,
  pollTimer: null,
  pollRetryDelayMs: UPLOAD_POLL_INTERVAL_MS,
  publicationPollAttempts: 0,
  galleryCatalogRefreshUploadId: null,
  message: "",
  failureMessage: "",
};
let metadataWorkspace = {
  openMediaId: null,
  openItem: null,
  loading: false,
  saving: false,
  unavailable: false,
  notFound: false,
  statusOverride: null,
  analyzing: false,
  analysisFailureCode: "",
  aiSuggestionApplied: false,
  editMode: "canonical",
  suggestedFilename: "",
  baseline: {
    displayTitle: null,
    description: null,
    tagKeys: [],
    collectionKey: null,
    processedAtMs: null,
    contentCategory: "general",
    acquisitionSource: "unknown",
    genres: [],
    creatorAttributionKind: null,
    creatorStableId: null,
    creatorHandle: null,
    creatorDisplayName: null,
  },
  current: {
    displayTitle: "",
    description: "",
    tagKeys: [],
    collectionKey: null,
    processedAtMs: null,
    contentCategory: "general",
    acquisitionSource: "unknown",
    genres: [],
    creatorAttributionKind: null,
    creatorStableId: null,
    creatorHandle: null,
    creatorDisplayName: null,
  },
};
let metadataTagSuggestionState = {
  items: [],
  activeIndex: -1,
};
let aiCapability = {
  available: false,
  provider_id: "",
  provider_display_name: "",
  model_id: "",
  prompt_version: "",
  execution: "server",
  status: "not_configured",
  configured: false,
  credential_available: false,
  last_connection_test: null,
  last_status_check: null,
  requires_explicit_confirmation: true,
};
let aiCapabilityDiscoveryPending = true;
let automaticAnalysisCapability = {
  automatic_analysis_enabled: false,
  provider_configured: false,
};
const automaticAnalysisByMediaId = new Map();
const automaticAnalysisPollControllers = new Map();
let metadataDurableAnalysis = {
  mediaId: null,
  fetching: false,
  state: null,
  analysisDefinition: null,
  result: null,
  statusMessage: "",
  errorMessage: "",
  detailsExpanded: false,
};
let metadataSuggestionList = {
  mediaId: null,
  fetching: false,
  items: [],
  selectedRunId: null,
  errorMessage: "",
  movieExcluded: false,
};
let metadataSuggestionListToken = 0;
let identityState = {
  resolved: false,
  available: false,
  audience: "",
  login: "",
  displayName: "",
  role: "",
  provenance: "",
  capabilities: new Set(),
};

let youtubeClaimState = {
  generation: 0,
  claimId: null,
  snapshot: null,
  requestOwner: null,
  pollOwner: null,
  pollTimer: null,
  pollRetryDelayMs: YOUTUBE_CLAIM_POLL_INTERVAL_MS,
  submitting: false,
  retrying: false,
  recoveryAttempted: false,
  submissionResult: null,
  message: "",
  errorMessage: "",
  urlError: "",
};

let adminBatchState = {
  selectedMediaIds: new Set(),
  driver: null,
};
let adminBatchTeardown = false;

function identityHasCapability(capability) {
  return identityState.capabilities.has(capability);
}

function isPublicPublishedAudience() {
  return identityState.audience === "public_published";
}

function isWorkspaceAudience() {
  return identityState.audience === "tailscale_workspace"
    || identityState.audience === "trusted_loopback";
}

function identityAllowsAdminWorkflow() {
  return identityState.resolved
    && identityState.available
    && identityState.capabilities.has("media.workflow.read");
}

function identityAllowsYouTubeClaim() {
  return identityState.resolved
    && identityState.available
    && identityState.capabilities.has("youtube.acquire");
}

function identityAllowsYouTubeRequest() {
  return identityState.resolved
    && identityState.available
    && identityState.capabilities.has("youtube.request");
}

function identityAllowsCoverEditing() {
  return isWorkspaceAudience() && identityHasCapability("metadata.canonical.write");
}

function identityAllowsMetadataEdit() {
  return isWorkspaceAudience()
    && (
      identityHasCapability("metadata.canonical.write")
      || identityHasCapability("metadata.alias.write")
    );
}

function identityUsesCanonicalMetadataWrite() {
  return identityHasCapability("metadata.canonical.write");
}

function metadataWorkspaceIsMovie() {
  return (metadataWorkspace.current.contentCategory || "general") === "movie";
}

function metadataWorkspaceIsAliasMode() {
  return metadataWorkspace.editMode === "alias";
}

function identityAllowsAiSuggestionChrome() {
  return isWorkspaceAudience()
    && (
      identityHasCapability("metadata.alias.write")
      || identityHasCapability("metadata.canonical.write")
    )
    && !metadataWorkspaceIsMovie();
}

function identityAllowsAiAnalyze() {
  return identityHasCapability("analysis.run")
    && !companionWebHosted()
    && !metadataWorkspaceIsMovie()
    && !metadataWorkspaceIsAliasMode();
}

function identityAllowsWorkspaceMedia() {
  return identityState.resolved
    && identityState.available
    && Boolean(identityState.login)
    && identityHasCapability("media.workspace.read");
}

function identityAllowsAnalysisPropose() {
  return identityState.resolved
    && identityState.available
    && Boolean(identityState.login)
    && identityHasCapability("analysis.propose");
}

function identityAllowsTeamAliasRead() {
  return identityState.resolved
    && identityState.available
    && identityHasCapability("media.workflow.read")
    && identityHasCapability("metadata.alias.team.read");
}

function resetAudienceState() {
  identityState.resolved = true;
  identityState.available = false;
  identityState.audience = "";
  identityState.login = "";
  identityState.displayName = "";
  identityState.role = "";
  identityState.provenance = "";
  identityState.capabilities = new Set();
}

function applyAudienceDocument(audience) {
  if (typeof document === "undefined" || !document.body) return;
  if (audience) {
    document.body.setAttribute("data-audience", audience);
  } else {
    document.body.removeAttribute("data-audience");
  }
}

function readMigratedStorageItem(storage, currentKey, retiredKey) {
  const current = storage.getItem(currentKey);
  if (current !== null && current !== undefined) {
    return current;
  }
  return storage.getItem(retiredKey);
}

function clearMigratedStorageItem(storage, currentKey, retiredKey) {
  storage.removeItem(currentKey);
  storage.removeItem(retiredKey);
}

function framenestMutationHeaders(headers) {
  const merged = Object.assign({ "X-FrameNest-Request": "1" }, headers);
  // The mutation gate is the one server contract this shell depends on. Both
  // spellings are sent on every request so the shell keeps working against a
  // server that predates the dual-read cut as well as one that has it: a server
  // compares only the spelling it knows and ignores the extra header. The gate
  // value is applied after the merge so a caller cannot weaken it.
  merged["X-Kronika-Request"] = "1";
  return merged;
}

async function loadIdentity() {
  try {
    const response = await fetch(AUDIENCE_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      resetAudienceState();
      return;
    }
    const payload = await response.json();
    const audience = payload && payload.audience;
    if (
      audience !== "public_published"
      && audience !== "tailscale_workspace"
      && audience !== "trusted_loopback"
    ) {
      resetAudienceState();
      return;
    }
    if (!Array.isArray(payload.capabilities)) {
      resetAudienceState();
      return;
    }
    identityState.resolved = true;
    identityState.audience = audience;
    identityState.available = audience === "tailscale_workspace";
    identityState.capabilities = new Set(
      payload.capabilities.filter((capability) => typeof capability === "string"),
    );
    const identity = payload.identity;
    if (identity && typeof identity === "object") {
      identityState.login = typeof identity.login === "string" ? identity.login : "";
      identityState.displayName = typeof identity.display_name === "string" ? identity.display_name : "";
      identityState.role = typeof identity.role === "string" ? identity.role : "";
      identityState.provenance = typeof identity.provenance === "string" ? identity.provenance : "";
    } else {
      identityState.login = "";
      identityState.displayName = "";
      identityState.role = "";
      identityState.provenance = "";
    }
  } catch {
    resetAudienceState();
  } finally {
    applyAudienceDocument(identityState.audience);
    applyIdentityCapabilities();
    renderIdentityBadge();
    if (typeof kronikaNoteIdentity === "function") kronikaNoteIdentity();
  }
}

function applyIdentityCapabilities() {
  const youtubeClaimAllowed = typeof identityAllowsYouTubeClaim === "function"
    && identityAllowsYouTubeClaim();
  if (uploadOpenButton) {
    uploadOpenButton.hidden = !identityHasCapability("upload.submit");
  }
  if (detailsEditButton) {
    detailsEditButton.hidden = !identityAllowsMetadataEdit();
  }
  if (typeof detailsChooseCoverButton !== "undefined" && detailsChooseCoverButton) {
    detailsChooseCoverButton.hidden = !identityAllowsCoverEditing();
  }
  if (adminMediaOpenButton) {
    adminMediaOpenButton.hidden = !identityAllowsAdminWorkflow();
  }
  const analysisProposalsAllowed = identityAllowsAdminWorkflow();
  if (typeof analysisProposalsOpenButton !== "undefined" && analysisProposalsOpenButton) {
    analysisProposalsOpenButton.hidden = !analysisProposalsAllowed;
  }
  const workspaceAllowed = typeof identityAllowsWorkspaceMedia === "function"
    && identityAllowsWorkspaceMedia();
  if (typeof workspaceMediaOpenButton !== "undefined" && workspaceMediaOpenButton) {
    workspaceMediaOpenButton.hidden = !workspaceAllowed;
  }
  if (typeof youtubeClaimOpenButton !== "undefined" && youtubeClaimOpenButton) {
    youtubeClaimOpenButton.hidden = !youtubeClaimAllowed;
  }
  const youtubeRequestAllowed = typeof identityAllowsYouTubeRequest === "function"
    && identityAllowsYouTubeRequest();
  if (typeof youtubeRequestOpenButton !== "undefined" && youtubeRequestOpenButton) {
    youtubeRequestOpenButton.hidden = !youtubeRequestAllowed;
  }
  const xReqAllowed = typeof identityAllowsXRequest === "function" && identityAllowsXRequest();
  if (typeof xRequestOpenButton !== "undefined" && xRequestOpenButton) {
    xRequestOpenButton.hidden = !xReqAllowed;
  }
  const xAdminAllowed = typeof identityAllowsXAdmin === "function" && identityAllowsXAdmin();
  if (typeof xAdminOpenButton !== "undefined" && xAdminOpenButton) {
    xAdminOpenButton.hidden = !xAdminAllowed;
  }
  if (
    !youtubeClaimAllowed
    && typeof youtubeClaimDialog !== "undefined"
    && youtubeClaimDialog
    && youtubeClaimDialog.hasAttribute("open")
  ) {
    closeYouTubeClaimDialog();
  }
  if (
    !youtubeRequestAllowed
    && typeof youtubeRequestDialog !== "undefined"
    && youtubeRequestDialog
    && youtubeRequestDialog.hasAttribute("open")
  ) {
    closeYouTubeRequestDialog();
  }
  if (!identityAllowsAdminWorkflow() && adminMediaBrowser && !adminMediaBrowser.hidden) {
    closeAdminMediaBrowser();
  }
  if (
    !analysisProposalsAllowed
    && typeof analysisProposalsBrowser !== "undefined"
    && analysisProposalsBrowser
    && !analysisProposalsBrowser.hidden
    && typeof closeAnalysisProposalsBrowser === "function"
  ) {
    closeAnalysisProposalsBrowser();
  }
  if (
    typeof adminMediaAliasesPanel !== "undefined"
    && adminMediaAliasesPanel
    && !identityAllowsTeamAliasRead()
  ) {
    adminMediaAliasesPanel.hidden = true;
    if (typeof adminMediaAliasesResults !== "undefined" && adminMediaAliasesResults) {
      adminMediaAliasesResults.replaceChildren();
    }
    if (typeof adminMediaAliasesStatus !== "undefined" && adminMediaAliasesStatus) {
      adminMediaAliasesStatus.textContent = "";
    }
  }
  if (
    !workspaceAllowed
    && typeof workspaceMediaBrowser !== "undefined"
    && workspaceMediaBrowser
    && !workspaceMediaBrowser.hidden
    && typeof closeWorkspaceMediaBrowser === "function"
  ) {
    closeWorkspaceMediaBrowser();
  }
  const providerAdministrationAllowed = typeof identityAllowsProviderAdministration === "function"
    && identityAllowsProviderAdministration();
  if (typeof aiProvidersButton !== "undefined" && aiProvidersButton) {
    aiProvidersButton.hidden = !providerAdministrationAllowed;
  }
  if (
    !providerAdministrationAllowed
    && typeof aiProvidersDialog !== "undefined"
    && aiProvidersDialog
    && typeof aiProvidersDialog.hasAttribute === "function"
    && aiProvidersDialog.hasAttribute("open")
    && typeof closeAiProvidersDialog === "function"
  ) {
    closeAiProvidersDialog();
  }
  updateMetadataControls();
}

function renderIdentityBadge() {
  if (!identityBadge) return;
  if (!identityState.available || !identityState.login) {
    identityBadge.hidden = true;
    if (identityStatusName) identityStatusName.textContent = "";
    identityBadge.removeAttribute("aria-label");
    identityBadge.title = "Open Tailscale identity status";
    applyTailscalePanelDensity();
    return;
  }
  const label = identityState.displayName || identityState.login;
  if (identityStatusName) identityStatusName.textContent = label;
  identityBadge.classList.remove("status-button--checking", "status-button--unhealthy");
  identityBadge.setAttribute(
    "aria-label",
    `Signed in as ${label}. Open Tailscale identity status.`,
  );
  identityBadge.title = `Signed in as ${identityState.login}. Open Tailscale identity status.`;
  identityBadge.hidden = false;
  applyTailscalePanelDensity();
}

function restoredCatalogPageSize() {
  try {
    const stored = Number(
      readMigratedStorageItem(window.localStorage, KRONIKA_CATALOG_PAGE_SIZE_STORAGE_KEY, CATALOG_PAGE_SIZE_STORAGE_KEY),
    );
    if (CATALOG_PAGE_SIZE_OPTIONS.includes(stored)) {
      return stored;
    }
  } catch {
    // Ignore unavailable localStorage and keep the default page size.
  }
  return CATALOG_PAGE_SIZE;
}

catalogState.limit = restoredCatalogPageSize();

const statusContainer = document.querySelector("#server-status");
const statusText = document.querySelector("#server-status-text");
const statusDetail = document.querySelector("#server-status-detail");
const aiStatus = document.querySelector("#ai-status");
const aiStatusText = document.querySelector("#ai-status-text");
const aiStatusDetail = document.querySelector("#ai-status-detail");
const serverHealthButton = document.querySelector("#server-health-button");
const serverHealthButtonText = document.querySelector("#server-health-button-text");
const aiStatusButton = document.querySelector("#ai-status-button");
const aiStatusButtonText = document.querySelector("#ai-status-button-text");
const statusDialog = document.querySelector("#status-dialog");
const statusCloseButton = document.querySelector("#status-close-button");
const statusTabAi = document.querySelector("#status-tab-ai");
const statusTabCloud = document.querySelector("#status-tab-cloud");
const statusTabTailscale = document.querySelector("#status-tab-tailscale");
const statusPanelAi = document.querySelector("#status-panel-ai");
const statusPanelCloud = document.querySelector("#status-panel-cloud");
const statusPanelTailscale = document.querySelector("#status-panel-tailscale");
const settingsAiProvider = document.querySelector("#settings-ai-provider");
const settingsAiModel = document.querySelector("#settings-ai-model");
const settingsAiConfiguration = document.querySelector("#settings-ai-configuration");
const settingsAiCredential = document.querySelector("#settings-ai-credential");
const settingsAiTestResult = document.querySelector("#settings-ai-test-result");
const settingsAiTestedAtRow = document.querySelector("#settings-ai-tested-at-row");
const settingsAiTestedAt = document.querySelector("#settings-ai-tested-at");
const statusCloudServer = document.querySelector("#status-cloud-server");
const statusCloudConnection = document.querySelector("#status-cloud-connection");
const statusCloudRemoteRow = document.querySelector("#status-cloud-remote-row");
const statusCloudRemote = document.querySelector("#status-cloud-remote");
const statusTailscaleConnection = document.querySelector("#status-tailscale-connection");
const statusTailscaleAccessMethod = document.querySelector("#status-tailscale-access-method");
const statusTailscaleHostname = document.querySelector("#status-tailscale-hostname");
const statusTailscaleUrl = document.querySelector("#status-tailscale-url");
const statusTailscaleHttps = document.querySelector("#status-tailscale-https");
const statusTailscaleLogin = document.querySelector("#status-tailscale-login");
const statusTailscaleDisplayName = document.querySelector("#status-tailscale-display-name");
const statusTailscaleRole = document.querySelector("#status-tailscale-role");
const statusTailscaleProvenance = document.querySelector("#status-tailscale-provenance");
const uploadOpenButton = document.querySelector("#upload-open-button");
const identityBadge = document.querySelector("#identity-badge");
const identityStatusName = document.querySelector("#identity-status-name");
const statusTailscaleAdminOnlyRows = document.querySelectorAll(".status-tailscale-admin-only");
const aiProvidersButton = document.querySelector("#ai-providers-button");
const aiProvidersDialog = document.querySelector("#ai-providers-dialog");
const aiProvidersCloseButton = document.querySelector("#ai-providers-close-button");
const aiProvidersActiveSummary = document.querySelector("#ai-providers-active-summary");
const aiProvidersStatus = document.querySelector("#ai-providers-status");
const aiProvidersList = document.querySelector("#ai-providers-list");
const aiProvidersForm = document.querySelector("#ai-providers-form");
const aiProvidersJsonPreview = document.querySelector("#ai-providers-json-preview");
const aiProviderIdInput = document.querySelector("#ai-provider-id");
const aiProviderNameInput = document.querySelector("#ai-provider-name");
const aiProviderBaseUrlInput = document.querySelector("#ai-provider-base-url");
const aiProviderCredentialEnvInput = document.querySelector("#ai-provider-credential-env");
const aiProviderProtocolInput = document.querySelector("#ai-provider-protocol");
const aiProviderModelsList = document.querySelector("#ai-provider-models");
const aiProviderAddModelButton = document.querySelector("#ai-provider-add-model");
const aiProviderSaveButton = document.querySelector("#ai-provider-save");
const aiProviderActivateButton = document.querySelector("#ai-provider-activate");
const aiProviderPingButton = document.querySelector("#ai-provider-ping");
const aiProviderPongButton = document.querySelector("#ai-provider-pong");
const aiProviderDeleteButton = document.querySelector("#ai-provider-delete");
const aiProviderPongConfirm = document.querySelector("#ai-provider-pong-confirm");
const aiProviderPongConfirmNote = document.querySelector("#ai-provider-pong-confirm-note");
const aiProviderPongCancelButton = document.querySelector("#ai-provider-pong-cancel");
const aiProviderPongConfirmButton = document.querySelector("#ai-provider-pong-confirm-button");
const researchSettingsSection = document.querySelector("#research-settings-section");
const researchSettingsStatus = document.querySelector("#research-settings-status");
const researchSettingsLoading = document.querySelector("#research-settings-loading");
const researchSettingsForm = document.querySelector("#research-settings-form");
const researchSettingsEnabled = document.querySelector("#research-settings-enabled");
const researchSettingsModel = document.querySelector("#research-settings-model");
const researchSettingsDailyBudget = document.querySelector("#research-settings-daily-budget");
const researchSettingsMonthlyBudget = document.querySelector("#research-settings-monthly-budget");
const researchSettingsSearchReservation = document.querySelector("#research-settings-search-reservation");
const researchSettingsResearchReservation = document.querySelector("#research-settings-research-reservation");
const researchSettingsCatalog = document.querySelector("#research-settings-catalog");
const researchSettingsCredential = document.querySelector("#research-settings-credential");
const researchSettingsSaveButton = document.querySelector("#research-settings-save");
const researchSettingsReloadButton = document.querySelector("#research-settings-reload");
const researchSettingsConfirm = document.querySelector("#research-settings-confirm");
const researchSettingsConfirmNote = document.querySelector("#research-settings-confirm-note");
const researchSettingsConfirmCancel = document.querySelector("#research-settings-confirm-cancel");
const researchSettingsConfirmButton = document.querySelector("#research-settings-confirm-button");
const uploadDialog = document.querySelector("#upload-dialog");
const uploadDialogTitle = document.querySelector("#upload-dialog-title");
const uploadCloseButton = document.querySelector("#upload-close-button");
const uploadFileInput = document.querySelector("#upload-file-input");
const uploadRow = document.querySelector("#upload-row");
const uploadFileName = document.querySelector("#upload-file-name");
const uploadStateLabel = document.querySelector("#upload-state-label");
const uploadProgress = document.querySelector("#upload-progress");
const uploadByteCount = document.querySelector("#upload-byte-count");
const uploadPercent = document.querySelector("#upload-percent");
const uploadMessage = document.querySelector("#upload-message");
const uploadFailure = document.querySelector("#upload-failure");
const uploadStartButton = document.querySelector("#upload-start-button");
const uploadPauseButton = document.querySelector("#upload-pause-button");
const uploadResumeButton = document.querySelector("#upload-resume-button");
const uploadDuplicateKeepButton = document.querySelector("#upload-duplicate-keep-button");
const uploadDuplicateDiscardButton = document.querySelector("#upload-duplicate-discard-button");
const uploadCancelButton = document.querySelector("#upload-cancel-button");
const confirmationDialog = document.querySelector("#confirmation-dialog");
const confirmationDialogTitle = document.querySelector("#confirmation-dialog-title");
const confirmationDialogMessage = document.querySelector("#confirmation-dialog-message");
const confirmationDismissButton = document.querySelector("#confirmation-dismiss-button");
const confirmationConfirmButton = document.querySelector("#confirmation-confirm-button");
let healthCheckInFlight = false;
let lastFocusedElementBeforeStatus = null;
let uploadOpenerElement = null;
let youtubeClaimOpenerElement = null;
let confirmationRequestSequence = 0;
let activeConfirmationRequest = null;
const catalogTagFilters = document.querySelector("#catalog-tag-filters");
const catalogTagsState = document.querySelector("#catalog-tags-state");
const commandSearchInput = document.querySelector("#command-search-input");
const commandSearchClear = document.querySelector("#command-search-clear");
const commandSearchSuggestions = document.querySelector("#command-search-suggestions");
const headerSearch = document.querySelector(".header-search");
const catalogBrowser = document.querySelector("#catalog-browser");
const catalogStateLoading = document.querySelector("#catalog-state-loading");
const catalogStateEmpty = document.querySelector("#catalog-state-empty");
const catalogStateEmptyMessage = document.querySelector("#catalog-state-empty-message");
const catalogStateUnavailable = document.querySelector("#catalog-state-unavailable");
const catalogStateError = document.querySelector("#catalog-state-error");
const catalogRetryButton = document.querySelector("#catalog-retry-button");
const catalogResults = document.querySelector("#catalog-results");
const catalogPrevButton = document.querySelector("#catalog-prev-button");
const catalogNextButton = document.querySelector("#catalog-next-button");
const catalogPageSummary = document.querySelector("#catalog-page-summary");
const catalogPageSizeSelect = document.querySelector("#catalog-page-size-select");
const adminMediaOpenButton = document.querySelector("#admin-media-open-button");
const workspaceMediaOpenButton = document.querySelector("#workspace-media-open-button");
const workspaceMediaBrowser = document.querySelector("#workspace-media-browser");
const workspaceMediaHeading = document.querySelector("#workspace-media-heading");
const workspaceMediaCloseButton = document.querySelector("#workspace-media-close-button");
const workspaceMediaActionStatus = document.querySelector("#workspace-media-action-status");
const workspaceMediaLoading = document.querySelector("#workspace-media-loading");
const workspaceMediaEmpty = document.querySelector("#workspace-media-empty");
const workspaceMediaError = document.querySelector("#workspace-media-error");
const workspaceMediaRetryButton = document.querySelector("#workspace-media-retry-button");
const workspaceMediaResults = document.querySelector("#workspace-media-results");
const workspaceMediaPageSummary = document.querySelector("#workspace-media-page-summary");
const workspaceMediaPrevButton = document.querySelector("#workspace-media-prev-button");
const workspaceMediaNextButton = document.querySelector("#workspace-media-next-button");
const analysisProposalsOpenButton = document.querySelector("#analysis-proposals-open-button");
const analysisProposalsBrowser = document.querySelector("#analysis-proposals-browser");
const analysisProposalsHeading = document.querySelector("#analysis-proposals-heading");
const analysisProposalsCloseButton = document.querySelector("#analysis-proposals-close-button");
const analysisProposalsActionStatus = document.querySelector("#analysis-proposals-action-status");
const analysisProposalsLoading = document.querySelector("#analysis-proposals-loading");
const analysisProposalsEmpty = document.querySelector("#analysis-proposals-empty");
const analysisProposalsError = document.querySelector("#analysis-proposals-error");
const analysisProposalsRetryButton = document.querySelector("#analysis-proposals-retry-button");
const analysisProposalsResults = document.querySelector("#analysis-proposals-results");
const analysisProposalsPageSummary = document.querySelector("#analysis-proposals-page-summary");
const analysisProposalsPrevButton = document.querySelector("#analysis-proposals-prev-button");
const analysisProposalsNextButton = document.querySelector("#analysis-proposals-next-button");
const adminMediaBrowser = document.querySelector("#admin-media-browser");
const adminMediaHeading = document.querySelector("#admin-media-heading");
const adminMediaCloseButton = document.querySelector("#admin-media-close-button");
const adminMediaFilters = document.querySelector("#admin-media-filters");
const adminMediaSearch = document.querySelector("#admin-media-search");
const adminMediaPublicationFilter = document.querySelector("#admin-media-publication-filter");
const adminMediaContributorFilter = document.querySelector("#admin-media-contributor-filter");
const adminMediaReadinessFilter = document.querySelector("#admin-media-readiness-filter");
const adminMediaAnalysisFilter = document.querySelector("#admin-media-analysis-filter");
const adminMediaRefreshButton = document.querySelector("#admin-media-refresh-button");
const adminMediaActionStatus = document.querySelector("#admin-media-action-status");
const adminCatalogCleanupRetryButton = document.querySelector(
  "#admin-catalog-cleanup-retry-button",
);
const adminMediaLoading = document.querySelector("#admin-media-loading");
const adminMediaEmpty = document.querySelector("#admin-media-empty");
const adminMediaError = document.querySelector("#admin-media-error");
const adminMediaRetryButton = document.querySelector("#admin-media-retry-button");
const adminMediaResults = document.querySelector("#admin-media-results");
const adminMediaAliasesPanel = document.querySelector("#admin-media-aliases-panel");
const adminMediaAliasesHeading = document.querySelector("#admin-media-aliases-heading");
const adminMediaAliasesStatus = document.querySelector("#admin-media-aliases-status");
const adminMediaAliasesResults = document.querySelector("#admin-media-aliases-results");
const adminMediaPageSummary = document.querySelector("#admin-media-page-summary");
const adminMediaPrevButton = document.querySelector("#admin-media-prev-button");
const adminMediaNextButton = document.querySelector("#admin-media-next-button");
const adminBatchBar = document.querySelector("#admin-media-batch-bar");
const adminBatchSelectAll = document.querySelector("#admin-batch-select-all");
const adminBatchSelectionCount = document.querySelector("#admin-batch-selection-count");
const adminBatchPublishButton = document.querySelector("#admin-batch-publish-button");
const adminBatchAnalyzeButton = document.querySelector("#admin-batch-analyze-button");
const adminBatchClearButton = document.querySelector("#admin-batch-clear-button");
const adminBatchStopButton = document.querySelector("#admin-batch-stop-button");
const adminBatchHint = document.querySelector("#admin-batch-hint");
const adminBatchProgress = document.querySelector("#admin-batch-progress");
const adminBatchOutcomes = document.querySelector("#admin-batch-outcomes");
const youtubeClaimOpenButton = document.querySelector("#youtube-claim-open-button");
const youtubeRequestOpenButton = document.querySelector("#youtube-request-open-button");
const youtubeRequestDialog = document.querySelector("#youtube-request-dialog");
const youtubeRequestDialogTitle = document.querySelector("#youtube-request-dialog-title");
const youtubeRequestCloseButton = document.querySelector("#youtube-request-close-button");
const youtubeRequestForm = document.querySelector("#youtube-request-form");
const youtubeRequestUrlInput = document.querySelector("#youtube-request-url");
const youtubeRequestUrlError = document.querySelector("#youtube-request-url-error");
const youtubeRequestSubmitButton = document.querySelector("#youtube-request-submit-button");
const youtubeRequestStatus = document.querySelector("#youtube-request-status");
const youtubeRequestList = document.querySelector("#youtube-request-list");
const youtubeClaimRequester = document.querySelector("#youtube-claim-requester");
const youtubeClaimDialog = document.querySelector("#youtube-claim-dialog");
const youtubeClaimDialogTitle = document.querySelector("#youtube-claim-dialog-title");
const youtubeClaimCloseButton = document.querySelector("#youtube-claim-close-button");
const youtubeClaimForm = document.querySelector("#youtube-claim-form");
const youtubeClaimUrlInput = document.querySelector("#youtube-claim-url");
const youtubeClaimSubmitButton = document.querySelector("#youtube-claim-submit-button");
const youtubeClaimResetButton = document.querySelector("#youtube-claim-reset-button");
const youtubeClaimRetryButton = document.querySelector("#youtube-claim-retry-button");
const youtubeClaimManageMediaButton = document.querySelector("#youtube-claim-manage-media-button");
const youtubeClaimRow = document.querySelector("#youtube-claim-row");
const youtubeClaimStateLabel = document.querySelector("#youtube-claim-state-label");
const youtubeClaimMessage = document.querySelector("#youtube-claim-message");
const youtubeClaimDetails = document.querySelector("#youtube-claim-details");
const youtubeClaimFailure = document.querySelector("#youtube-claim-failure");
const youtubeClaimUrlError = document.querySelector("#youtube-claim-url-error");
const youtubeClaimMetadata = document.querySelector("#youtube-claim-metadata");
const youtubeClaimPublication = document.querySelector("#youtube-claim-publication");
const metadataWorkspaceElement = document.querySelector("#metadata-workspace");
const metadataWorkspaceTitle = document.querySelector("#metadata-workspace-title");
const metadataWorkspaceContext = document.querySelector("#metadata-workspace-context");
const metadataCloseButton = document.querySelector("#metadata-close-button");
const metadataStatus = document.querySelector("#metadata-status");
const metadataTitleInput = document.querySelector("#metadata-title-input");
const metadataTitleFallback = document.querySelector("#metadata-title-fallback");
const metadataValidationMessage = document.querySelector("#metadata-validation-message");
const metadataDescriptionInput = document.querySelector("#metadata-description-input");
const metadataDescriptionStatus = document.querySelector("#metadata-description-status");
const metadataTagSearchInput = document.querySelector("#metadata-tag-search-input");
const metadataTagSuggestions = document.querySelector("#metadata-tag-suggestions");
const metadataSelectedTags = document.querySelector("#metadata-selected-tags");
const metadataTagStatus = document.querySelector("#metadata-tag-status");
const metadataAiPanel = document.querySelector("#metadata-ai-panel");
const metadataAiHeading = document.querySelector("#metadata-ai-heading");
const metadataAiAnalyzeButton = document.querySelector("#metadata-ai-analyze-button");
const metadataAiSuggestionDropdown = document.querySelector("#metadata-ai-suggestion-dropdown");
const metadataAiSuggestionToggle = document.querySelector("#metadata-ai-suggestion-toggle");
const metadataAiSuggestionToggleLabel = document.querySelector("#metadata-ai-suggestion-toggle-label");
const metadataAiSuggestionList = document.querySelector("#metadata-ai-suggestion-list");
const metadataAiStatus = document.querySelector("#metadata-ai-status");
const metadataAiProgress = document.querySelector("#metadata-ai-progress");
const metadataAiFilenameNote = document.querySelector("#metadata-ai-filename-note");
const metadataAiTitleStrip = document.querySelector("#metadata-ai-title-strip");
const metadataAiDescriptionStrip = document.querySelector("#metadata-ai-description-strip");
const metadataAiTagsStrip = document.querySelector("#metadata-ai-tags-strip");
const metadataDialog = document.querySelector("#metadata-dialog");
const detailsDialog = document.querySelector("#media-details-dialog");
const detailsCloseButton = document.querySelector("#media-details-close");
const detailsEditButton = document.querySelector("#media-details-edit");
const detailsLoading = document.querySelector("#media-details-loading");
const detailsError = document.querySelector("#media-details-error");
const detailsContent = document.querySelector("#media-details-content");
const detailsPreviewContainer = document.querySelector("#details-preview-container");
const detailsDialogTitle = document.querySelector("#media-details-title");
const detailsTagsContainer = document.querySelector("#media-details-tags");
const detailsDescription = document.querySelector("#media-details-description");
const detailsTechnical = document.querySelector("#media-details-technical");
const detailsTechnicalList = document.querySelector("#media-details-technical-list");
let metadataOpenerElement = null;
let detailsOpenerElement = null;
let detailsCurrentItem = null;
let detailsMetadataToken = 0;
let detailsPlayRequested = false;
const detailsChooseCoverButton = document.querySelector("#media-details-choose-cover");
const coverDialog = document.querySelector("#cover-dialog");
const coverDialogCloseButton = document.querySelector("#cover-dialog-close");
const coverDialogTitle = document.querySelector("#cover-dialog-title");
const coverDialogLoading = document.querySelector("#cover-dialog-loading");
const coverDialogError = document.querySelector("#cover-dialog-error");
const coverDialogContent = document.querySelector("#cover-dialog-content");
const coverCurrent = document.querySelector("#cover-current");
const coverCurrentThumbnail = document.querySelector("#cover-current-thumbnail");
const coverCurrentTimestamp = document.querySelector("#cover-current-timestamp");
const coverTimelineRange = document.querySelector("#cover-timeline-range");
const coverTimeline = document.querySelector(".cover-timeline");
const coverTimestampReadout = document.querySelector("#cover-timestamp-readout");
const coverDurationReadout = document.querySelector("#cover-duration-readout");
const coverStepBackButton = document.querySelector("#cover-step-back");
const coverStepForwardButton = document.querySelector("#cover-step-forward");
const coverPreviewButton = document.querySelector("#cover-preview-button");
const coverPreviewRegion = document.querySelector("#cover-preview-region");
const coverPreviewContainer = document.querySelector("#cover-preview-container");
const coverPreviewStatus = document.querySelector("#cover-preview-status");
const coverReplaceConfirm = document.querySelector("#cover-replace-confirm");
const coverReplaceYesButton = document.querySelector("#cover-replace-yes");
const coverReplaceNoButton = document.querySelector("#cover-replace-no");
const coverSetButton = document.querySelector("#cover-set-button");
const coverCancelButton = document.querySelector("#cover-cancel-button");
const coverDialogStatus = document.querySelector("#cover-dialog-status");
const metadataSaveButton = document.querySelector("#metadata-save-button");
const metadataDiscardButton = document.querySelector("#metadata-discard-button");
let confirmationEscapeDismissalInProgress = false;

function confirmationOwnsTopmostModal() {
  return Boolean(
    activeConfirmationRequest
    || confirmationEscapeDismissalInProgress
    || (confirmationDialog && confirmationDialog.hasAttribute("open"))
  );
}

function settleConfirmationEscape(request) {
  if (!request || activeConfirmationRequest !== request || request.settled) return;
  confirmationEscapeDismissalInProgress = true;
  settleConfirmation(request, false);
  Promise.resolve().then(() => {
    confirmationEscapeDismissalInProgress = false;
  });
}

function resetConfirmationDialog() {
  confirmationDialogTitle.textContent = "";
  confirmationDialogMessage.textContent = "";
  confirmationDismissButton.textContent = "";
  confirmationConfirmButton.textContent = "";
  confirmationConfirmButton.classList.remove("danger-button");
}

function restoreConfirmationFocus(target) {
  if (target && typeof target.focus === "function" && !target.hidden && !target.disabled) {
    target.focus();
    if (document.activeElement === target) return;
  }
  if (uploadDialog && uploadDialog.hasAttribute("open") && uploadMessage) {
    uploadMessage.focus();
    return;
  }
  if (metadataDialog && metadataDialog.hasAttribute("open") && metadataWorkspaceTitle) {
    metadataWorkspaceTitle.focus();
    return;
  }
  if (detailsDialog && detailsDialog.hasAttribute("open") && detailsCloseButton) {
    detailsCloseButton.focus();
    return;
  }
  if (statusDialog && statusDialog.hasAttribute("open")) {
    const activePanel =
      (statusPanelTailscale && !statusPanelTailscale.hidden && statusPanelTailscale) ||
      (statusPanelCloud && !statusPanelCloud.hidden && statusPanelCloud) ||
      statusPanelAi;
    if (activePanel) activePanel.focus();
  }
}

function youtubeClaimDialogIsOpen() {
  return Boolean(
    youtubeClaimDialog
      && (
        (typeof youtubeClaimDialog.hasAttribute === "function"
          && youtubeClaimDialog.hasAttribute("open"))
        || youtubeClaimDialog.open === true
      ),
  );
}

const YOUTUBE_CLAIM_VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_CLAIM_HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com"]);
const YOUTUBE_CLAIM_WATCH_QUERY_KEYS = new Set(["v", "t", "si", "feature"]);
const YOUTUBE_CLAIM_PATH_QUERY_KEYS = new Set(["t", "si", "feature"]);

function validateYouTubeClaimUrl(value) {
  const unsupported = (code = "unsupported") => ({
    supported: false,
    code,
    message: code === "required"
      ? "Enter a YouTube URL before submitting the claim."
      : "Enter a supported single-video YouTube URL before submitting the claim.",
  });
  if (typeof value !== "string" || !value || !value.trim()) return unsupported("required");
  if (
    value.trim() !== value
    || Array.from(value).length > 2048
    || /\p{Cc}/u.test(value)
  ) return unsupported();

  const rawUrl = value.match(/^https:\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/iu);
  if (!rawUrl) return unsupported();
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return unsupported();
  }
  const host = parsed.hostname.toLowerCase();
  const rawAuthority = rawUrl[1].toLowerCase();
  const rawPath = rawUrl[2];
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || (parsed.port && parsed.port !== "443")
    || parsed.hash
    || (rawAuthority !== host && rawAuthority !== `${host}:443`)
    || parsed.pathname !== rawPath
  ) return unsupported();

  const rawQuery = rawUrl[3] === undefined ? "" : rawUrl[3];
  const queryFields = rawQuery ? rawQuery.split("&") : [];
  if (queryFields.length > 8 || queryFields.some((field) => !field.includes("="))) {
    return unsupported();
  }
  const queryPairs = [...new URLSearchParams(rawQuery).entries()];
  const query = new Map();
  for (const [key, queryValue] of queryPairs) {
    if (query.has(key)) return unsupported();
    query.set(key, queryValue);
  }

  let videoId = null;
  if (host === "youtu.be") {
    if ([...query.keys()].some((key) => !YOUTUBE_CLAIM_PATH_QUERY_KEYS.has(key))) {
      return unsupported();
    }
    const pathParts = rawPath.split("/");
    if (pathParts.length === 2 || (pathParts.length === 3 && pathParts[2] === "")) {
      videoId = pathParts[1];
    }
  } else if (YOUTUBE_CLAIM_HOSTS.has(host) && rawPath === "/watch") {
    if (
      !query.has("v")
      || [...query.keys()].some((key) => !YOUTUBE_CLAIM_WATCH_QUERY_KEYS.has(key))
    ) return unsupported();
    videoId = query.get("v");
  } else if (YOUTUBE_CLAIM_HOSTS.has(host)) {
    if ([...query.keys()].some((key) => !YOUTUBE_CLAIM_PATH_QUERY_KEYS.has(key))) {
      return unsupported();
    }
    const pathParts = rawPath.split("/");
    if (
      (pathParts.length === 3 || (pathParts.length === 4 && pathParts[3] === ""))
      && pathParts[1] === "shorts"
    ) videoId = pathParts[2];
  }
  if (!YOUTUBE_CLAIM_VIDEO_ID_PATTERN.test(videoId || "")) return unsupported();
  return { supported: true, code: "supported", url: value, videoId };
}

function youtubeClaimStateIsTerminal(snapshot) {
  return Boolean(snapshot && ["failed", "cataloged", "duplicate_resolved"].includes(snapshot.state));
}

function youtubeClaimShouldPoll(snapshot) {
  return Boolean(snapshot && !youtubeClaimStateIsTerminal(snapshot));
}

function normalizeYouTubeClaimSnapshot(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const claimId = typeof payload.claim_id === "string" ? payload.claim_id.trim() : "";
  if (!claimId || claimId.length > 256 || /[\u0000-\u001f\u007f]/u.test(claimId)) return null;
  const failure = payload.failure && typeof payload.failure === "object"
    ? {
      stage: typeof payload.failure.stage === "string" ? payload.failure.stage : "",
      code: typeof payload.failure.code === "string" ? payload.failure.code : "",
    }
    : null;
  return {
    claim_id: claimId,
    state: typeof payload.state === "string" ? payload.state : "unknown",
    phase: typeof payload.phase === "string" ? payload.phase : "unknown",
    submission_result: typeof payload.submission_result === "string"
      ? payload.submission_result
      : null,
    media_id: payload.media_id === null || payload.media_id === undefined
      ? null
      : String(payload.media_id),
    catalog_state: typeof payload.catalog_state === "string" ? payload.catalog_state : "not_cataloged",
    metadata_state: typeof payload.metadata_state === "string" ? payload.metadata_state : "unknown",
    missing_metadata_fields: Array.isArray(payload.missing_metadata_fields)
      ? payload.missing_metadata_fields
        .filter((field) => typeof field === "string")
        .slice(0, 16)
      : [],
    publication_state: typeof payload.publication_state === "string"
      ? payload.publication_state
      : "unknown",
    failure: failure && (failure.stage || failure.code) ? failure : null,
    retry_of_claim_id: typeof payload.retry_of_claim_id === "string"
      ? payload.retry_of_claim_id
      : null,
  };
}

function youtubeClaimStorage() {
  return typeof window !== "undefined" && window.sessionStorage
    ? window.sessionStorage
    : null;
}

function saveYouTubeClaimRecovery(claimId = youtubeClaimState.claimId) {
  if (typeof claimId !== "string" || !claimId) return;
  try {
    const storage = youtubeClaimStorage();
    if (storage) storage.setItem(KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY, claimId);
  } catch {
    youtubeClaimState.message = "Claim recovery could not be saved in this browser.";
  }
}

function clearYouTubeClaimRecovery() {
  try {
    const storage = youtubeClaimStorage();
    if (storage) clearMigratedStorageItem(storage, KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY, YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY);
  } catch {
    // The in-memory claim remains authoritative for this browser view.
  }
}

function loadYouTubeClaimRecovery() {
  try {
    const storage = youtubeClaimStorage();
    const claimId = storage
      ? readMigratedStorageItem(storage, KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY, YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY)
      : null;
    if (
      typeof claimId !== "string"
      || !claimId
      || claimId.length > 256
      || /[\u0000-\u001f\u007f]/u.test(claimId)
    ) {
      if (claimId !== null) clearYouTubeClaimRecovery();
      return null;
    }
    return claimId;
  } catch {
    clearYouTubeClaimRecovery();
    return null;
  }
}

function clearYouTubeClaimPollTimer() {
  if (youtubeClaimState.pollTimer) {
    clearTimeout(youtubeClaimState.pollTimer);
    youtubeClaimState.pollTimer = null;
  }
}

function stopYouTubeClaimPolling() {
  clearYouTubeClaimPollTimer();
  youtubeClaimState.pollOwner = null;
  youtubeClaimState.pollRetryDelayMs = YOUTUBE_CLAIM_POLL_INTERVAL_MS;
}

function nextYouTubeClaimGeneration() {
  youtubeClaimState.generation += 1;
  return youtubeClaimState.generation;
}

function youtubeClaimContext(claimId = youtubeClaimState.claimId) {
  return {
    generation: youtubeClaimState.generation,
    claimId: claimId || null,
  };
}

function youtubeClaimContextStillCurrent(owner) {
  return Boolean(
    owner
      && youtubeClaimState.generation === owner.generation
      && (!owner.claimId || youtubeClaimState.claimId === owner.claimId),
  );
}

function claimYouTubeRequest(kind) {
  if (youtubeClaimState.requestOwner) return null;
  stopYouTubeClaimPolling();
  const owner = Object.freeze({
    generation: nextYouTubeClaimGeneration(),
    claimId: youtubeClaimState.claimId,
    kind,
  });
  youtubeClaimState.requestOwner = owner;
  return owner;
}

function releaseYouTubeRequest(owner) {
  if (youtubeClaimState.requestOwner !== owner) return false;
  youtubeClaimState.requestOwner = null;
  return true;
}

function invalidateYouTubeClaimOwnership() {
  stopYouTubeClaimPolling();
  nextYouTubeClaimGeneration();
  youtubeClaimState.requestOwner = null;
  youtubeClaimState.submitting = false;
  youtubeClaimState.retrying = false;
}

function youtubeClaimErrorMessage(payload, status = 0) {
  const code = payload && payload.error && typeof payload.error.code === "string"
    ? payload.error.code
    : "";
  const messages = {
    YOUTUBE_BROWSER_NOT_CONFIGURED: "YouTube acquisition is not configured on this local server.",
    YOUTUBE_BROWSER_IDENTITY_REQUIRED: "A verified application identity is required.",
    IDENTITY_REQUIRED: "A verified application identity is required.",
    YOUTUBE_BROWSER_CAPABILITY_DENIED: "Your current identity is not authorized to claim YouTube media.",
    CAPABILITY_DENIED: "Your current identity is not authorized to claim YouTube media.",
    YOUTUBE_BROWSER_INVALID_URL: "The server rejected this YouTube URL.",
    YOUTUBE_BROWSER_INVALID_REQUEST: "The claim request was invalid.",
    YOUTUBE_BROWSER_CLAIM_NOT_FOUND: "This claim is no longer available on the server.",
    YOUTUBE_BROWSER_STATE_CONFLICT: "This claim changed state and cannot be retried yet.",
    YOUTUBE_BROWSER_UNAVAILABLE: "YouTube acquisition is temporarily unavailable.",
    YOUTUBE_BROWSER_AUDIT_UNAVAILABLE: "The privileged action could not be recorded.",
  };
  if (messages[code]) return messages[code];
  if (status === 401) return "A verified application identity is required.";
  if (status === 403) return "Your current identity is not authorized for this action.";
  if (status >= 500) return "The local server could not complete the YouTube claim.";
  return "The YouTube claim request could not be completed.";
}

async function requestYouTubeClaimStatus(claimId) {
  if (typeof claimId !== "string" || !claimId) {
    return { ok: false, status: 404, payload: null, notFound: true };
  }
  try {
    const response = await fetch(
      `${YOUTUBE_CLAIMS_ENDPOINT}/${encodeURIComponent(claimId)}`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        payload,
        notFound: response.status === 404,
      };
    }
    const snapshot = normalizeYouTubeClaimSnapshot(payload);
    return snapshot
      ? { ok: true, snapshot }
      : { ok: false, status: response.status, payload, parseFailed: true };
  } catch {
    return { ok: false, status: 0, payload: null, networkError: true };
  }
}

function youtubeClaimPhaseLabel(snapshot) {
  if (!snapshot) return "Ready";
  if (snapshot.state === "claimed" || snapshot.phase === "queued") return "Queued";
  if (snapshot.state === "inspecting" || snapshot.phase === "inspecting") return "Inspecting";
  if (snapshot.state === "download_pending" || snapshot.state === "downloading") return "Downloading";
  if (["downloaded", "handoff", "handed_off"].includes(snapshot.state)) return "Handing off";
  if (snapshot.state === "duplicate_resolved") return "Duplicate resolved";
  if (snapshot.state === "cataloged") return "Cataloged";
  if (snapshot.state === "failed") return "Failed";
  return snapshot.phase ? String(snapshot.phase).replaceAll("_", " ") : "Status unavailable";
}

function youtubeClaimStatusMessage(snapshot) {
  if (!snapshot) return youtubeClaimState.message || "Paste a YouTube URL to begin.";
  if (snapshot.submission_result === "active_reuse") {
    return "An active claim for this URL was already in progress. Reusing it.";
  }
  if (snapshot.submission_result === "terminal_duplicate_reuse") {
    return "This source matched existing catalog media. No new download was required, and no second catalog item was created.";
  }
  if (snapshot.state === "failed") {
    return "The claim failed with sanitized server failure information. Retry is available.";
  }
  if (snapshot.state === "duplicate_resolved") {
    return "This source matched existing catalog media. No new download was required, and no second catalog item was created.";
  }
  if (snapshot.state === "cataloged") {
    if (snapshot.publication_state === "unpublished") {
      return "Cataloged. The media is not visible to ordinary Gallery users until it is published in Manage media.";
    }
    if (snapshot.publication_state === "published") {
      return "Cataloged. The media is available in Gallery.";
    }
    return "Cataloged. Review publication in Manage media before expecting it in Gallery.";
  }
  if (snapshot.state === "handoff" || snapshot.state === "handed_off") {
    return "Media was acquired and is being handed off to the local catalog.";
  }
  if (snapshot.state === "downloading" || snapshot.state === "download_pending") {
    return "The server is acquiring media from the confirmed source.";
  }
  if (snapshot.state === "inspecting") {
    return "The server is inspecting the confirmed source.";
  }
  return "The server accepted the claim and is processing it.";
}

function youtubeClaimMetadataLabel(snapshot) {
  if (!snapshot || snapshot.catalog_state !== "cataloged") return "Metadata: Not cataloged yet";
  if (snapshot.metadata_state === "complete") return "Metadata: Complete";
  if (snapshot.metadata_state === "incomplete") {
    const missing = snapshot.missing_metadata_fields.length > 0
      ? ` Missing: ${snapshot.missing_metadata_fields.join(", ")}.`
      : "";
    return `Metadata: Incomplete.${missing}`;
  }
  return "Metadata: Unknown";
}

function youtubeClaimPublicationLabel(snapshot) {
  if (!snapshot || snapshot.catalog_state !== "cataloged") return "Publication: Not cataloged yet";
  if (snapshot.publication_state === "published") return "Publication: Published";
  if (snapshot.publication_state === "unpublished") return "Publication: Unpublished; review in Manage media.";
  return "Publication: Unknown";
}

function renderYouTubeClaimCockpit() {
  const snapshot = youtubeClaimState.snapshot;
  const hasClaim = Boolean(snapshot || youtubeClaimState.claimId);
  const busy = youtubeClaimState.submitting || youtubeClaimState.retrying;
  const canManageMedia = Boolean(
    snapshot
      && snapshot.media_id
      && identityAllowsAdminWorkflow()
      && (snapshot.state === "cataloged" || snapshot.state === "duplicate_resolved"),
  );
  if (youtubeClaimRow) youtubeClaimRow.dataset.state = snapshot ? snapshot.state : "idle";
  if (youtubeClaimStateLabel) {
    youtubeClaimStateLabel.textContent = busy
      ? (youtubeClaimState.retrying ? "Retrying" : "Submitting")
      : youtubeClaimPhaseLabel(snapshot);
  }
  if (youtubeClaimMessage) {
    youtubeClaimMessage.textContent = youtubeClaimState.errorMessage
      || youtubeClaimStatusMessage(snapshot);
  }
  const urlError = youtubeClaimState.urlError || "";
  if (youtubeClaimUrlInput) {
    youtubeClaimUrlInput.setAttribute("aria-invalid", String(Boolean(urlError)));
    youtubeClaimUrlInput.setAttribute(
      "aria-describedby",
      urlError ? "youtube-claim-url-note youtube-claim-url-error" : "youtube-claim-url-note",
    );
  }
  if (youtubeClaimUrlError) {
    youtubeClaimUrlError.hidden = !urlError;
    youtubeClaimUrlError.textContent = urlError;
  }
  if (youtubeClaimDetails) youtubeClaimDetails.hidden = !snapshot;
  if (typeof youtubeClaimRequester !== "undefined" && youtubeClaimRequester) {
    const key = snapshot && typeof snapshot.requester_login_key === "string"
      ? snapshot.requester_login_key
      : null;
    youtubeClaimRequester.textContent = key
      ? `Requested by ${key}`
      : "Administrator claim";
  }
  if (youtubeClaimMetadata) youtubeClaimMetadata.textContent = youtubeClaimMetadataLabel(snapshot);
  if (youtubeClaimPublication) youtubeClaimPublication.textContent = youtubeClaimPublicationLabel(snapshot);
  if (youtubeClaimFailure) {
    const failure = snapshot && snapshot.failure;
    youtubeClaimFailure.hidden = !failure;
    youtubeClaimFailure.textContent = failure
      ? `Failure stage: ${failure.stage || "unknown"}. Sanitized failure code: ${failure.code || "unknown"}.`
      : "";
  }
  if (youtubeClaimUrlInput) youtubeClaimUrlInput.disabled = hasClaim || busy;
  if (youtubeClaimSubmitButton) {
    youtubeClaimSubmitButton.hidden = hasClaim;
    youtubeClaimSubmitButton.disabled = busy || hasClaim;
  }
  if (youtubeClaimRetryButton) {
    youtubeClaimRetryButton.hidden = !snapshot || snapshot.state !== "failed";
    youtubeClaimRetryButton.disabled = busy;
  }
  if (youtubeClaimResetButton) {
    youtubeClaimResetButton.hidden = !hasClaim;
    youtubeClaimResetButton.disabled = busy;
  }
  if (youtubeClaimManageMediaButton) {
    youtubeClaimManageMediaButton.hidden = !canManageMedia;
    youtubeClaimManageMediaButton.disabled = busy;
  }
  if (youtubeClaimDialogTitle) {
    youtubeClaimDialogTitle.textContent = hasClaim ? "YouTube claim status" : "Claim YouTube media";
  }
}

function applyYouTubeClaimSnapshot(snapshot, owner, { allowClaimChange = false } = {}) {
  if (!snapshot || !youtubeClaimContextStillCurrent(owner)) return false;
  if (owner.claimId && snapshot.claim_id !== owner.claimId && !allowClaimChange) return false;
  youtubeClaimState.claimId = snapshot.claim_id;
  youtubeClaimState.snapshot = snapshot;
  if (snapshot.submission_result) {
    youtubeClaimState.submissionResult = snapshot.submission_result;
  }
  youtubeClaimState.errorMessage = "";
  saveYouTubeClaimRecovery(snapshot.claim_id);
  renderYouTubeClaimCockpit();
  if (youtubeClaimStateIsTerminal(snapshot)) stopYouTubeClaimPolling();
  return true;
}

function scheduleYouTubeClaimPolling(
  owner = youtubeClaimContext(),
  requestedDelayMs = youtubeClaimState.pollRetryDelayMs,
) {
  clearYouTubeClaimPollTimer();
  const snapshot = youtubeClaimState.snapshot;
  if (!youtubeClaimShouldPoll(snapshot)) return;
  if (!youtubeClaimDialogIsOpen()) return;
  if (!youtubeClaimContextStillCurrent(owner)) return;
  const pollOwner = Object.freeze({ ...owner });
  youtubeClaimState.pollOwner = pollOwner;
  const delay = Math.min(requestedDelayMs, YOUTUBE_CLAIM_POLL_RETRY_MAX_MS);
  youtubeClaimState.pollTimer = window.setTimeout(() => {
    youtubeClaimState.pollTimer = null;
    pollYouTubeClaimStatus(pollOwner);
  }, delay);
}

async function pollYouTubeClaimStatus(owner = youtubeClaimState.pollOwner) {
  if (owner !== youtubeClaimState.pollOwner || !youtubeClaimContextStillCurrent(owner)) return;
  const result = await requestYouTubeClaimStatus(owner.claimId || youtubeClaimState.claimId);
  if (owner !== youtubeClaimState.pollOwner || !youtubeClaimContextStillCurrent(owner)) return;
  if (result.ok) {
    youtubeClaimState.pollRetryDelayMs = YOUTUBE_CLAIM_POLL_INTERVAL_MS;
    if (!applyYouTubeClaimSnapshot(result.snapshot, owner)) return;
    if (youtubeClaimShouldPoll(result.snapshot)) {
      scheduleYouTubeClaimPolling(youtubeClaimContext(result.snapshot.claim_id));
    } else {
      stopYouTubeClaimPolling();
    }
    return;
  }
  if (result.notFound) {
    stopYouTubeClaimPolling();
    clearYouTubeClaimRecovery();
    youtubeClaimState.claimId = null;
    youtubeClaimState.snapshot = null;
    youtubeClaimState.errorMessage = "This saved claim was not found on the local server.";
    renderYouTubeClaimCockpit();
    return;
  }
  youtubeClaimState.errorMessage = result.networkError
    ? "Claim status is temporarily unavailable; retrying."
    : youtubeClaimErrorMessage(result.payload, result.status);
  renderYouTubeClaimCockpit();
  if (
    youtubeClaimShouldPoll(youtubeClaimState.snapshot)
    && youtubeClaimDialogIsOpen()
    && result.status !== 401
    && result.status !== 403
  ) {
    const retryDelayMs = Math.min(
      YOUTUBE_CLAIM_POLL_RETRY_MAX_MS,
      Math.max(
        YOUTUBE_CLAIM_POLL_INTERVAL_MS,
        youtubeClaimState.pollRetryDelayMs * 2,
      ),
    );
    scheduleYouTubeClaimPolling(owner, youtubeClaimState.pollRetryDelayMs);
    youtubeClaimState.pollRetryDelayMs = retryDelayMs;
  } else {
    stopYouTubeClaimPolling();
  }
}

async function refreshYouTubeClaimStatus(owner = youtubeClaimContext()) {
  if (!owner || !owner.claimId) return null;
  const result = await requestYouTubeClaimStatus(owner.claimId);
  if (!youtubeClaimContextStillCurrent(owner)) return null;
  if (!result.ok) {
    if (result.notFound) {
      clearYouTubeClaimRecovery();
      youtubeClaimState.claimId = null;
      youtubeClaimState.snapshot = null;
      youtubeClaimState.errorMessage = "This saved claim was not found on the local server.";
    } else {
      youtubeClaimState.errorMessage = result.networkError
        ? "Claim status could not be loaded from the local server."
        : youtubeClaimErrorMessage(result.payload, result.status);
    }
    renderYouTubeClaimCockpit();
    return null;
  }
  if (!applyYouTubeClaimSnapshot(result.snapshot, owner)) return null;
  if (youtubeClaimShouldPoll(result.snapshot) && youtubeClaimDialogIsOpen()) {
    scheduleYouTubeClaimPolling(youtubeClaimContext(result.snapshot.claim_id));
  }
  return result.snapshot;
}

async function restoreYouTubeClaim() {
  if (youtubeClaimState.recoveryAttempted || !identityAllowsYouTubeClaim()) return;
  youtubeClaimState.recoveryAttempted = true;
  const claimId = loadYouTubeClaimRecovery();
  if (!claimId || youtubeClaimState.claimId) {
    renderYouTubeClaimCockpit();
    return;
  }
  invalidateYouTubeClaimOwnership();
  youtubeClaimState.claimId = claimId;
  youtubeClaimState.snapshot = null;
  youtubeClaimState.message = "Recovering saved YouTube claim...";
  renderYouTubeClaimCockpit();
  await refreshYouTubeClaimStatus(youtubeClaimContext(claimId));
}

function resetYouTubeClaimState() {
  const generation = youtubeClaimState.generation;
  invalidateYouTubeClaimOwnership();
  clearYouTubeClaimRecovery();
  youtubeClaimState = {
    generation: youtubeClaimState.generation || generation,
    claimId: null,
    snapshot: null,
    requestOwner: null,
    pollOwner: null,
    pollTimer: null,
    pollRetryDelayMs: YOUTUBE_CLAIM_POLL_INTERVAL_MS,
    submitting: false,
    retrying: false,
    recoveryAttempted: true,
    submissionResult: null,
    message: "",
    errorMessage: "",
    urlError: "",
  };
  if (youtubeClaimUrlInput) youtubeClaimUrlInput.value = "";
  renderYouTubeClaimCockpit();
}

function openYouTubeClaimDialog() {
  if (!identityAllowsYouTubeClaim() || !youtubeClaimDialog) return;
  youtubeClaimOpenerElement = document.activeElement;
  if (typeof youtubeClaimDialog.showModal === "function") {
    youtubeClaimDialog.showModal();
  } else {
    youtubeClaimDialog.setAttribute("open", "");
  }
  renderYouTubeClaimCockpit();
  if (youtubeClaimState.claimId) {
    refreshYouTubeClaimStatus(youtubeClaimContext(youtubeClaimState.claimId));
  }
  if (youtubeClaimState.claimId) {
    if (youtubeClaimDialogTitle) youtubeClaimDialogTitle.focus();
  } else if (youtubeClaimUrlInput) {
    youtubeClaimUrlInput.focus();
  }
}

function closeYouTubeClaimDialog() {
  if (!youtubeClaimDialog) return;
  invalidateYouTubeClaimOwnership();
  if (typeof youtubeClaimDialog.close === "function") {
    youtubeClaimDialog.close();
  } else {
    youtubeClaimDialog.removeAttribute("open");
  }
  if (youtubeClaimOpenerElement && !youtubeClaimOpenerElement.hidden) {
    youtubeClaimOpenerElement.focus();
  } else if (youtubeClaimOpenButton && !youtubeClaimOpenButton.hidden) {
    youtubeClaimOpenButton.focus();
  }
  youtubeClaimOpenerElement = null;
}

async function submitYouTubeClaim() {
  if (!identityAllowsYouTubeClaim() || youtubeClaimState.requestOwner) return;
  const validation = validateYouTubeClaimUrl(youtubeClaimUrlInput ? youtubeClaimUrlInput.value : "");
  if (!validation.supported) {
    youtubeClaimState.urlError = validation.message;
    renderYouTubeClaimCockpit();
    if (youtubeClaimUrlInput) youtubeClaimUrlInput.focus();
    return;
  }
  const url = validation.url;
  youtubeClaimState.urlError = "";
  const accepted = await requestConfirmation({
    title: "Confirm YouTube claim",
    message: "Kronika will start the acquisition in the background. Closing the cockpit will not cancel it. Acquired media remains unpublished until it is reviewed and published in Manage media.",
    dismissLabel: "Cancel",
    confirmLabel: "Claim media",
    focusReturn: youtubeClaimSubmitButton,
  });
  if (!accepted || !identityAllowsYouTubeClaim()) return;
  const owner = claimYouTubeRequest("create");
  if (!owner) return;
  youtubeClaimState.submitting = true;
  youtubeClaimState.errorMessage = "";
  youtubeClaimState.message = "Submitting confirmed YouTube claim...";
  renderYouTubeClaimCockpit();
  try {
    const response = await fetch(YOUTUBE_CLAIMS_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ url, confirmation_method: "interactive" }),
      cache: "no-store",
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!youtubeClaimState.requestOwner || !youtubeClaimContextStillCurrent(owner)) return;
    const snapshot = normalizeYouTubeClaimSnapshot(payload);
    if (!response.ok || !snapshot) {
      youtubeClaimState.errorMessage = response.ok
        ? "The local server returned an invalid claim response."
        : youtubeClaimErrorMessage(payload, response.status);
      youtubeClaimState.message = "Claim was not started.";
      return;
    }
    if (!applyYouTubeClaimSnapshot(snapshot, owner, { allowClaimChange: true })) return;
    if (youtubeClaimUrlInput) youtubeClaimUrlInput.value = "";
    releaseYouTubeRequest(owner);
    if (youtubeClaimShouldPoll(snapshot)) {
      scheduleYouTubeClaimPolling(youtubeClaimContext(snapshot.claim_id));
    }
  } catch {
    if (youtubeClaimState.requestOwner === owner) {
      youtubeClaimState.errorMessage = "The claim could not reach the local server.";
      youtubeClaimState.message = "Claim was not started.";
    }
  } finally {
    if (youtubeClaimState.requestOwner === owner) releaseYouTubeRequest(owner);
    if (youtubeClaimContextStillCurrent(owner)) {
      youtubeClaimState.submitting = false;
      renderYouTubeClaimCockpit();
    }
  }
}

async function retryYouTubeClaim() {
  const snapshot = youtubeClaimState.snapshot;
  if (!snapshot || snapshot.state !== "failed" || youtubeClaimState.requestOwner) return;
  const accepted = await requestConfirmation({
    title: "Retry YouTube claim",
    message: "Retry the failed claim using the same server-owned claim context?",
    dismissLabel: "Cancel",
    confirmLabel: "Retry claim",
    focusReturn: youtubeClaimRetryButton,
  });
  if (!accepted || !identityAllowsYouTubeClaim()) return;
  const owner = claimYouTubeRequest("retry");
  if (!owner) return;
  youtubeClaimState.retrying = true;
  youtubeClaimState.errorMessage = "";
  youtubeClaimState.message = "Submitting claim retry...";
  renderYouTubeClaimCockpit();
  try {
    const response = await fetch(
      `${YOUTUBE_CLAIMS_ENDPOINT}/${encodeURIComponent(snapshot.claim_id)}/retry`,
      {
        method: "POST",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({ confirmation_method: "interactive" }),
        cache: "no-store",
      },
    );
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!youtubeClaimState.requestOwner || !youtubeClaimContextStillCurrent(owner)) return;
    const nextSnapshot = normalizeYouTubeClaimSnapshot(payload);
    if (!response.ok || !nextSnapshot) {
      youtubeClaimState.errorMessage = response.ok
        ? "The local server returned an invalid retry response."
        : youtubeClaimErrorMessage(payload, response.status);
      youtubeClaimState.message = "Retry was not started.";
      return;
    }
    if (!applyYouTubeClaimSnapshot(nextSnapshot, owner, { allowClaimChange: true })) return;
    releaseYouTubeRequest(owner);
    if (youtubeClaimShouldPoll(nextSnapshot)) {
      scheduleYouTubeClaimPolling(youtubeClaimContext(nextSnapshot.claim_id));
    }
  } catch {
    if (youtubeClaimState.requestOwner === owner) {
      youtubeClaimState.errorMessage = "The retry could not reach the local server.";
      youtubeClaimState.message = "Retry was not started.";
    }
  } finally {
    if (youtubeClaimState.requestOwner === owner) releaseYouTubeRequest(owner);
    if (youtubeClaimState.generation === owner.generation) {
      youtubeClaimState.retrying = false;
      renderYouTubeClaimCockpit();
    }
  }
}

function handoffYouTubeClaimToManageMedia() {
  if (!youtubeClaimState.snapshot || !youtubeClaimState.snapshot.media_id) return;
  closeYouTubeClaimDialog();
  openAdminMediaBrowser();
}

function settleConfirmation(request, accepted) {
  if (!request || activeConfirmationRequest !== request || request.settled) return;
  request.settled = true;
  activeConfirmationRequest = null;
  try {
    if (confirmationDialog.hasAttribute("open")) {
      if (typeof confirmationDialog.close === "function") {
        confirmationDialog.close();
      } else {
        confirmationDialog.removeAttribute("open");
      }
    }
  } catch {
    try {
      confirmationDialog.removeAttribute("open");
    } catch {
      // Confirmation ownership is already released; cleanup remains best-effort.
    }
  }
  try {
    resetConfirmationDialog();
  } catch {
    // Continue settlement even if one dialog normalization operation fails.
  }
  try {
    restoreConfirmationFocus(request.focusReturn);
  } catch {
    // Focus recovery must never prevent fail-closed settlement.
  }
  try {
    request.resolve(Boolean(accepted));
  } catch {
    // A misbehaving resolver must not escape from confirmation cleanup.
  }
}

function requestConfirmation({
  title,
  message,
  dismissLabel,
  confirmLabel,
  destructive = false,
  focusReturn = null,
}) {
  if (activeConfirmationRequest) {
    return Promise.resolve(false);
  }
  const request = {
    id: ++confirmationRequestSequence,
    focusReturn: focusReturn || document.activeElement,
    resolve: null,
    settled: false,
  };
  const result = new Promise((resolve) => {
    request.resolve = resolve;
  });
  activeConfirmationRequest = request;
  confirmationDialogTitle.textContent = String(title || "");
  confirmationDialogMessage.textContent = String(message || "");
  confirmationDismissButton.textContent = String(dismissLabel || "Cancel");
  confirmationConfirmButton.textContent = String(confirmLabel || "Confirm");
  confirmationConfirmButton.classList.toggle("danger-button", Boolean(destructive));
  try {
    if (typeof confirmationDialog.showModal === "function") {
      confirmationDialog.showModal();
    } else {
      confirmationDialog.setAttribute("open", "");
    }
  } catch {
    settleConfirmation(request, false);
    return result;
  }
  confirmationDismissButton.focus();
  return result;
}

function handleConfirmationKeydown(event) {
  if (!activeConfirmationRequest) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    settleConfirmationEscape(activeConfirmationRequest);
    return;
  }
  if (event.key !== "Tab") return;
  const focusableActions = [confirmationDismissButton, confirmationConfirmButton];
  const activeIndex = focusableActions.indexOf(document.activeElement);
  if (activeIndex === -1) {
    event.preventDefault();
    confirmationDismissButton.focus();
    return;
  }
  if (event.shiftKey && activeIndex === 0) {
    event.preventDefault();
    confirmationConfirmButton.focus();
  } else if (!event.shiftKey && activeIndex === focusableActions.length - 1) {
    event.preventDefault();
    confirmationDismissButton.focus();
  }
}

function handleParentDialogCancel(event, closeDialog) {
  event.preventDefault();
  if (confirmationOwnsTopmostModal()) return;
  closeDialog();
}

function setStatusClass(className) {
  statusContainer.classList.remove("status--loading", "status--healthy", "status--error");
  statusContainer.classList.add(className);
}

function setLoadingState() {
  setStatusClass("status--loading");
  statusText.textContent = "Checking local server...";
  statusDetail.textContent = "Waiting for the same-origin health response.";
  setServerHealthButtonState("checking", "Checking server");
}

function setHealthyState() {
  setStatusClass("status--healthy");
  statusText.textContent = "Local server healthy";
  statusDetail.textContent = "The Kronika application process answered the health check.";
  setServerHealthButtonState("healthy", "Server healthy");
}

function setErrorState() {
  setStatusClass("status--error");
  statusText.textContent = "Health check unavailable";
  statusDetail.textContent =
    "The page loaded, but the local health endpoint did not return the expected response.";
  setServerHealthButtonState("unhealthy", "Server unavailable");
}

function setServerHealthButtonState(state, label) {
  if (!serverHealthButton) return;
  serverHealthButton.classList.remove("status-button--checking", "status-button--healthy", "status-button--unhealthy");
  serverHealthButton.classList.add("status-button--" + state);
  if (serverHealthButtonText) serverHealthButtonText.textContent = label;
  if (state === "healthy") {
    serverHealthButton.setAttribute("aria-label", "Cloud status: connected");
    serverHealthButton.title = "Cloud status: connected. Open Cloud status.";
  } else if (state === "unhealthy") {
    serverHealthButton.setAttribute("aria-label", "Cloud status: unavailable");
    serverHealthButton.title = "Cloud status: unavailable. Open Cloud status.";
  } else {
    serverHealthButton.setAttribute("aria-label", "Cloud status: checking");
    serverHealthButton.title = "Cloud status: checking";
  }
}

function setAiStatusButtonState(state, label) {
  if (!aiStatusButton) return;
  aiStatusButton.classList.remove("status-button--checking", "status-button--healthy", "status-button--unhealthy");
  aiStatusButton.classList.add("status-button--" + state);
  if (aiStatusButtonText) aiStatusButtonText.textContent = label;
  if (state === "healthy") {
    aiStatusButton.setAttribute("aria-label", "AI status: available");
    aiStatusButton.title = "AI status: available. Open AI status.";
  } else if (state === "unhealthy") {
    aiStatusButton.setAttribute("aria-label", "AI status: unavailable");
    aiStatusButton.title = "AI status: unavailable. Open AI status.";
  } else {
    aiStatusButton.setAttribute("aria-label", "AI status: checking");
    aiStatusButton.title = "AI status: checking";
  }
}

async function checkHealth() {
  if (healthCheckInFlight) return;
  healthCheckInFlight = true;
  setLoadingState();
  try {
    const response = await fetch(HEALTH_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      setErrorState();
      return;
    }
    const payload = await response.json();
    if (payload && payload.status === "ok") {
      setHealthyState();
      return;
    }
    setErrorState();
  } catch {
    setErrorState();
  } finally {
    healthCheckInFlight = false;
  }
}

async function retryHealth() {
  await checkHealth();
}

function setAiStatusClass(className) {
  aiStatus.classList.remove("status--loading", "status--healthy", "status--error");
  aiStatus.classList.add(className);
}

function updateSettingsAiStatus() {
  const providerName = aiCapability.provider_display_name || aiCapability.provider_id || "Not configured";
  if (settingsAiProvider) settingsAiProvider.textContent = providerName;
  if (settingsAiModel) settingsAiModel.textContent = aiCapability.model_id || "Not configured";
  if (settingsAiConfiguration) {
    settingsAiConfiguration.textContent = aiCapability.configured ? "Configured" : "Not configured";
  }
  if (settingsAiCredential) {
    settingsAiCredential.textContent = aiCapability.credential_available ? "Available to server" : "Unavailable";
  }
  if (settingsAiTestResult) {
    settingsAiTestResult.textContent = providerTestStatusText(aiCapability.last_connection_test);
  }
  renderOptionalStatusRow(
    settingsAiTestedAtRow,
    settingsAiTestedAt,
    aiCapability.last_connection_test,
    providerTestTimestampText,
  );
}

function aiStatusLabel(status) {
  if (status === "success") return "Successful";
  if (status === "available") return "Available";
  if (status === "configured_unverified") return "Configured, unverified";
  if (status === "credential_unavailable") return "Credential unavailable";
  if (status === "authentication_failed") return "Authentication failed";
  if (status === "rate_limited_or_quota_exhausted") return "Rate limited or quota exhausted";
  if (status === "model_unavailable") return "Model unavailable";
  if (status === "provider_unreachable") return "Provider unreachable";
  if (status === "provider_error") return "Provider error";
  return "Not configured";
}

function aiStatusInfo(status) {
  if (status === "credential_unavailable") {
    return {
      heading: "Server credential unavailable",
      reason: "The selected provider credential is not available to this Kronika server process.",
    };
  }
  if (status === "configured_unverified") {
    return {
      heading: "AI configured, not verified",
      reason: "A provider is selected, but no matching successful server test is recorded.",
    };
  }
  if (status === "available") {
    return {
      heading: "AI available",
      reason: "A credentialed server provider is available for explicit analysis requests.",
    };
  }
  if (status === "authentication_failed") {
    return {
      heading: "Authentication failed",
      reason: "The provider rejected the configured server credential.",
    };
  }
  if (status === "rate_limited_or_quota_exhausted") {
    return {
      heading: "Rate limited",
      reason: "The configured provider reported a rate limit or quota condition.",
    };
  }
  if (status === "model_unavailable") {
    return {
      heading: "Model unavailable",
      reason: "The selected provider model is not currently available.",
    };
  }
  if (status === "provider_unreachable") {
    return {
      heading: "Provider unreachable",
      reason: "The server could not reach the configured provider during the last test.",
    };
  }
  if (status === "provider_error") {
    return {
      heading: "Provider error",
      reason: "The configured provider returned an unavailable or invalid response.",
    };
  }
  if (status === "status_unavailable") {
    return {
      heading: "AI status unavailable",
      reason: "The server capability endpoint did not return a usable status.",
    };
  }
  return {
    heading: "AI not configured",
    reason: "No server AI provider has been selected.",
  };
}

function formatLocalTimestamp(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) return "";
  const date = new Date(numeric);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

function renderOptionalStatusRow(row, valueElement, payload, formatter) {
  if (!row || !valueElement) return;
  if (!payload) {
    row.hidden = true;
    valueElement.textContent = "";
    return;
  }
  const text = formatter(payload);
  row.hidden = !text;
  valueElement.textContent = text;
}

function providerTestStatusText(payload) {
  if (!payload) return "Not tested";
  const status = payload.status ? aiStatusLabel(String(payload.status)) : "";
  if (!status || status === "Not configured") return "Not tested";
  return status;
}

function providerTestTimestampText(payload) {
  if (!payload) return "";
  return formatLocalTimestamp(payload.tested_at_ms);
}

function renderAiCapability(payload) {
  aiCapability = {
    available: payload && payload.available === true,
    provider_id: payload && payload.provider_id ? String(payload.provider_id) : "",
    provider_display_name: payload && payload.provider_display_name ? String(payload.provider_display_name) : "",
    model_id: payload && payload.model_id ? String(payload.model_id) : "",
    prompt_version: payload && payload.prompt_version ? String(payload.prompt_version) : "",
    execution: payload && payload.execution ? String(payload.execution) : "server",
    status: payload && payload.status ? String(payload.status) : "not_configured",
    configured: payload && payload.configured === true,
    credential_available: payload && payload.credential_available === true,
    last_status_check: payload && payload.last_status_check ? payload.last_status_check : null,
    last_connection_test: payload && payload.last_connection_test ? payload.last_connection_test : null,
    requires_explicit_confirmation: !payload || payload.requires_explicit_confirmation !== false,
  };
  aiCapabilityDiscoveryPending = false;
  aiCapabilityRevision += 1;
  const providerName = aiCapability.provider_display_name || aiCapability.provider_id;
  const providerInfo = aiCapability.model_id ? `${providerName} / ${aiCapability.model_id}` : providerName;
  const status = aiStatusInfo(aiCapability.status);
  if (aiCapability.status === "available") {
    setAiStatusClass("status--healthy");
    aiStatusText.textContent = status.heading;
    aiStatusDetail.textContent =
      `${providerInfo}; ${status.reason}; ${aiCapability.execution}.`;
    setAiStatusButtonState("healthy", "AI test successful");
    updateSettingsAiStatus();
    reconcileCatalogCardAiQuickActions();
    return;
  }
  setAiStatusClass(aiCapability.status === "configured_unverified" ? "status--loading" : "status--error");
  aiStatusText.textContent = status.heading;
  aiStatusDetail.textContent = status.reason;
  if (aiCapability.status === "configured_unverified") {
    setAiStatusButtonState("checking", "AI configured, untested");
  } else if (aiCapability.configured && aiCapability.credential_available) {
    setAiStatusButtonState("unhealthy", "AI test failed");
  } else {
    setAiStatusButtonState("unhealthy", "AI unavailable");
  }
  updateSettingsAiStatus();
  reconcileCatalogCardAiQuickActions();
}

async function loadAiCapability() {
  aiCapabilityDiscoveryPending = true;
  setAiStatusClass("status--loading");
  aiStatusText.textContent = "Checking AI status...";
  aiStatusDetail.textContent = "No provider request is made for capability discovery.";
  setAiStatusButtonState("checking", "Checking AI");
  updateSettingsAiStatus();
  reconcileCatalogCardAiQuickActions();
  try {
    const response = await fetch(AI_CAPABILITY_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      renderAiCapability({ available: false, status: "status_unavailable" });
      return;
    }
    renderAiCapability(await response.json());
  } catch {
    renderAiCapability({ available: false, status: "status_unavailable" });
  }
  await loadAutomaticAnalysisCapability();
}

async function loadAutomaticAnalysisCapability() {
  try {
    const response = await fetch(AUTOMATIC_ANALYSIS_CAPABILITY_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      automaticAnalysisCapability = {
        automatic_analysis_enabled: false,
        provider_configured: false,
      };
      return;
    }
    const payload = await response.json();
    automaticAnalysisCapability = {
      automatic_analysis_enabled: payload && payload.automatic_analysis_enabled === true,
      provider_configured: payload && payload.provider_configured === true,
    };
  } catch {
    automaticAnalysisCapability = {
      automatic_analysis_enabled: false,
      provider_configured: false,
    };
  }
}

function automaticAnalysisEndpoint(mediaId) {
  return `${MEDIA_METADATA_ENDPOINT_PREFIX}/${encodeURIComponent(mediaId)}/automatic-analysis`;
}

function durableAnalysisEndpoint(mediaId, locationId) {
  return `${MEDIA_METADATA_ENDPOINT_PREFIX}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/durable-analysis`;
}

function movieIdentificationEndpoint(mediaId) {
  return `${MEDIA_METADATA_ENDPOINT_PREFIX}/${encodeURIComponent(mediaId)}/movie-identification`;
}

function automaticAnalysisStatusMessage(payload) {
  if (!payload || !payload.state) return "";
  if (payload.state === "pending") return "AI analysis queued.";
  if (payload.state === "analyzing") return "AI analysis in progress.";
  if (payload.state === "analyzed") return "AI suggestion ready for review.";
  if (payload.state === "failed") {
    return payload.error_message || "AI analysis failed.";
  }
  return "";
}

function movieIdentificationStatusMessage(payload) {
  if (!payload || !payload.state) return "";
  if (payload.state === "pending") return "Movie identification queued.";
  if (payload.state === "analyzing") return "Movie identification in progress.";
  if (payload.state === "analyzed") {
    const result = payload.movie_identification_result;
    if (result && result.identification_status === "unknown") {
      return "Movie identification returned unknown with no editable suggestions.";
    }
    if (result && result.identification_status === "identified") {
      return "Movie identification ready for review.";
    }
    return "Movie identification ready for review.";
  }
  if (payload.state === "failed") {
    return payload.error_message || "Movie identification failed.";
  }
  return "";
}

function applyAutomaticAnalysisStatusToCard(mediaId, payload) {
  if (!mediaId || !payload) return;
  automaticAnalysisByMediaId.set(mediaId, payload);
  const card = catalogResults && Array.from(catalogResults.querySelectorAll(".catalog-card")).find(
    (element) => element.dataset.mediaId === String(mediaId),
  );
  if (!card) return;
  const status = card.querySelector(".catalog-card__analysis-status");
  if (!status) return;
  const message = automaticAnalysisStatusMessage(payload);
  status.textContent = message;
  status.hidden = !message;
  status.dataset.analysisLifecycle = payload.state;
  if (payload.state === "analyzed") {
    status.dataset.analysisSuccess = "true";
  } else {
    delete status.dataset.analysisSuccess;
  }
}

function stopAutomaticAnalysisPolling(mediaId) {
  const controller = automaticAnalysisPollControllers.get(mediaId);
  if (!controller) return;
  controller.stopped = true;
  automaticAnalysisPollControllers.delete(mediaId);
}

async function pollAutomaticAnalysisForMedia(mediaId) {
  if (!mediaId || !automaticAnalysisCapability.automatic_analysis_enabled) return;
  stopAutomaticAnalysisPolling(mediaId);
  const controller = { stopped: false, attempts: 0 };
  automaticAnalysisPollControllers.set(mediaId, controller);
  while (!controller.stopped && controller.attempts < AUTOMATIC_ANALYSIS_POLL_MAX_ATTEMPTS) {
    controller.attempts += 1;
    try {
      const response = await fetch(automaticAnalysisEndpoint(mediaId), {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) break;
      const payload = await response.json();
      applyAutomaticAnalysisStatusToCard(mediaId, payload);
      if (AUTOMATIC_ANALYSIS_TERMINAL_STATES.has(payload.state)) {
        stopAutomaticAnalysisPolling(mediaId);
        return;
      }
    } catch {
      break;
    }
    await new Promise((resolve) => {
      window.setTimeout(resolve, AUTOMATIC_ANALYSIS_POLL_INTERVAL_MS);
    });
  }
  stopAutomaticAnalysisPolling(mediaId);
}

function maybeTrackAutomaticAnalysisAfterCatalog(snapshot) {
  if (!snapshot || snapshot.state !== "cataloged" || !snapshot.media_id) return;
  if (!automaticAnalysisCapability.automatic_analysis_enabled) return;
  pollAutomaticAnalysisForMedia(snapshot.media_id);
}

function renderCloudStatus(payload) {
  const server = payload && payload.server === "connected" ? "Connected" : "Unavailable";
  const connection = payload && payload.connection ? String(payload.connection) : "unknown";
  const labels = {
    loopback: "Local loopback",
    lan: "LAN",
    tailscale: "Tailscale",
    unknown: "Unknown",
  };
  if (statusCloudServer) statusCloudServer.textContent = server;
  if (statusCloudConnection) statusCloudConnection.textContent = labels[connection] || "Unknown";
  const remote = payload && payload.remote_access ? String(payload.remote_access) : "";
  if (statusCloudRemoteRow && statusCloudRemote) {
    statusCloudRemoteRow.hidden = !remote;
    statusCloudRemote.textContent = remote;
  }
  renderTailscaleConnectionFields(payload);
}

function identityRoleLabel() {
  return identityState.role === "admin" ? "Admin" : identityState.role === "user" ? "User" : "";
}

function renderTailscaleIdentityFields() {
  const hostname = typeof location !== "undefined" && location.hostname ? location.hostname : "";
  const origin = typeof location !== "undefined" && location.origin ? location.origin : "";
  const httpsLabel =
    typeof location !== "undefined" && location.protocol === "https:"
      ? "Yes"
      : typeof location !== "undefined" && location.protocol
        ? "No"
        : "Unknown";
  if (statusTailscaleHostname) statusTailscaleHostname.textContent = hostname || "Unavailable";
  if (statusTailscaleUrl) statusTailscaleUrl.textContent = origin || "Unavailable";
  if (statusTailscaleHttps) statusTailscaleHttps.textContent = httpsLabel;
  if (statusTailscaleLogin) {
    statusTailscaleLogin.textContent = identityState.login || "Unavailable";
  }
  if (statusTailscaleDisplayName) {
    statusTailscaleDisplayName.textContent =
      identityState.displayName || identityState.login || "Unavailable";
  }
  if (statusTailscaleRole) {
    statusTailscaleRole.textContent = identityRoleLabel() || "Unavailable";
  }
  if (statusTailscaleProvenance) {
    statusTailscaleProvenance.textContent = identityState.provenance || "Unavailable";
  }
  if (statusTailscaleAccessMethod) {
    const viaTailscale =
      identityState.provenance === "tailscale-serve" ||
      (lastCloudStatusPayload && lastCloudStatusPayload.connection === "tailscale");
    statusTailscaleAccessMethod.textContent = viaTailscale ? "Tailscale" : "Unknown";
  }
}

function renderTailscaleConnectionFields(payload) {
  if (!statusTailscaleConnection) return;
  if (!payload) {
    statusTailscaleConnection.textContent = "Checking...";
    return;
  }
  const server = payload.server === "connected" ? "Connected" : "Unavailable";
  const connection = payload.connection ? String(payload.connection) : "unknown";
  const labels = {
    loopback: "Local loopback",
    lan: "LAN",
    tailscale: "Tailscale",
    unknown: "Unknown",
  };
  const connectionLabel = labels[connection] || "Unknown";
  statusTailscaleConnection.textContent = `${server} · ${connectionLabel}`;
  if (statusTailscaleAccessMethod) {
    const viaTailscale =
      identityState.provenance === "tailscale-serve" || connection === "tailscale";
    statusTailscaleAccessMethod.textContent = viaTailscale ? "Tailscale" : connectionLabel;
  }
}

let lastCloudStatusPayload = null;

async function loadCloudStatus() {
  renderCloudStatus({ server: "unavailable", connection: "unknown" });
  try {
    const response = await fetch(CLOUD_STATUS_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    lastCloudStatusPayload = payload;
    renderCloudStatus(payload);
  } catch {
    lastCloudStatusPayload = { server: "unavailable", connection: "unknown" };
    renderCloudStatus(lastCloudStatusPayload);
  }
}

function applyTailscalePanelDensity() {
  const showAdminDiagnostics = identityState.available && identityState.role === "admin";
  statusTailscaleAdminOnlyRows.forEach((row) => {
    row.hidden = !showAdminDiagnostics;
  });
}

function renderTailscaleStatus() {
  applyTailscalePanelDensity();
  renderTailscaleIdentityFields();
  if (lastCloudStatusPayload) {
    renderTailscaleConnectionFields(lastCloudStatusPayload);
  } else if (statusTailscaleConnection) {
    statusTailscaleConnection.textContent = "Checking...";
  }
}

async function loadTailscaleStatus() {
  renderTailscaleStatus();
  try {
    const response = await fetch(CLOUD_STATUS_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      lastCloudStatusPayload = { server: "unavailable", connection: "unknown" };
      renderTailscaleConnectionFields(lastCloudStatusPayload);
      renderTailscaleIdentityFields();
      return;
    }
    const payload = await response.json();
    lastCloudStatusPayload = payload;
    renderCloudStatus(payload);
    renderTailscaleIdentityFields();
  } catch {
    lastCloudStatusPayload = { server: "unavailable", connection: "unknown" };
    renderTailscaleConnectionFields(lastCloudStatusPayload);
    renderTailscaleIdentityFields();
  }
}

function showCatalogState(state) {
  catalogStateLoading.hidden = state !== "loading";
  catalogStateEmpty.hidden = state !== "empty";
  catalogStateUnavailable.hidden = state !== "unavailable";
  catalogStateError.hidden = state !== "error";
  catalogResults.hidden = state !== "success";
  if (catalogStateLoading) {
    catalogStateLoading.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }
  if (catalogBrowser) {
    catalogBrowser.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }
}

function inlineIcon(pathData, label) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const title = document.createElementNS(SVG_NAMESPACE, "title");
  title.textContent = label;
  const path = document.createElementNS(SVG_NAMESPACE, "path");
  path.setAttribute("d", pathData);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(title, path);
  return svg;
}

function editIcon() {
  return inlineIcon("M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z", "Edit");
}

function openOriginalIcon() {
  return inlineIcon("M14 5h5v5M19 5l-8 8M17 13v5H5V6h5", "Open original media");
}

function metadataEndpoint(mediaId) {
  return `${MEDIA_METADATA_ENDPOINT_PREFIX}/${mediaId}/metadata`;
}

function mediaAliasEndpoint(mediaId) {
  return `${MEDIA_METADATA_ENDPOINT_PREFIX}/${mediaId}/alias`;
}

function mediaAiSuggestionsEndpoint(mediaId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${mediaId}/ai-suggestions?limit=100`;
}

function mediaAiSuggestionEndpoint(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${mediaId}/locations/${locationId}/ai-suggestion-preview`;
}

function unicodeCodePointLength(value) {
  return [...value].length;
}

function hasControlCharacter(value) {
  return [...value].some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint <= 0x1f || (codePoint >= 0x7f && codePoint <= 0x9f);
  });
}

function hasForbiddenDescriptionControlChar(value) {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint === 0x0a) {
      continue;
    }
    if (
      codePoint <= 0x1f ||
      (codePoint >= 0x7f && codePoint <= 0x9f)
    ) {
      return true;
    }
  }
  return false;
}

function semanticArraysEqual(left, right) {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

function normalizedDescriptionState() {
  const rawDescription = metadataDescriptionInput.value;
  if (hasForbiddenDescriptionControlChar(rawDescription)) {
    return { error: "Description must not contain NUL, tab, carriage return, or control characters." };
  }
  if (unicodeCodePointLength(rawDescription) > MAX_METADATA_DESCRIPTION_CODE_POINTS) {
    return { error: `Description must be ${MAX_METADATA_DESCRIPTION_CODE_POINTS} characters or fewer.` };
  }
  const trimmed = rawDescription.trim();
  if (trimmed === "") {
    return { description: null };
  }
  if (trimmed !== rawDescription) {
    return { error: "Non-empty descriptions must not start or end with whitespace." };
  }
  if (/[\r\t]/.test(rawDescription)) {
    return { error: "Description must not contain tab or carriage return characters." };
  }
  return { description: rawDescription };
}

function normalizedMetadataFormState() {
  const rawTitle = metadataTitleInput.value;
  if (hasControlCharacter(rawTitle)) {
    return { error: "Title must not contain NUL or control characters." };
  }
  if (unicodeCodePointLength(rawTitle) > MAX_METADATA_TITLE_CODE_POINTS) {
    return { error: "Title must be 240 characters or fewer." };
  }
  if ((metadataWorkspace.current.genres || []).length > MAX_METADATA_GENRES) {
    return { error: `Select at most ${MAX_METADATA_GENRES} genres.` };
  }
  if (rawTitle.trim() === "") {
    const desc = normalizedDescriptionState();
    if (desc.error) {
      return desc;
    }
    return {
      displayTitle: null,
      description: desc.description,
      tagKeys: [...metadataWorkspace.current.tagKeys],
      contentCategory: metadataWorkspace.current.contentCategory || "general",
      acquisitionSource: metadataWorkspace.current.acquisitionSource || "unknown",
      genres: [...(metadataWorkspace.current.genres || [])],
      creatorAttributionKind: metadataWorkspace.current.creatorAttributionKind || null,
      creatorStableId: metadataWorkspace.current.creatorStableId || null,
      creatorHandle: metadataWorkspace.current.creatorHandle || null,
      creatorDisplayName: metadataWorkspace.current.creatorDisplayName || null,
    };
  }
  if (rawTitle.trim() !== rawTitle) {
    return { error: "Non-empty titles must not start or end with whitespace." };
  }
  const desc = normalizedDescriptionState();
  if (desc.error) {
    return desc;
  }
  return {
    displayTitle: rawTitle,
    description: desc.description,
    tagKeys: [...metadataWorkspace.current.tagKeys],
    contentCategory: metadataWorkspace.current.contentCategory || "general",
    acquisitionSource: metadataWorkspace.current.acquisitionSource || "unknown",
    genres: [...(metadataWorkspace.current.genres || [])],
    creatorAttributionKind: metadataWorkspace.current.creatorAttributionKind || null,
    creatorStableId: metadataWorkspace.current.creatorStableId || null,
    creatorHandle: metadataWorkspace.current.creatorHandle || null,
    creatorDisplayName: metadataWorkspace.current.creatorDisplayName || null,
  };
}

function metadataIsDirty() {
  const normalized = normalizedMetadataFormState();
  if (normalized.error) {
    return true;
  }
  const baselineCategory = metadataWorkspace.baseline.contentCategory || "general";
  const baselineGenres = metadataWorkspace.baseline.genres || [];
  return normalized.displayTitle !== metadataWorkspace.baseline.displayTitle
    || normalized.description !== metadataWorkspace.baseline.description
    || !semanticArraysEqual(normalized.tagKeys, metadataWorkspace.baseline.tagKeys)
    || (
      !metadataWorkspaceIsAliasMode()
      && (
        normalized.contentCategory !== baselineCategory
        || !semanticArraysEqual(normalized.genres, baselineGenres)
      )
    );
}

function selectedTagDefinition(key) {
  return canonicalTagDefinitions.find((tag) => tag.key === key) || null;
}

function normalizedTagDisplayName(value) {
  return value.trim().replace(/\s+/g, " ");
}

function tagDisplayNameError(displayName) {
  if (!displayName) {
    return "Enter a tag name.";
  }
  if (unicodeCodePointLength(displayName) > 80 || hasControlCharacter(displayName)) {
    return "Tag names must be 1 to 80 characters and contain no control characters.";
  }
  return "";
}

function tagSlugFromDisplayName(displayName) {
  const slug = displayName
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
  if (!slug || !/^[a-z]/.test(slug)) {
    return "";
  }
  return slug.slice(0, 64).replace(/-+$/g, "");
}

function uniqueTagKeyForDisplayName(displayName) {
  const base = tagSlugFromDisplayName(displayName);
  if (!base || !TAG_KEY_PATTERN.test(base)) {
    return "";
  }
  const existingKeys = new Set(canonicalTagDefinitions.map((tag) => tag.key));
  if (!existingKeys.has(base)) {
    return base;
  }
  for (let suffix = 2; suffix < 100; suffix += 1) {
    const suffixText = `-${suffix}`;
    const candidate = `${base.slice(0, 64 - suffixText.length).replace(/-+$/g, "")}${suffixText}`;
    if (TAG_KEY_PATTERN.test(candidate) && !existingKeys.has(candidate)) {
      return candidate;
    }
  }
  return "";
}

function findTagByDisplayName(displayName) {
  const normalized = displayName.toLocaleLowerCase();
  return canonicalTagDefinitions.find((tag) => tag.display_name.toLocaleLowerCase() === normalized) || null;
}

function metadataDirtyForBeforeUnload() {
  return metadataWorkspace.openMediaId !== null && (metadataIsDirty() || metadataWorkspace.aiSuggestionApplied);
}

function metadataBeforeUnloadHandler(event) {
  if (!metadataDirtyForBeforeUnload()) {
    return;
  }
  event.preventDefault();
  event.returnValue = "";
}

function syncMetadataBeforeUnloadProtection() {
  const shouldAttach = metadataDirtyForBeforeUnload();
  if (shouldAttach && !metadataBeforeUnloadAttached) {
    window.addEventListener("beforeunload", metadataBeforeUnloadHandler);
    metadataBeforeUnloadAttached = true;
  }
  if (!shouldAttach && metadataBeforeUnloadAttached) {
    window.removeEventListener("beforeunload", metadataBeforeUnloadHandler);
    metadataBeforeUnloadAttached = false;
  }
}

function formatSize(sizeBytes) {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KiB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function uploadEndpoint(uploadId) {
  return `${UPLOADS_ENDPOINT}/${encodeURIComponent(uploadId)}`;
}

function uploadCompleteEndpoint(uploadId) {
  return `${uploadEndpoint(uploadId)}/complete`;
}

function uploadDuplicateResolutionEndpoint(uploadId) {
  return `${uploadEndpoint(uploadId)}/duplicate-resolution`;
}

function activeUploadSnapshot() {
  return uploadState.snapshot;
}

function nextUploadGeneration() {
  uploadState.generation += 1;
  return uploadState.generation;
}

function currentUploadContext({
  uploadId = uploadState.uploadId,
  file = uploadState.file,
  generation = uploadState.generation,
} = {}) {
  return {
    generation,
    uploadId: uploadId || null,
    file: file || null,
  };
}

function uploadContextStillCurrent(context, { allowMissingUploadId = false } = {}) {
  if (!context || uploadState.generation !== context.generation) {
    return false;
  }
  if (context.file !== uploadState.file) {
    return false;
  }
  if (context.uploadId) {
    return uploadState.uploadId === context.uploadId;
  }
  return allowMissingUploadId ? !uploadState.uploadId : uploadState.uploadId === null;
}

function clearUploadRecovery() {
  try {
    clearMigratedStorageItem(window.localStorage, KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY, UPLOAD_RECOVERY_STORAGE_KEY);
  } catch {
    // The in-memory upload state remains authoritative for this browser view.
  }
}

function clearUploadPollTimer() {
  if (uploadState.pollTimer) {
    clearTimeout(uploadState.pollTimer);
    uploadState.pollTimer = null;
  }
}

function stopUploadPolling() {
  clearUploadPollTimer();
  uploadState.pollOwner = null;
  uploadState.pollRetryDelayMs = UPLOAD_POLL_INTERVAL_MS;
}

function invalidateUploadOwnership() {
  stopUploadPolling();
  uploadState.publicationPollAttempts = 0;
  nextUploadGeneration();
  uploadState.actionOwner = null;
  uploadState.uploadLoopOwner = null;
  uploadState.completionOwner = null;
  uploadState.preparing = false;
  uploadState.running = false;
  uploadState.paused = false;
  uploadState.completing = false;
}

function claimUploadAction(kind, { uploadId = uploadState.uploadId, file = uploadState.file } = {}, options = {}) {
  if (uploadState.actionOwner && !options.supersede) {
    return null;
  }
  stopUploadPolling();
  const owner = currentUploadContext({
    generation: nextUploadGeneration(),
    uploadId,
    file,
  });
  owner.kind = kind;
  uploadState.actionOwner = owner;
  return owner;
}

function releaseUploadAction(owner) {
  if (uploadState.actionOwner === owner) {
    uploadState.actionOwner = null;
  }
}

function uploadStatusErrorCode(result) {
  return result && result.payload && result.payload.error
    ? String(result.payload.error.code || "")
    : "";
}

function uploadStatusWasNotFound(result) {
  return Boolean(
    result
      && (
        result.status === 404
        || uploadStatusErrorCode(result) === "UPLOAD_SESSION_NOT_FOUND"
      ),
  );
}

function uploadIsByteReceiving(snapshot) {
  return Boolean(
    snapshot
      && (snapshot.state === "created" || snapshot.state === "receiving")
      && snapshot.received_size_bytes < snapshot.declared_size_bytes,
  );
}

function uploadShouldPoll(snapshot) {
  return Boolean(
    snapshot
      && (
        snapshot.state === "received"
        || snapshot.state === "validating"
        || (
          snapshot.state === "publish_pending"
          && uploadState.publicationPollAttempts < UPLOAD_PUBLICATION_POLL_MAX_ATTEMPTS
        )
        || snapshot.state === "published"
      ),
  );
}

function uploadIsPollingStopState(snapshot) {
  return Boolean(
    snapshot
      && ["published", "cataloged", "rejected", "failed", "cancelled", "expired"].includes(snapshot.state),
  );
}

function uploadCancelPermitted(snapshot) {
  return Boolean(
    snapshot
      && ["created", "receiving", "received"].includes(snapshot.state),
  );
}

function uploadDisplayState(snapshot) {
  if (!snapshot) {
    if (uploadState.preparing) return "Preparing";
    if (selectedUploadLimitMessage()) return "File too large";
    return uploadState.file ? "Ready" : "No file selected";
  }
  if (
    uploadState.actionOwner
    && uploadState.actionOwner.kind === "cancel"
    && uploadCancelPermitted(snapshot)
  ) {
    return "Cancelling";
  }
  if (snapshot.state === "duplicate_pending" && uploadState.actionOwner) {
    if (uploadState.actionOwner.kind === "duplicate-keep") return "Keeping duplicate";
    if (uploadState.actionOwner.kind === "duplicate-discard") return "Discarding duplicate";
  }
  if (uploadState.needsReselection && uploadIsByteReceiving(snapshot)) {
    return "Reselect file to resume";
  }
  if (uploadState.preparing && uploadIsByteReceiving(snapshot)) {
    return "Preparing";
  }
  if (uploadState.paused && uploadState.running && uploadIsByteReceiving(snapshot)) {
    return "Pausing";
  }
  if (uploadState.paused && uploadIsByteReceiving(snapshot)) {
    return "Paused";
  }
  if (uploadState.running && uploadIsByteReceiving(snapshot)) {
    return "Uploading";
  }
  if (snapshot.state === "created") {
    return uploadState.file ? "Ready to resume" : "Reselect file to resume";
  }
  if (snapshot.state === "receiving") {
    return uploadIsByteReceiving(snapshot) ? "Ready to resume" : "Validating";
  }
  if (snapshot.state === "received") return "Validating";
  if (snapshot.state === "validating") return "Validating";
  if (snapshot.state === "duplicate_pending") {
    return identityHasCapability("upload.manage") ? "Duplicate found" : "Validating";
  }
  if (snapshot.state === "publish_pending") return "Publishing";
  if (snapshot.state === "published") return "Published";
  if (snapshot.state === "cataloged") return "Completed";
  if (snapshot.state === "rejected") return "Failed";
  if (snapshot.state === "failed") return "Failed";
  if (snapshot.state === "cancelled") return "Cancelled";
  if (snapshot.state === "expired") return "Failed";
  return "Status unavailable";
}

function uploadFailureText(snapshot) {
  if (!snapshot) return "";
  if (snapshot.failure_code) {
    return `Error: Sanitized failure code: ${snapshot.failure_code}`;
  }
  if (snapshot.state === "rejected") {
    return "Rejected: The server rejected the uploaded media with sanitized failure information.";
  }
  if (snapshot.state === "failed") {
    return "Failed: The upload failed with sanitized server failure information.";
  }
  if (snapshot.state === "cancelled") {
    return "Cancelled: Upload was cancelled before Gallery publication.";
  }
  if (snapshot.state === "expired") {
    return "Expired: Upload session expired before completion.";
  }
  return "";
}

function uploadProgressPercentValue(snapshot) {
  if (!snapshot || snapshot.declared_size_bytes <= 0) return 0;
  return Math.min(100, Math.floor((snapshot.received_size_bytes / snapshot.declared_size_bytes) * 100));
}

function uploadStatusMessage(snapshot) {
  if (!snapshot) {
    const fileLimitMessage = selectedUploadLimitMessage();
    if (fileLimitMessage) return fileLimitMessage;
    return uploadState.message || "Select one local GIF, MP4, JPEG, or PNG.";
  }
  if (snapshot.state === "publish_pending") {
    if (uploadState.publicationPollAttempts >= UPLOAD_PUBLICATION_POLL_MAX_ATTEMPTS) {
      return "Validated. Publication is still pending. Reload to check again. Not yet available in Gallery.";
    }
    return "Validated. Awaiting publication. Not yet available in Gallery.";
  }
  if (snapshot.state === "published") {
    return "Published. Awaiting cataloging. Not yet available in Gallery.";
  }
  if (snapshot.state === "cataloged") {
    if (identityHasCapability("upload.manage")) {
      if (automaticAnalysisCapability.automatic_analysis_enabled) {
        return "Cataloged. Available in Gallery. Automatic AI analysis may follow.";
      }
      return "Cataloged. Available in Gallery.";
    }
    return "Submission succeeded. It awaits administrator review and is not public yet.";
  }
  if (snapshot.state === "received") {
    return "Bytes received. Waiting for server validation.";
  }
  if (snapshot.state === "validating") {
    return "Server validation is running.";
  }
  if (snapshot.state === "duplicate_pending") {
    if (!identityHasCapability("upload.manage")) {
      return "Server validation is running.";
    }
    if (uploadState.actionOwner && uploadState.message) return uploadState.message;
    return "Exact duplicate found.";
  }
  if (uploadState.needsReselection && uploadIsByteReceiving(snapshot)) {
    return "Reselect the original local file to resume from the server-confirmed offset.";
  }
  if (uploadState.paused && uploadIsByteReceiving(snapshot)) {
    return "Paused in this browser after the current request settled.";
  }
  if (uploadState.message) {
    return uploadState.message;
  }
  return "Server state is authoritative.";
}

function selectedUploadLimitMessage() {
  if (
    uploadState.file
    && uploadCapability.max_total_size_bytes > 0
    && uploadState.file.size > uploadCapability.max_total_size_bytes
  ) {
    return `File is too large. Maximum size is ${formatSize(uploadCapability.max_total_size_bytes)}.`;
  }
  return "";
}

function normalizeUploadSnapshot(payload) {
  const uploadId = payload && payload.id ? String(payload.id) : "";
  if (!UPLOAD_PUBLIC_ID_PATTERN.test(uploadId)) {
    return null;
  }
  const declaredSize = Number(payload.declared_size_bytes);
  const receivedSize = Number(payload.received_size_bytes);
  const expiresAt = Number(payload.expires_at);
  if (
    !Number.isSafeInteger(declaredSize)
    || declaredSize <= 0
    || !Number.isSafeInteger(receivedSize)
    || receivedSize < 0
    || receivedSize > declaredSize
  ) {
    return null;
  }
  return {
    id: uploadId,
    state: String(payload.state || ""),
    display_filename: payload.display_filename === undefined
      ? String(
        activeUploadSnapshot() && activeUploadSnapshot().id === uploadId
          ? activeUploadSnapshot().display_filename
          : uploadState.fileNameHint,
      )
      : String(payload.display_filename || ""),
    declared_size_bytes: declaredSize,
    received_size_bytes: receivedSize,
    expires_at: Number.isFinite(expiresAt) ? expiresAt : 0,
    failure_code: payload.failure_code ? String(payload.failure_code) : "",
  };
}

function uploadRecoveryStateCanPersist(snapshot) {
  return Boolean(
    snapshot
      && UPLOAD_KNOWN_STATES.has(snapshot.state)
      && !UPLOAD_RECOVERY_CLEANUP_STATES.has(snapshot.state),
  );
}

function uploadRecoveryFileNameHint(value) {
  if (typeof value !== "string") return null;
  if (value.length > 255) return null;
  if (/[\u0000-\u001f/\\]/u.test(value)) return null;
  return value;
}

function uploadRecoverySize(value) {
  const size = Number(value);
  return Number.isSafeInteger(size) && size > 0 ? size : null;
}

function uploadRecoveryLastModified(value) {
  if (value === undefined || value === null) return null;
  const lastModified = Number(value);
  return Number.isSafeInteger(lastModified) && lastModified >= 0 ? lastModified : undefined;
}

function saveUploadRecovery(snapshot = activeUploadSnapshot()) {
  if (!snapshot || !snapshot.id) return;
  if (!uploadRecoveryStateCanPersist(snapshot)) {
    clearUploadRecovery();
    return;
  }
  const expectedSizeBytes = uploadRecoverySize(snapshot.declared_size_bytes);
  if (expectedSizeBytes === null) {
    clearUploadRecovery();
    return;
  }
  const fileNameHint = uploadRecoveryFileNameHint(
    snapshot.display_filename || uploadState.fileNameHint || "",
  );
  const lastModifiedHint = uploadRecoveryLastModified(uploadState.lastModifiedHint);
  if (fileNameHint === null || lastModifiedHint === undefined) {
    clearUploadRecovery();
    return;
  }
  const recovery = {
    upload_id: snapshot.id,
    file_name_hint: fileNameHint,
    expected_size_bytes: expectedSizeBytes,
    last_modified_hint: lastModifiedHint,
    last_known_state: snapshot.state,
  };
  try {
    window.localStorage.setItem(KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY, JSON.stringify(recovery));
  } catch {
    uploadState.message = "Upload recovery could not be saved in this browser.";
  }
}

function loadUploadRecovery() {
  try {
    const raw = readMigratedStorageItem(
      window.localStorage,
      KRONIKA_UPLOAD_RECOVERY_STORAGE_KEY,
      UPLOAD_RECOVERY_STORAGE_KEY,
    );
    if (!raw) return null;
    if (raw.length > 4096) {
      clearUploadRecovery();
      return null;
    }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      clearUploadRecovery();
      return null;
    }
    const uploadId = typeof parsed.upload_id === "string" ? parsed.upload_id : "";
    const fileNameHint = uploadRecoveryFileNameHint(parsed.file_name_hint || "");
    const expectedSizeBytes = uploadRecoverySize(parsed.expected_size_bytes);
    const lastModifiedHint = uploadRecoveryLastModified(parsed.last_modified_hint);
    const lastKnownState = parsed.last_known_state === undefined || parsed.last_known_state === null
      ? ""
      : String(parsed.last_known_state);
    if (
      !UPLOAD_PUBLIC_ID_PATTERN.test(uploadId)
      || fileNameHint === null
      || expectedSizeBytes === null
      || lastModifiedHint === undefined
      || (lastKnownState && !UPLOAD_KNOWN_STATES.has(lastKnownState))
    ) {
      clearUploadRecovery();
      return null;
    }
    if (lastKnownState && UPLOAD_RECOVERY_CLEANUP_STATES.has(lastKnownState)) {
      clearUploadRecovery();
      return null;
    }
    return {
      upload_id: uploadId,
      file_name_hint: fileNameHint,
      expected_size_bytes: expectedSizeBytes,
      last_modified_hint: lastModifiedHint,
      last_known_state: lastKnownState,
    };
  } catch {
    clearUploadRecovery();
    return null;
  }
}

function applyUploadSnapshot(snapshot, owner = null, options = {}) {
  if (!snapshot) return false;
  const context = owner || currentUploadContext({ uploadId: uploadState.uploadId });
  const allowAdoptUploadId = options.allowAdoptUploadId === true;
  if (!uploadContextStillCurrent(context, { allowMissingUploadId: allowAdoptUploadId })) {
    return false;
  }
  const expectedUploadId = context.uploadId || uploadState.uploadId;
  if (expectedUploadId && snapshot.id !== expectedUploadId) {
    return false;
  }
  if (!expectedUploadId && !allowAdoptUploadId) {
    return false;
  }
  if (allowAdoptUploadId && !context.uploadId && !uploadState.uploadId) {
    context.uploadId = snapshot.id;
  }
  const previousSnapshot = uploadState.snapshot;
  if (snapshot.state === "publish_pending") {
    uploadState.publicationPollAttempts = (
      previousSnapshot
      && previousSnapshot.id === snapshot.id
      && previousSnapshot.state === "publish_pending"
    ) ? uploadState.publicationPollAttempts + 1 : 0;
  } else {
    uploadState.publicationPollAttempts = 0;
  }
  uploadState.snapshot = snapshot;
  uploadState.uploadId = snapshot.id;
  uploadState.fileNameHint = snapshot.display_filename || uploadState.fileNameHint;
  uploadState.expectedSizeBytes = snapshot.declared_size_bytes;
  uploadState.failureMessage = uploadFailureText(snapshot);
  if (!uploadIsByteReceiving(snapshot)) {
    uploadState.needsReselection = false;
  }
  saveUploadRecovery(snapshot);
  renderUploadCockpit();
  if (snapshot.state === "cataloged") {
    refreshGalleryAfterCataloged(snapshot.id);
    if (identityHasCapability("upload.manage")) {
      maybeTrackAutomaticAnalysisAfterCatalog(snapshot);
    }
  }
  return true;
}

function refreshGalleryAfterCataloged(uploadId) {
  if (!uploadId || uploadState.galleryCatalogRefreshUploadId === uploadId) {
    return;
  }
  uploadState.galleryCatalogRefreshUploadId = uploadId;
  if (typeof loadCatalog === "function") {
    loadCatalog();
  }
}

function resetUploadForFile(file) {
  invalidateUploadOwnership();
  clearUploadRecovery();
  uploadState = {
    generation: uploadState.generation,
    uploadId: null,
    file,
    fileNameHint: file ? file.name : "",
    expectedSizeBytes: file ? file.size : 0,
    lastModifiedHint: file && Number.isFinite(file.lastModified) ? file.lastModified : null,
    snapshot: null,
    actionOwner: null,
    preparing: false,
    running: false,
    paused: false,
    needsReselection: false,
    completing: false,
    uploadLoopOwner: null,
    completionOwner: null,
    pollOwner: null,
    pollTimer: null,
    pollRetryDelayMs: UPLOAD_POLL_INTERVAL_MS,
    publicationPollAttempts: 0,
    galleryCatalogRefreshUploadId: null,
    message: file ? "Ready to upload." : "Select one local GIF, MP4, JPEG, or PNG.",
    failureMessage: "",
  };
  renderUploadCockpit();
}

function renderUploadCockpit() {
  const snapshot = activeUploadSnapshot();
  const displayName = snapshot
    ? snapshot.display_filename
    : (uploadState.file ? uploadState.file.name : uploadState.fileNameHint);
  const totalBytes = snapshot ? snapshot.declared_size_bytes : (uploadState.file ? uploadState.file.size : 0);
  const receivedBytes = snapshot ? snapshot.received_size_bytes : 0;
  const percent = uploadProgressPercentValue(snapshot);
  if (uploadFileName) uploadFileName.textContent = displayName || "";
  if (uploadStateLabel) uploadStateLabel.textContent = uploadDisplayState(snapshot);
  if (uploadProgress) uploadProgress.value = percent;
  if (uploadByteCount) uploadByteCount.textContent = `${formatSize(receivedBytes)} / ${formatSize(totalBytes)}`;
  if (uploadPercent) uploadPercent.textContent = `${percent}%`;
  if (uploadMessage) uploadMessage.textContent = uploadStatusMessage(snapshot);
  if (uploadFailure) {
    const failure = uploadState.failureMessage || uploadFailureText(snapshot);
    uploadFailure.hidden = !failure;
    uploadFailure.textContent = failure;
  }
  if (uploadRow) {
    uploadRow.dataset.state = snapshot ? snapshot.state : "idle";
  }
  updateUploadActions();
}

function updateUploadActions() {
  const snapshot = activeUploadSnapshot();
  const hasFile = Boolean(uploadState.file);
  const hasActiveSession = Boolean(snapshot);
  const fileWithinLimit = hasFile
    && uploadCapability.max_total_size_bytes > 0
    && uploadState.file.size <= uploadCapability.max_total_size_bytes;
  const startVisible = uploadCapability.uploads_enabled
    && hasFile
    && !hasActiveSession
    && fileWithinLimit
    && !uploadState.actionOwner
    && !uploadState.preparing
    && !uploadState.running
    && !uploadState.completing;
  const pauseVisible = uploadState.running
    && !uploadState.paused
    && uploadIsByteReceiving(snapshot);
  const resumeVisible = Boolean(snapshot)
    && !uploadState.actionOwner
    && !uploadState.preparing
    && !uploadState.running
    && !uploadState.completing
    && uploadIsByteReceiving(snapshot)
    && hasFile
    && !uploadState.needsReselection;
  const cancelVisible = uploadCancelPermitted(snapshot)
    && !uploadState.completing
    && (!uploadState.actionOwner || uploadState.actionOwner.kind !== "cancel");
  const duplicateVisible = Boolean(
    snapshot
    && snapshot.state === "duplicate_pending"
    && identityHasCapability("upload.manage"),
  );
  const duplicateDisabled = !duplicateVisible
    || Boolean(uploadState.actionOwner)
    || uploadState.completing;
  const focusedAction = [
    uploadStartButton,
    uploadPauseButton,
    uploadResumeButton,
    uploadDuplicateKeepButton,
    uploadDuplicateDiscardButton,
    uploadCancelButton,
  ].find((button) => button && document.activeElement === button);
  if (uploadStartButton) {
    uploadStartButton.hidden = !startVisible;
    uploadStartButton.disabled = !startVisible;
  }
  if (uploadPauseButton) {
    uploadPauseButton.hidden = !pauseVisible;
    uploadPauseButton.disabled = !pauseVisible;
  }
  if (uploadResumeButton) {
    uploadResumeButton.hidden = !resumeVisible;
    uploadResumeButton.disabled = !resumeVisible;
  }
  if (uploadDuplicateKeepButton) {
    uploadDuplicateKeepButton.hidden = !duplicateVisible;
    uploadDuplicateKeepButton.disabled = duplicateDisabled;
  }
  if (uploadDuplicateDiscardButton) {
    uploadDuplicateDiscardButton.hidden = !duplicateVisible;
    uploadDuplicateDiscardButton.disabled = duplicateDisabled;
  }
  if (uploadCancelButton) {
    uploadCancelButton.hidden = !cancelVisible;
    uploadCancelButton.disabled = !cancelVisible;
  }
  if (focusedAction && focusedAction.hidden && uploadMessage) {
    uploadMessage.focus();
  }
}

async function fetchUploadJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  return { response, payload };
}

function uploadErrorMessage(payload) {
  const code = payload && payload.error ? String(payload.error.code || "") : "";
  if (code === "UPLOAD_CAPABILITY_NOT_CONFIGURED") return "Upload capability is not configured.";
  if (code === "UPLOAD_TOO_LARGE") return "Upload exceeds the server limit.";
  if (code === "UPLOAD_CHUNK_TOO_LARGE") return "Upload chunk exceeded the server limit.";
  if (code === "UPLOAD_OFFSET_CONFLICT") return "Server offset changed; refreshed from server truth.";
  if (code === "UPLOAD_SESSION_STATE_CONFLICT") return "Upload state changed on the server.";
  if (code === "UPLOAD_SESSION_EXPIRED") return "Upload session expired.";
  if (code === "UPLOAD_SESSION_NOT_FOUND") return "Upload session was not found.";
  if (code === "INSUFFICIENT_QUARANTINE_STORAGE") return "Insufficient quarantine storage.";
  if (code === "UPLOAD_BODY_LENGTH_MISMATCH") return "Upload body length mismatch.";
  if (code === "QUARANTINE_STATE_INCONSISTENT") return "Quarantine state is inconsistent.";
  if (code === "UPLOAD_CONCURRENCY_CONFLICT") return "Upload concurrency conflict.";
  if (code === "QUARANTINE_STORAGE_UNAVAILABLE") return "Quarantine storage is unavailable.";
  return "Upload failed with a sanitized local error.";
}

async function loadUploadCapability(owner = null) {
  try {
    const { response, payload } = await fetchUploadJson(UPLOAD_CAPABILITY_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (owner && !uploadContextStillCurrent(owner, { allowMissingUploadId: true })) {
      return null;
    }
    if (!response.ok) {
      uploadCapability = {
        uploads_enabled: false,
        max_total_size_bytes: 0,
        max_chunk_size_bytes: DEFAULT_UPLOAD_CHUNK_BYTES,
        session_ttl_seconds: 0,
      };
      uploadState.message = uploadErrorMessage(payload);
      renderUploadCockpit();
      return false;
    }
    uploadCapability = {
      uploads_enabled: payload && payload.uploads_enabled === true,
      max_total_size_bytes: Number(payload && payload.max_total_size_bytes) || 0,
      max_chunk_size_bytes: Number(payload && payload.max_chunk_size_bytes) || DEFAULT_UPLOAD_CHUNK_BYTES,
      session_ttl_seconds: Number(payload && payload.session_ttl_seconds) || 0,
    };
    if (!uploadCapability.uploads_enabled) {
      uploadState.message = "Uploads are not configured on this local server.";
    }
    renderUploadCockpit();
    return uploadCapability.uploads_enabled;
  } catch {
    if (owner && !uploadContextStillCurrent(owner, { allowMissingUploadId: true })) {
      return null;
    }
    uploadCapability = {
      uploads_enabled: false,
      max_total_size_bytes: 0,
      max_chunk_size_bytes: DEFAULT_UPLOAD_CHUNK_BYTES,
      session_ttl_seconds: 0,
    };
    uploadState.message = "Upload capability could not be loaded.";
    renderUploadCockpit();
    return false;
  }
}

async function requestUploadStatus(uploadId) {
  if (!uploadId || !UPLOAD_PUBLIC_ID_PATTERN.test(String(uploadId))) {
    return {
      ok: false,
      status: 404,
      payload: { error: { code: "UPLOAD_SESSION_NOT_FOUND" } },
    };
  }
  try {
    const { response, payload } = await fetchUploadJson(uploadEndpoint(uploadId), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        payload,
      };
    }
    const snapshot = normalizeUploadSnapshot(payload);
    if (!snapshot) {
      return {
        ok: false,
        status: response.status,
        payload,
        parseFailed: true,
      };
    }
    return { ok: true, snapshot };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      payload: null,
      networkError: true,
      error,
    };
  }
}

function uploadStatusResultMessage(result) {
  if (result && result.payload) {
    return uploadErrorMessage(result.payload);
  }
  if (result && result.parseFailed) {
    return "Upload status response could not be parsed.";
  }
  return "Upload status could not be loaded.";
}

function clearStaleUploadRecoveryState(message) {
  stopUploadPolling();
  clearUploadRecovery();
  uploadState.uploadId = null;
  uploadState.snapshot = null;
  uploadState.fileNameHint = "";
  uploadState.expectedSizeBytes = 0;
  uploadState.lastModifiedHint = null;
  uploadState.needsReselection = false;
  uploadState.publicationPollAttempts = 0;
  uploadState.galleryCatalogRefreshUploadId = null;
  uploadState.failureMessage = "";
  uploadState.message = message
    || "This submission expired or is unavailable. You can start a new upload.";
  renderUploadCockpit();
}

async function refreshUploadStatus(
  uploadId = uploadState.uploadId,
  owner = currentUploadContext({ uploadId }),
  options = {},
) {
  if (!uploadId) return null;
  const result = await requestUploadStatus(uploadId);
  if (!uploadContextStillCurrent(owner)) {
    return null;
  }
  if (result.ok) {
    return applyUploadSnapshot(result.snapshot, owner) ? result.snapshot : null;
  }
  if (options.clearNotFound && uploadStatusWasNotFound(result)) {
    clearStaleUploadRecoveryState();
    return null;
  }
  uploadState.message = uploadStatusResultMessage(result);
  renderUploadCockpit();
  return null;
}

async function restoreUploadRecovery() {
  const recovery = loadUploadRecovery();
  if (!recovery || uploadState.uploadId) {
    renderUploadCockpit();
    return;
  }
  invalidateUploadOwnership();
  const owner = currentUploadContext({
    generation: uploadState.generation,
    uploadId: recovery.upload_id,
    file: null,
  });
  uploadState.uploadId = recovery.upload_id;
  uploadState.fileNameHint = recovery.file_name_hint;
  uploadState.expectedSizeBytes = recovery.expected_size_bytes;
  uploadState.lastModifiedHint = recovery.last_modified_hint;
  uploadState.message = "Recovering saved upload session...";
  renderUploadCockpit();
  const result = await requestUploadStatus(recovery.upload_id);
  if (!uploadContextStillCurrent(owner)) return;
  if (!result.ok) {
    if (uploadStatusWasNotFound(result)) {
      clearStaleUploadRecoveryState();
    } else {
      uploadState.message = uploadStatusResultMessage(result);
      renderUploadCockpit();
    }
    return;
  }
  const snapshot = result.snapshot;
  if (!applyUploadSnapshot(snapshot, owner)) return;
  if (uploadIsByteReceiving(snapshot)) {
    uploadState.needsReselection = true;
    uploadState.message = "Reselect the original file to resume this upload.";
    renderUploadCockpit();
  } else if (uploadShouldPoll(snapshot) && uploadDialog && uploadDialog.hasAttribute("open")) {
    scheduleUploadPolling(currentUploadContext({ uploadId: snapshot.id }));
  }
}

function scheduleUploadPolling(owner = currentUploadContext()) {
  clearUploadPollTimer();
  const snapshot = activeUploadSnapshot();
  if (!uploadShouldPoll(snapshot)) return;
  if (uploadDialog && !uploadDialog.hasAttribute("open")) return;
  if (!uploadContextStillCurrent(owner)) return;
  owner.uploadId = snapshot.id;
  uploadState.pollOwner = owner;
  const retryDelayMs = Math.min(uploadState.pollRetryDelayMs, UPLOAD_POLL_RETRY_MAX_MS);
  uploadState.pollTimer = window.setTimeout(() => {
    pollUploadStatus(owner);
  }, retryDelayMs);
}

async function pollUploadStatus(owner = uploadState.pollOwner) {
  if (owner !== uploadState.pollOwner || !uploadContextStillCurrent(owner)) return;
  const result = await requestUploadStatus(owner.uploadId);
  if (owner !== uploadState.pollOwner || !uploadContextStillCurrent(owner)) return;
  if (result.ok) {
    uploadState.pollRetryDelayMs = UPLOAD_POLL_INTERVAL_MS;
    const snapshot = result.snapshot;
    if (!applyUploadSnapshot(snapshot, owner)) return;
    if (uploadShouldPoll(snapshot)) {
      scheduleUploadPolling(currentUploadContext({ uploadId: snapshot.id }));
    } else {
      stopUploadPolling();
    }
    return;
  }
  if (uploadStatusWasNotFound(result)) {
    clearStaleUploadRecoveryState();
    return;
  }
  uploadState.message = "Upload status is temporarily unavailable; retrying.";
  renderUploadCockpit();
  if (uploadShouldPoll(activeUploadSnapshot())) {
    uploadState.pollRetryDelayMs = Math.min(
      UPLOAD_POLL_RETRY_MAX_MS,
      Math.max(UPLOAD_POLL_INTERVAL_MS, uploadState.pollRetryDelayMs * 2),
    );
    scheduleUploadPolling(owner);
  } else {
    stopUploadPolling();
  }
}

function selectedUploadChunkSize(remainingBytes) {
  const serverLimit = Number(uploadCapability.max_chunk_size_bytes) || DEFAULT_UPLOAD_CHUNK_BYTES;
  return Math.max(1, Math.min(serverLimit, DEFAULT_UPLOAD_CHUNK_BYTES, remainingBytes));
}

async function completeUploadIfReady(owner = currentUploadContext()) {
  if (!uploadContextStillCurrent(owner)) return false;
  const snapshot = activeUploadSnapshot();
  if (
    !snapshot
    || snapshot.id !== owner.uploadId
    || snapshot.received_size_bytes !== snapshot.declared_size_bytes
  ) {
    return false;
  }
  if (uploadState.completionOwner) {
    return false;
  }
  uploadState.completionOwner = owner;
  uploadState.completing = true;
  uploadState.message = "Completing byte reception.";
  renderUploadCockpit();
  try {
    const { response, payload } = await fetchUploadJson(uploadCompleteEndpoint(snapshot.id), {
      method: "POST",
      headers: framenestMutationHeaders({ Accept: "application/json" }),
      cache: "no-store",
    });
    if (!uploadContextStillCurrent(owner) || uploadState.completionOwner !== owner) return false;
    if (response.ok) {
      const completed = normalizeUploadSnapshot(payload);
      if (!applyUploadSnapshot(completed, owner)) return false;
      uploadState.message = "Bytes received. Waiting for server validation.";
      if (uploadShouldPoll(completed)) {
        scheduleUploadPolling(currentUploadContext({ uploadId: completed.id }));
      }
      return true;
    }
    uploadState.message = uploadErrorMessage(payload);
    renderUploadCockpit();
    const refreshed = await refreshUploadStatus(snapshot.id, owner);
    if (uploadContextStillCurrent(owner) && uploadShouldPoll(refreshed)) {
      scheduleUploadPolling(currentUploadContext({ uploadId: refreshed.id }));
    }
  } catch {
    if (uploadContextStillCurrent(owner) && uploadState.completionOwner === owner) {
      uploadState.message = "Completion status could not be confirmed.";
      renderUploadCockpit();
      await refreshUploadStatus(snapshot.id, owner);
    }
  } finally {
    if (uploadState.completionOwner === owner) {
      uploadState.completionOwner = null;
      uploadState.completing = false;
      uploadState.running = false;
      renderUploadCockpit();
    }
  }
  return false;
}

async function runUploadLoop(owner = currentUploadContext()) {
  if (!uploadContextStillCurrent(owner)) return false;
  if (uploadState.uploadLoopOwner && uploadState.uploadLoopOwner !== owner) return false;
  uploadState.uploadLoopOwner = owner;
  uploadState.running = true;
  uploadState.paused = false;
  uploadState.message = "Uploading from server-confirmed offset.";
  renderUploadCockpit();
  while (uploadState.uploadLoopOwner === owner && uploadContextStillCurrent(owner)) {
    let snapshot = await refreshUploadStatus(owner.uploadId, owner);
    if (uploadState.uploadLoopOwner !== owner || !uploadContextStillCurrent(owner) || !snapshot) break;
    if (!uploadIsByteReceiving(snapshot)) {
      if (
        uploadState.paused
        && snapshot.state === "receiving"
        && snapshot.received_size_bytes === snapshot.declared_size_bytes
      ) {
        uploadState.running = false;
        uploadState.message = "Paused in this browser.";
        renderUploadCockpit();
        break;
      }
      if (snapshot.received_size_bytes === snapshot.declared_size_bytes && snapshot.state === "receiving") {
        await completeUploadIfReady(owner);
      } else if (uploadShouldPoll(snapshot)) {
        scheduleUploadPolling(currentUploadContext({ uploadId: snapshot.id }));
      }
      break;
    }
    if (uploadState.paused) {
      uploadState.running = false;
      uploadState.message = "Paused in this browser.";
      renderUploadCockpit();
      break;
    }
    if (!owner.file || uploadState.file !== owner.file) {
      uploadState.running = false;
      uploadState.needsReselection = true;
      uploadState.message = "Reselect the original local file to resume.";
      renderUploadCockpit();
      break;
    }
    if (owner.file.size !== snapshot.declared_size_bytes) {
      uploadState.running = false;
      uploadState.needsReselection = true;
      uploadState.file = null;
      uploadState.message = "Selected file size does not match this upload session.";
      renderUploadCockpit();
      break;
    }
    const serverOffset = snapshot.received_size_bytes;
    const remainingBytes = snapshot.declared_size_bytes - serverOffset;
    if (remainingBytes <= 0) {
      await completeUploadIfReady(owner);
      break;
    }
    const chunkSize = selectedUploadChunkSize(remainingBytes);
    const body = owner.file.slice(serverOffset, serverOffset + chunkSize);
    try {
      const { response, payload } = await fetchUploadJson(uploadEndpoint(snapshot.id), {
        method: "PATCH",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/offset+octet-stream",
          "Upload-Offset": String(serverOffset),
        }),
        body,
        cache: "no-store",
      });
      if (!uploadContextStillCurrent(owner) || uploadState.uploadLoopOwner !== owner) return false;
      if (!response.ok) {
        uploadState.message = uploadErrorMessage(payload);
        renderUploadCockpit();
        await refreshUploadStatus(snapshot.id, owner);
        if (!uploadContextStillCurrent(owner) || uploadState.uploadLoopOwner !== owner) return false;
        if (payload && payload.error && payload.error.current_offset !== undefined) {
          continue;
        }
        break;
      }
      snapshot = normalizeUploadSnapshot(payload);
      if (!applyUploadSnapshot(snapshot, owner)) return false;
      if (uploadState.paused) {
        uploadState.running = false;
        uploadState.message = "Paused in this browser.";
        renderUploadCockpit();
        break;
      }
      if (snapshot.received_size_bytes === snapshot.declared_size_bytes) {
        await completeUploadIfReady(owner);
        break;
      }
    } catch {
      if (uploadContextStillCurrent(owner) && uploadState.uploadLoopOwner === owner) {
        uploadState.message = "Upload request failed before a local response was read.";
        renderUploadCockpit();
        await refreshUploadStatus(snapshot.id, owner);
      }
      break;
    }
  }
  if (uploadState.uploadLoopOwner === owner) {
    uploadState.uploadLoopOwner = null;
    uploadState.running = false;
    renderUploadCockpit();
  }
  return true;
}

async function handleStartUpload() {
  const file = uploadState.file;
  if (
    !file
    || uploadState.snapshot
    || uploadState.actionOwner
    || uploadState.preparing
    || uploadState.running
    || uploadState.completing
  ) return;
  const owner = claimUploadAction("start", { uploadId: null, file });
  if (!owner) return;
  uploadState.preparing = true;
  uploadState.paused = false;
  uploadState.needsReselection = false;
  uploadState.message = "Preparing upload session.";
  renderUploadCockpit();
  try {
    const capabilityLoaded = await loadUploadCapability(owner);
    if (!uploadContextStillCurrent(owner, { allowMissingUploadId: true })) return;
    if (capabilityLoaded === null) return;
    if (!uploadCapability.uploads_enabled) {
      uploadState.message = "Uploads are not configured on this local server.";
      renderUploadCockpit();
      return;
    }
    if (file.size > uploadCapability.max_total_size_bytes) {
      uploadState.message = selectedUploadLimitMessage() || "File is too large for this local server.";
      renderUploadCockpit();
      return;
    }
    uploadState.message = "Creating upload session.";
    renderUploadCockpit();
    const { response, payload } = await fetchUploadJson(UPLOADS_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        display_filename: file.name,
        declared_size_bytes: file.size,
      }),
      cache: "no-store",
    });
    if (!uploadContextStillCurrent(owner, { allowMissingUploadId: true })) return;
    if (!response.ok) {
      uploadState.message = uploadErrorMessage(payload);
      renderUploadCockpit();
      return;
    }
    const snapshot = normalizeUploadSnapshot(payload);
    if (!applyUploadSnapshot(snapshot, owner, { allowAdoptUploadId: true })) return;
    uploadState.preparing = false;
    await runUploadLoop(owner);
  } catch {
    if (uploadContextStillCurrent(owner, { allowMissingUploadId: true })) {
      uploadState.message = "Upload session could not be created.";
      renderUploadCockpit();
    }
  } finally {
    if (uploadState.actionOwner === owner) {
      uploadState.preparing = false;
      releaseUploadAction(owner);
      renderUploadCockpit();
    }
  }
}

function handlePauseUpload() {
  if (!uploadState.running) return;
  uploadState.paused = true;
  uploadState.message = "Pausing after the active request settles.";
  renderUploadCockpit();
}

async function handleResumeUpload() {
  const snapshot = activeUploadSnapshot();
  if (
    !snapshot
    || uploadState.actionOwner
    || uploadState.preparing
    || uploadState.running
    || uploadState.completing
  ) return;
  if (!uploadState.file) {
    uploadState.needsReselection = true;
    uploadState.message = "Reselect the original local file before resuming.";
    renderUploadCockpit();
    return;
  }
  const owner = claimUploadAction("resume", { uploadId: snapshot.id, file: uploadState.file });
  if (!owner) return;
  uploadState.preparing = true;
  uploadState.message = "Refreshing server offset before resuming.";
  renderUploadCockpit();
  try {
    const refreshed = await refreshUploadStatus(snapshot.id, owner);
    if (!uploadContextStillCurrent(owner) || !refreshed) return;
    if (!uploadIsByteReceiving(refreshed)) {
      if (
        refreshed.state === "receiving"
        && refreshed.received_size_bytes === refreshed.declared_size_bytes
      ) {
        uploadState.paused = false;
        uploadState.needsReselection = false;
        uploadState.preparing = false;
        await runUploadLoop(owner);
        return;
      }
      if (uploadShouldPoll(refreshed)) {
        uploadState.preparing = false;
        scheduleUploadPolling(currentUploadContext({ uploadId: refreshed.id }));
      }
      return;
    }
    uploadState.paused = false;
    uploadState.needsReselection = false;
    uploadState.preparing = false;
    await runUploadLoop(owner);
  } finally {
    if (uploadState.actionOwner === owner) {
      uploadState.preparing = false;
      releaseUploadAction(owner);
      renderUploadCockpit();
    }
  }
}

async function handleCancelUpload() {
  const snapshot = activeUploadSnapshot();
  if (!snapshot || !uploadCancelPermitted(snapshot)) return;
  if (uploadState.actionOwner && uploadState.actionOwner.kind === "cancel") return;
  const accepted = await requestConfirmation({
    title: "Cancel upload?",
    message: "Uploaded progress will be discarded.",
    dismissLabel: "Keep upload",
    confirmLabel: "Cancel upload",
    destructive: true,
  });
  if (!accepted) return;
  const currentSnapshot = activeUploadSnapshot();
  if (
    !currentSnapshot
    || currentSnapshot.id !== snapshot.id
    || !uploadCancelPermitted(currentSnapshot)
    || (uploadState.actionOwner && uploadState.actionOwner.kind === "cancel")
  ) return;
  const owner = claimUploadAction("cancel", { uploadId: currentSnapshot.id, file: uploadState.file }, { supersede: true });
  if (!owner) return;
  uploadState.uploadLoopOwner = null;
  uploadState.completionOwner = null;
  uploadState.preparing = false;
  uploadState.running = false;
  uploadState.paused = false;
  uploadState.completing = false;
  uploadState.message = "Cancelling upload.";
  renderUploadCockpit();
  try {
    const { response, payload } = await fetchUploadJson(uploadEndpoint(snapshot.id), {
      method: "DELETE",
      headers: framenestMutationHeaders({ Accept: "application/json" }),
      cache: "no-store",
    });
    if (!uploadContextStillCurrent(owner)) return;
    if (response.ok) {
      const cancelled = normalizeUploadSnapshot(payload);
      if (!applyUploadSnapshot(cancelled, owner)) return;
      uploadState.message = "Cancelled by this browser.";
      renderUploadCockpit();
      return;
    }
    if (uploadStatusWasNotFound({ status: response.status, payload })) {
      clearStaleUploadRecoveryState();
      return;
    }
    uploadState.message = uploadErrorMessage(payload);
    renderUploadCockpit();
    const refreshed = await refreshUploadStatus(snapshot.id, owner);
    if (uploadContextStillCurrent(owner) && uploadShouldPoll(refreshed)) {
      scheduleUploadPolling(currentUploadContext({ uploadId: refreshed.id }));
    }
  } catch {
    if (uploadContextStillCurrent(owner)) {
      uploadState.message = "Cancel request failed before the local response could be read.";
      renderUploadCockpit();
      await refreshUploadStatus(snapshot.id, owner);
    }
  } finally {
    if (uploadState.actionOwner === owner) {
      releaseUploadAction(owner);
      renderUploadCockpit();
    }
  }
}

async function submitDuplicateResolution(owner, resolution) {
  if (!uploadContextStillCurrent(owner) || uploadState.actionOwner !== owner) return false;
  try {
    const { response, payload } = await fetchUploadJson(
      uploadDuplicateResolutionEndpoint(owner.uploadId),
      {
        method: "POST",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({ resolution }),
        cache: "no-store",
      },
    );
    if (!uploadContextStillCurrent(owner) || uploadState.actionOwner !== owner) return false;
    if (response.ok) {
      const resolved = normalizeUploadSnapshot(payload);
      const applied = applyUploadSnapshot(resolved, owner);
      if (applied && uploadShouldPoll(resolved)) {
        scheduleUploadPolling(currentUploadContext({ uploadId: resolved.id }));
      }
      return applied;
    }
    uploadState.message = uploadErrorMessage(payload);
    renderUploadCockpit();
    await refreshUploadStatus(owner.uploadId, owner);
  } catch {
    if (uploadContextStillCurrent(owner) && uploadState.actionOwner === owner) {
      uploadState.message = "Duplicate resolution could not be confirmed.";
      renderUploadCockpit();
      await refreshUploadStatus(owner.uploadId, owner);
    }
  } finally {
    if (uploadState.actionOwner === owner) {
      releaseUploadAction(owner);
      renderUploadCockpit();
    }
  }
  return false;
}

async function handleKeepDuplicate() {
  const snapshot = activeUploadSnapshot();
  if (!snapshot || snapshot.state !== "duplicate_pending" || uploadState.actionOwner) return;
  const owner = claimUploadAction("duplicate-keep", {
    uploadId: snapshot.id,
    file: uploadState.file,
  });
  if (!owner) return;
  uploadState.message = "Keeping this upload as a separate future item.";
  renderUploadCockpit();
  await submitDuplicateResolution(owner, "keep_separate");
}

async function handleDiscardDuplicate() {
  const snapshot = activeUploadSnapshot();
  if (!snapshot || snapshot.state !== "duplicate_pending" || uploadState.actionOwner) return;
  const confirmationContext = currentUploadContext({
    uploadId: snapshot.id,
    file: uploadState.file,
  });
  const accepted = await requestConfirmation({
    title: "Discard duplicate?",
    message: "This uploaded copy will be removed. The earlier upload is not affected.",
    dismissLabel: "Keep reviewing",
    confirmLabel: "Discard duplicate",
    destructive: true,
  });
  if (!accepted || !uploadContextStillCurrent(confirmationContext)) return;
  const currentSnapshot = activeUploadSnapshot();
  if (
    !currentSnapshot
    || currentSnapshot.id !== snapshot.id
    || currentSnapshot.state !== "duplicate_pending"
    || uploadState.actionOwner
  ) return;
  const owner = claimUploadAction("duplicate-discard", {
    uploadId: currentSnapshot.id,
    file: uploadState.file,
  });
  if (!owner) return;
  uploadState.message = "Discarding this duplicate upload.";
  renderUploadCockpit();
  await submitDuplicateResolution(owner, "discard");
}

function handleUploadFileSelection() {
  const file = uploadFileInput && uploadFileInput.files && uploadFileInput.files[0]
    ? uploadFileInput.files[0]
    : null;
  if (!file) {
    invalidateUploadOwnership();
    uploadState.file = null;
    if (uploadIsByteReceiving(activeUploadSnapshot())) {
      uploadState.needsReselection = true;
    }
    uploadState.message = "Select one local GIF, MP4, JPEG, or PNG.";
    renderUploadCockpit();
    return;
  }
  const snapshot = activeUploadSnapshot();
  if (!snapshot || uploadIsPollingStopState(snapshot)) {
    resetUploadForFile(file);
    return;
  }
  if (uploadIsByteReceiving(snapshot)) {
    invalidateUploadOwnership();
    if (file.size !== snapshot.declared_size_bytes) {
      uploadState.file = null;
      uploadState.needsReselection = true;
      uploadState.message = "Selected file size does not match this upload session.";
      renderUploadCockpit();
      return;
    }
    const recovery = loadUploadRecovery();
    const storedFileNameHint = recovery && recovery.file_name_hint
      ? recovery.file_name_hint
      : uploadState.fileNameHint;
    const storedLastModifiedHint = recovery && recovery.last_modified_hint !== null
      ? recovery.last_modified_hint
      : uploadState.lastModifiedHint;
    const nextLastModifiedHint = Number.isFinite(file.lastModified) ? file.lastModified : null;
    const hintDiffers = Boolean(
      (storedFileNameHint && file.name !== storedFileNameHint)
        || (
          storedLastModifiedHint !== null
          && nextLastModifiedHint !== null
          && nextLastModifiedHint !== storedLastModifiedHint
        ),
    );
    uploadState.file = file;
    uploadState.fileNameHint = file.name;
    uploadState.lastModifiedHint = nextLastModifiedHint;
    uploadState.needsReselection = false;
    uploadState.message = hintDiffers
      ? "File size matches. Name or modified-time hint differs; server validation remains authoritative."
      : "Ready to resume.";
    saveUploadRecovery(snapshot);
    renderUploadCockpit();
    return;
  }
  invalidateUploadOwnership();
  uploadState.file = null;
  uploadState.message = "The current server state no longer needs the local file.";
  renderUploadCockpit();
  if (uploadShouldPoll(snapshot) && uploadDialog && uploadDialog.hasAttribute("open")) {
    scheduleUploadPolling(currentUploadContext({ uploadId: snapshot.id }));
  }
}

function openUploadDialog() {
  if (!uploadDialog) return;
  uploadOpenerElement = document.activeElement;
  if (typeof uploadDialog.showModal === "function") {
    uploadDialog.showModal();
  } else {
    uploadDialog.setAttribute("open", "");
  }
  loadUploadCapability();
  if (uploadState.uploadId) {
    const owner = currentUploadContext({ uploadId: uploadState.uploadId });
    refreshUploadStatus(uploadState.uploadId, owner).then((snapshot) => {
      if (uploadContextStillCurrent(owner) && uploadShouldPoll(snapshot)) {
        scheduleUploadPolling(currentUploadContext({ uploadId: snapshot.id }));
      }
    });
  } else {
    restoreUploadRecovery();
  }
  renderUploadCockpit();
  if (uploadDialogTitle) uploadDialogTitle.focus();
}

function closeUploadDialog() {
  if (!uploadDialog) return;
  if (uploadShouldPoll(activeUploadSnapshot())) {
    stopUploadPolling();
  }
  if (typeof uploadDialog.close === "function") {
    uploadDialog.close();
  } else {
    uploadDialog.removeAttribute("open");
  }
  if (uploadOpenerElement) {
    uploadOpenerElement.focus();
    uploadOpenerElement = null;
  } else if (uploadOpenButton) {
    uploadOpenButton.focus();
  }
}

function cleanupUploadRuntime() {
  invalidateUploadOwnership();
  saveUploadRecovery();
}

function revokePreviewObjectUrls() {
  previewObjectUrls.forEach((url) => {
    URL.revokeObjectURL(url);
  });
  previewObjectUrls = [];
}

function decodeBase64Png(payloadBase64) {
  const binary = atob(payloadBase64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: "image/png" });
}

function selectPreviewableLocation(item) {
  if (!item.locations || item.locations.length === 0) return null;
  for (const location of item.locations) {
    if (location.availability === "available" && location.library_id && location.relative_path) {
      return { libraryId: location.library_id, relativePath: location.relative_path };
    }
  }
  return null;
}

function getCachedPreview(mediaId) {
  if (previewCacheMap.has(mediaId)) {
    const entry = previewCacheMap.get(mediaId);
    previewCacheMap.delete(mediaId);
    previewCacheMap.set(mediaId, entry);
    return entry;
  }
  return null;
}

function setCachedPreview(mediaId, entry) {
  if (previewCacheMap.has(mediaId)) {
    previewCacheMap.delete(mediaId);
  }
  previewCacheMap.set(mediaId, entry);
  while (previewCacheMap.size > MAX_PREVIEW_CACHE) {
    const oldestKey = previewCacheMap.keys().next().value;
    previewCacheMap.delete(oldestKey);
  }
}

function stopCardPreviewTimer() {
  if (activePreviewTimer) {
    clearInterval(activePreviewTimer);
    activePreviewTimer = null;
  }
  activePreviewMediaId = null;
}

function normalizeVideoPlaybackPosition(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  return seconds;
}

function rememberVideoPlaybackPosition(mediaId, seconds) {
  if (!mediaId) return;
  const normalized = normalizeVideoPlaybackPosition(seconds);
  if (normalized === null) return;
  videoPlaybackPositionByMediaId.set(mediaId, normalized);
}

function captureVideoPlaybackPosition(mediaId, video) {
  if (!mediaId || !video || video.tagName !== "VIDEO") return;
  rememberVideoPlaybackPosition(mediaId, video.currentTime);
}

function applyStoredVideoPlaybackPosition(mediaId, video) {
  if (!mediaId || !video || video.tagName !== "VIDEO") return;
  const stored = normalizeVideoPlaybackPosition(videoPlaybackPositionByMediaId.get(mediaId));
  if (stored === null) return;
  const duration = video.duration;
  if (Number.isFinite(duration) && duration > 0) {
    if (video.ended || stored >= duration - VIDEO_PLAYBACK_END_EPSILON_SECONDS) {
      video.currentTime = 0;
      videoPlaybackPositionByMediaId.set(mediaId, 0);
      return;
    }
    video.currentTime = Math.min(stored, Math.max(0, duration - VIDEO_PLAYBACK_END_EPSILON_SECONDS));
    return;
  }
  video.currentTime = stored;
}

function captureActiveCardVideoPlaybackPosition() {
  if (!activeCardMediaRestore || !activeCardMediaRestore.item) return;
  const mediaId = activeCardMediaRestore.item.media_id;
  if (!mediaId || activeCardMediaRestore.item.media_kind !== "video") return;
  const video = cardSurfaceVideoElement(activeCardMediaRestore.surface);
  captureVideoPlaybackPosition(mediaId, video);
}

function cleanupCatalogCardMedia() {
  captureActiveCardVideoPlaybackPosition();
  if (activeCardMediaRestore && activeCardMediaRestore.surface && activeCardMediaRestore.surface.isConnected) {
    const restore = activeCardMediaRestore;
    renderPersistentPreview(
      restore.surface,
      restore.item,
      restore.location,
      restore.title,
    );
    syncCardMediaSurfaceToggleState(restore.surface, restore.item, restore.title, false);
  }
  activeCardMediaSurface = null;
  activeCardMediaRestore = null;
  cardMediaElements.forEach((element) => {
    if (typeof element.__framenestCleanup === "function") {
      element.__framenestCleanup();
      element.__framenestCleanup = null;
    }
    if (element.tagName === "VIDEO") {
      element.pause();
      element.removeAttribute("src");
      try {
        element.load();
      } catch {
        // Ignore load failures during card media cleanup.
      }
    } else if (element.tagName === "IMG") {
      element.removeAttribute("src");
    } else if (element.__framenestGifImage) {
      element.__framenestGifImage.onload = null;
      element.__framenestGifImage.onerror = null;
      element.__framenestGifImage.removeAttribute("src");
      element.__framenestGifImage = null;
    }
    element.onerror = null;
    element.onload = null;
    element.onloadeddata = null;
    element.onloadedmetadata = null;
    element.oncanplay = null;
  });
  cardMediaElements = new Set();
}

function cleanupDetailsMedia({ invalidate = true } = {}) {
  if (invalidate) {
    detailsMediaToken += 1;
  }
  if (detailsMediaElement) {
    if (detailsMediaElement.tagName === "VIDEO") {
      // Detach position listeners before teardown so pause/load cannot overwrite
      // the stored resume timestamp with a reset currentTime of 0.
      detailsMediaElement.ontimeupdate = null;
      detailsMediaElement.onpause = null;
      if (detailsCurrentItem && detailsCurrentItem.media_kind === "video") {
        captureVideoPlaybackPosition(detailsCurrentItem.media_id, detailsMediaElement);
      }
      detailsMediaElement.pause();
      detailsMediaElement.removeAttribute("src");
      detailsMediaElement.querySelectorAll("source").forEach((source) => source.remove());
      try {
        detailsMediaElement.load();
      } catch {
        // Ignore load failures during cleanup.
      }
    }
    detailsMediaElement.onerror = null;
    detailsMediaElement.onload = null;
    detailsMediaElement.onloadeddata = null;
    detailsMediaElement.onloadedmetadata = null;
    detailsMediaElement.oncanplay = null;
    detailsMediaElement.ontimeupdate = null;
    detailsMediaElement.onpause = null;
    detailsMediaElement = null;
  }
  if (detailsPreviewContainer) {
    detailsPreviewContainer.onclick = null;
    detailsPreviewContainer.onkeydown = null;
    detailsPreviewContainer.replaceChildren();
  }
}

async function handleCardPreview(item, card, placeholder) {
  if (activePreviewMediaId === item.media_id) {
    stopCardPreviewTimer();
    const cached = getCachedPreview(item.media_id);
    if (cached && !cached.error && cached.frames) {
      renderCardPreviewFrames(card, item, cached);
    }
    return;
  }
  stopCardPreviewTimer();
  const cached = getCachedPreview(item.media_id);
  if (cached) {
    if (cached.error) {
      renderCardPreviewState(card, item, "error");
      return;
    }
    renderCardPreviewFrames(card, item, cached);
    startCardPreviewCycling(card, item, cached);
    return;
  }
  const location = selectPreviewableLocation(item);
  if (!location) {
    renderCardPreviewState(card, item, "unavailable-location");
    return;
  }
  const token = ++previewRequestToken;
  renderCardPreviewState(card, item, "loading");
  try {
    const response = await fetch(`${LIBRARIES_ENDPOINT}/${location.libraryId}/media-analysis-preview`, {
      method: "POST",
      headers: framenestMutationHeaders({ Accept: "application/json", "Content-Type": "application/json" }),
      body: JSON.stringify({ relative_path: location.relativePath }),
      cache: "no-store",
    });
    if (token !== previewRequestToken) return;
    const payload = await response.json();
    if (token !== previewRequestToken) return;
    if (!response.ok) {
      setCachedPreview(item.media_id, { error: true });
      renderCardPreviewState(card, item, "error");
      return;
    }
    const frames = (payload.representative_frames || []).map((frame) => ({
      timestampMs: frame.timestamp_ms,
      objectUrl: URL.createObjectURL(decodeBase64Png(frame.payload_base64)),
    }));
    const cacheEntry = { frames, technicalMetadata: payload.technical_metadata || null };
    setCachedPreview(item.media_id, cacheEntry);
    renderCardPreviewFrames(card, item, cacheEntry);
    startCardPreviewCycling(card, item, cacheEntry);
  } catch {
    if (token === previewRequestToken) {
      setCachedPreview(item.media_id, { error: true });
      renderCardPreviewState(card, item, "error");
    }
  }
}

function renderCardPreviewState(card, item, state) {
  const placeholder = card.querySelector(".media-placeholder");
  if (!placeholder) return;
  placeholder.replaceChildren();
  placeholder.removeAttribute("data-preview-state");
  if (state === "loading") {
    placeholder.setAttribute("data-preview-state", "loading");
    const text = document.createElement("span");
    text.className = "media-placeholder__loading";
    text.textContent = "Loading preview…";
    placeholder.appendChild(text);
  } else if (state === "error") {
    placeholder.setAttribute("data-preview-state", "error");
    const text = document.createElement("span");
    text.className = "media-placeholder__error";
    text.textContent = "Preview unavailable.";
    placeholder.appendChild(text);
    const retry = document.createElement("button");
    retry.className = "media-placeholder__retry";
    retry.type = "button";
    retry.textContent = "Retry";
    retry.setAttribute("aria-label", "Retry preview");
    retry.addEventListener("click", () => {
      previewCacheMap.delete(item.media_id);
      const ph = card.querySelector(".media-placeholder");
      handleCardPreview(item, card, ph);
    });
    placeholder.appendChild(retry);
  } else if (state === "unavailable-location") {
    placeholder.setAttribute("data-preview-state", "unavailable");
    const text = document.createElement("span");
    text.className = "media-placeholder__error";
    text.textContent = "No local preview available.";
    placeholder.appendChild(text);
  } else if (state === "stopped") {
    placeholder.setAttribute("data-preview-state", "stopped");
    const text = document.createElement("span");
    text.className = "media-placeholder__stopped";
    text.textContent = "Preview stopped.";
    placeholder.appendChild(text);
  }
}

function renderCardPreviewFrames(card, item, cacheEntry) {
  const placeholder = card.querySelector(".media-placeholder");
  if (!placeholder) return;
  placeholder.replaceChildren();
  placeholder.removeAttribute("data-preview-state");
  if (!cacheEntry.frames || cacheEntry.frames.length === 0) {
    placeholder.setAttribute("data-preview-state", "no-frames");
    const text = document.createElement("span");
    text.className = "media-placeholder__error";
    text.textContent = "No preview frame available.";
    placeholder.appendChild(text);
    return;
  }
  placeholder.setAttribute("data-preview-state", "loaded");
  const img = document.createElement("img");
  img.className = "media-placeholder__preview-img";
  img.src = cacheEntry.frames[0].objectUrl;
  img.alt = `Local preview frame for ${item.display_title || deriveCatalogFallbackTitle(item)}`;
  img.style.width = "100%";
  img.style.height = "100%";
  img.style.objectFit = "contain";
  placeholder.appendChild(img);
}

function startCardPreviewCycling(card, item, cacheEntry) {
  if (!cacheEntry.frames || cacheEntry.frames.length <= 1) return;
  if (prefersReducedMotion) return;
  stopCardPreviewTimer();
  activePreviewMediaId = item.media_id;
  let frameIndex = 0;
  const img = card.querySelector(".media-placeholder__preview-img");
  if (!img) return;
  activePreviewTimer = setInterval(() => {
    frameIndex = (frameIndex + 1) % cacheEntry.frames.length;
    img.src = cacheEntry.frames[frameIndex].objectUrl;
  }, PREVIEW_FRAME_INTERVAL_MS);
}

function selectPlaybackLocation(item) {
  if (!item.locations || item.locations.length === 0) return null;
  return item.locations.find((location) => location.availability === "available" && location.location_id) || null;
}

function selectSupportedAvailableLocation(item) {
  if (!item.locations || item.locations.length === 0) return null;
  if (
    item.media_kind !== "video"
    && item.media_kind !== "animated_image"
    && item.media_kind !== "image"
  ) {
    return null;
  }
  return item.locations.find((location) => location.availability === "available" && location.location_id) || null;
}

function mediaContentUrl(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/content`;
}

function mediaGalleryPreviewUrl(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/gallery-preview`;
}

function mediaCoverThumbnailUrl(mediaId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/cover-thumbnail`;
}

function coverTimelineEndpoint(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/cover-timeline`;
}

function coverFrameEndpoint(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/cover-frame`;
}

function coverMutationEndpoint(mediaId, locationId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(locationId)}/cover`;
}

function coverAdminStateEndpoint(mediaId) {
  return `/api/admin/media/${encodeURIComponent(mediaId)}/cover`;
}

function renderDetailsMediaUnavailable(container) {
  if (!container) return;
  container.replaceChildren();
  const text = document.createElement("p");
  text.className = "details-media-unavailable";
  text.textContent = "Media unavailable.";
  container.appendChild(text);
}

function renderDetailsMedia(item, { playWhenReady = false } = {}) {
  if (!detailsPreviewContainer) return;
  cleanupDetailsMedia();
  const token = ++detailsMediaToken;
  detailsPreviewContainer.className = "details-preview-container";
  detailsPreviewContainer.removeAttribute("role");
  detailsPreviewContainer.removeAttribute("tabindex");
  detailsPreviewContainer.removeAttribute("aria-label");
  detailsPreviewContainer.removeAttribute("title");

  const location = selectPlaybackLocation(item);
  if (!location) {
    renderDetailsMediaUnavailable(detailsPreviewContainer);
    return;
  }

  const loading = document.createElement("p");
  loading.className = "details-media-loading";
  loading.textContent = "Loading media…";
  detailsPreviewContainer.appendChild(loading);

  const url = mediaContentUrl(item.media_id, location.location_id);
  const title = item.display_title || deriveCatalogFallbackTitle(item);

  if (item.media_kind === "video") {
    const video = document.createElement("video");
    video.className = "details-media-video";
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.autoplay = false;
    video.setAttribute("aria-label", title);
    video.hidden = true;

    video.onloadedmetadata = () => {
      if (token !== detailsMediaToken) return;
      applyStoredVideoPlaybackPosition(item.media_id, video);
    };
    video.onloadeddata = () => {
      if (token !== detailsMediaToken) return;
      loading.remove();
      video.hidden = false;
    };
    video.oncanplay = () => {
      if (token !== detailsMediaToken) return;
      loading.remove();
      video.hidden = false;
      applyStoredVideoPlaybackPosition(item.media_id, video);
      if (playWhenReady) {
        video.play().catch(() => {
          // Native controls remain available when the browser blocks playback.
        });
        playWhenReady = false;
      }
    };
    video.ontimeupdate = () => {
      if (token !== detailsMediaToken) return;
      captureVideoPlaybackPosition(item.media_id, video);
    };
    video.onpause = () => {
      if (token !== detailsMediaToken) return;
      captureVideoPlaybackPosition(item.media_id, video);
    };
    video.onerror = () => {
      if (token !== detailsMediaToken) return;
      cleanupDetailsMedia({ invalidate: false });
      renderDetailsMediaUnavailable(detailsPreviewContainer);
    };
    detailsMediaElement = video;
    video.src = url;
    if (token === detailsMediaToken && detailsMediaElement === video) {
      detailsPreviewContainer.appendChild(video);
    }
  } else {
    const img = document.createElement("img");
    img.className = "details-media-img";
    img.alt = title;
    img.hidden = true;

    img.onload = () => {
      if (token !== detailsMediaToken) return;
      loading.remove();
      img.hidden = false;
    };
    img.onerror = () => {
      if (token !== detailsMediaToken) return;
      cleanupDetailsMedia({ invalidate: false });
      renderDetailsMediaUnavailable(detailsPreviewContainer);
    };
    detailsMediaElement = img;
    img.src = url;
    if (token === detailsMediaToken && detailsMediaElement === img) {
      detailsPreviewContainer.appendChild(img);
    }
  }
}

function addMetadataValue(metadataList, label, value) {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  wrapper.append(term, detail);
  metadataList.appendChild(wrapper);
}

function snapshotCatalogQueryState() {
  const tagKeys = Object.freeze([...catalogState.tagKeys]);
  return Object.freeze({
    q: typeof catalogState.q === "string" ? catalogState.q.trim() : "",
    tagKeys,
    collection: catalogState.collection,
    contentCategory: catalogState.contentCategory || "",
    acquisitionSource: catalogState.acquisitionSource || "",
    creatorAttributionKind: catalogState.creatorAttributionKind || "",
    creatorStableId: catalogState.creatorStableId || "",
    creatorHandle: catalogState.creatorHandle || "",
    limit: CATALOG_PAGE_SIZE_OPTIONS.includes(catalogState.limit) ? catalogState.limit : CATALOG_PAGE_SIZE,
    offset: catalogState.offset,
  });
}

function buildCatalogQueryParams(snapshot = snapshotCatalogQueryState()) {
  const params = new URLSearchParams();
  const trimmed = snapshot.q.trim();
  if (trimmed) {
    params.set("q", trimmed);
  }
  snapshot.tagKeys.forEach((key) => {
    params.append("tag", key);
  });
  if (snapshot.collection) {
    params.set("collection", snapshot.collection);
  }
  if (snapshot.contentCategory) {
    params.set("content_category", snapshot.contentCategory);
  }
  if (snapshot.acquisitionSource) {
    params.set("acquisition_source", snapshot.acquisitionSource);
  }
  if (snapshot.creatorAttributionKind) {
    params.set("creator_attribution_kind", snapshot.creatorAttributionKind);
  }
  if (snapshot.creatorStableId) {
    params.set("creator_stable_id", snapshot.creatorStableId);
  }
  if (snapshot.creatorHandle) {
    params.set("creator_handle", snapshot.creatorHandle);
  }
  params.set("limit", String(snapshot.limit));
  params.set("offset", String(snapshot.offset));
  return params;
}

function claimCatalogRequest() {
  const snapshot = snapshotCatalogQueryState();
  const owner = Object.freeze({
    ...snapshot,
    token: catalogRequestToken + 1,
  });
  catalogRequestToken = owner.token;
  catalogRequestOwner = owner;
  return owner;
}

function catalogRequestOwnerIsCurrent(owner) {
  return Boolean(owner)
    && catalogRequestOwner === owner
    && catalogRequestToken === owner.token
    && (typeof catalogState.q === "string" ? catalogState.q.trim() : "") === owner.q
    && semanticArraysEqual(catalogState.tagKeys, owner.tagKeys)
    && catalogState.collection === owner.collection
    && (catalogState.contentCategory || "") === (owner.contentCategory || "")
    && (catalogState.acquisitionSource || "") === (owner.acquisitionSource || "")
    && (catalogState.creatorAttributionKind || "") === (owner.creatorAttributionKind || "")
    && (catalogState.creatorStableId || "") === (owner.creatorStableId || "")
    && (catalogState.creatorHandle || "") === (owner.creatorHandle || "")
    && catalogState.limit === owner.limit
    && catalogState.offset === owner.offset;
}

function releaseCatalogRequest(owner) {
  if (!owner || catalogRequestOwner !== owner || catalogRequestToken !== owner.token) return false;
  catalogRequestOwner = null;
  return true;
}

function setCatalogSearchText(query) {
  const normalizedQuery = typeof query === "string" ? query.trim() : "";
  if (catalogState.q === normalizedQuery) return false;
  catalogState.q = normalizedQuery;
  catalogState.offset = 0;
  return true;
}

function syncCatalogPageSizeControl() {
  if (catalogPageSizeSelect) {
    catalogPageSizeSelect.value = String(catalogState.limit);
  }
}

function deriveCatalogFallbackTitle(item) {
  if (!item.locations || item.locations.length === 0) {
    return "Untitled media";
  }
  const firstPath = String(item.locations[0].relative_path || "");
  const parts = firstPath.split("/");
  return parts[parts.length - 1] || "Untitled media";
}

function metadataDialogHeading() {
  const currentTitle = metadataWorkspace.current.displayTitle.trim();
  if (currentTitle) {
    return currentTitle;
  }
  if (metadataWorkspace.openItem) {
    const fallback = deriveCatalogFallbackTitle(metadataWorkspace.openItem).trim();
    if (fallback && fallback !== "Untitled media") {
      return fallback;
    }
  }
  return "Media";
}

function formatCatalogKind(kind) {
  if (kind === "animated_image") {
    return "animated image";
  }
  if (kind === "image") {
    return "image";
  }
  return String(kind).replaceAll("_", " ");
}

function summarizeAvailability(locations) {
  if (!locations || locations.length === 0) {
    return "No known locations";
  }
  const counts = new Map();
  locations.forEach((location) => {
    const key = String(location.availability || "unknown");
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([key, count]) => `${count} ${key}`)
    .join(", ");
}

function renderUnavailableCardMediaSurface(item, title) {
  const surface = document.createElement("div");
  surface.className = "media-placeholder media-placeholder--unavailable";
  surface.setAttribute("data-media-state", "unavailable");
  surface.setAttribute("aria-label", `No local playback available for ${title}`);
  const text = document.createElement("span");
  text.className = "media-placeholder__error";
  text.textContent = "Unavailable";
  surface.appendChild(text);
  return surface;
}

function renderPreviewFallback(surface, title) {
  surface.replaceChildren();
  surface.setAttribute("data-media-state", "preview-unavailable");
  const text = document.createElement("span");
  text.className = "media-placeholder__error";
  text.textContent = "Preview unavailable.";
  surface.appendChild(text);
}

function isCoverReadyItem(item) {
  return Boolean(item) && item.cover_ready === true;
}

function coverPreviewCandidates(item, location) {
  const candidates = [];
  if (
    isCoverReadyItem(item)
    && (item.media_kind === "video" || item.media_kind === "animated_image")
  ) {
    candidates.push(mediaCoverThumbnailUrl(item.media_id));
  }
  if (location && item.media_kind === "image") {
    candidates.push(mediaContentUrl(item.media_id, location.location_id));
  } else if (location) {
    candidates.push(mediaGalleryPreviewUrl(item.media_id, location.location_id));
  }
  return candidates;
}

function renderPersistentPreview(surface, item, location, title) {
  surface.replaceChildren();
  surface.className = `media-placeholder media-placeholder--preview media-placeholder--${item.media_kind}`;
  surface.setAttribute("data-media-state", "preview");

  const image = document.createElement("img");
  image.className = "media-placeholder__preview-img";
  image.alt = item.media_kind === "image"
    ? `Still image for ${title}`
    : `Gallery preview for ${title}`;
  image.loading = "lazy";
  image.decoding = "async";

  const candidates = typeof coverPreviewCandidates === "function"
    ? coverPreviewCandidates(item, location)
    : [];
  if (!candidates.length && typeof location !== "undefined" && location) {
    if (item.media_kind === "image") {
      candidates.push(mediaContentUrl(item.media_id, location.location_id));
    } else {
      candidates.push(mediaGalleryPreviewUrl(item.media_id, location.location_id));
    }
  }

  let candidateIndex = 0;
  image.onerror = () => {
    candidateIndex += 1;
    if (candidateIndex < candidates.length) {
      image.src = candidates[candidateIndex];
      return;
    }
    image.onerror = null;
    image.removeAttribute("src");
    renderPreviewFallback(surface, title);
  };
  if (candidates.length) {
    image.src = candidates[0];
    surface.appendChild(image);
  } else {
    renderPreviewFallback(surface, title);
  }
}

function renderCardOriginalPlayback(surface, item, location, title) {
  surface.replaceChildren();
  surface.className = `media-placeholder media-placeholder--live media-placeholder--${item.media_kind}`;
  surface.setAttribute("data-media-state", "playing");
  activeCardMediaSurface = surface;
  activeCardMediaRestore = { surface, item, location, title };

  const url = mediaContentUrl(item.media_id, location.location_id);
  const showPreviewAgain = () => {
    if (activeCardMediaSurface === surface) {
      activeCardMediaSurface = null;
      activeCardMediaRestore = null;
    }
    renderPersistentPreview(surface, item, location, title);
  };

  if (item.media_kind === "video") {
    const video = document.createElement("video");
    video.className = "media-placeholder__video";
    video.preload = "metadata";
    video.playsInline = true;
    video.autoplay = false;
    video.muted = true;
    video.controls = false;
    video.loop = false;
    video.setAttribute("aria-label", `Playing video preview for ${title}`);
    video.onloadedmetadata = () => {
      applyStoredVideoPlaybackPosition(item.media_id, video);
    };
    video.onerror = showPreviewAgain;
    cardMediaElements.add(video);
    video.src = url;
    surface.appendChild(video);
    video.play().catch(() => {
      // The explicit Details surface remains available if compact-card playback is blocked.
    });
  } else {
    const image = document.createElement("img");
    image.className = "media-placeholder__image";
    image.alt = item.media_kind === "image"
      ? `Still image preview for ${title}`
      : `Playing animated image preview for ${title}`;
    image.onerror = showPreviewAgain;
    cardMediaElements.add(image);
    image.src = url;
    surface.appendChild(image);
  }
}

function cardSurfaceVideoElement(surface) {
  if (!surface || !surface.children) return null;
  return [...surface.children].find((child) => child.tagName === "VIDEO") || null;
}

function syncCardMediaSurfaceToggleState(surface, item, title, playing) {
  if (!surface) return;
  if (item.media_kind === "animated_image") {
    surface.setAttribute("aria-pressed", playing ? "true" : "false");
    surface.setAttribute(
      "aria-label",
      playing ? `Show static preview for ${title}` : `Play animated preview for ${title}`,
    );
    surface.title = playing ? "Show static preview" : "Play";
    return;
  }
  if (item.media_kind === "video") {
    surface.setAttribute("aria-pressed", playing ? "true" : "false");
    surface.setAttribute(
      "aria-label",
      playing ? `Pause ${title}` : `Play ${title}`,
    );
    surface.title = playing ? "Pause" : "Play";
    return;
  }
  surface.removeAttribute("aria-pressed");
  surface.setAttribute("aria-label", `Play ${title}`);
  surface.title = "Play";
}

function activateCardPlayback(item, surface) {
  const title = item.display_title || deriveCatalogFallbackTitle(item);
  const location = selectSupportedAvailableLocation(item);
  if (!location) {
    renderPreviewFallback(surface, title);
    return;
  }
  if (item.media_kind === "image") {
    // Still images already render identity-only original content in the card.
    return;
  }
  if (
    item.media_kind === "animated_image"
    && activeCardMediaSurface === surface
    && surface.getAttribute("data-media-state") === "playing"
  ) {
    cleanupCatalogCardMedia();
    syncCardMediaSurfaceToggleState(surface, item, title, false);
    return;
  }
  if (item.media_kind === "video" && activeCardMediaSurface === surface) {
    const video = cardSurfaceVideoElement(surface);
    if (video) {
      const isActivelyPlaying = surface.getAttribute("data-media-state") === "playing" && !video.paused;
      if (isActivelyPlaying) {
        video.pause();
        captureVideoPlaybackPosition(item.media_id, video);
        surface.setAttribute("data-media-state", "paused");
        syncCardMediaSurfaceToggleState(surface, item, title, false);
        return;
      }
      video.play().catch(() => {
        // The explicit Details surface remains available if compact-card playback is blocked.
      });
      surface.setAttribute("data-media-state", "playing");
      syncCardMediaSurfaceToggleState(surface, item, title, true);
      return;
    }
  }
  cleanupCatalogCardMedia();
  renderCardOriginalPlayback(surface, item, location, title);
  syncCardMediaSurfaceToggleState(surface, item, title, true);
}

function renderCatalogCardMediaSurface(item) {
  const title = item.display_title || deriveCatalogFallbackTitle(item);
  const location = selectSupportedAvailableLocation(item);
  if (!location) {
    return renderUnavailableCardMediaSurface(item, title);
  }

  const surface = document.createElement("div");
  surface.className = `media-placeholder media-placeholder--preview media-placeholder--${item.media_kind}`;
  surface.setAttribute("data-media-state", "preview");
  renderPersistentPreview(surface, item, location, title);

  if (item.media_kind === "image") {
    surface.setAttribute("aria-label", `Still image for ${title}`);
    surface.removeAttribute("role");
    surface.removeAttribute("tabindex");
    surface.removeAttribute("title");
    return surface;
  }

  syncCardMediaSurfaceToggleState(surface, item, title, false);
  surface.addEventListener("click", () => activateCardPlayback(item, surface));
  surface.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateCardPlayback(item, surface);
    }
  });
  surface.setAttribute("role", "button");
  surface.setAttribute("tabindex", "0");
  return surface;
}

function catalogItemHasCompleteMetadata(item) {
  return Boolean(
    item
    && typeof item.display_title === "string"
    && item.display_title.trim()
    && typeof item.description === "string"
    && item.description.trim()
    && Array.isArray(item.tags)
    && item.tags.length > 0,
  );
}

function identityAllowsCardAiQuickAction() {
  return identityState.resolved
    && identityState.available
    && identityState.capabilities.has("analysis.run")
    && identityState.capabilities.has("metadata.canonical.write")
    && !companionWebHosted();
}

function cardAiQuickActionEligible(item) {
  return identityAllowsCardAiQuickAction()
    && selectSupportedAvailableLocation(item) !== null
    && (item.content_category || "general") !== "movie";
}

function cardAiQuickActionProviderBlocked() {
  return aiCapabilityDiscoveryPending || !aiCapability.available;
}

function cardAiPreviewResponseMatchesRequest(payload, mediaId, locationId) {
  return Boolean(
    payload
    && payload.media_id != null
    && payload.location_id != null
    && String(payload.media_id) === String(mediaId)
    && String(payload.location_id) === String(locationId),
  );
}

function getCardAiQuickAction(mediaId) {
  return cardAiQuickActionByMediaId.get(mediaId) || { state: "idle", requestToken: 0 };
}

function setCardAiQuickActionController(mediaId, patch) {
  const current = getCardAiQuickAction(mediaId);
  const next = Object.assign({}, current, patch);
  cardAiQuickActionByMediaId.set(mediaId, next);
  return next;
}

function cardAiQuickActionIsLocked(mediaId) {
  return CARD_AI_QUICK_ACTION_LOCKED.has(getCardAiQuickAction(mediaId).state);
}

function cardAiQuickActionStatusMessage(state, detail = "") {
  if (state === "failed_analysis") return detail || "AI analysis failed.";
  if (state === "unavailable") return detail || "AI analysis is not configured.";
  return detail || "";
}

function setCardAnalysisStatus(status, options) {
  if (!status) return;
  const text = options && options.text ? options.text : "";
  const success = Boolean(options && options.success);
  const visuallyHidden = Boolean(options && options.visuallyHidden && text);
  status.textContent = text;
  status.hidden = !text;
  status.classList.toggle("visually-hidden", visuallyHidden);
  if (success) {
    status.dataset.analysisSuccess = "true";
    status.setAttribute("role", "status");
  } else {
    status.removeAttribute("data-analysis-success");
    if (!text) status.removeAttribute("role");
  }
}

function setCardAnalyzeButtonState(button, state, message = "") {
  if (!button || !button.isConnected) return;
  const title = button.dataset.mediaTitle || "media";
  const busy = state === "analyzing";
  const locked = CARD_AI_QUICK_ACTION_LOCKED.has(state);
  const providerBlocked = cardAiQuickActionProviderBlocked() && !locked;
  button.dataset.analysisState = state;
  button.setAttribute("aria-busy", busy ? "true" : "false");
  button.disabled = locked || providerBlocked || state === "unavailable";
  if (providerBlocked || state === "unavailable") {
    button.setAttribute("aria-disabled", "true");
  } else {
    button.removeAttribute("aria-disabled");
  }
  if (state === "analyzing") {
    button.setAttribute("aria-label", `Analyzing ${title}`);
    button.title = "Analyzing with AI";
  } else if (state === "failed_analysis") {
    button.setAttribute("aria-label", `AI analysis failed for ${title}. Retry Analyze by AI`);
    button.title = "AI analysis failed — retry";
  } else if (providerBlocked || state === "unavailable") {
    if (aiCapabilityDiscoveryPending) {
      button.setAttribute("aria-label", `Checking AI availability for ${title}`);
      button.title = `Checking AI availability for ${title}`;
    } else {
      button.setAttribute("aria-label", `AI analysis unavailable for ${title}`);
      button.title = `AI analysis unavailable for ${title}`;
    }
  } else if (state === "confirming") {
    button.setAttribute("aria-label", `Confirm AI analysis for ${title}`);
    button.title = "Confirm AI analysis";
  } else {
    button.setAttribute("aria-label", `Generate first-pass AI metadata for ${title}`);
    button.title = "Analyze by AI";
  }
  button.textContent = "🧠";
  const status = button.closest(".catalog-card")?.querySelector(".catalog-card__analysis-status");
  if (status) {
    const failureText = message || cardAiQuickActionStatusMessage(state);
    const showFailure = state === "failed_analysis" || state === "unavailable";
    setCardAnalysisStatus(status, {
      text: showFailure ? failureText : "",
      success: false,
      visuallyHidden: false,
    });
  }
}

function reconcileCatalogCardAiQuickActions() {
  catalogResults.querySelectorAll(".catalog-card__action--analyze").forEach((button) => {
    const state = button.dataset.analysisState || "idle";
    if (CARD_AI_QUICK_ACTION_LOCKED.has(state)) return;
    const title = button.dataset.mediaTitle || "media";
    const providerBlocked = cardAiQuickActionProviderBlocked();
    if (providerBlocked) {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
      if (aiCapabilityDiscoveryPending) {
        button.setAttribute("aria-label", `Checking AI availability for ${title}`);
        button.title = `Checking AI availability for ${title}`;
      } else {
        button.setAttribute("aria-label", `AI analysis unavailable for ${title}`);
        button.title = `AI analysis unavailable for ${title}`;
      }
      return;
    }
    if (state === "unavailable") {
      const mediaId = button.closest(".catalog-card")?.dataset.mediaId;
      if (mediaId) {
        setCardAiQuickActionController(mediaId, { state: "idle" });
      }
      setCardAnalyzeButtonState(button, "idle");
      return;
    }
    const status = button.closest(".catalog-card")?.querySelector(".catalog-card__analysis-status");
    const preservedMessage = status && !status.hidden ? status.textContent : "";
    setCardAnalyzeButtonState(button, state, preservedMessage);
  });
}

function movieIdentificationIsPureUnknown(result) {
  if (!result || typeof result !== "object") return false;
  if (String(result.identification_status || "") !== "unknown") return false;
  const title = typeof result.identified_title === "string"
    ? result.identified_title.trim()
    : "";
  const genres = Array.isArray(result.genres) ? result.genres.filter(Boolean) : [];
  const tags = Array.isArray(result.tags) ? result.tags.filter(Boolean) : [];
  return !title && genres.length === 0 && tags.length === 0;
}

async function handleAnalyzeCatalogCard(item, button) {
  const mediaId = item.media_id;
  if (cardAiQuickActionIsLocked(mediaId)) return;
  if (!cardAiQuickActionEligible(item)) return;
  const location = selectSupportedAvailableLocation(item);
  if (!location) return;
  if (cardAiQuickActionProviderBlocked()) return;
  const confirmationContext = Object.freeze({
    mediaId,
    locationId: location.location_id,
    capabilityRevision: aiCapabilityRevision,
  });
  setCardAiQuickActionController(mediaId, { state: "confirming" });
  setCardAnalyzeButtonState(button, "confirming");
  const accepted = await requestConfirmation({
    title: "Analyze with AI?",
    message: "Kronika will send up to 3 optimized preview frames and bounded metadata to the configured server-side AI provider. The original file, local path, and API key are not uploaded. The editor will open with proposal strips beside Title, Description, and Tags. Current canonical values are not replaced. Nothing is saved until you click Save, and the physical file is not renamed.",
    dismissLabel: "Not now",
    confirmLabel: "Analyze by AI",
    destructive: false,
    focusReturn: button,
  });
  if (!accepted) {
    setCardAiQuickActionController(mediaId, { state: "idle" });
    setCardAnalyzeButtonState(button, "idle");
    return;
  }
  const currentLocation = selectSupportedAvailableLocation(item);
  if (
    getCardAiQuickAction(mediaId).state !== "confirming"
    || !cardAiQuickActionEligible(item)
    || !currentLocation
    || currentLocation.location_id !== confirmationContext.locationId
    || aiCapabilityRevision !== confirmationContext.capabilityRevision
  ) {
    setCardAiQuickActionController(mediaId, { state: "idle" });
    setCardAnalyzeButtonState(button, "idle");
    return;
  }
  const requestToken = (getCardAiQuickAction(mediaId).requestToken || 0) + 1;
  setCardAiQuickActionController(mediaId, { state: "confirming", requestToken });
  const opened = await handleOpenMetadataWorkspace(item, button);
  if (
    getCardAiQuickAction(mediaId).requestToken !== requestToken
    || getCardAiQuickAction(mediaId).state !== "confirming"
  ) return;
  if (!opened) {
    setCardAiQuickActionController(mediaId, { state: "idle" });
    setCardAnalyzeButtonState(button, "idle");
    const status = button.closest(".catalog-card")?.querySelector(".catalog-card__analysis-status");
    setCardAnalysisStatus(status, {
      text: "Analysis was not started. Keep editing or try again.",
    });
    return;
  }
  const workspaceLocation = metadataAiLocation();
  if (
    metadataWorkspace.openMediaId !== confirmationContext.mediaId
    || metadataWorkspace.loading
    || metadataWorkspace.unavailable
    || metadataWorkspace.notFound
    || !workspaceLocation
    || workspaceLocation.location_id !== confirmationContext.locationId
    || aiCapabilityRevision !== confirmationContext.capabilityRevision
  ) {
    setCardAiQuickActionController(mediaId, { state: "idle" });
    setCardAnalyzeButtonState(button, "idle");
    return;
  }
  const metadataConfirmationContext = captureMetadataAiConfirmationContext(workspaceLocation);
  setCardAiQuickActionController(mediaId, { state: "analyzing", requestToken });
  setCardAnalyzeButtonState(button, "analyzing");
  await runMetadataAiAnalysis(metadataConfirmationContext, {
    requestGuard: () => {
      const controller = getCardAiQuickAction(mediaId);
      return controller.requestToken === requestToken && controller.state === "analyzing";
    },
  });
  if (getCardAiQuickAction(mediaId).requestToken !== requestToken) return;
  setCardAiQuickActionController(mediaId, { state: "idle" });
  setCardAnalyzeButtonState(button, "idle");
}

function setCatalogPagination(page, renderedCount = page.items.length) {
  const start = page.total === 0 ? 0 : page.offset + 1;
  const end = Math.min(page.offset + page.limit, page.total);
  const processedPageWasRefined = catalogState.collection === PROCESSED_COLLECTION
    && renderedCount !== page.items.length;
  if (processedPageWasRefined) {
    catalogPageSummary.textContent = renderedCount === 0
      ? "No metadata-complete results on this page."
      : `${renderedCount} metadata-complete ${renderedCount === 1 ? "result" : "results"} on this page.`;
  } else {
    catalogPageSummary.textContent = page.total === 0
      ? "No catalog results."
      : `${start}-${end} of ${page.total}`;
  }
  catalogPrevButton.disabled = page.offset <= 0;
  catalogNextButton.disabled = page.offset + page.limit >= page.total;
}

function mediaCreatorAttributionFields(source) {
  if (!source) {
    return { kind: null, stableId: null, handle: null, displayName: null };
  }
  return {
    kind: source.creator_attribution_kind || source.creatorAttributionKind || null,
    stableId: source.creator_stable_id || source.creatorStableId || null,
    handle: source.creator_handle || source.creatorHandle || null,
    displayName: source.creator_display_name || source.creatorDisplayName || null,
  };
}

function mediaHasCreatorAttribution(source) {
  const attribution = mediaCreatorAttributionFields(source);
  return Boolean(
    attribution.kind
    && (attribution.displayName || attribution.handle || attribution.stableId),
  );
}

function mediaCreatorChipLabel(source) {
  const attribution = mediaCreatorAttributionFields(source);
  if (attribution.displayName) return attribution.displayName;
  if (attribution.handle) return `@${attribution.handle}`;
  return attribution.stableId || "";
}

function catalogCreatorFilterIsActive(kind, stableId, handle) {
  if (!kind) return false;
  if (catalogState.creatorAttributionKind !== kind) return false;
  if (stableId) {
    return catalogState.creatorStableId === stableId && !catalogState.creatorHandle;
  }
  if (handle) {
    return catalogState.creatorHandle === handle && !catalogState.creatorStableId;
  }
  return false;
}

function appendCatalogCreatorChip(container, item) {
  if (!mediaHasCreatorAttribution(item)) return;
  const attribution = mediaCreatorAttributionFields(item);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "catalog-card__tag catalog-card__tag--creator";
  button.textContent = mediaCreatorChipLabel(item);
  button.dataset.creatorAttributionKind = attribution.kind || "";
  button.dataset.creatorStableId = attribution.stableId || "";
  button.dataset.creatorHandle = attribution.handle || "";
  button.setAttribute("aria-label", `Filter Gallery by creator ${button.textContent}`);
  button.setAttribute(
    "aria-pressed",
    String(catalogCreatorFilterIsActive(attribution.kind, attribution.stableId, attribution.handle)),
  );
  button.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") event.stopPropagation();
  });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    setCatalogCreatorFilter({
      kind: attribution.kind,
      stableId: attribution.stableId,
      handle: attribution.handle,
    });
  });
  container.appendChild(button);
}

function appendDetailsCreatorChip(container, item) {
  if (!mediaHasCreatorAttribution(item)) return;
  const attribution = mediaCreatorAttributionFields(item);
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "media-details-dialog__tag media-details-dialog__tag--creator";
  pill.textContent = mediaCreatorChipLabel(item);
  pill.dataset.creatorAttributionKind = attribution.kind || "";
  pill.dataset.creatorStableId = attribution.stableId || "";
  pill.dataset.creatorHandle = attribution.handle || "";
  pill.setAttribute("aria-label", `Filter Gallery by creator ${pill.textContent}`);
  pill.addEventListener("click", () => {
    closeDetailsDialog({ restoreFocus: false });
    setCatalogCreatorFilter({
      kind: attribution.kind,
      stableId: attribution.stableId,
      handle: attribution.handle,
    });
  });
  container.appendChild(pill);
}

function renderCatalogCardTags(item) {
  const tags = document.createElement("div");
  tags.className = "catalog-card__tags";
  tags.setAttribute("role", "group");
  tags.setAttribute("aria-label", "Media tags");
  appendCatalogCreatorChip(tags, item);
  (item.tags || []).forEach((tag) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "catalog-card__tag";
    button.dataset.tagKey = tag.key;
    button.textContent = tag.display_name;
    button.setAttribute("aria-label", `Filter Gallery by ${tag.display_name}`);
    button.setAttribute("aria-pressed", String(catalogState.tagKeys.includes(tag.key)));
    button.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") event.stopPropagation();
    });
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleCatalogCardTagFilter(tag.key, { focusChip: catalogTagActivationShouldFocusChip(event) });
    });
    tags.appendChild(button);
  });
  return tags;
}

function reconcileCatalogSelectedCard() {
  const selectedMediaId = metadataWorkspace.openMediaId === null
    ? null
    : String(metadataWorkspace.openMediaId);
  catalogResults.querySelectorAll(".catalog-card").forEach((card) => {
    card.classList.toggle(
      "catalog-card--selected",
      selectedMediaId !== null && card.dataset.mediaId === selectedMediaId,
    );
  });
}

function companionWebHosted() {
  const host = globalThis.FrameNestCompanionWeb;
  return Boolean(host && typeof host.isHosted === "function" && host.isHosted());
}

function renderCatalogCard(item) {
  const card = document.createElement("article");
  card.className = "catalog-card";
  card.dataset.mediaId = item.media_id;

  const mediaSurface = renderCatalogCardMediaSurface(item);
  const mediaFrame = document.createElement("div");
  mediaFrame.className = "catalog-card__media-frame";
  mediaFrame.appendChild(mediaSurface);

  const body = document.createElement("div");
  body.className = "catalog-card__body";
  const title = document.createElement("h3");
  const titleButton = document.createElement("button");
  titleButton.className = "catalog-card__title-button";
  titleButton.type = "button";
  titleButton.textContent = item.display_title || deriveCatalogFallbackTitle(item);
  titleButton.setAttribute("aria-label", `Open details for ${item.display_title || deriveCatalogFallbackTitle(item)}`);
  titleButton.addEventListener("click", () => openDetailsDialog(item, titleButton));
  title.appendChild(titleButton);
  body.append(title, renderCatalogCardTags(item));

  const supportedLocation = selectSupportedAvailableLocation(item);
  const displayTitle = item.display_title || deriveCatalogFallbackTitle(item);
  const actions = document.createElement("div");
  actions.className = "catalog-card__actions catalog-card__actions--overlay";
  if (cardAiQuickActionEligible(item)) {
    const analyzeButton = document.createElement("button");
    analyzeButton.className = "catalog-card__action catalog-card__action--overlay catalog-card__action--analyze catalog-card__action--top-right";
    analyzeButton.type = "button";
    analyzeButton.textContent = "🧠";
    analyzeButton.dataset.mediaTitle = displayTitle;
    const controller = getCardAiQuickAction(item.media_id);
    const initialState = controller.state || "idle";
    analyzeButton.dataset.analysisState = initialState;
    analyzeButton.setAttribute("aria-busy", "false");
    if (cardAiQuickActionProviderBlocked()) {
      analyzeButton.disabled = true;
      analyzeButton.setAttribute("aria-disabled", "true");
      if (aiCapabilityDiscoveryPending) {
        analyzeButton.setAttribute("aria-label", `Checking AI availability for ${displayTitle}`);
        analyzeButton.title = `Checking AI availability for ${displayTitle}`;
      } else {
        analyzeButton.setAttribute("aria-label", `AI analysis unavailable for ${displayTitle}`);
        analyzeButton.title = `AI analysis unavailable for ${displayTitle}`;
      }
    } else {
      analyzeButton.disabled = false;
      analyzeButton.removeAttribute("aria-disabled");
      analyzeButton.setAttribute("aria-label", `Generate first-pass AI metadata for ${displayTitle}`);
      analyzeButton.title = "Analyze by AI";
    }
    analyzeButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      handleAnalyzeCatalogCard(item, analyzeButton);
    });
    actions.appendChild(analyzeButton);
  }
  if (identityAllowsMetadataEdit()) {
    const editButton = document.createElement("button");
    editButton.className = "catalog-card__action catalog-card__action--overlay catalog-card__action--edit catalog-card__action--bottom-left";
    editButton.type = "button";
    editButton.setAttribute("aria-label", `Edit ${displayTitle}`);
    editButton.title = "Edit";
    editButton.appendChild(editIcon());
    editButton.addEventListener("click", () => handleOpenMetadataWorkspace(item, editButton));
    actions.appendChild(editButton);
  }
  if (supportedLocation) {
    if (companionWebHosted()) {
      const attachButton = document.createElement("button");
      attachButton.type = "button";
      attachButton.className = "catalog-card__action catalog-card__action--overlay catalog-card__action--top-left catalog-card__action--attach";
      attachButton.textContent = "📎";
      attachButton.title = "Attach to X composer";
      attachButton.setAttribute("aria-label", "Attach to X composer");
      attachButton.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const host = globalThis.FrameNestCompanionWeb;
        if (!host || typeof host.attach !== "function") {
          return;
        }
        void host.attach(item.media_id, supportedLocation.location_id).then((result) => {
          if (result && !result.ok && result.error === "composer_unbound") {
            attachButton.title = "Composer is not bound";
          }
        });
      });
      actions.appendChild(attachButton);
    }
    const openOriginalLink = document.createElement("a");
    openOriginalLink.className = "catalog-card__action catalog-card__action--overlay catalog-card__action--open-original catalog-card__action--bottom-right";
    openOriginalLink.href = mediaContentUrl(item.media_id, supportedLocation.location_id);
    openOriginalLink.target = "_blank";
    openOriginalLink.rel = "noopener noreferrer";
    openOriginalLink.setAttribute("aria-label", `Open original media ${displayTitle}`);
    openOriginalLink.title = "Open original media";
    openOriginalLink.appendChild(openOriginalIcon());
    actions.appendChild(openOriginalLink);
  }
  mediaFrame.appendChild(actions);
  const analysisStatus = document.createElement("p");
  analysisStatus.className = "catalog-card__analysis-status";
  analysisStatus.hidden = true;
  const trackedAnalysis = automaticAnalysisByMediaId.get(item.media_id);
  if (trackedAnalysis) {
    const message = automaticAnalysisStatusMessage(trackedAnalysis);
    analysisStatus.textContent = message;
    analysisStatus.hidden = !message;
    analysisStatus.dataset.analysisLifecycle = trackedAnalysis.state;
    if (trackedAnalysis.state === "analyzed") {
      analysisStatus.dataset.analysisSuccess = "true";
    }
  }
  card.append(mediaFrame, body, analysisStatus);
  const analyzeButton = card.querySelector(".catalog-card__action--analyze");
  if (analyzeButton) {
    const controller = getCardAiQuickAction(item.media_id);
    const initialState = controller.state || "idle";
    if (initialState !== "idle") {
      setCardAnalyzeButtonState(
        analyzeButton,
        initialState,
        cardAiQuickActionStatusMessage(initialState),
      );
    } else if (cardAiQuickActionProviderBlocked()) {
      setCardAnalyzeButtonState(analyzeButton, "idle");
    }
  }
  return card;
}

function renderCatalogEmptyState() {
  const hasSearch = Boolean(catalogState.q.trim());
  const hasTags = catalogState.tagKeys.length > 0;
  const hasClassFilters = Boolean(catalogState.contentCategory)
    || Boolean(catalogState.acquisitionSource)
    || Boolean(catalogState.creatorAttributionKind)
    || Boolean(catalogState.creatorStableId)
    || Boolean(catalogState.creatorHandle);
  const hasCollection = Boolean(catalogState.collection);
  const hasAnyFilter = hasSearch || hasTags || hasClassFilters || hasCollection;
  const messageTarget = catalogStateEmptyMessage || catalogStateEmpty;
  if (!messageTarget) return;
  if (hasSearch && hasTags) {
    messageTarget.textContent = "No media match the current search and tag filters.";
  } else if (hasSearch) {
    messageTarget.textContent = "No media match the current search.";
  } else if (hasTags) {
    messageTarget.textContent = "No media match the active tag filters.";
  } else if (hasAnyFilter) {
    messageTarget.textContent = "No media match the active filters.";
  } else {
    messageTarget.textContent = "No media are available in this catalog view.";
  }
}

function catalogItemsForCurrentScope(items) {
  if (!Array.isArray(items)) return [];
  if (catalogState.collection !== PROCESSED_COLLECTION) return items;
  return items.filter(catalogItemHasCompleteMetadata);
}

function renderCatalogSuccess(page) {
  cleanupCatalogCardMedia();
  catalogResults.replaceChildren();
  catalogState.total = page.total;
  catalogState.offset = page.offset;
  catalogState.limit = CATALOG_PAGE_SIZE_OPTIONS.includes(page.limit) ? page.limit : catalogState.limit;
  syncCatalogPageSizeControl();
  const visibleItems = catalogItemsForCurrentScope(page.items);
  setCatalogPagination(page, visibleItems.length);
  if (visibleItems.length === 0) {
    renderCatalogEmptyState();
    reconcileCatalogSelectedCard();
    showCatalogState("empty");
    return;
  }
  visibleItems.forEach((item) => {
    catalogResults.appendChild(renderCatalogCard(item));
  });
  renderCatalogTagFilterStates();
  reconcileCatalogSelectedCard();
  showCatalogState("success");
}

function renderCatalogTagFilterStates() {
  catalogResults.querySelectorAll(".catalog-card__tag").forEach((button) => {
    button.setAttribute("aria-pressed", String(catalogState.tagKeys.includes(button.dataset.tagKey)));
  });
}

function catalogTagDisplayName(tagKey) {
  const definition = selectedTagDefinition(tagKey);
  if (definition) return definition.display_name;
  const visibleTag = [...catalogResults.querySelectorAll(".catalog-card__tag")]
    .find((button) => button.dataset.tagKey === tagKey);
  return visibleTag ? visibleTag.textContent : tagKey;
}

function renderActiveCatalogTagFilters() {
  if (!catalogTagFilters) return;
  catalogTagFilters.replaceChildren();
  catalogTagFilters.hidden = catalogState.tagKeys.length === 0;
  catalogState.tagKeys.forEach((tagKey) => {
    const displayName = catalogTagDisplayName(tagKey);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "catalog-filter-chip";
    button.dataset.tagKey = tagKey;
    button.setAttribute("aria-label", `Remove ${displayName} tag filter`);
    const label = document.createElement("span");
    label.textContent = displayName;
    const removeSpan = document.createElement("span");
    removeSpan.className = "catalog-filter-chip__remove";
    removeSpan.textContent = "×";
    removeSpan.setAttribute("aria-hidden", "true");
    button.append(label, removeSpan);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      removeCatalogTagFilter(tagKey);
    });
    catalogTagFilters.appendChild(button);
  });
}

function focusCatalogFilterChip(tagKey) {
  const chip = catalogTagFilters?.querySelector(`.catalog-filter-chip[data-tag-key="${tagKey}"]`);
  if (!chip) return false;
  chip.focus();
  return true;
}

function catalogTagActivationShouldFocusChip(event) {
  return !event.pointerType && event.detail === 0;
}

function activateCatalogTagFilter(tagKey, { focusChip = false } = {}) {
  const alreadyActive = catalogState.tagKeys.includes(tagKey);
  if (!alreadyActive) {
    catalogState.tagKeys = [...catalogState.tagKeys, tagKey];
    catalogState.offset = 0;
    renderActiveCatalogTagFilters();
  }
  renderCatalogTagFilterStates();
  syncCatalogFilterControls();
  if (focusChip) {
    focusCatalogFilterChip(tagKey);
  }
  if (!alreadyActive) {
    loadCatalog();
  }
  return !alreadyActive;
}

function toggleCatalogCardTagFilter(tagKey, { focusChip = false } = {}) {
  if (catalogState.tagKeys.includes(tagKey)) {
    return removeCatalogTagFilter(tagKey, { restoreFocus: focusChip });
  }
  return activateCatalogTagFilter(tagKey, { focusChip });
}

function removeCatalogTagFilter(tagKey, { restoreFocus = true } = {}) {
  const removedIndex = catalogState.tagKeys.indexOf(tagKey);
  if (removedIndex === -1) return false;
  catalogState.tagKeys = catalogState.tagKeys.filter((activeKey) => activeKey !== tagKey);
  catalogState.offset = 0;
  renderActiveCatalogTagFilters();
  renderCatalogTagFilterStates();
  syncCatalogFilterControls();
  const remainingChips = [...catalogTagFilters.querySelectorAll(".catalog-filter-chip")];
  const focusTarget = remainingChips[removedIndex]
    || remainingChips[removedIndex - 1]
    || commandSearchInput;
  if (restoreFocus && focusTarget) focusTarget.focus();
  loadCatalog();
  return true;
}

function renderCatalogTagFilters(tags) {
  canonicalTagDefinitions = tags;
  canonicalTagsLoaded = true;
  renderActiveCatalogTagFilters();
  if (tags.length === 0) {
    if (catalogTagsState) {
      catalogTagsState.hidden = false;
      catalogTagsState.textContent = "No tags.";
    }
  } else if (catalogTagsState) {
    catalogTagsState.hidden = true;
  }
  renderCatalogTagFilterStates();
  if (metadataWorkspace.openMediaId !== null) {
    renderMetadataWorkspace();
  }
}

function activateDetailsTagFilter(tagKey) {
  closeDetailsDialog({ restoreFocus: false });
  activateCatalogTagFilter(tagKey, { focusChip: true });
}

async function loadCatalogTags() {
  if (catalogTagsState) {
    catalogTagsState.hidden = false;
    catalogTagsState.textContent = "Loading tags...";
  }
  try {
    const response = await fetch(CANONICAL_TAGS_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      if (catalogTagsState) catalogTagsState.textContent = "Tags unavailable.";
      return false;
    }
    renderCatalogTagFilters(payload.tags || []);
    return true;
  } catch {
    if (catalogTagsState) catalogTagsState.textContent = "Tags could not be loaded.";
    return false;
  }
}

async function ensureCanonicalTags() {
  if (canonicalTagsLoaded) {
    return true;
  }
  return loadCatalogTags();
}

function setMetadataStatus(state, message) {
  metadataWorkspace.statusOverride = state;
  if (metadataStatus && message) {
    metadataStatus.textContent = message;
  }
}

function advanceMetadataWorkspaceRevision() {
  metadataWorkspaceRevision += 1;
}

function metadataOpenItemMediaId() {
  return metadataWorkspace.openItem ? metadataWorkspace.openItem.media_id : null;
}

function snapshotCatalogItemForMetadata(item) {
  return {
    ...item,
    tags: (item.tags || []).map((tag) => ({ ...tag })),
    locations: (item.locations || []).map((location) => ({ ...location })),
  };
}

function captureMetadataDiscardContext({
  action = "close",
  targetMediaId = null,
  targetScope = null,
} = {}) {
  return Object.freeze({
    action,
    targetMediaId,
    targetScope,
    openMediaId: metadataWorkspace.openMediaId,
    openItemMediaId: metadataOpenItemMediaId(),
    workspaceRevision: metadataWorkspaceRevision,
    catalogScope: catalogState.collection,
  });
}

function metadataDiscardContextIsCurrent(context) {
  return Boolean(context)
    && metadataWorkspaceRevision === context.workspaceRevision
    && metadataWorkspace.openMediaId === context.openMediaId
    && metadataOpenItemMediaId() === context.openItemMediaId
    && catalogState.collection === context.catalogScope;
}

function reopenMetadataWorkspaceAfterRejectedSwitch(context) {
  if (
    !metadataDiscardContextIsCurrent(context)
    || !metadataDirtyForBeforeUnload()
    || !metadataDialog
    || metadataDialog.hasAttribute("open")
  ) {
    return false;
  }
  if (typeof metadataDialog.showModal === "function") {
    metadataDialog.showModal();
  } else {
    metadataDialog.setAttribute("open", "");
  }
  (metadataWorkspace.loading ? metadataWorkspaceTitle : metadataTitleInput).focus();
  return true;
}

function metadataSaveLocationId() {
  const location = metadataAiLocation();
  return location ? location.location_id : null;
}

function claimMetadataSaveOwner(normalized, { closeAfterSave = true } = {}) {
  metadataWorkspace.saving = true;
  advanceMetadataWorkspaceRevision();
  const aliasMode = metadataWorkspaceIsAliasMode();
  const requestPayload = aliasMode
    ? {
      display_title: normalized.displayTitle,
      description: normalized.description,
      tag_keys: normalized.tagKeys,
    }
    : {
      display_title: normalized.displayTitle,
      description: normalized.description,
      tag_keys: normalized.tagKeys,
      content_category: normalized.contentCategory || "general",
      genres: normalized.genres || [],
      creator_attribution_kind: normalized.creatorAttributionKind || null,
      creator_stable_id: normalized.creatorStableId || null,
      creator_handle: normalized.creatorHandle || null,
      creator_display_name: normalized.creatorDisplayName || null,
    };
  Object.freeze(requestPayload.tag_keys);
  if (!aliasMode) Object.freeze(requestPayload.genres);
  Object.freeze(requestPayload);
  const owner = Object.freeze({
    token: ++metadataSaveRequestToken,
    mediaId: metadataWorkspace.openMediaId,
    openItemMediaId: metadataOpenItemMediaId(),
    locationId: metadataSaveLocationId(),
    catalogScope: catalogState.collection,
    workspaceRevision: metadataWorkspaceRevision,
    requestPayload,
    editMode: aliasMode ? "alias" : "canonical",
    closeAfterSave: Boolean(closeAfterSave),
  });
  metadataSaveOwner = owner;
  return owner;
}

function metadataSaveOwnerOwnsWorkspace(owner) {
  return Boolean(owner)
    && metadataSaveOwner === owner
    && metadataSaveRequestToken === owner.token
    && metadataWorkspace.openMediaId === owner.mediaId
    && metadataOpenItemMediaId() === owner.openItemMediaId
    && metadataSaveLocationId() === owner.locationId
    && catalogState.collection === owner.catalogScope;
}

function metadataSaveOwnerIsCurrent(owner, expectedRevision = owner ? owner.workspaceRevision : null) {
  return metadataSaveOwnerOwnsWorkspace(owner)
    && metadataWorkspaceRevision === expectedRevision
    && metadataWorkspace.saving;
}

function releaseMetadataSaveOwner(owner) {
  if (!owner || metadataSaveOwner !== owner || metadataSaveRequestToken !== owner.token) return false;
  if (metadataWorkspace.openMediaId !== owner.mediaId) return false;
  if (metadataOpenItemMediaId() !== owner.openItemMediaId) return false;
  if (catalogState.collection !== owner.catalogScope) return false;
  metadataSaveOwner = null;
  if (metadataWorkspace.saving) {
    metadataWorkspace.saving = false;
    advanceMetadataWorkspaceRevision();
    updateMetadataControls();
  }
  return true;
}

function closeMetadataWorkspaceAfterSave(owner, expectedRevision) {
  if (!owner.closeAfterSave || !metadataSaveOwnerIsCurrent(owner, expectedRevision)) return false;
  metadataWorkspace.saving = false;
  metadataSaveOwner = null;
  advanceMetadataWorkspaceRevision();
  const closeContext = Object.freeze({
    action: "close-after-save",
    targetMediaId: null,
    targetScope: null,
    openMediaId: owner.mediaId,
    openItemMediaId: owner.openItemMediaId,
    workspaceRevision: metadataWorkspaceRevision,
    catalogScope: owner.catalogScope,
  });
  return closeMetadataWorkspaceWithContext(closeContext);
}

async function confirmDiscardDirtyMetadata(intent = {}) {
  const context = captureMetadataDiscardContext(intent);
  if (!metadataDirtyForBeforeUnload()) {
    return context;
  }
  const accepted = await requestConfirmation({
    title: "Discard changes?",
    message: "Unsaved metadata changes will be discarded.",
    dismissLabel: "Keep editing",
    confirmLabel: "Discard changes",
    destructive: true,
  });
  if (!accepted) return null;
  if (!metadataDiscardContextIsCurrent(context)) return null;
  return context;
}

function updateMetadataControls() {
  const normalized = normalizedMetadataFormState();
  const validation = normalized.error || "";
  const dirty = metadataIsDirty();
  metadataValidationMessage.textContent = validation;
  metadataSaveButton.textContent = metadataWorkspace.saving ? "Saving..." : "Save";
  metadataSaveButton.disabled = metadataWorkspace.loading || metadataWorkspace.saving || !dirty || Boolean(validation);
  metadataDiscardButton.disabled = metadataWorkspace.saving;
  syncMetadataBeforeUnloadProtection();
  if (metadataWorkspace.loading) {
    metadataStatus.textContent = "Loading...";
  } else if (metadataWorkspace.saving) {
    metadataStatus.textContent = "Saving...";
  } else if (metadataWorkspace.statusOverride === "saving") {
    metadataStatus.textContent = metadataStatus.textContent || "Working...";
  } else if (metadataWorkspace.unavailable) {
    metadataStatus.textContent = "Catalog unavailable.";
  } else if (metadataWorkspace.notFound) {
    metadataStatus.textContent = "Medium no longer available.";
  } else if (validation) {
    metadataStatus.textContent = validation;
  } else if (metadataWorkspace.statusOverride === "validation") {
    metadataStatus.textContent = metadataStatus.textContent || "Please check this edit.";
  } else if (metadataWorkspace.statusOverride === "error") {
    metadataStatus.textContent = metadataStatus.textContent || "Save failed.";
  } else {
    metadataStatus.textContent = "";
  }
  if (metadataAiAnalyzeButton) {
    const analysisAvailable = Boolean(aiCapability.available && metadataAiLocation())
      && identityAllowsAiAnalyze();
    const showAnalyze = analysisAvailable;
    metadataAiAnalyzeButton.hidden = !showAnalyze;
    metadataAiAnalyzeButton.disabled = !showAnalyze
      || metadataWorkspace.loading
      || metadataWorkspace.saving
      || metadataWorkspace.analyzing;
    renderMetadataAiAnalyzeButtonContent(metadataWorkspace.analyzing);
    metadataAiAnalyzeButton.setAttribute("aria-busy", metadataWorkspace.analyzing ? "true" : "false");
  }
  if (metadataAiProgress) {
    metadataAiProgress.hidden = !metadataWorkspace.analyzing;
    metadataAiProgress.setAttribute("aria-busy", metadataWorkspace.analyzing ? "true" : "false");
  }
  if (metadataAiSuggestionDropdown) {
    const showSelect = identityAllowsAiSuggestionChrome()
      && metadataSuggestionList.items.length > 0;
    metadataAiSuggestionDropdown.hidden = !showSelect;
    const dropdownBusy = metadataWorkspace.loading
      || metadataWorkspace.saving
      || metadataWorkspace.analyzing
      || metadataSuggestionList.fetching;
    if (metadataAiSuggestionToggle) {
      metadataAiSuggestionToggle.disabled = !showSelect || dropdownBusy;
    }
    if (!showSelect) closeMetadataSuggestionDropdown();
  }
}

function renderMetadataAiAnalyzeButtonContent(isAnalyzing) {
  if (!metadataAiAnalyzeButton) return;
  metadataAiAnalyzeButton.replaceChildren();
  if (!isAnalyzing) {
    metadataAiAnalyzeButton.textContent = metadataWorkspace.analysisFailureCode
      ? "Retry analysis"
      : "Analyze by AI";
    return;
  }
  const label = document.createElement("span");
  label.textContent = "Analyzing…";
  metadataAiAnalyzeButton.append(label);
}

function renderSelectedMetadataTags() {
  metadataSelectedTags.replaceChildren();
  if (mediaHasCreatorAttribution(metadataWorkspace.current)) {
    const chip = document.createElement("span");
    chip.className = "metadata-tag-chip metadata-tag-chip--creator";
    const label = document.createElement("span");
    label.textContent = mediaCreatorChipLabel(metadataWorkspace.current);
    chip.appendChild(label);
    metadataSelectedTags.appendChild(chip);
  }
  metadataWorkspace.current.tagKeys.forEach((key) => {
    const definition = selectedTagDefinition(key);
    const chip = document.createElement("span");
    chip.className = "metadata-tag-chip";
    const label = document.createElement("span");
    const displayName = definition ? definition.display_name : "Tag";
    label.textContent = displayName;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "metadata-tag-chip__remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${displayName} from this media`);
    remove.addEventListener("click", () => removeSelectedMetadataTag(key));
    chip.append(label, remove);
    metadataSelectedTags.appendChild(chip);
  });
  metadataTagStatus.textContent = metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS ? "Tag limit reached." : "";
}

function renderMetadataTagSuggestions() {
  metadataTagSuggestions.replaceChildren();
  const displayName = normalizedTagDisplayName(metadataTagSearchInput.value);
  const query = displayName.toLocaleLowerCase();
  const matches = canonicalTagDefinitions.filter((tag) => {
    return query
      && !metadataWorkspace.current.tagKeys.includes(tag.key)
      && tag.display_name.toLocaleLowerCase().includes(query);
  });
  const exactMatch = displayName ? findTagByDisplayName(displayName) : null;
  const items = matches.map((tag) => ({ type: "select", tag, label: tag.display_name }));
  if (displayName && !exactMatch && identityUsesCanonicalMetadataWrite()) {
    items.push({ type: "add", displayName, label: `Add “${displayName}”` });
  }
  metadataTagSuggestionState.items = items;
  if (items.length === 0) {
    metadataTagSuggestionState.activeIndex = -1;
    metadataTagSearchInput.setAttribute("aria-expanded", "false");
    return;
  }
  if (metadataTagSuggestionState.activeIndex < 0 || metadataTagSuggestionState.activeIndex >= items.length) {
    metadataTagSuggestionState.activeIndex = 0;
  }
  metadataTagSearchInput.setAttribute("aria-expanded", "true");
  items.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "metadata-tag-suggestion";
    button.id = `metadata-tag-suggestion-${index}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(index === metadataTagSuggestionState.activeIndex));
    button.textContent = item.label;
    button.disabled = metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS;
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => activateMetadataTagSuggestion(index));
    metadataTagSuggestions.appendChild(button);
  });
}

function updateDescriptionStatus() {
  const length = unicodeCodePointLength(metadataDescriptionInput.value);
  if (length > MAX_METADATA_DESCRIPTION_CODE_POINTS) {
    metadataDescriptionStatus.hidden = false;
    metadataDescriptionStatus.textContent = `Description must be ${MAX_METADATA_DESCRIPTION_CODE_POINTS} characters or fewer.`;
    return;
  }
  metadataDescriptionStatus.hidden = true;
  metadataDescriptionStatus.textContent = "";
}

function renderMetadataWorkspace() {
  reconcileCatalogSelectedCard();
  if (metadataWorkspace.openMediaId === null) {
    metadataWorkspaceElement.hidden = true;
    syncMetadataBeforeUnloadProtection();
    return;
  }
  metadataWorkspaceElement.hidden = false;
  metadataWorkspaceTitle.textContent = metadataDialogHeading();
  metadataWorkspaceContext.textContent = metadataWorkspace.openItem
    ? (metadataWorkspace.openItem.display_title || deriveCatalogFallbackTitle(metadataWorkspace.openItem))
    : `Media ID ${metadataWorkspace.openMediaId}`;
  metadataTitleInput.value = metadataWorkspace.current.displayTitle || "";
  if (metadataWorkspace.baseline.displayTitle === null && metadataWorkspace.openItem) {
    metadataTitleFallback.hidden = false;
    metadataTitleFallback.textContent = `Fallback: ${deriveCatalogFallbackTitle(metadataWorkspace.openItem)}`;
  } else {
    metadataTitleFallback.hidden = true;
  }
  metadataDescriptionInput.value = metadataWorkspace.current.description || "";
  updateDescriptionStatus();

  renderSelectedMetadataTags();
  renderMetadataTagSuggestions();
  if (metadataTagSearchInput) {
    metadataTagSearchInput.placeholder = metadataWorkspaceIsAliasMode()
      ? "Search existing tags"
      : "Search or add a tag";
  }
  renderMetadataAiPanel();
  updateMetadataControls();
}

function metadataAiLocation() {
  if (!metadataWorkspace.openItem) return null;
  return selectPlaybackLocation(metadataWorkspace.openItem);
}

function captureMetadataAiConfirmationContext(location) {
  return Object.freeze({
    mediaId: metadataWorkspace.openMediaId,
    openItemMediaId: metadataOpenItemMediaId(),
    locationId: location.location_id,
    workspaceRevision: metadataWorkspaceRevision,
    capabilityRevision: aiCapabilityRevision,
    requestToken: metadataAiRequestToken,
    capabilityAvailable: aiCapability.available,
    analyzing: metadataWorkspace.analyzing,
    aiSuggestionApplied: metadataWorkspace.aiSuggestionApplied,
    loading: metadataWorkspace.loading,
    saving: metadataWorkspace.saving,
  });
}

function metadataAiConfirmationContextIsCurrent(context) {
  const location = metadataAiLocation();
  return Boolean(context)
    && context.mediaId !== null
    && metadataWorkspace.openMediaId === context.mediaId
    && metadataOpenItemMediaId() === context.openItemMediaId
    && Boolean(location)
    && location.location_id === context.locationId
    && metadataWorkspaceRevision === context.workspaceRevision
    && aiCapabilityRevision === context.capabilityRevision
    && metadataAiRequestToken === context.requestToken
    && context.capabilityAvailable
    && aiCapability.available
    && !context.analyzing
    && !metadataWorkspace.analyzing
    && !context.loading
    && !metadataWorkspace.loading
    && !context.saving
    && !metadataWorkspace.saving;
}

function metadataAiRequestContextIsCurrent(requestContext) {
  if (!requestContext) return false;
  const token = requestContext.token;
  const location = metadataAiLocation();
  return token === metadataAiRequestToken
    && metadataWorkspace.openMediaId === requestContext.mediaId
    && metadataOpenItemMediaId() === requestContext.openItemMediaId
    && Boolean(location)
    && location.location_id === requestContext.locationId
    && metadataWorkspaceRevision === requestContext.workspaceRevision
    && aiCapabilityRevision === requestContext.capabilityRevision
    && metadataWorkspace.analyzing;
}

function releaseMetadataAiRequest(requestContext) {
  if (!requestContext) return false;
  const token = requestContext.token;
  if (token !== metadataAiRequestToken) return false;
  if (metadataWorkspace.openMediaId !== requestContext.mediaId) return false;
  if (metadataOpenItemMediaId() !== requestContext.openItemMediaId) return false;
  if (!metadataWorkspace.analyzing) return false;
  metadataWorkspace.analyzing = false;
  advanceMetadataWorkspaceRevision();
  updateMetadataControls();
  return true;
}

function renderMetadataAiPanel() {
  if (!metadataAiPanel) return;
  const showChrome = identityAllowsAiSuggestionChrome();
  metadataAiPanel.hidden = !showChrome;
  if (metadataAiHeading) {
    metadataAiHeading.textContent = "AI suggestions";
  }
  renderMetadataSuggestionSelect();
  renderMetadataSuggestionStrips();
  if (metadataAiFilenameNote) {
    const selected = selectedMetadataSuggestion();
    const showFilename = Boolean(
      showChrome
      && selected
      && selected.suggestedFilename,
    );
    metadataAiFilenameNote.hidden = !showFilename;
    metadataAiFilenameNote.replaceChildren();
    if (showFilename) {
      const text = document.createElement("span");
      text.textContent = `Suggested filename: ${selected.suggestedFilename}`;
      metadataAiFilenameNote.appendChild(text);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "metadata-ai-filename-copy";
      copy.textContent = "Copy";
      copy.setAttribute("aria-label", "Copy suggested filename");
      copy.addEventListener("click", () => {
        copySuggestedFilename(selected.suggestedFilename);
      });
      metadataAiFilenameNote.appendChild(copy);
    }
  }
}

function aiSuggestionFromPayload(payload) {
  const suggestion = payload.suggestion || {};
  return {
    title: String(suggestion.title || ""),
    description: String(suggestion.description || ""),
    tags: Array.isArray(suggestion.tags) ? suggestion.tags.map((tag) => String(tag)) : [],
    suggestedFilename: String(suggestion.suggested_filename || ""),
  };
}

function aiSuggestionFromAutomaticAnalysisResult(result) {
  if (!result || typeof result !== "object" || !Array.isArray(result.tags)) {
    return null;
  }
  return {
    title: String(result.title || ""),
    description: String(result.description || ""),
    tags: result.tags.map((tag) => String(tag)),
    suggestedFilename: String(result.suggested_filename || ""),
  };
}

function resetMetadataDurableAnalysisState() {
  metadataDurableAnalysis = {
    mediaId: null,
    fetching: false,
    state: null,
    analysisDefinition: null,
    result: null,
    statusMessage: "",
    errorMessage: "",
    detailsExpanded: false,
  };
}

function resetMetadataSuggestionListState() {
  metadataSuggestionList = {
    mediaId: null,
    fetching: false,
    items: [],
    selectedRunId: null,
    errorMessage: "",
    movieExcluded: false,
  };
}

function selectedMetadataSuggestion() {
  if (!metadataSuggestionList.selectedRunId) return null;
  return metadataSuggestionList.items.find(
    (item) => item.analysisRunId === metadataSuggestionList.selectedRunId,
  ) || null;
}

function suggestionOptionLabel(item) {
  const title = typeof item.title === "string" ? item.title.trim() : "";
  const model = item.modelId || "AI";
  if (title) return `${title} · ${model}`;
  return model;
}

function inboxSuggestionFromDetail(raw) {
  if (!raw || typeof raw !== "object") return null;
  const analysisRunId = String(raw.analysis_run_id || "");
  if (!analysisRunId) return null;
  const tags = Array.isArray(raw.tags) ? raw.tags.map((tag) => ({
    value: String(tag && tag.value != null ? tag.value : ""),
    status: String(tag && tag.status ? tag.status : ""),
    key: tag && tag.key ? String(tag.key) : null,
    displayName: tag && tag.display_name ? String(tag.display_name) : null,
  })) : [];
  return {
    analysisRunId,
    completedAtMs: Number(raw.completed_at_ms) || 0,
    providerId: String(raw.provider_id || ""),
    modelId: String(raw.model_id || ""),
    promptVersion: String(raw.prompt_version || ""),
    title: String(raw.title || ""),
    description: String(raw.description || ""),
    tags,
    suggestedFilename: String(raw.suggested_filename || ""),
  };
}

function inSessionSuggestionFromPreview(suggestion, payload) {
  const analysisRunId = payload && payload.analysis_run_id
    ? String(payload.analysis_run_id)
    : `preview-${Date.now()}`;
  const tags = Array.isArray(suggestion.tags)
    ? suggestion.tags.map((tag) => {
      const displayName = String(tag || "");
      const existing = findTagByDisplayName(displayName);
      return {
        value: displayName,
        status: existing ? "mapped" : "unknown",
        key: existing ? existing.key : null,
        displayName: existing ? existing.display_name : displayName,
      };
    })
    : [];
  return {
    analysisRunId,
    completedAtMs: Date.now(),
    providerId: payload && payload.provider_id ? String(payload.provider_id) : "",
    modelId: payload && payload.model_id ? String(payload.model_id) : (aiCapability.model_id || "AI"),
    promptVersion: payload && payload.prompt_version ? String(payload.prompt_version) : "",
    title: suggestion.title || "",
    description: suggestion.description || "",
    tags,
    suggestedFilename: suggestion.suggestedFilename || "",
  };
}

function renderMetadataSuggestionSelect() {
  const selected = selectedMetadataSuggestion();
  if (metadataAiSuggestionToggleLabel) {
    metadataAiSuggestionToggleLabel.textContent = selected
      ? suggestionOptionLabel(selected)
      : "";
  }
  if (!metadataAiSuggestionList) return;
  metadataAiSuggestionList.replaceChildren();
  metadataSuggestionList.items.forEach((item) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "metadata-ai-suggestion-option";
    option.setAttribute("role", "option");
    option.dataset.runId = item.analysisRunId;
    option.id = `metadata-ai-suggestion-option-${item.analysisRunId}`;
    option.textContent = suggestionOptionLabel(item);
    option.setAttribute(
      "aria-selected",
      String(item.analysisRunId === metadataSuggestionList.selectedRunId),
    );
    option.addEventListener("click", () => {
      selectMetadataSuggestion(item.analysisRunId);
    });
    metadataAiSuggestionList.appendChild(option);
  });
  if (metadataAiSuggestionToggle && selected) {
    metadataAiSuggestionToggle.setAttribute(
      "aria-activedescendant",
      `metadata-ai-suggestion-option-${selected.analysisRunId}`,
    );
  }
  if (
    metadataSuggestionList.selectedRunId
    && !metadataSuggestionList.items.some((item) => item.analysisRunId === metadataSuggestionList.selectedRunId)
  ) {
    metadataSuggestionList.selectedRunId = metadataSuggestionList.items.length > 0
      ? metadataSuggestionList.items[0].analysisRunId
      : null;
  } else if (!metadataSuggestionList.selectedRunId && metadataSuggestionList.items.length > 0) {
    metadataSuggestionList.selectedRunId = metadataSuggestionList.items[0].analysisRunId;
  }
}

function closeMetadataSuggestionDropdown() {
  if (!metadataAiSuggestionList || !metadataAiSuggestionToggle) return;
  metadataAiSuggestionList.hidden = true;
  metadataAiSuggestionToggle.setAttribute("aria-expanded", "false");
}

function toggleMetadataSuggestionDropdown() {
  if (!metadataAiSuggestionList || !metadataAiSuggestionToggle) return;
  if (metadataAiSuggestionToggle.disabled) return;
  const open = metadataAiSuggestionList.hidden;
  metadataAiSuggestionList.hidden = !open;
  metadataAiSuggestionToggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    const selected = metadataAiSuggestionList.querySelector('[aria-selected="true"]');
    (selected || metadataAiSuggestionList.querySelector(".metadata-ai-suggestion-option"))?.focus();
  }
}

function copySuggestedFilename(filename) {
  if (!filename) return;
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    void navigator.clipboard.writeText(filename);
  }
}

function clearMetadataSuggestionStrip(container) {
  if (!container) return;
  container.replaceChildren();
  container.hidden = true;
}

function appendSuggestionApplyButton(container, field, tagKey) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "metadata-suggestion-strip__apply";
  button.textContent = "✅";
  button.setAttribute("aria-label", tagKey ? `Copy suggested tag ${tagKey}` : `Copy suggested ${field}`);
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    copySuggestionFieldToCurrent(field, tagKey);
  });
  container.appendChild(button);
}

function renderMetadataSuggestionStrips() {
  const show = identityAllowsAiSuggestionChrome();
  const item = show ? selectedMetadataSuggestion() : null;
  if (!item) {
    clearMetadataSuggestionStrip(metadataAiTitleStrip);
    clearMetadataSuggestionStrip(metadataAiDescriptionStrip);
    clearMetadataSuggestionStrip(metadataAiTagsStrip);
    return;
  }
  if (metadataAiTitleStrip) {
    metadataAiTitleStrip.replaceChildren();
    const text = document.createElement("span");
    text.className = "metadata-suggestion-strip__text";
    text.textContent = item.title || "(No title)";
    metadataAiTitleStrip.appendChild(text);
    if (item.title) appendSuggestionApplyButton(metadataAiTitleStrip, "title");
    metadataAiTitleStrip.hidden = false;
  }
  if (metadataAiDescriptionStrip) {
    metadataAiDescriptionStrip.replaceChildren();
    const text = document.createElement("span");
    text.className = "metadata-suggestion-strip__text";
    text.textContent = item.description || "(No description)";
    metadataAiDescriptionStrip.appendChild(text);
    if (item.description) appendSuggestionApplyButton(metadataAiDescriptionStrip, "description");
    metadataAiDescriptionStrip.hidden = false;
  }
  if (metadataAiTagsStrip) {
    metadataAiTagsStrip.replaceChildren();
    if (!Array.isArray(item.tags) || item.tags.length === 0) {
      metadataAiTagsStrip.hidden = true;
    } else {
      item.tags.forEach((tag) => {
        const mapped = tag.status === "mapped" && tag.key;
        if (mapped) {
          const alreadyAdded = metadataWorkspace.current.tagKeys.includes(tag.key);
          const label = tag.displayName || tag.value || tag.key || "";
          const button = document.createElement("button");
          button.type = "button";
          button.className = "metadata-suggestion-tag metadata-suggestion-tag--mapped";
          button.textContent = label;
          button.setAttribute("aria-pressed", String(alreadyAdded));
          button.setAttribute(
            "aria-label",
            alreadyAdded ? `${label}, already added to Current` : `Add suggested tag ${label} to Current`,
          );
          button.classList.toggle("metadata-suggestion-tag--already-added", alreadyAdded);
          if (alreadyAdded) button.title = "Already added to Current";
          button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            copySuggestionFieldToCurrent("tag", tag.key);
          });
          metadataAiTagsStrip.appendChild(button);
        } else {
          const label = tag.displayName || tag.value || tag.key || "";
          if (!metadataWorkspaceIsAliasMode()) {
            const button = document.createElement("button");
            button.type = "button";
            button.className =
              "metadata-suggestion-tag metadata-suggestion-tag--unmapped metadata-suggestion-tag--unmapped-actionable";
            button.textContent = label;
            button.setAttribute("title", "Not in catalog tags — click to add");
            button.addEventListener("click", (event) => {
              event.preventDefault();
              event.stopPropagation();
              createAndSelectMetadataTag(label);
            });
            metadataAiTagsStrip.appendChild(button);
          } else {
            const chip = document.createElement("span");
            chip.className = "metadata-suggestion-tag metadata-suggestion-tag--unmapped";
            chip.textContent = label;
            chip.setAttribute("title", "Not in catalog tags");
            metadataAiTagsStrip.appendChild(chip);
          }
        }
      });
      metadataAiTagsStrip.hidden = false;
    }
  }
}

function copySuggestionFieldToCurrent(field, tagKey) {
  const item = selectedMetadataSuggestion();
  if (!item) return;
  if (field === "title") {
    metadataWorkspace.current.displayTitle = item.title || "";
  } else if (field === "description") {
    metadataWorkspace.current.description = item.description || "";
  } else if (field === "tag" && tagKey) {
    if (metadataWorkspace.current.tagKeys.includes(tagKey)) {
      const definition = selectedTagDefinition(tagKey);
      const label = definition ? definition.display_name : "That tag";
      metadataAiStatus.textContent = `${label} is already in Current.`;
      return;
    }
    if (metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS) {
      setMetadataStatus("validation", "Tag limit reached.");
      updateMetadataControls();
      return;
    }
    metadataWorkspace.current.tagKeys = [...metadataWorkspace.current.tagKeys, tagKey];
  } else {
    return;
  }
  metadataWorkspace.statusOverride = null;
  advanceMetadataWorkspaceRevision();
  renderMetadataWorkspace();
}

function selectMetadataSuggestion(runId) {
  metadataSuggestionList.selectedRunId = runId || null;
  closeMetadataSuggestionDropdown();
  renderMetadataSuggestionSelect();
  renderMetadataSuggestionStrips();
  updateMetadataControls();
}

function presentInSessionSuggestion(suggestion, payload) {
  const item = inSessionSuggestionFromPreview(suggestion, payload);
  const without = metadataSuggestionList.items.filter(
    (existing) => existing.analysisRunId !== item.analysisRunId,
  );
  metadataSuggestionList.items = [item, ...without];
  metadataSuggestionList.selectedRunId = item.analysisRunId;
  metadataWorkspace.suggestedFilename = item.suggestedFilename || "";
}

async function refreshMetadataSuggestionList(mediaId) {
  if (!mediaId || !identityAllowsAiSuggestionChrome()) return;
  const token = ++metadataSuggestionListToken;
  metadataSuggestionList.mediaId = mediaId;
  metadataSuggestionList.fetching = true;
  metadataSuggestionList.movieExcluded = false;
  updateMetadataControls();
  try {
    const response = await fetch(mediaAiSuggestionsEndpoint(mediaId), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (token !== metadataSuggestionListToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    metadataSuggestionList.fetching = false;
    if (response.status === 409) {
      metadataSuggestionList.items = [];
      metadataSuggestionList.selectedRunId = null;
      metadataSuggestionList.movieExcluded = true;
      renderMetadataWorkspace();
      return;
    }
    if (!response.ok) {
      metadataSuggestionList.errorMessage = "";
      renderMetadataWorkspace();
      return;
    }
    const payload = await response.json();
    if (token !== metadataSuggestionListToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    const previousId = metadataSuggestionList.selectedRunId;
    const selectedItem = selectedMetadataSuggestion();
    let items = Array.isArray(payload.suggestions)
      ? payload.suggestions.map(inboxSuggestionFromDetail).filter(Boolean)
      : [];
    const seen = new Set();
    items = items.filter((item) => {
      if (seen.has(item.analysisRunId)) return false;
      seen.add(item.analysisRunId);
      return true;
    });
    if (selectedItem && selectedItem.suggestedFilename) {
      items = items.map((item) => {
        if (item.analysisRunId === selectedItem.analysisRunId && !item.suggestedFilename) {
          return { ...item, suggestedFilename: selectedItem.suggestedFilename };
        }
        return item;
      });
    }
    metadataSuggestionList.items = items;
    if (previousId && items.some((item) => item.analysisRunId === previousId)) {
      metadataSuggestionList.selectedRunId = previousId;
    } else {
      metadataSuggestionList.selectedRunId = items.length > 0 ? items[0].analysisRunId : null;
    }
    renderMetadataWorkspace();
  } catch {
    if (token !== metadataSuggestionListToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    metadataSuggestionList.fetching = false;
    renderMetadataWorkspace();
  }
}

async function refreshMetadataDurableAnalysis(mediaId, requestToken) {
  if (!mediaId) return;
  const preferMovie = (metadataWorkspace.current.contentCategory || "general") === "movie";
  metadataDurableAnalysis = {
    mediaId,
    fetching: true,
    state: null,
    analysisDefinition: null,
    result: null,
    statusMessage: "",
    errorMessage: "",
    detailsExpanded: false,
  };
  updateMetadataControls();
  try {
    const endpoint = preferMovie
      ? movieIdentificationEndpoint(mediaId)
      : automaticAnalysisEndpoint(mediaId);
    const response = await fetch(endpoint, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (requestToken !== metadataDurableAnalysisToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    if (!response.ok) {
      metadataDurableAnalysis = {
        mediaId,
        fetching: false,
        state: null,
        analysisDefinition: null,
        result: null,
        statusMessage: "",
        errorMessage: preferMovie
          ? "Movie identification status could not be loaded."
          : "Automatic analysis status could not be loaded.",
        detailsExpanded: false,
      };
      renderMetadataWorkspace();
      return;
    }
    const payload = await response.json();
    if (requestToken !== metadataDurableAnalysisToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    metadataDurableAnalysis = applyAnalysisStatusPayload(mediaId, payload, preferMovie);
    renderMetadataWorkspace();
  } catch {
    if (requestToken !== metadataDurableAnalysisToken) return;
    if (metadataWorkspace.openMediaId !== mediaId) return;
    metadataDurableAnalysis = {
      mediaId,
      fetching: false,
      state: null,
      analysisDefinition: null,
      result: null,
      statusMessage: "",
      errorMessage: preferMovie
        ? "Movie identification status could not be loaded."
        : "Automatic analysis status could not be loaded.",
      detailsExpanded: false,
    };
    renderMetadataWorkspace();
  }
}

function applyAnalysisStatusPayload(mediaId, payload, preferMovie) {
  const state = payload && payload.state ? String(payload.state) : null;
  const analysisDefinition = payload && payload.analysis_definition
    ? String(payload.analysis_definition)
    : (preferMovie ? "movie_identification" : null);
  const isMovie = analysisDefinition === "movie_identification" || preferMovie;
  let statusMessage = "";
  let errorMessage = "";
  let result = null;
  let movieResult = null;
  if (state === "pending" || state === "analyzing" || state === "failed" || state === "analyzed") {
    statusMessage = isMovie
      ? movieIdentificationStatusMessage(payload)
      : automaticAnalysisStatusMessage(payload);
  } else if (state === "not_requested") {
    statusMessage = "";
  } else if (state) {
    errorMessage = isMovie
      ? "Movie identification status is incomplete."
      : "Automatic analysis status is incomplete.";
  }
  if (state === "analyzed") {
    if (isMovie) {
      movieResult = payload.movie_identification_result || null;
      if (!movieResult || typeof movieResult !== "object") {
        movieResult = null;
        errorMessage = "Movie identification result is incomplete.";
        statusMessage = "";
      } else if (movieIdentificationIsPureUnknown(movieResult)) {
        statusMessage = movieIdentificationStatusMessage(payload);
      }
    } else {
      result = payload.result || null;
      if (!aiSuggestionFromAutomaticAnalysisResult(result)) {
        result = null;
        errorMessage = "Automatic analysis result is incomplete.";
        statusMessage = "";
      }
    }
  }
  return {
    mediaId,
    fetching: false,
    state,
    analysisDefinition,
    result,
    statusMessage,
    errorMessage,
    detailsExpanded: false,
  };
}

async function runMetadataAiAnalysis(confirmationContext, options) {
  const requestGuard = options && typeof options.requestGuard === "function"
    ? options.requestGuard
    : null;
  if (!metadataAiConfirmationContextIsCurrent(confirmationContext)) {
    metadataAiStatus.textContent = "This editor changed before analysis could start. Confirm again to analyze.";
    return false;
  }
  const token = ++metadataAiRequestToken;
  metadataWorkspace.analyzing = true;
  metadataWorkspace.analysisFailureCode = "";
  advanceMetadataWorkspaceRevision();
  const requestContext = Object.freeze({
    token,
    mediaId: confirmationContext.mediaId,
    openItemMediaId: confirmationContext.openItemMediaId,
    locationId: confirmationContext.locationId,
    workspaceRevision: metadataWorkspaceRevision,
    capabilityRevision: aiCapabilityRevision,
  });
  metadataAiStatus.textContent = "";
  renderMetadataWorkspace();
  try {
    const response = await fetch(mediaAiSuggestionEndpoint(requestContext.mediaId, requestContext.locationId), {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ confirm_cloud_upload: true }),
      cache: "no-store",
    });
    const payload = await response.json();
    const externallyCurrent = typeof requestGuard !== "function" || requestGuard();
    if (!metadataAiRequestContextIsCurrent(requestContext) || !externallyCurrent) {
      releaseMetadataAiRequest(requestContext);
      return false;
    }
    if (!response.ok) {
      metadataWorkspace.analysisFailureCode = payload && payload.error
        ? String(payload.error.code || "AI_ANALYSIS_FAILED")
        : "AI_ANALYSIS_FAILED";
      releaseMetadataAiRequest(requestContext);
      metadataAiStatus.textContent = aiSuggestionErrorMessage(payload);
      renderMetadataWorkspace();
      return false;
    }
    if (!cardAiPreviewResponseMatchesRequest(payload, requestContext.mediaId, requestContext.locationId)) {
      metadataWorkspace.analysisFailureCode = "AI_RESPONSE_CONTEXT_MISMATCH";
      releaseMetadataAiRequest(requestContext);
      metadataAiStatus.textContent = "The AI result did not match this media. Try analysis again.";
      renderMetadataWorkspace();
      return false;
    }
    const suggestion = aiSuggestionFromPayload(payload);
    metadataWorkspace.analyzing = false;
    metadataWorkspace.analysisFailureCode = "";
    advanceMetadataWorkspaceRevision();
    presentInSessionSuggestion(suggestion, payload);
    metadataAiStatus.textContent = "Suggestion ready. Review and copy only the fields you want.";
    renderMetadataWorkspace();
  } catch {
    const requestIsCurrent = metadataAiRequestContextIsCurrent(requestContext);
    if (requestIsCurrent) metadataWorkspace.analysisFailureCode = "AI_ANALYSIS_FAILED";
    if (releaseMetadataAiRequest(requestContext) && requestIsCurrent) {
      metadataAiStatus.textContent = "AI analysis failed. Try again.";
      renderMetadataWorkspace();
    }
    return false;
  }
  await refreshMetadataSuggestionList(requestContext.mediaId);
  return true;
}

async function handleAnalyzeMetadataByAi() {
  if (!aiCapability.available) {
    metadataAiStatus.textContent = "AI analysis is not configured.";
    return false;
  }
  if (
    metadataWorkspace.openMediaId === null
    || metadataWorkspace.loading
    || metadataWorkspace.saving
  ) return false;
  if (metadataWorkspace.analyzing) return false;
  if (companionWebHosted() || metadataWorkspaceIsMovie() || metadataWorkspaceIsAliasMode()) return false;
  if (!identityHasCapability("analysis.run")) return false;
  const location = metadataAiLocation();
  if (!location) {
    metadataAiStatus.textContent = "AI analysis needs an available local GIF or MP4.";
    return false;
  }
  const confirmationContext = captureMetadataAiConfirmationContext(location);
  const accepted = await requestConfirmation({
    title: metadataWorkspace.analysisFailureCode ? "Retry AI analysis?" : "Use AI analysis?",
    message: "Kronika will send up to 3 optimized preview frames and bounded metadata to the configured server-side AI provider. The original file, local path, and API key are not uploaded. Returned values become proposal strips beside Title, Description, and Tags. They do not replace the current unsaved values. The result will not be saved automatically, and the physical file will not be renamed.",
    dismissLabel: "Not now",
    confirmLabel: metadataWorkspace.analysisFailureCode ? "Retry analysis" : "Analyze by AI",
    destructive: false,
  });
  if (!accepted) return false;
  return runMetadataAiAnalysis(confirmationContext);
}

function aiSuggestionErrorMessage(payload) {
  const code = payload && payload.error ? payload.error.code : "";
  if (code === "AI_PROVIDER_NOT_CONFIGURED") return "AI analysis is not configured. Ask the server operator to configure it.";
  if (code === "CLOUD_CONFIRMATION_REQUIRED") return "AI analysis needs confirmation. Try again and confirm the cloud request.";
  if (code === "MEDIA_PREPARATION_UNAVAILABLE") return "This media cannot be prepared for analysis. Continue editing manually.";
  if (code === "AI_PROVIDER_AUTHENTICATION_FAILED") return "AI provider authentication was rejected. Ask the server operator to check AI configuration.";
  if (code === "AI_PROVIDER_RATE_LIMITED") return "The AI provider rate limit was reached. Try again later.";
  if (code === "AI_PROVIDER_MODEL_UNAVAILABLE") return "The configured AI model is unavailable. Ask the server operator to check AI configuration.";
  if (code === "AI_PROVIDER_INVALID_RESPONSE") return "The model returned an unusable response. Try analysis again.";
  if (code === "AI_PROVIDER_UNAVAILABLE") return "The AI provider is unavailable. Try again later.";
  return "AI analysis failed. Try again.";
}

function applyMetadataPayloadToWorkspace(payload) {
  const tagKeys = (payload.tags || []).map((tag) => tag.key);
  const collectionKey = payload.collection_key ?? null;
  const processedAtMs = payload.processed_at_ms ?? null;
  const contentCategory = payload.content_category || "general";
  const acquisitionSource = payload.acquisition_source || "unknown";
  const genres = Array.isArray(payload.genres) ? [...payload.genres] : [];
  const creatorAttributionKind = payload.creator_attribution_kind || null;
  const creatorStableId = payload.creator_stable_id || null;
  const creatorHandle = payload.creator_handle || null;
  const creatorDisplayName = payload.creator_display_name || null;
  metadataWorkspace.baseline = {
    displayTitle: payload.display_title === null ? null : payload.display_title,
    description: payload.description === null ? null : payload.description,
    tagKeys,
    collectionKey,
    processedAtMs,
    contentCategory,
    acquisitionSource,
    genres: [...genres],
    creatorAttributionKind,
    creatorStableId,
    creatorHandle,
    creatorDisplayName,
  };
  metadataWorkspace.current = {
    displayTitle: payload.display_title === null ? "" : payload.display_title,
    description: payload.description === null ? "" : payload.description,
    tagKeys: [...tagKeys],
    collectionKey,
    processedAtMs,
    contentCategory,
    acquisitionSource,
    genres: [...genres],
    creatorAttributionKind,
    creatorStableId,
    creatorHandle,
    creatorDisplayName,
  };
  advanceMetadataWorkspaceRevision();
  syncClassificationControlsFromWorkspace();
}

function aliasOverlayIsNonEmpty(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (payload.display_title != null && String(payload.display_title) !== "") return true;
  if (payload.description != null && String(payload.description) !== "") return true;
  return Array.isArray(payload.tag_keys) && payload.tag_keys.length > 0;
}

function applyAliasOverlayToWorkspace(payload) {
  const displayTitle = payload.display_title == null ? null : payload.display_title;
  const description = payload.description == null ? null : payload.description;
  const tagKeys = Array.isArray(payload.tag_keys) ? [...payload.tag_keys] : [];
  metadataWorkspace.baseline.displayTitle = displayTitle;
  metadataWorkspace.baseline.description = description;
  metadataWorkspace.baseline.tagKeys = [...tagKeys];
  metadataWorkspace.current.displayTitle = displayTitle || "";
  metadataWorkspace.current.description = description || "";
  metadataWorkspace.current.tagKeys = [...tagKeys];
  advanceMetadataWorkspaceRevision();
}

function detailsMediaItemLooksComplete(item) {
  return Boolean(
    item
    && item.media_id
    && item.media_kind
    && Array.isArray(item.locations)
    && item.locations.length > 0
  );
}

function mediaDetailEndpoint(mediaId) {
  return `${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(mediaId)}`;
}

async function resolveDetailsMediaItem(item) {
  if (detailsMediaItemLooksComplete(item)) {
    return item;
  }
  const mediaId = item && (item.media_id || item.id);
  if (!mediaId) return null;
  const response = await fetch(mediaDetailEndpoint(mediaId), {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) return null;
  const payload = await response.json();
  if (!payload || !payload.media_id) return null;
  return payload;
}

async function openDetailsDialog(item, openerElement, { playWhenReady = false } = {}) {
  if (!detailsDialog) return;
  const requestedMediaId = item && (item.media_id || item.id);
  if (metadataWorkspace.openMediaId !== null) {
    const targetMediaId = requestedMediaId;
    const discardContext = await confirmDiscardDirtyMetadata({ action: "open-details", targetMediaId });
    if (!discardContext || requestedMediaId !== targetMediaId) return;
    if (!closeMetadataWorkspaceWithContext(discardContext)) return;
  }
  if (
    item.media_kind === "video"
    && activeCardMediaRestore
    && activeCardMediaRestore.item
    && activeCardMediaRestore.item.media_id === requestedMediaId
  ) {
    captureVideoPlaybackPosition(requestedMediaId, cardSurfaceVideoElement(activeCardMediaRestore.surface));
  } else {
    captureActiveCardVideoPlaybackPosition();
  }
  stopCardPreviewTimer();
  cleanupDetailsMedia();
  detailsOpenerElement = openerElement || document.activeElement;
  detailsCurrentItem = item;
  detailsPlayRequested = playWhenReady;
  detailsLoading.hidden = false;
  detailsError.hidden = true;
  detailsContent.hidden = true;
  if (typeof detailsDialog.showModal === "function") {
    detailsDialog.showModal();
  } else {
    detailsDialog.setAttribute("open", "");
  }
  detailsCloseButton.focus();
  if (typeof kronikaRememberDetailsAddress === "function") {
    kronikaRememberDetailsAddress(requestedMediaId);
  }
  let resolved = item;
  try {
    resolved = await resolveDetailsMediaItem(item);
  } catch {
    resolved = null;
  }
  if (!resolved) {
    detailsLoading.hidden = true;
    detailsError.hidden = false;
    detailsContent.hidden = true;
    return;
  }
  detailsCurrentItem = resolved;
  populateDetailsDialog(resolved);
}

async function populateDetailsDialog(item) {
  const token = ++detailsMetadataToken;
  try {
    const response = await fetch(metadataEndpoint(item.media_id), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (token !== detailsMetadataToken) return;
    const payload = await response.json();
    if (token !== detailsMetadataToken) return;
    if (!response.ok) {
      detailsLoading.hidden = true;
      detailsError.hidden = false;
      return;
    }
    detailsLoading.hidden = true;
    detailsContent.hidden = false;

    const displayTitle = item.display_title
      || payload.display_title
      || deriveCatalogFallbackTitle(item);
    const tags = Array.isArray(item.tags) ? item.tags : (payload.tags || []);
    const description = (typeof item.description === "string" && item.description)
      ? item.description
      : (payload.description || "");
    const locations = Array.isArray(item.locations) ? item.locations : [];
    const hydratedItem = {
      ...item,
      display_title: displayTitle,
      tags,
      description,
      locations,
      collection_key: item.collection_key ?? payload.collection_key ?? null,
      processed_at_ms: item.processed_at_ms ?? payload.processed_at_ms ?? null,
      content_category: item.content_category || payload.content_category || "general",
      acquisition_source: item.acquisition_source || payload.acquisition_source || "unknown",
      creator_attribution_kind: item.creator_attribution_kind || payload.creator_attribution_kind || null,
      creator_stable_id: item.creator_stable_id || payload.creator_stable_id || null,
      creator_handle: item.creator_handle || payload.creator_handle || null,
      creator_display_name: item.creator_display_name || payload.creator_display_name || null,
    };
    detailsCurrentItem = hydratedItem;

    renderDetailsMedia(hydratedItem, { playWhenReady: detailsPlayRequested });

    if (detailsDialogTitle) {
      detailsDialogTitle.textContent = displayTitle;
    }

    detailsTagsContainer.replaceChildren();
    appendDetailsCreatorChip(detailsTagsContainer, hydratedItem);
    tags.forEach((tag) => {
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "media-details-dialog__tag";
      pill.textContent = tag.display_name;
      pill.setAttribute("aria-label", `Filter Gallery by ${tag.display_name}`);
      pill.addEventListener("click", () => activateDetailsTagFilter(tag.key));
      detailsTagsContainer.appendChild(pill);
    });

    detailsDescription.textContent = description;
    detailsDescription.hidden = !description;

    detailsTechnicalList.replaceChildren();
    if (detailsTechnical) {
      detailsTechnical.removeAttribute("open");
    }
    addMetadataValue(detailsTechnicalList, "Media ID", hydratedItem.media_id);
    addMetadataValue(detailsTechnicalList, "Kind", formatCatalogKind(hydratedItem.media_kind));
    if (!isPublicPublishedAudience()) {
      if (hydratedItem.created_at_ms !== null && hydratedItem.created_at_ms !== undefined) {
        addMetadataValue(detailsTechnicalList, "Created", new Date(hydratedItem.created_at_ms).toISOString());
      }
      addMetadataValue(detailsTechnicalList, "Collection", hydratedItem.collection_key || "none");
      const processedAtMs = hydratedItem.processed_at_ms;
      if (processedAtMs !== null && processedAtMs !== undefined) {
        addMetadataValue(detailsTechnicalList, "Processed at", new Date(processedAtMs).toISOString());
      }
    }
    locations.forEach((location, index) => {
      if (!isPublicPublishedAudience() && location.relative_path) {
        addMetadataValue(detailsTechnicalList, `Location ${index + 1}`, location.relative_path);
      }
      addMetadataValue(detailsTechnicalList, `Availability ${index + 1}`, location.availability);
    });
  } catch {
    if (token === detailsMetadataToken) {
      detailsLoading.hidden = true;
      detailsError.hidden = false;
    }
  }
}

function closeDetailsDialog({ restoreFocus = true } = {}) {
  if (!detailsDialog) return;
  cleanupDetailsMedia();
  if (typeof detailsDialog.close === "function") {
    detailsDialog.close();
  } else {
    detailsDialog.removeAttribute("open");
  }
  detailsCurrentItem = null;
  detailsPlayRequested = false;
  detailsMetadataToken++;
  if (restoreFocus && detailsOpenerElement) {
    detailsOpenerElement.focus();
  }
  detailsOpenerElement = null;
  if (typeof kronikaReleaseDetailsAddress === "function") {
    kronikaReleaseDetailsAddress();
  }
}

function presentPreviewSuggestionInMetadataWorkspace(previewSuggestion, previewPayload) {
  if (!previewSuggestion) return;
  presentInSessionSuggestion(previewSuggestion, previewPayload);
  if (metadataAiStatus) metadataAiStatus.textContent = "Loaded";
}

async function handleOpenMetadataWorkspace(item, openerElement, { previewSuggestion = null, previewPayload = null } = {}) {
  const targetMediaId = item.media_id;
  const reopensCurrentWorkspace = metadataWorkspace.openMediaId === targetMediaId
    && metadataOpenItemMediaId() === targetMediaId;
  if (reopensCurrentWorkspace) {
    if (detailsDialog && detailsDialog.hasAttribute("open")) {
      closeDetailsDialog();
    }
    metadataOpenerElement = openerElement || metadataOpenerElement || document.activeElement;
    if (metadataDialog && !metadataDialog.hasAttribute("open")) {
      if (typeof metadataDialog.showModal === "function") {
        metadataDialog.showModal();
      } else {
        metadataDialog.setAttribute("open", "");
      }
    }
    presentPreviewSuggestionInMetadataWorkspace(previewSuggestion, previewPayload);
    if (previewSuggestion && identityAllowsAiSuggestionChrome()) {
      await refreshMetadataSuggestionList(targetMediaId);
    }
    renderMetadataWorkspace();
    (metadataWorkspace.loading ? metadataWorkspaceTitle : metadataTitleInput).focus();
    return true;
  }
  const switchContext = captureMetadataDiscardContext({ action: "open-metadata", targetMediaId });
  const discardContext = await confirmDiscardDirtyMetadata({ action: "open-metadata", targetMediaId });
  if (!discardContext) {
    reopenMetadataWorkspaceAfterRejectedSwitch(switchContext);
    return false;
  }
  if (!metadataDiscardContextIsCurrent(discardContext) || item.media_id !== targetMediaId) return false;
  if (metadataWorkspace.openMediaId !== null) {
    if (!closeMetadataWorkspaceWithContext(discardContext)) return false;
  }
  if (detailsDialog && detailsDialog.hasAttribute("open")) {
    closeDetailsDialog();
  }
  metadataOpenerElement = openerElement || document.activeElement;
  const token = metadataRequestToken + 1;
  metadataRequestToken = token;
  metadataDurableAnalysisToken += 1;
  metadataSuggestionListToken += 1;
  advanceMetadataWorkspaceRevision();
  metadataWorkspace = {
    openMediaId: item.media_id,
    openItem: snapshotCatalogItemForMetadata(item),
    loading: true,
    saving: false,
    unavailable: false,
    notFound: false,
    statusOverride: null,
    analyzing: false,
    analysisFailureCode: "",
    aiSuggestionApplied: false,
    editMode: identityUsesCanonicalMetadataWrite() ? "canonical" : "alias",
    suggestedFilename: "",
    baseline: { displayTitle: null, description: null, tagKeys: [], collectionKey: null, processedAtMs: null },
    current: { displayTitle: "", description: "", tagKeys: [], collectionKey: null, processedAtMs: null },
  };
  resetMetadataDurableAnalysisState();
  resetMetadataSuggestionListState();
  metadataStatus.textContent = "";
  metadataValidationMessage.textContent = "";
  metadataAiStatus.textContent = "";
  if (metadataAiFilenameNote) {
    metadataAiFilenameNote.textContent = "";
    metadataAiFilenameNote.hidden = true;
  }
  metadataTagSearchInput.value = "";
  metadataTagSuggestionState = { items: [], activeIndex: -1 };
  if (metadataDialog && typeof metadataDialog.showModal === "function") {
    metadataDialog.showModal();
  }
  renderMetadataWorkspace();
  metadataWorkspaceTitle.focus();
  const tagsReady = await ensureCanonicalTags();
  if (token !== metadataRequestToken) {
    return false;
  }
  if (!tagsReady) {
    metadataWorkspace.loading = false;
    metadataWorkspace.unavailable = true;
    setMetadataStatus("unavailable", "Canonical tag definitions are unavailable.");
    updateMetadataControls();
    return true;
  }
  try {
    const mediaId = item.media_id;
    const response = await fetch(metadataEndpoint(mediaId), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (token !== metadataRequestToken) {
      return false;
    }
    metadataWorkspace.loading = false;
    if (!response.ok) {
      const code = payload.error ? payload.error.code : "";
      if (code === "MEDIA_NOT_FOUND") {
        metadataWorkspace.notFound = true;
        setMetadataStatus("notFound", "The selected medium is no longer available.");
        loadCatalog();
      } else if (code === "CATALOG_UNAVAILABLE") {
        metadataWorkspace.unavailable = true;
        setMetadataStatus("unavailable", "The local catalog is not available.");
      } else {
        setMetadataStatus("error", "Metadata could not be loaded from the local catalog.");
      }
      updateMetadataControls();
      return true;
    }
    applyMetadataPayloadToWorkspace(payload);
    if (metadataWorkspace.editMode === "alias") {
      const aliasResponse = await fetch(mediaAliasEndpoint(mediaId), {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (token !== metadataRequestToken) {
        return false;
      }
      if (aliasResponse.ok) {
        const aliasPayload = await aliasResponse.json();
        if (token !== metadataRequestToken) {
          return false;
        }
        if (aliasOverlayIsNonEmpty(aliasPayload)) {
          applyAliasOverlayToWorkspace(aliasPayload);
        }
      }
    }
    presentPreviewSuggestionInMetadataWorkspace(previewSuggestion, previewPayload);
    renderMetadataWorkspace();
    metadataTitleInput.focus();
    if (metadataWorkspaceIsMovie()) {
      const durableToken = ++metadataDurableAnalysisToken;
      await refreshMetadataDurableAnalysis(mediaId, durableToken);
    } else if (identityAllowsAiSuggestionChrome()) {
      await refreshMetadataSuggestionList(mediaId);
    }
    return true;
  } catch {
    if (token === metadataRequestToken) {
      metadataWorkspace.loading = false;
      setMetadataStatus("error", "Metadata could not be loaded from the local catalog.");
      updateMetadataControls();
      return true;
    }
    return false;
  }
}

function selectMetadataTag(key) {
  if (metadataWorkspace.current.tagKeys.includes(key)) {
    metadataTagSearchInput.value = "";
    renderMetadataTagSuggestions();
    return;
  }
  if (metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS) {
    setMetadataStatus("validation", "Tag limit reached.");
    updateMetadataControls();
    return;
  }
  metadataWorkspace.current.tagKeys = [...metadataWorkspace.current.tagKeys, key];
  metadataWorkspace.statusOverride = null;
  advanceMetadataWorkspaceRevision();
  metadataTagSearchInput.value = "";
  renderMetadataWorkspace();
}

function removeSelectedMetadataTag(key) {
  metadataWorkspace.current.tagKeys = metadataWorkspace.current.tagKeys.filter((tagKey) => tagKey !== key);
  metadataWorkspace.statusOverride = null;
  advanceMetadataWorkspaceRevision();
  renderMetadataWorkspace();
}

async function handleDiscardMetadataChanges() {
  await closeMetadataWorkspace();
}

function closeMetadataWorkspaceWithContext(discardContext, { reloadCatalog = true } = {}) {
  if (!metadataDiscardContextIsCurrent(discardContext)) return false;
  if (metadataWorkspace.openMediaId === null) return false;
  metadataSaveOwner = null;
  metadataRequestToken += 1;
  metadataAiRequestToken += 1;
  metadataDurableAnalysisToken += 1;
  metadataSuggestionListToken += 1;
  advanceMetadataWorkspaceRevision();
  metadataWorkspace = {
    openMediaId: null,
    openItem: null,
    loading: false,
    saving: false,
    unavailable: false,
    notFound: false,
    statusOverride: null,
    analyzing: false,
    analysisFailureCode: "",
    aiSuggestionApplied: false,
    editMode: "canonical",
    suggestedFilename: "",
    baseline: { displayTitle: null, description: null, tagKeys: [], collectionKey: null, processedAtMs: null },
    current: { displayTitle: "", description: "", tagKeys: [], collectionKey: null, processedAtMs: null },
  };
  resetMetadataDurableAnalysisState();
  resetMetadataSuggestionListState();
  reconcileCatalogSelectedCard();
  metadataWorkspaceElement.hidden = true;
  metadataStatus.textContent = "";
  metadataValidationMessage.textContent = "";
  metadataAiStatus.textContent = "";
  if (metadataAiFilenameNote) {
    metadataAiFilenameNote.textContent = "";
    metadataAiFilenameNote.hidden = true;
  }
  metadataTagSearchInput.value = "";
  metadataTagSuggestionState = { items: [], activeIndex: -1 };
  metadataTagSuggestions.replaceChildren();
  metadataTagSearchInput.setAttribute("aria-expanded", "false");
  if (metadataDialog && typeof metadataDialog.close === "function") {
    metadataDialog.close();
  }
  syncMetadataBeforeUnloadProtection();
  if (metadataOpenerElement) {
    metadataOpenerElement.focus();
    metadataOpenerElement = null;
  }
  if (reloadCatalog) loadCatalog();
  return true;
}

async function closeMetadataWorkspace({ discardContext = null } = {}) {
  const context = discardContext || await confirmDiscardDirtyMetadata({ action: "close" });
  if (!context) return false;
  return closeMetadataWorkspaceWithContext(context);
}

async function createAndSelectMetadataTag(displayName) {
  if (!identityUsesCanonicalMetadataWrite()) {
    setMetadataStatus("validation", "Use an existing tag.");
    updateMetadataControls();
    return;
  }
  const validation = tagDisplayNameError(displayName);
  if (validation) {
    setMetadataStatus("validation", validation);
    updateMetadataControls();
    return;
  }
  const existing = findTagByDisplayName(displayName);
  if (existing) {
    selectMetadataTag(existing.key);
    return;
  }
  const key = uniqueTagKeyForDisplayName(displayName);
  if (!key) {
    setMetadataStatus("validation", "Use at least one English letter or number in the tag name.");
    updateMetadataControls();
    return;
  }
  setMetadataStatus("saving", "Adding tag...");
  updateMetadataControls();
  try {
    const response = await fetch(CANONICAL_TAGS_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ key, display_name: displayName }),
      cache: "no-store",
    });
    const payload = await response.json();
    if (response.ok) {
      await loadCatalogTags();
      selectMetadataTag(payload.tag.key);
      return;
    }
    const code = payload.error ? payload.error.code : "";
    if (code === "CANONICAL_TAG_DEFINITION_CONFLICT") {
      await loadCatalogTags();
      setMetadataStatus("validation", "That tag could not be added. Try a different name.");
      updateMetadataControls();
      return;
    }
    if (code === "CATALOG_UNAVAILABLE") {
      setMetadataStatus("unavailable", "The local catalog is not available. Unsaved edits are preserved.");
      updateMetadataControls();
      return;
    }
    setMetadataStatus("error", "Tag could not be added.");
    updateMetadataControls();
  } catch {
    setMetadataStatus("error", "Tag could not be added.");
    updateMetadataControls();
  }
}

function activateMetadataTagSuggestion(index) {
  const item = metadataTagSuggestionState.items[index];
  if (!item) return;
  if (metadataWorkspace.current.tagKeys.length >= MAX_METADATA_TAGS) {
    setMetadataStatus("validation", "Tag limit reached.");
    updateMetadataControls();
    return;
  }
  if (item.type === "select") {
    selectMetadataTag(item.tag.key);
    return;
  }
  createAndSelectMetadataTag(item.displayName);
}

function handleMetadataTagSearchKeydown(event) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (metadataTagSuggestionState.items.length === 0) {
      renderMetadataTagSuggestions();
      return;
    }
    metadataTagSuggestionState.activeIndex = Math.min(
      metadataTagSuggestionState.activeIndex + 1,
      metadataTagSuggestionState.items.length - 1,
    );
    renderMetadataTagSuggestions();
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (metadataTagSuggestionState.items.length === 0) {
      renderMetadataTagSuggestions();
      return;
    }
    metadataTagSuggestionState.activeIndex = Math.max(metadataTagSuggestionState.activeIndex - 1, 0);
    renderMetadataTagSuggestions();
    return;
  }
  if (event.key === "Escape") {
    if (metadataTagSuggestionState.items.length > 0) {
      event.stopPropagation();
    }
    metadataTagSuggestionState = { items: [], activeIndex: -1 };
    metadataTagSuggestions.replaceChildren();
    metadataTagSearchInput.setAttribute("aria-expanded", "false");
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    const displayName = normalizedTagDisplayName(metadataTagSearchInput.value);
    const exactMatch = displayName ? findTagByDisplayName(displayName) : null;
    if (exactMatch && !metadataWorkspace.current.tagKeys.includes(exactMatch.key)) {
      selectMetadataTag(exactMatch.key);
      return;
    }
    if (metadataTagSuggestionState.activeIndex >= 0) {
      activateMetadataTagSuggestion(metadataTagSuggestionState.activeIndex);
      return;
    }
    if (displayName) {
      if (identityUsesCanonicalMetadataWrite()) {
        createAndSelectMetadataTag(displayName);
      } else {
        setMetadataStatus("validation", "Use an existing tag.");
        updateMetadataControls();
      }
    }
  }
}

async function handleSaveMetadata() {
  const formState = normalizedMetadataFormState();
  if (formState.error || metadataWorkspace.openMediaId === null || metadataWorkspace.saving) {
    updateMetadataControls();
    return;
  }
  const saveOwner = claimMetadataSaveOwner(formState, { closeAfterSave: true });
  metadataWorkspace.unavailable = false;
  metadataWorkspace.notFound = false;
  setMetadataStatus("saving", "Saving...");
  updateMetadataControls();
  try {
    const endpoint = saveOwner.editMode === "alias"
      ? mediaAliasEndpoint(saveOwner.mediaId)
      : metadataEndpoint(saveOwner.mediaId);
    const response = await fetch(endpoint, {
      method: "PUT",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify(saveOwner.requestPayload),
      cache: "no-store",
    });
    const payload = await response.json();
    if (!metadataSaveOwnerIsCurrent(saveOwner)) return;
    if (response.ok) {
      if (saveOwner.editMode === "alias") {
        applyAliasOverlayToWorkspace(payload);
      } else {
        applyMetadataPayloadToWorkspace(payload.metadata);
      }
      metadataWorkspace.aiSuggestionApplied = false;
      metadataWorkspace.suggestedFilename = "";
      const responseRevision = metadataWorkspaceRevision;
      await loadCatalog();
      const closeMetadataWorkspace = () => closeMetadataWorkspaceAfterSave(saveOwner, responseRevision);
      closeMetadataWorkspace();
      return;
    }
    const code = payload.error ? payload.error.code : "";
    if (code === "CANONICAL_TAG_NOT_FOUND" || code === "ALIAS_TAG_NOT_FOUND") {
      await loadCatalogTags();
      if (!metadataSaveOwnerIsCurrent(saveOwner)) return;
      setMetadataStatus("validation", "One selected tag is no longer available. Update the tags before retrying.");
    } else if (code === "MEDIA_NOT_FOUND") {
      metadataWorkspace.notFound = true;
      setMetadataStatus("notFound", "The selected medium is no longer available.");
      await loadCatalog();
    } else if (code === "CATALOG_UNAVAILABLE") {
      metadataWorkspace.unavailable = true;
      setMetadataStatus("unavailable", "The local catalog is not available. Unsaved edits are preserved.");
    } else {
      setMetadataStatus("error", "Save failed.");
    }
  } catch {
    if (metadataSaveOwnerIsCurrent(saveOwner)) {
      setMetadataStatus("error", "Save failed.");
    }
  } finally {
    releaseMetadataSaveOwner(saveOwner);
  }
}

async function loadCatalog() {
  const owner = claimCatalogRequest();
  showCatalogState("loading");
  catalogPrevButton.disabled = true;
  catalogNextButton.disabled = true;
  catalogPageSummary.textContent = "Loading catalog page...";
  try {
    const params = buildCatalogQueryParams(owner);
    const response = await fetch(`${MEDIA_CATALOG_ENDPOINT}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!catalogRequestOwnerIsCurrent(owner)) {
      return;
    }
    if (!response.ok) {
      const code = payload.error ? payload.error.code : "";
      showCatalogState(code === "CATALOG_UNAVAILABLE" ? "unavailable" : "error");
      catalogPageSummary.textContent = "Catalog page unavailable.";
      return;
    }
    renderCatalogSuccess(payload);
  } catch {
    if (catalogRequestOwnerIsCurrent(owner)) {
      showCatalogState("error");
      catalogPageSummary.textContent = "Catalog page unavailable.";
    }
  } finally {
    releaseCatalogRequest(owner);
  }
}

function snapshotAdminCatalogQueryState() {
  return Object.freeze({
    q: adminCatalogState.q,
    publication: adminCatalogState.publication,
    readiness: adminCatalogState.readiness,
    analysis: adminCatalogState.analysis,
    contributor: adminCatalogState.contributor,
    limit: adminCatalogState.limit,
    offset: adminCatalogState.offset,
  });
}

function buildAdminCatalogQueryParams(snapshot = snapshotAdminCatalogQueryState()) {
  const params = new URLSearchParams();
  const trimmed = snapshot.q.trim();
  if (trimmed) params.set("q", trimmed);
  params.set("publication", snapshot.publication);
  params.set("readiness", snapshot.readiness);
  params.set("analysis", snapshot.analysis);
  const contributor = typeof snapshot.contributor === "string" ? snapshot.contributor.trim() : "";
  if (contributor) params.set("contributor", contributor);
  params.set("limit", String(snapshot.limit));
  params.set("offset", String(snapshot.offset));
  return params;
}

function claimAdminCatalogRequest() {
  const owner = Object.freeze({
    ...snapshotAdminCatalogQueryState(),
    token: adminCatalogRequestToken + 1,
  });
  adminCatalogRequestToken = owner.token;
  adminCatalogState.requestOwner = owner;
  return owner;
}

function adminCatalogRequestOwnerIsCurrent(owner) {
  return Boolean(owner)
    && adminCatalogState.requestOwner === owner
    && adminCatalogRequestToken === owner.token
    && adminCatalogState.q === owner.q
    && adminCatalogState.publication === owner.publication
    && adminCatalogState.readiness === owner.readiness
    && adminCatalogState.analysis === owner.analysis
    && adminCatalogState.contributor === owner.contributor
    && adminCatalogState.limit === owner.limit
    && adminCatalogState.offset === owner.offset;
}

function releaseAdminCatalogRequest(owner) {
  if (!adminCatalogRequestOwnerIsCurrent(owner)) return false;
  adminCatalogState.requestOwner = null;
  return true;
}

function setAdminCatalogViewState(state) {
  adminCatalogState.loading = state === "loading";
  adminCatalogState.error = state === "error";
  if (adminMediaLoading) adminMediaLoading.hidden = state !== "loading";
  if (adminMediaEmpty) adminMediaEmpty.hidden = state !== "empty";
  if (adminMediaError) adminMediaError.hidden = state !== "error";
  if (adminMediaResults) {
    adminMediaResults.hidden = state === "loading" || state === "error" || state === "empty";
    adminMediaResults.setAttribute("aria-busy", String(state === "loading"));
  }
}

function setAdminActionStatus(message) {
  if (adminMediaActionStatus) adminMediaActionStatus.textContent = message;
}

function setAdminPendingCleanupReceipt(receiptId) {
  adminCatalogState.pendingCleanupReceiptId = receiptId || null;
  if (!adminCatalogCleanupRetryButton) return;
  const visible = Boolean(receiptId) && identityHasCapability("media.catalog.remove");
  adminCatalogCleanupRetryButton.hidden = !visible;
  adminCatalogCleanupRetryButton.disabled = false;
  adminCatalogCleanupRetryButton.removeAttribute("aria-busy");
  if (receiptId) {
    adminCatalogCleanupRetryButton.dataset.receiptId = receiptId;
  } else {
    delete adminCatalogCleanupRetryButton.dataset.receiptId;
  }
}

function adminMediaTitle(item) {
  const title = typeof item.display_title === "string" ? item.display_title.trim() : "";
  return title || "Untitled media";
}

function adminReadinessLabel(item) {
  return item.publication_ready ? "Ready to publish" : "Incomplete metadata";
}

function adminAnalysisPresentation(state) {
  if (state === "pending") {
    return {
      label: "Analysis queued",
      icon: "◷",
      badgeModifier: "analysis-pending",
      rowModifier: "analysis-pending",
    };
  }
  if (state === "analyzing") {
    return {
      label: "Analysis in progress",
      icon: "↻",
      badgeModifier: "analysis-analyzing",
      rowModifier: "analysis-analyzing",
    };
  }
  if (state === "analyzed") {
    return {
      label: "AI suggestion ready",
      icon: "✦",
      badgeModifier: "analysis-ready",
      rowModifier: "analysis-analyzed",
    };
  }
  if (state === "failed") {
    return {
      label: "Analysis failed",
      icon: "!",
      badgeModifier: "analysis-failed",
      rowModifier: "analysis-failed",
    };
  }
  return {
    label: "Analysis not requested",
    icon: "·",
    badgeModifier: "analysis-not-requested",
    rowModifier: "analysis-not-requested",
  };
}

function adminPublicationLabel(item) {
  return item.content_publication_state === "published" ? "Published" : "Unpublished";
}

function adminMissingFieldsLabel(item) {
  const labels = {
    display_title: "title",
    description: "description",
    tags: "canonical tag",
  };
  const missing = Array.isArray(item.missing_fields)
    ? item.missing_fields.map((field) => labels[field] || field)
    : [];
  return missing.length > 0 ? `Missing: ${missing.join(", ")}` : "";
}

function createAdminStateBadge(text, modifier, icon) {
  const badge = document.createElement("span");
  badge.className = `admin-media-badge admin-media-badge--${modifier}`;
  const symbol = document.createElement("span");
  symbol.className = "admin-media-badge__icon";
  symbol.setAttribute("aria-hidden", "true");
  symbol.textContent = icon;
  const label = document.createElement("span");
  label.textContent = text;
  badge.append(symbol, label);
  return badge;
}

function safeAdminDetailsItem(item) {
  return {
    media_id: item.media_id,
    media_kind: item.media_kind,
    created_at_ms: item.created_at_ms,
    updated_at_ms: item.updated_at_ms,
    display_title: item.display_title,
    description: item.description,
    collection_key: item.collection_key,
    processed_at_ms: item.processed_at_ms,
    processed: item.processed,
    content_category: item.content_category,
    acquisition_source: item.acquisition_source,
    tags: (item.tags || []).map((tag) => ({
      key: tag.key,
      display_name: tag.display_name,
      position: tag.position,
    })),
    locations: (item.locations || []).map((location) => ({
      location_id: location.location_id,
      library_id: location.library_id,
      relative_path: "Local catalog location",
      availability: location.availability,
      observed_size_bytes: location.observed_size_bytes,
      observed_mtime_ns: location.observed_mtime_ns,
    })),
  };
}

function renderAdminThumbnail(item) {
  const wrapper = document.createElement("div");
  wrapper.className = "admin-media-thumbnail";
  const location = selectSupportedAvailableLocation(item);
  if (!location) {
    wrapper.classList.add("admin-media-thumbnail--fallback");
    wrapper.textContent = formatCatalogKind(item.media_kind).slice(0, 1).toUpperCase() || "M";
    wrapper.setAttribute("aria-label", `${formatCatalogKind(item.media_kind)} preview unavailable`);
    return wrapper;
  }
  const image = document.createElement("img");
  image.src = mediaGalleryPreviewUrl(item.media_id, location.location_id);
  image.alt = "";
  image.loading = "lazy";
  image.addEventListener("error", () => {
    wrapper.classList.add("admin-media-thumbnail--fallback");
    wrapper.replaceChildren();
    wrapper.textContent = formatCatalogKind(item.media_kind).slice(0, 1).toUpperCase() || "M";
    wrapper.setAttribute("aria-label", `${formatCatalogKind(item.media_kind)} preview unavailable`);
  }, { once: true });
  wrapper.appendChild(image);
  return wrapper;
}

function findAdminItemAction(mediaId, action) {
  if (!adminMediaResults) return null;
  return [...adminMediaResults.querySelectorAll("button")].find(
    (button) => button.dataset.mediaId === mediaId && button.dataset.adminAction === action,
  ) || null;
}

function publicationOwnerIsCurrent(owner) {
  return Boolean(owner)
    && adminCatalogState.publishOwners.get(owner.mediaId) === owner;
}

function claimPublicationRequest(mediaId, opener) {
  if (adminCatalogState.publishOwners.has(mediaId)) return null;
  const owner = Object.freeze({
    token: adminPublicationRequestToken + 1,
    mediaId,
    opener,
  });
  adminPublicationRequestToken = owner.token;
  adminCatalogState.publishOwners.set(mediaId, owner);
  return owner;
}

function releasePublicationRequest(owner) {
  if (!owner || adminCatalogState.publishOwners.get(owner.mediaId) !== owner) return false;
  adminCatalogState.publishOwners.delete(owner.mediaId);
  return true;
}

function renderAdminMediaItem(item) {
  const row = document.createElement("article");
  const published = item.content_publication_state === "published";
  const analysisPresentation = adminAnalysisPresentation(item.analysis_state);
  row.className = published
    ? "admin-media-row admin-media-row--published"
    : `admin-media-row admin-media-row--unpublished admin-media-row--${analysisPresentation.rowModifier}`;
  row.setAttribute("role", "row");
  row.dataset.mediaId = item.media_id;
  row.dataset.analysisState = item.analysis_state;
  row.dataset.publicationState = published ? "published" : "unpublished";

  const visualCell = document.createElement("div");
  visualCell.className = "admin-media-cell admin-media-cell--visual";
  visualCell.setAttribute("role", "cell");
  visualCell.appendChild(renderAdminThumbnail(item));
  visualCell.appendChild(renderAdminSelectControl(item));

  const summaryCell = document.createElement("div");
  summaryCell.className = "admin-media-cell admin-media-cell--summary";
  summaryCell.setAttribute("role", "cell");
  const title = document.createElement("h2");
  title.className = "admin-media-row__title";
  const titleButton = document.createElement("button");
  titleButton.type = "button";
  titleButton.className = "admin-media-row__title-button";
  titleButton.textContent = adminMediaTitle(item);
  titleButton.setAttribute("aria-label", `Open details for ${adminMediaTitle(item)}`);
  titleButton.addEventListener("click", (event) => {
    event.stopPropagation();
    openDetailsDialog(safeAdminDetailsItem(item), titleButton);
  });
  title.appendChild(titleButton);
  const metadata = document.createElement("p");
  metadata.className = "admin-media-row__metadata";
  metadata.textContent = `${formatCatalogKind(item.media_kind)} · ${summarizeAvailability(item.locations)}`;
  const processed = document.createElement("p");
  processed.className = "admin-media-processed";
  processed.textContent = item.processed ? "✓ Processed" : "Not processed";
  const contributors = Array.isArray(item.contributors) ? item.contributors : [];
  if (contributors.length) {
    const contrib = document.createElement("p");
    contrib.className = "admin-media-contributors";
    contrib.textContent = contributors.map((entry) => {
      const login = typeof entry.login_key === "string" ? entry.login_key : "";
      const sources = Array.isArray(entry.sources) ? entry.sources.join(", ") : "";
      return sources ? `${login} (${sources})` : login;
    }).filter(Boolean).join(" · ");
    summaryCell.append(title, metadata, processed, contrib);
  } else {
    summaryCell.append(title, metadata, processed);
  }

  const readinessCell = document.createElement("div");
  readinessCell.className = "admin-media-cell admin-media-cell--readiness";
  readinessCell.setAttribute("role", "cell");
  const publicationReady = item.publication_ready;
  readinessCell.appendChild(
    createAdminStateBadge(
      adminReadinessLabel(item),
      publicationReady ? "ready" : "incomplete",
      publicationReady ? "✓" : "!",
    ),
  );
  const missing = adminMissingFieldsLabel(item);
  if (missing) {
    const missingText = document.createElement("span");
    missingText.className = "admin-media-cell__detail";
    missingText.textContent = missing;
    readinessCell.appendChild(missingText);
  }

  const analysisCell = document.createElement("div");
  analysisCell.className = "admin-media-cell admin-media-cell--analysis";
  analysisCell.setAttribute("role", "cell");
  analysisCell.appendChild(
    createAdminStateBadge(
      analysisPresentation.label,
      analysisPresentation.badgeModifier,
      analysisPresentation.icon,
    ),
  );

  const publicationCell = document.createElement("div");
  publicationCell.className = "admin-media-cell admin-media-cell--publication";
  publicationCell.setAttribute("role", "cell");
  publicationCell.appendChild(
    createAdminStateBadge(
      adminPublicationLabel(item),
      item.content_publication_state === "published" ? "published" : "neutral",
      item.content_publication_state === "published" ? "✓" : "○",
    ),
  );

  const actionsCell = document.createElement("div");
  actionsCell.className = "admin-media-cell admin-media-cell--actions";
  actionsCell.setAttribute("role", "cell");

  const actionStatus = adminCatalogState.actionStatusByMediaId.get(item.media_id);
  if (item.content_publication_state !== "published") {
    const publishButton = document.createElement("button");
    publishButton.type = "button";
    publishButton.className = "admin-media-action admin-media-action--publish";
    publishButton.textContent = "Publish";
    publishButton.dataset.mediaId = item.media_id;
    publishButton.dataset.adminAction = "publish";
    publishButton.disabled = !item.publication_ready
      || adminCatalogState.publishOwners.has(item.media_id)
      || Boolean(actionStatus)
      || adminBatchDriverActive()
      || Boolean(adminCatalogState.removalOwners.has(item.media_id));
    if (!item.publication_ready) publishButton.title = adminMissingFieldsLabel(item);
    if (actionStatus) publishButton.title = actionStatus.message;
    if (adminCatalogState.publishOwners.has(item.media_id)) {
      publishButton.setAttribute("aria-busy", "true");
      publishButton.textContent = "Publishing…";
    }
    publishButton.addEventListener("click", () => publishAdminMediaItem(item, publishButton));
    actionsCell.appendChild(publishButton);
  } else {
    const unpublishButton = document.createElement("button");
    unpublishButton.type = "button";
    unpublishButton.className = "admin-media-action admin-media-action--unpublish";
    unpublishButton.textContent = "Unpublish";
    unpublishButton.dataset.mediaId = item.media_id;
    unpublishButton.dataset.adminAction = "unpublish";
    unpublishButton.disabled = adminCatalogState.publishOwners.has(item.media_id)
      || Boolean(actionStatus)
      || adminBatchDriverActive()
      || Boolean(adminCatalogState.removalOwners.has(item.media_id));
    if (actionStatus) unpublishButton.title = actionStatus.message;
    if (adminCatalogState.publishOwners.has(item.media_id)) {
      unpublishButton.setAttribute("aria-busy", "true");
      unpublishButton.textContent = "Unpublishing…";
    }
    unpublishButton.addEventListener("click", () => unpublishAdminMediaItem(item, unpublishButton));
    actionsCell.appendChild(unpublishButton);
  }

  if (identityHasCapability("media.catalog.remove")) {
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "admin-media-action admin-media-action--remove";
    removeButton.textContent = adminCatalogState.removalOwners.has(item.media_id)
      ? "Removing…"
      : "Remove from catalog";
    removeButton.dataset.mediaId = item.media_id;
    removeButton.dataset.adminAction = "catalog-remove";
    removeButton.disabled = adminCatalogState.removalOwners.has(item.media_id)
      || adminBatchDriverActive();
    if (adminCatalogState.removalOwners.has(item.media_id)) {
      removeButton.setAttribute("aria-busy", "true");
    }
    removeButton.addEventListener("click", () => removeAdminMediaFromCatalog(item, removeButton));
    actionsCell.appendChild(removeButton);
  }

  if (identityHasCapability("metadata.alias.team.read")) {
    const aliasesButton = document.createElement("button");
    aliasesButton.type = "button";
    aliasesButton.className = "admin-media-action admin-media-action--team-aliases";
    aliasesButton.textContent = "Team aliases";
    aliasesButton.dataset.mediaId = item.media_id;
    aliasesButton.dataset.adminAction = "team-aliases";
    aliasesButton.addEventListener("click", () => {
      loadAdminMediaTeamAliases(item, aliasesButton);
    });
    actionsCell.appendChild(aliasesButton);
  }

  if (actionStatus && actionStatus.retryable) {
    const retryButton = document.createElement("button");
    retryButton.type = "button";
    retryButton.className = "admin-media-action admin-media-action--retry";
    const retryUnpublish = actionStatus.mutation === "unpublish";
    retryButton.textContent = retryUnpublish ? "Retry unpublish" : "Retry publish";
    retryButton.dataset.mediaId = item.media_id;
    retryButton.dataset.adminAction = "retry";
    retryButton.addEventListener("click", () => {
      if (retryUnpublish) unpublishAdminMediaItem(item, retryButton);
      else publishAdminMediaItem(item, retryButton);
    });
    actionsCell.appendChild(retryButton);
  }

  row.append(visualCell, summaryCell, readinessCell, analysisCell, publicationCell, actionsCell);
  return row;
}

function adminMediaTeamAliasesUrl(mediaId) {
  return `${ADMIN_MEDIA_ENDPOINT}/${encodeURIComponent(mediaId)}${ADMIN_MEDIA_ALIASES_PATH}`;
}

function renderAdminTeamAliasEntry(entry) {
  const article = document.createElement("article");
  article.className = "admin-media-aliases-entry";
  const login = document.createElement("h3");
  login.className = "admin-media-aliases-entry__login";
  login.textContent = typeof entry.login_key === "string" ? entry.login_key : "";
  const title = document.createElement("p");
  title.className = "admin-media-aliases-entry__title";
  title.textContent = entry.display_title
    ? `Title: ${entry.display_title}`
    : "Title: (none)";
  const description = document.createElement("p");
  description.className = "admin-media-aliases-entry__description";
  description.textContent = entry.description
    ? `Description: ${entry.description}`
    : "Description: (none)";
  const tags = document.createElement("p");
  tags.className = "admin-media-aliases-entry__tags";
  const tagKeys = Array.isArray(entry.tag_keys) ? entry.tag_keys.filter((key) => typeof key === "string") : [];
  tags.textContent = tagKeys.length ? `Tags: ${tagKeys.join(", ")}` : "Tags: (none)";
  const timestamps = document.createElement("p");
  timestamps.className = "admin-media-aliases-entry__timestamps";
  const created = Number.isInteger(entry.created_at_ms) ? String(entry.created_at_ms) : "unavailable";
  const updated = Number.isInteger(entry.updated_at_ms) ? String(entry.updated_at_ms) : "unavailable";
  timestamps.textContent = `Created ${created} ms · Updated ${updated} ms`;
  article.append(login, title, description, tags, timestamps);
  return article;
}

async function loadAdminMediaTeamAliases(item, opener) {
  if (!identityAllowsTeamAliasRead() || !item || typeof item.media_id !== "string") return;
  if (!adminMediaAliasesPanel) return;
  if (opener) opener.disabled = true;
  adminMediaAliasesPanel.hidden = false;
  if (adminMediaAliasesHeading) {
    adminMediaAliasesHeading.textContent = `Team aliases for ${adminMediaTitle(item)}`;
  }
  if (adminMediaAliasesStatus) {
    adminMediaAliasesStatus.textContent = "Loading team aliases…";
  }
  if (adminMediaAliasesResults) adminMediaAliasesResults.replaceChildren();
  try {
    const response = await fetch(adminMediaTeamAliasesUrl(item.media_id), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      if (adminMediaAliasesStatus) {
        adminMediaAliasesStatus.textContent = response.status === 404
          ? "That medium could not be found."
          : "Team aliases could not be loaded.";
      }
      return;
    }
    const payload = await response.json();
    const entries = Array.isArray(payload.items) ? payload.items : [];
    if (adminMediaAliasesStatus) {
      adminMediaAliasesStatus.textContent = entries.length
        ? `${entries.length} team alias ${entries.length === 1 ? "row" : "rows"}.`
        : "No team aliases for this item.";
    }
    if (adminMediaAliasesResults) {
      adminMediaAliasesResults.replaceChildren(
        ...entries.map((entry) => renderAdminTeamAliasEntry(entry)),
      );
    }
  } catch {
    if (adminMediaAliasesStatus) {
      adminMediaAliasesStatus.textContent = "Team aliases could not be loaded.";
    }
  } finally {
    if (opener) opener.disabled = false;
  }
}

function renderAdminCatalogPage(page) {
  adminCatalogState.items = Array.isArray(page.items) ? page.items : [];
  adminCatalogState.items.forEach((item) => {
    const status = adminCatalogState.actionStatusByMediaId.get(item.media_id);
    if (status && status.kind === "readiness" && item.publication_ready) {
      adminCatalogState.actionStatusByMediaId.delete(item.media_id);
    }
  });
  adminCatalogState.total = Number.isInteger(page.total) ? page.total : 0;
  adminCatalogState.limit = Number.isInteger(page.limit) ? page.limit : ADMIN_MEDIA_PAGE_SIZE;
  adminCatalogState.offset = Number.isInteger(page.offset) ? page.offset : 0;
  reconcileAdminBatchSelection();
  if (adminMediaResults) {
    adminMediaResults.replaceChildren(
      ...adminCatalogState.items.map((item) => renderAdminMediaItem(item)),
    );
  }
  const start = adminCatalogState.total === 0 ? 0 : adminCatalogState.offset + 1;
  const end = Math.min(adminCatalogState.offset + adminCatalogState.items.length, adminCatalogState.total);
  if (adminMediaPageSummary) {
    adminMediaPageSummary.textContent = adminCatalogState.total === 0
      ? "No workflow results."
      : `Showing ${start}–${end} of ${adminCatalogState.total}.`;
  }
  if (adminMediaPrevButton) adminMediaPrevButton.disabled = !page.has_previous;
  if (adminMediaNextButton) adminMediaNextButton.disabled = !page.has_next;
  setAdminCatalogViewState(adminCatalogState.items.length === 0 ? "empty" : "results");
  renderAdminBatchBar();
}

async function loadAdminCatalog({ focusMediaId = null } = {}) {
  if (!identityAllowsAdminWorkflow()) return false;
  const owner = claimAdminCatalogRequest();
  setAdminCatalogViewState("loading");
  if (adminMediaPrevButton) adminMediaPrevButton.disabled = true;
  if (adminMediaNextButton) adminMediaNextButton.disabled = true;
  if (adminMediaPageSummary) adminMediaPageSummary.textContent = "Loading workflow page…";
  try {
    const params = buildAdminCatalogQueryParams(owner);
    const response = await fetch(`${ADMIN_MEDIA_ENDPOINT}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!adminCatalogRequestOwnerIsCurrent(owner)) return false;
    if (!response.ok) {
      setAdminCatalogViewState("error");
      if (adminMediaPageSummary) adminMediaPageSummary.textContent = "Workflow page unavailable.";
      if (response.status === 401 || response.status === 403) {
        setAdminActionStatus("Your current identity is not authorized for this workflow.");
      }
      return false;
    }
    if (
      Array.isArray(payload.items)
      && payload.items.length === 0
      && Number.isInteger(payload.total)
      && payload.total > 0
      && owner.offset >= payload.total
    ) {
      adminCatalogState.offset = Math.floor((payload.total - 1) / owner.limit) * owner.limit;
      return loadAdminCatalog({ focusMediaId });
    }
    renderAdminCatalogPage(payload);
    if (focusMediaId) {
      const stableAction = findAdminItemAction(focusMediaId, "inspect");
      if (stableAction) stableAction.focus();
      else if (adminMediaHeading) adminMediaHeading.focus();
    }
    return true;
  } catch {
    if (adminCatalogRequestOwnerIsCurrent(owner)) {
      setAdminCatalogViewState("error");
      if (adminMediaPageSummary) adminMediaPageSummary.textContent = "Workflow page unavailable.";
    }
    return false;
  } finally {
    releaseAdminCatalogRequest(owner);
  }
}

function publishAdminMediaItem(item, opener) {
  return mutateAdminContentPublication(item, opener, true);
}

function unpublishAdminMediaItem(item, opener) {
  return mutateAdminContentPublication(item, opener, false);
}

async function mutateAdminContentPublication(item, opener, published) {
  const owner = claimPublicationRequest(item.media_id, opener);
  if (!owner) return;
  const mutation = published ? "publish" : "unpublish";
  adminCatalogState.actionStatusByMediaId.delete(item.media_id);
  opener.disabled = true;
  opener.setAttribute("aria-busy", "true");
  opener.textContent = published ? "Publishing…" : "Unpublishing…";
  setAdminActionStatus(
    published
      ? `Publishing ${adminMediaTitle(item)}…`
      : `Unpublishing ${adminMediaTitle(item)}…`,
  );
  try {
    const response = await fetch(
      `${ADMIN_MEDIA_ENDPOINT}/${encodeURIComponent(item.media_id)}/content-publication`,
      {
        method: "PUT",
        headers: framenestMutationHeaders(
          published
            ? { Accept: "application/json" }
            : { Accept: "application/json", "Content-Type": "application/json" },
        ),
        cache: "no-store",
        body: published ? undefined : JSON.stringify({ published: false }),
      },
    );
    const payload = await response.json();
    if (!publicationOwnerIsCurrent(owner)) return;
    if (response.ok) {
      let message = `${adminMediaTitle(item)} published.`;
      if (payload.status === "already_published") {
        message = `${adminMediaTitle(item)} was already published.`;
      } else if (payload.status === "unpublished") {
        message = `${adminMediaTitle(item)} unpublished.`;
      } else if (payload.status === "already_unpublished") {
        message = `${adminMediaTitle(item)} was already unpublished.`;
      }
      setAdminActionStatus(message);
      releasePublicationRequest(owner);
      await loadAdminCatalog({ focusMediaId: item.media_id });
      return;
    }
    const error = payload && payload.error ? payload.error : {};
    if (published && response.status === 409) {
      const orderedMissingFields = Array.isArray(error.missing_fields)
        ? error.missing_fields
        : [];
      const missingFields = orderedMissingFields.length > 0
        ? orderedMissingFields.join(", ")
        : "metadata";
      item.publication_ready = false;
      item.missing_fields = orderedMissingFields;
      adminCatalogState.actionStatusByMediaId.set(item.media_id, {
        kind: "readiness",
        mutation,
        retryable: false,
        message: `Publication is blocked by incomplete metadata: ${missingFields}.`,
      });
      setAdminActionStatus(`Publication is blocked by incomplete metadata: ${missingFields}.`);
    } else if (response.status === 401 || response.status === 403) {
      const message = published
        ? "Your current identity is not authorized to publish this item."
        : "Your current identity is not authorized to unpublish this item.";
      adminCatalogState.actionStatusByMediaId.set(item.media_id, {
        kind: "authorization",
        mutation,
        retryable: false,
        message,
      });
      setAdminActionStatus(message);
    } else {
      const message = published
        ? "Publication failed without changing the durable state."
        : "Unpublication failed without changing the durable state.";
      adminCatalogState.actionStatusByMediaId.set(item.media_id, {
        kind: "transient",
        mutation,
        retryable: response.status >= 500,
        message,
      });
      setAdminActionStatus(message);
    }
  } catch {
    if (!publicationOwnerIsCurrent(owner)) return;
    const message = published
      ? "Publication could not reach the local server."
      : "Unpublication could not reach the local server.";
    adminCatalogState.actionStatusByMediaId.set(item.media_id, {
      kind: "transient",
      mutation,
      retryable: true,
      message,
    });
    setAdminActionStatus(`${message} Retry is available.`);
  } finally {
    if (publicationOwnerIsCurrent(owner)) {
      releasePublicationRequest(owner);
      renderAdminCatalogPage({
        items: adminCatalogState.items,
        total: adminCatalogState.total,
        limit: adminCatalogState.limit,
        offset: adminCatalogState.offset,
        has_previous: adminCatalogState.offset > 0,
        has_next: adminCatalogState.offset + adminCatalogState.limit < adminCatalogState.total,
      });
      const retry = findAdminItemAction(item.media_id, "retry");
      const publish = findAdminItemAction(item.media_id, "publish");
      const unpublish = findAdminItemAction(item.media_id, "unpublish");
      if (retry) retry.focus();
      else if (publish && !publish.disabled) publish.focus();
      else if (unpublish && !unpublish.disabled) unpublish.focus();
      else if (adminMediaHeading) adminMediaHeading.focus();
    }
  }
}

function buildCatalogRemovalConfirmationMessage(preview) {
  const title = preview.display_title || "Untitled media";
  const lines = [
    `Remove “${title}” from the Kronika catalog?`,
    "The original media file remains on disk. This action does not purge originals.",
    `Publication state: ${preview.publication_state}.`,
    `Storage class: ${preview.storage_class}.`,
    "Active Gallery, Details, streaming, download, cover, and preview access for this catalog identity will end.",
  ];
  if (preview.analysis_run_count > 0) {
    lines.push(
      `Analysis history rows removed: ${preview.analysis_run_count}`
        + (
          preview.provider_submission_count > 0
            ? ` (provider submissions recorded: ${preview.provider_submission_count}).`
            : "."
        ),
    );
  }
  if (Array.isArray(preview.provenance_effects) && preview.provenance_effects.length > 0) {
    lines.push(`Provenance: ${preview.provenance_effects.join("; ")}.`);
  }
  if (
    Array.isArray(preview.derived_artifact_cleanup_intent)
    && preview.derived_artifact_cleanup_intent.length > 0
  ) {
    lines.push(
      `Derived cleanup after catalog removal: ${preview.derived_artifact_cleanup_intent.join(", ")}.`,
    );
  }
  return lines.join(" ");
}

async function removeAdminMediaFromCatalog(item, opener) {
  if (!item || !item.media_id) return;
  if (adminCatalogState.removalOwners.has(item.media_id) || adminBatchDriverActive()) return;
  let preview;
  try {
    opener.disabled = true;
    setAdminActionStatus(`Loading removal preview for ${adminMediaTitle(item)}…`);
    const previewResponse = await fetch(
      `${ADMIN_MEDIA_ENDPOINT}/${encodeURIComponent(item.media_id)}/catalog-removal`,
      {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      },
    );
    preview = await previewResponse.json();
    if (!previewResponse.ok) {
      const code = preview && preview.error ? preview.error.code : "";
      setAdminActionStatus(
        code === "CAPABILITY_DENIED"
          ? "Your current identity is not authorized to remove catalog media."
          : "Catalog removal preview is unavailable.",
      );
      return;
    }
  } catch {
    setAdminActionStatus("Catalog removal preview could not reach the local server.");
    return;
  } finally {
    opener.disabled = false;
    opener.removeAttribute("aria-busy");
  }

  const confirmed = await requestConfirmation({
    title: "Remove from catalog?",
    message: buildCatalogRemovalConfirmationMessage(preview),
    confirmLabel: "Remove from catalog",
    dismissLabel: "Cancel",
    destructive: true,
    focusReturn: opener,
  });
  if (!confirmed) {
    setAdminActionStatus("");
    opener.focus();
    return;
  }

  adminCatalogState.removalOwners.set(item.media_id, opener);
  opener.disabled = true;
  opener.setAttribute("aria-busy", "true");
  opener.textContent = "Removing…";
  setAdminActionStatus(`Removing ${adminMediaTitle(item)} from catalog…`);
  try {
    const response = await fetch(
      `${ADMIN_MEDIA_ENDPOINT}/${encodeURIComponent(item.media_id)}/catalog-removal`,
      {
        method: "POST",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/json",
        }),
        cache: "no-store",
        body: JSON.stringify({
          acknowledge_consequences: true,
          consequence_fingerprint: preview.consequence_fingerprint,
        }),
      },
    );
    const payload = await response.json();
    if (response.status === 409) {
      setAdminActionStatus(
        "Catalog state changed. Review the refreshed consequences and confirm again.",
      );
      adminCatalogState.removalOwners.delete(item.media_id);
      await removeAdminMediaFromCatalog(item, opener);
      return;
    }
    if (!response.ok) {
      setAdminActionStatus(
        response.status === 403
          ? "Your current identity is not authorized to remove catalog media."
          : "Catalog removal failed without changing the durable catalog state.",
      );
      return;
    }
    const receipt = payload.receipt || {};
    adminBatchState.selectedMediaIds.delete(item.media_id);
    if (receipt.cleanup_retry_available) {
      setAdminPendingCleanupReceipt(receipt.receipt_id);
      setAdminActionStatus(
        `${adminMediaTitle(item)} removed from catalog. Derived cleanup is pending; retry is available.`,
      );
    } else {
      setAdminPendingCleanupReceipt(null);
      setAdminActionStatus(
        `${adminMediaTitle(item)} removed from catalog. Original media files remain on disk.`,
      );
    }
    await loadAdminCatalog({
      focusMediaId: adminCatalogState.items.find((candidate) => (
        candidate.media_id !== item.media_id
      ))?.media_id,
    });
    if (typeof loadCatalog === "function") {
      await loadCatalog();
    }
  } catch {
    setAdminActionStatus("Catalog removal could not reach the local server.");
  } finally {
    adminCatalogState.removalOwners.delete(item.media_id);
    if (opener.isConnected) {
      opener.disabled = false;
      opener.removeAttribute("aria-busy");
      opener.textContent = "Remove from catalog";
    }
  }
}

async function retryAdminCatalogRemovalCleanup(receiptId, opener) {
  if (!receiptId) return;
  opener.disabled = true;
  opener.setAttribute("aria-busy", "true");
  setAdminActionStatus("Retrying derived-artifact cleanup…");
  try {
    const response = await fetch(
      `/api/admin/catalog-removal-receipts/${encodeURIComponent(receiptId)}/cleanup-retry`,
      {
        method: "POST",
        headers: framenestMutationHeaders({ Accept: "application/json" }),
        cache: "no-store",
      },
    );
    const payload = await response.json();
    if (!response.ok) {
      setAdminActionStatus("Derived-artifact cleanup retry failed.");
      return;
    }
    const receipt = payload.receipt || {};
    if (receipt.cleanup_retry_available) {
      setAdminPendingCleanupReceipt(receipt.receipt_id || receiptId);
      setAdminActionStatus("Derived-artifact cleanup is still pending.");
    } else {
      setAdminPendingCleanupReceipt(null);
      setAdminActionStatus("Derived-artifact cleanup completed.");
      await loadAdminCatalog();
    }
  } catch {
    setAdminActionStatus("Derived-artifact cleanup retry could not reach the local server.");
  } finally {
    opener.disabled = false;
    opener.removeAttribute("aria-busy");
  }
}

function adminBatchDriverActive() {
  return Boolean(adminBatchState.driver)
    && adminBatchState.driver.lifecycle !== "done"
    && adminBatchState.driver.lifecycle !== "stopped";
}

function adminPageItemById(mediaId) {
  return adminCatalogState.items.find((item) => item.media_id === mediaId) || null;
}

function adminPageSelectedIds() {
  return adminCatalogState.items
    .filter((item) => adminBatchState.selectedMediaIds.has(item.media_id))
    .map((item) => item.media_id);
}

function reconcileAdminBatchSelection() {
  if (adminBatchDriverActive()) return;
  const pageIds = new Set(adminCatalogState.items.map((item) => item.media_id));
  [...adminBatchState.selectedMediaIds].forEach((mediaId) => {
    if (!pageIds.has(mediaId)) adminBatchState.selectedMediaIds.delete(mediaId);
  });
}

function resetAdminBatchForQueryChange() {
  if (adminBatchDriverActive()) return false;
  adminBatchState.selectedMediaIds.clear();
  adminBatchState.driver = null;
  renderAdminBatchBar();
  return true;
}

function clearAdminBatchSelection() {
  if (adminBatchDriverActive()) return false;
  adminBatchState.selectedMediaIds.clear();
  if (adminBatchState.driver) adminBatchState.driver = null;
  renderAdminBatchBar();
  return true;
}

function setAdminItemSelection(mediaId, selected, control) {
  if (adminBatchDriverActive() || !adminPageItemById(mediaId)) {
    if (control) control.checked = adminBatchState.selectedMediaIds.has(mediaId);
    return false;
  }
  if (selected) adminBatchState.selectedMediaIds.add(mediaId);
  else adminBatchState.selectedMediaIds.delete(mediaId);
  renderAdminBatchBar();
  return true;
}

function setAdminPageSelection(selected) {
  if (adminBatchDriverActive()) return false;
  adminCatalogState.items.forEach((item) => {
    if (selected) adminBatchState.selectedMediaIds.add(item.media_id);
    else adminBatchState.selectedMediaIds.delete(item.media_id);
  });
  renderAdminBatchBar();
  return true;
}

function adminAnalysisEligibility() {
  const eligible = [];
  const ineligible = [];
  adminCatalogState.items.forEach((item) => {
    if (!adminBatchState.selectedMediaIds.has(item.media_id)) return;
    if (item.analysis_state === "not_requested") eligible.push(item.media_id);
    else ineligible.push(item.media_id);
  });
  return { eligible, ineligible };
}

function renderAdminSelectControl(item) {
  const label = document.createElement("label");
  label.className = "admin-media-select";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "admin-media-select__input";
  checkbox.dataset.mediaId = item.media_id;
  checkbox.checked = adminBatchState.selectedMediaIds.has(item.media_id);
  checkbox.disabled = adminBatchDriverActive();
  checkbox.setAttribute("aria-label", `Select ${adminMediaTitle(item)}`);
  checkbox.addEventListener("change", () => {
    setAdminItemSelection(item.media_id, checkbox.checked, checkbox);
  });
  label.appendChild(checkbox);
  return label;
}

const ADMIN_BATCH_OUTCOME_LABELS = {
  pending: "Waiting",
  publishing: "Publishing…",
  published: "Published",
  already_published: "Already published",
  not_ready: "Not ready",
  failed: "Failed",
  not_started_due_to_stop: "Not started (stopped)",
  ineligible: "Skipped",
  location_unavailable: "No available location",
  queueing: "Queueing…",
  queued: "Queued",
  analyzing: "Analyzing",
  analyzed: "Analyzed",
  provider_unavailable: "Provider unavailable",
  status_unavailable: "Status unavailable",
  not_started_provider_halt: "Not queued (provider unavailable)",
};

function adminBatchOutcomeLabel(record) {
  return ADMIN_BATCH_OUTCOME_LABELS[record.status] || record.status;
}

function adminBatchProgressText(driver) {
  const counts = {};
  driver.items.forEach((record) => {
    counts[record.status] = (counts[record.status] || 0) + 1;
  });
  if (driver.lifecycle === "running" || driver.lifecycle === "stopping") {
    const activeStatuses = ["publishing", "queueing", "queued", "analyzing"];
    const currentIndex = driver.items.findIndex(
      (record) => activeStatuses.includes(record.status) || record.status === "pending",
    );
    const position = currentIndex === -1 ? driver.items.length : currentIndex + 1;
    const verb = driver.type === "publish" ? "Publishing" : "Queueing first analysis";
    const base = `${verb} item ${position} of ${driver.items.length}…`;
    return driver.lifecycle === "stopping"
      ? `${base} Stopping after the current item…`
      : base;
  }
  const parts = [];
  if (driver.type === "publish") {
    if (counts.published) parts.push(`${counts.published} published`);
    if (counts.already_published) parts.push(`${counts.already_published} already published`);
    if (counts.not_ready) parts.push(`${counts.not_ready} not ready`);
    if (counts.failed) parts.push(`${counts.failed} failed`);
  } else {
    if (counts.analyzed) parts.push(`${counts.analyzed} analyzed`);
    if (counts.failed) parts.push(`${counts.failed} failed`);
    if (counts.provider_unavailable) parts.push("provider unavailable");
    if (counts.location_unavailable) parts.push(`${counts.location_unavailable} without an available location`);
    if (counts.ineligible) parts.push(`${counts.ineligible} skipped (not eligible)`);
    if (counts.status_unavailable) parts.push(`${counts.status_unavailable} with unavailable status`);
    if (counts.not_started_provider_halt) {
      parts.push(`${counts.not_started_provider_halt} not queued (provider unavailable)`);
    }
    const stillQueued = (counts.queueing || 0) + (counts.queued || 0) + (counts.analyzing || 0);
    if (driver.lifecycle === "stopped" && stillQueued > 0) {
      parts.push(`${stillQueued} still queued or analyzing`);
    }
  }
  if (counts.not_started_due_to_stop) {
    parts.push(`${counts.not_started_due_to_stop} not started`);
  }
  const summary = parts.length > 0 ? parts.join(", ") : "no items processed";
  if (driver.lifecycle === "stopped") {
    const suffix = driver.type === "publish"
      ? "Completed changes remain applied."
      : "Already queued analysis continues under the server lifecycle.";
    return `Stopped: ${summary}. ${suffix}`;
  }
  if (driver.type === "analysis" && driver.providerUnavailable) {
    return `Finished: ${summary}. The AI provider is unavailable, so remaining eligible items were not queued.`;
  }
  return `Finished: ${summary}.`;
}

function renderAdminBatchOutcomes(driver) {
  if (adminBatchProgress) {
    const showProgress = Boolean(driver) && driver.lifecycle !== "confirming";
    adminBatchProgress.hidden = !showProgress;
    adminBatchProgress.textContent = showProgress ? adminBatchProgressText(driver) : "";
  }
  if (!adminBatchOutcomes) return;
  if (!driver) {
    adminBatchOutcomes.hidden = true;
    adminBatchOutcomes.replaceChildren();
    return;
  }
  adminBatchOutcomes.hidden = false;
  adminBatchOutcomes.replaceChildren(
    ...driver.items.map((record) => {
      const entry = document.createElement("li");
      entry.className = `admin-batch__outcome admin-batch__outcome--${record.status}`;
      const title = document.createElement("span");
      title.className = "admin-batch__outcome-title";
      title.textContent = record.title;
      const state = document.createElement("span");
      state.className = "admin-batch__outcome-state";
      state.textContent = record.message
        ? `${adminBatchOutcomeLabel(record)} — ${record.message}`
        : adminBatchOutcomeLabel(record);
      entry.append(title, state);
      return entry;
    }),
  );
}

function syncAdminSelectControls() {
  if (!adminMediaResults) return;
  const active = adminBatchDriverActive();
  adminMediaResults.querySelectorAll(".admin-media-select__input").forEach((checkbox) => {
    checkbox.checked = adminBatchState.selectedMediaIds.has(checkbox.dataset.mediaId);
    checkbox.disabled = active;
  });
}

function renderAdminBatchBar() {
  if (!adminBatchBar) return;
  const driver = adminBatchState.driver;
  const active = adminBatchDriverActive();
  const pageIds = adminCatalogState.items.map((item) => item.media_id);
  const selectedCount = adminBatchState.selectedMediaIds.size;
  adminBatchBar.hidden = pageIds.length === 0 && !driver;
  if (adminBatchSelectAll) {
    const selectedOnPage = pageIds.filter(
      (mediaId) => adminBatchState.selectedMediaIds.has(mediaId),
    ).length;
    adminBatchSelectAll.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
    adminBatchSelectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
    adminBatchSelectAll.disabled = active || pageIds.length === 0;
  }
  if (adminBatchSelectionCount) {
    adminBatchSelectionCount.textContent = `${selectedCount} selected`;
  }
  const eligibility = adminAnalysisEligibility();
  if (adminBatchPublishButton) {
    adminBatchPublishButton.disabled = active || selectedCount === 0;
  }
  if (adminBatchAnalyzeButton) {
    adminBatchAnalyzeButton.disabled = active
      || eligibility.eligible.length === 0
      || eligibility.eligible.length > ADMIN_ANALYSIS_BATCH_MAX_ITEMS;
  }
  if (adminBatchClearButton) {
    adminBatchClearButton.disabled = active || (selectedCount === 0 && !driver);
  }
  if (adminBatchHint) {
    let hint = "";
    if (selectedCount > 0 && eligibility.eligible.length === 0) {
      hint = "No selected item is eligible for first analysis; every selected item already has a requested or finished analysis.";
    } else if (eligibility.eligible.length > ADMIN_ANALYSIS_BATCH_MAX_ITEMS) {
      hint = `${eligibility.eligible.length} selected items are eligible for first analysis. Narrow the selection to at most ${ADMIN_ANALYSIS_BATCH_MAX_ITEMS} eligible items.`;
    }
    adminBatchHint.hidden = hint === "";
    adminBatchHint.textContent = hint;
  }
  if (adminBatchStopButton) {
    const running = driver && driver.lifecycle === "running";
    const stopping = driver && driver.lifecycle === "stopping";
    adminBatchStopButton.hidden = !running && !stopping;
    adminBatchStopButton.disabled = !running;
    adminBatchStopButton.textContent = stopping ? "Stopping…" : "Stop";
  }
  renderAdminBatchOutcomes(driver);
  syncAdminSelectControls();
}

function setAdminBatchInteractionLock(locked) {
  [
    adminMediaSearch,
    adminMediaPublicationFilter,
    adminMediaReadinessFilter,
    adminMediaAnalysisFilter,
    adminMediaContributorFilter,
    adminMediaRefreshButton,
    adminMediaPrevButton,
    adminMediaNextButton,
    adminMediaCloseButton,
  ].forEach((control) => {
    if (control) control.disabled = locked;
  });
  if (adminMediaResults) {
    adminMediaResults.querySelectorAll(".admin-media-select__input").forEach((checkbox) => {
      checkbox.disabled = locked;
    });
    adminMediaResults.querySelectorAll("button[data-admin-action]").forEach((button) => {
      button.disabled = locked || button.disabled;
    });
  }
}

function createAdminBatchDriver(type, records) {
  return {
    type,
    lifecycle: "confirming",
    stopRequested: false,
    providerUnavailable: false,
    items: records,
  };
}

function adminBatchRecordForItem(item) {
  return {
    mediaId: item.media_id,
    title: adminMediaTitle(item),
    status: "pending",
    message: "",
  };
}

function cancelAdminBatchConfirmation(driver, opener) {
  const cleared = adminBatchState.driver === driver;
  if (cleared) adminBatchState.driver = null;
  renderAdminBatchBar();
  if (
    cleared
    && opener
    && typeof opener.focus === "function"
    && document.contains(opener)
    && !opener.disabled
    && !opener.hidden
  ) {
    opener.focus();
  }
}

async function startAdminPublishBatch(opener) {
  if (adminBatchDriverActive() || !identityAllowsAdminWorkflow()) return;
  const ids = adminPageSelectedIds();
  if (ids.length === 0) return;
  const driver = createAdminBatchDriver(
    "publish",
    ids.map((mediaId) => adminBatchRecordForItem(adminPageItemById(mediaId))),
  );
  adminBatchState.driver = driver;
  renderAdminBatchBar();
  const accepted = await requestConfirmation({
    title: `Publish ${ids.length} selected ${ids.length === 1 ? "item" : "items"}?`,
    message: "Items are published one at a time in the current page order. Already published items keep their existing state and report it. Items blocked by incomplete metadata report the server readiness reason. You can stop after the current item; completed changes remain applied.",
    dismissLabel: "Cancel",
    confirmLabel: "Publish selected",
    destructive: false,
    focusReturn: opener,
  });
  if (!accepted || adminBatchState.driver !== driver) {
    cancelAdminBatchConfirmation(driver, opener);
    return;
  }
  driver.lifecycle = "running";
  setAdminBatchInteractionLock(true);
  renderAdminBatchBar();
  if (adminBatchStopButton) adminBatchStopButton.focus();
  await runAdminPublishBatch(driver);
}

async function runAdminPublishBatch(driver) {
  for (let index = 0; index < driver.items.length; index += 1) {
    const record = driver.items[index];
    if (driver.stopRequested || adminBatchTeardown || adminBatchState.driver !== driver) {
      markAdminBatchItemsNotStarted(driver, index);
      break;
    }
    record.status = "publishing";
    renderAdminBatchOutcomes(driver);
    await executeAdminPublishBatchItem(record);
    renderAdminBatchOutcomes(driver);
  }
  await finalizeAdminBatch(driver, driver.stopRequested ? "stopped" : "done");
}

async function executeAdminPublishBatchItem(record) {
  try {
    const response = await fetch(
      `${ADMIN_MEDIA_ENDPOINT}/${encodeURIComponent(record.mediaId)}/content-publication`,
      {
        method: "PUT",
        headers: framenestMutationHeaders({ Accept: "application/json" }),
        cache: "no-store",
      },
    );
    const payload = await response.json();
    if (response.ok) {
      if (payload && payload.status === "already_published") {
        record.status = "already_published";
        record.message = "The server already lists this item as published.";
      } else {
        record.status = "published";
        record.message = "Published by the server.";
      }
      return;
    }
    const error = payload && payload.error ? payload.error : {};
    if (response.status === 409) {
      const missingFields = adminMissingFieldsLabel({ missing_fields: error.missing_fields });
      record.status = "not_ready";
      record.message = missingFields || "Publication is blocked by incomplete metadata.";
    } else if (response.status === 401 || response.status === 403) {
      record.status = "failed";
      record.message = "The current identity is not authorized to publish this item.";
    } else {
      record.status = "failed";
      record.message = "Publication failed without changing the durable state.";
    }
  } catch {
    record.status = "failed";
    record.message = "Publication could not reach the local server.";
  }
}

async function startAdminAnalysisBatch(opener) {
  if (adminBatchDriverActive() || !identityAllowsAdminWorkflow()) return;
  const eligibility = adminAnalysisEligibility();
  if (
    eligibility.eligible.length === 0
    || eligibility.eligible.length > ADMIN_ANALYSIS_BATCH_MAX_ITEMS
  ) {
    return;
  }
  const eligibleIds = new Set(eligibility.eligible);
  const records = adminPageSelectedIds().map((mediaId) => {
    const item = adminPageItemById(mediaId);
    const record = adminBatchRecordForItem(item);
    if (!eligibleIds.has(mediaId)) {
      record.status = "ineligible";
      record.message = `First analysis is only available before any analysis request; current state is "${adminAnalysisPresentation(item.analysis_state).label}".`;
    }
    return record;
  });
  const driver = createAdminBatchDriver("analysis", records);
  adminBatchState.driver = driver;
  renderAdminBatchBar();
  const skippedNote = eligibility.ineligible.length > 0
    ? ` ${eligibility.ineligible.length} selected ${eligibility.ineligible.length === 1 ? "item is" : "items are"} not eligible and will be skipped.`
    : "";
  const accepted = await requestConfirmation({
    title: `Queue first analysis for ${eligibility.eligible.length} eligible ${eligibility.eligible.length === 1 ? "item" : "items"}?`,
    message: `Eligible items are queued one at a time in the current page order.${skippedNote} Each eligible item may create one durable analysis run. Provider retries, when required, remain governed and recorded by the existing server policy. You can stop after the current item; already queued analysis continues under the server lifecycle.`,
    dismissLabel: "Cancel",
    confirmLabel: "Analyze selected",
    destructive: false,
    focusReturn: opener,
  });
  if (!accepted || adminBatchState.driver !== driver) {
    cancelAdminBatchConfirmation(driver, opener);
    return;
  }
  driver.lifecycle = "running";
  setAdminBatchInteractionLock(true);
  renderAdminBatchBar();
  if (adminBatchStopButton) adminBatchStopButton.focus();
  await runAdminAnalysisBatch(driver);
}

async function runAdminAnalysisBatch(driver) {
  for (let index = 0; index < driver.items.length; index += 1) {
    const record = driver.items[index];
    if (record.status === "ineligible") continue;
    if (driver.stopRequested || adminBatchTeardown || adminBatchState.driver !== driver) {
      markAdminBatchItemsNotStarted(driver, index);
      break;
    }
    const item = adminPageItemById(record.mediaId);
    const location = item ? selectSupportedAvailableLocation(item) : null;
    if (!location) {
      record.status = "location_unavailable";
      record.message = "No supported and available location exists for this item.";
      renderAdminBatchOutcomes(driver);
      continue;
    }
    record.status = "queueing";
    renderAdminBatchOutcomes(driver);
    const enqueue = await executeAdminAnalysisEnqueue(record, location.location_id);
    renderAdminBatchOutcomes(driver);
    if (enqueue === "halt") {
      driver.providerUnavailable = true;
      markAdminBatchItemsNotStarted(
        driver,
        index + 1,
        "not_started_provider_halt",
        "Not queued; the AI provider is unavailable.",
      );
      break;
    }
    if (enqueue === "failed") continue;
    if (AUTOMATIC_ANALYSIS_TERMINAL_STATES.has(record.serverState)) {
      applyAdminAnalysisTerminalState(record, record.serverPayload);
      renderAdminBatchOutcomes(driver);
      continue;
    }
    await pollAdminAnalysisBatchItem(driver, record);
    renderAdminBatchOutcomes(driver);
  }
  await finalizeAdminBatch(driver, driver.stopRequested ? "stopped" : "done");
}

async function executeAdminAnalysisEnqueue(record, locationId) {
  try {
    const response = await fetch(durableAnalysisEndpoint(record.mediaId, locationId), {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ confirm_cloud_upload: true }),
      cache: "no-store",
    });
    const payload = await response.json();
    if (response.ok) {
      record.serverState = payload && payload.state ? String(payload.state) : "";
      record.serverPayload = payload || null;
      record.status = record.serverState === "analyzing" ? "analyzing" : "queued";
      record.message = "Analysis request accepted by the server.";
      return "accepted";
    }
    const code = payload && payload.error ? payload.error.code : "";
    if (code === "AI_PROVIDER_NOT_CONFIGURED" || code === "AI_PROVIDER_UNAVAILABLE") {
      record.status = "provider_unavailable";
      record.message = "The AI provider is unavailable; no further items were queued.";
      return "halt";
    }
    record.status = "failed";
    if (code === "MEDIA_NOT_FOUND") {
      record.message = "Media was not found.";
    } else if (code === "CLOUD_CONFIRMATION_REQUIRED") {
      record.message = "The server rejected the request without the required confirmation.";
    } else {
      record.message = "The analysis request was not accepted; no run was created by this item.";
    }
    return "failed";
  } catch {
    record.status = "failed";
    record.message = "The analysis request could not reach the local server.";
    return "failed";
  }
}

async function pollAdminAnalysisBatchItem(driver, record) {
  let attempts = 0;
  while (attempts < AUTOMATIC_ANALYSIS_POLL_MAX_ATTEMPTS) {
    if (driver.stopRequested || adminBatchTeardown || adminBatchState.driver !== driver) return;
    attempts += 1;
    let payload;
    try {
      const response = await fetch(automaticAnalysisEndpoint(record.mediaId), {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        record.status = "status_unavailable";
        record.message = "Analysis status is unavailable; any queued run continues under the server lifecycle.";
        return;
      }
      payload = await response.json();
    } catch {
      record.status = "status_unavailable";
      record.message = "Analysis status is unavailable; any queued run continues under the server lifecycle.";
      return;
    }
    const state = payload && payload.state ? String(payload.state) : "";
    if (state === "pending" || state === "analyzing") {
      const nextStatus = state === "analyzing" ? "analyzing" : "queued";
      if (record.status !== nextStatus) {
        record.status = nextStatus;
        record.message = automaticAnalysisStatusMessage(payload);
        renderAdminBatchOutcomes(driver);
      }
    } else if (AUTOMATIC_ANALYSIS_TERMINAL_STATES.has(state)) {
      applyAdminAnalysisTerminalState(record, payload);
      return;
    } else {
      record.status = "status_unavailable";
      record.message = "Analysis status is incomplete; any queued run continues under the server lifecycle.";
      return;
    }
    await new Promise((resolve) => {
      window.setTimeout(resolve, AUTOMATIC_ANALYSIS_POLL_INTERVAL_MS);
    });
  }
  record.status = "status_unavailable";
  record.message = "Analysis did not reach a terminal state within the polling window; the server run continues under its own lifecycle.";
}

function applyAdminAnalysisTerminalState(record, payload) {
  const state = payload && payload.state ? String(payload.state) : "";
  if (state === "analyzed") {
    record.status = "analyzed";
    record.message = automaticAnalysisStatusMessage(payload);
  } else if (state === "failed") {
    record.status = "failed";
    record.message = automaticAnalysisStatusMessage(payload) || "AI analysis failed.";
  } else {
    record.status = "status_unavailable";
    record.message = "Analysis returned an unexpected state; any queued run continues under the server lifecycle.";
  }
}

function markAdminBatchItemsNotStarted(
  driver,
  fromIndex,
  status = "not_started_due_to_stop",
  message = "Not started; the batch was stopped before this item.",
) {
  for (let index = fromIndex; index < driver.items.length; index += 1) {
    const record = driver.items[index];
    if (record.status !== "pending") continue;
    record.status = status;
    record.message = message;
  }
}

function requestAdminBatchStop() {
  const driver = adminBatchState.driver;
  if (!driver || driver.lifecycle !== "running") return;
  driver.stopRequested = true;
  driver.lifecycle = "stopping";
  renderAdminBatchBar();
}

async function finalizeAdminBatch(driver, lifecycle) {
  driver.lifecycle = lifecycle;
  if (adminBatchState.driver !== driver) return;
  adminBatchState.selectedMediaIds.clear();
  setAdminBatchInteractionLock(false);
  renderAdminBatchBar();
  await loadAdminCatalog();
  if (adminBatchClearButton && !adminBatchClearButton.disabled) {
    adminBatchClearButton.focus();
  }
}

function invalidateAdminBatchOnTeardown() {
  adminBatchTeardown = true;
  if (adminBatchState.driver) adminBatchState.driver.stopRequested = true;
}

function openAdminMediaBrowser() {
  if (!identityAllowsAdminWorkflow() || !adminMediaBrowser) return;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (catalogBrowser) catalogBrowser.hidden = true;
  if (headerSearch) headerSearch.hidden = true;
  if (workspaceMediaBrowser) workspaceMediaBrowser.hidden = true;
  if (typeof analysisProposalsBrowser !== "undefined" && analysisProposalsBrowser) {
    analysisProposalsBrowser.hidden = true;
  }
  adminMediaBrowser.hidden = false;
  adminCatalogState.offset = 0;
  resetAdminBatchForQueryChange();
  setAdminActionStatus("");
  if (adminMediaAliasesPanel) adminMediaAliasesPanel.hidden = true;
  if (adminMediaAliasesResults) adminMediaAliasesResults.replaceChildren();
  if (adminMediaAliasesStatus) adminMediaAliasesStatus.textContent = "";
  loadAdminCatalog();
  if (adminMediaHeading) adminMediaHeading.focus();
}

function closeAdminMediaBrowser() {
  adminCatalogRequestToken += 1;
  adminCatalogState.requestOwner = null;
  if (adminMediaBrowser) adminMediaBrowser.hidden = true;
  if (adminMediaAliasesPanel) adminMediaAliasesPanel.hidden = true;
  if (adminMediaAliasesResults) adminMediaAliasesResults.replaceChildren();
  if (adminMediaAliasesStatus) adminMediaAliasesStatus.textContent = "";
  if (catalogBrowser) catalogBrowser.hidden = false;
  if (headerSearch) headerSearch.hidden = false;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (typeof kronikaMarkGalleryCurrent === "function") kronikaMarkGalleryCurrent();
  if (adminMediaOpenButton && !adminMediaOpenButton.hidden) adminMediaOpenButton.focus();
}

function identityAllowsWorkspaceSurface() {
  return typeof identityAllowsWorkspaceMedia === "function" && identityAllowsWorkspaceMedia();
}

function snapshotWorkspaceMediaQueryState() {
  return Object.freeze({
    limit: workspaceMediaState.limit,
    offset: workspaceMediaState.offset,
  });
}

function buildWorkspaceMediaQueryParams(snapshot = snapshotWorkspaceMediaQueryState()) {
  const params = new URLSearchParams();
  params.set("limit", String(snapshot.limit));
  params.set("offset", String(snapshot.offset));
  return params;
}

function claimWorkspaceMediaRequest() {
  const owner = Object.freeze({
    ...snapshotWorkspaceMediaQueryState(),
    token: workspaceMediaRequestToken + 1,
  });
  workspaceMediaRequestToken = owner.token;
  workspaceMediaState.requestOwner = owner;
  return owner;
}

function workspaceMediaRequestOwnerIsCurrent(owner) {
  return Boolean(owner)
    && workspaceMediaState.requestOwner === owner
    && workspaceMediaRequestToken === owner.token
    && workspaceMediaState.limit === owner.limit
    && workspaceMediaState.offset === owner.offset;
}

function setWorkspaceMediaViewState(state) {
  workspaceMediaState.loading = state === "loading";
  workspaceMediaState.error = state === "error";
  if (workspaceMediaLoading) workspaceMediaLoading.hidden = state !== "loading";
  if (workspaceMediaEmpty) workspaceMediaEmpty.hidden = state !== "empty";
  if (workspaceMediaError) workspaceMediaError.hidden = state !== "error";
  if (workspaceMediaResults) {
    workspaceMediaResults.hidden = state === "loading" || state === "error" || state === "empty";
    workspaceMediaResults.setAttribute("aria-busy", String(state === "loading"));
  }
}

function workspaceContributionLabel(sources) {
  const labels = {
    upload: "Upload",
    youtube: "YouTube",
    x: "X",
  };
  return (Array.isArray(sources) ? sources : [])
    .map((source) => labels[source] || source)
    .join(" · ");
}

function renderWorkspaceMediaItem(item) {
  const row = document.createElement("article");
  const published = item.content_publication_state === "published";
  row.className = published
    ? "admin-media-row admin-media-row--published"
    : "admin-media-row admin-media-row--unpublished";
  row.setAttribute("role", "row");
  row.dataset.mediaId = item.media_id;
  const titleCell = document.createElement("div");
  titleCell.className = "admin-media-cell admin-media-cell--summary";
  titleCell.setAttribute("role", "cell");
  const titleButton = document.createElement("button");
  titleButton.type = "button";
  titleButton.className = "admin-media-row__title-button";
  const title = typeof item.display_title === "string" && item.display_title.trim()
    ? item.display_title.trim()
    : "Untitled media";
  titleButton.textContent = title;
  titleButton.addEventListener("click", () => {
    openWorkspaceMediaDetails(item, titleButton);
  });
  const meta = document.createElement("p");
  meta.className = "admin-media-row__metadata";
  const sources = workspaceContributionLabel(item.contribution_sources);
  meta.textContent = sources
    ? `${formatCatalogKind(item.media_kind)} · ${sources}`
    : formatCatalogKind(item.media_kind);
  titleCell.append(titleButton, meta);
  const readinessCell = document.createElement("div");
  readinessCell.className = "admin-media-cell admin-media-cell--readiness";
  readinessCell.setAttribute("role", "cell");
  readinessCell.appendChild(
    createAdminStateBadge(
      item.publication_ready ? "Ready to publish" : "Incomplete metadata",
      item.publication_ready ? "ready" : "incomplete",
      item.publication_ready ? "✓" : "!",
    ),
  );
  const missing = Array.isArray(item.missing_fields) ? item.missing_fields.filter(Boolean) : [];
  if (missing.length) {
    const missingText = document.createElement("span");
    missingText.className = "admin-media-cell__detail";
    missingText.textContent = `Missing: ${missing.join(", ")}`;
    readinessCell.appendChild(missingText);
  }
  const publicationCell = document.createElement("div");
  publicationCell.className = "admin-media-cell admin-media-cell--publication";
  publicationCell.setAttribute("role", "cell");
  publicationCell.appendChild(
    createAdminStateBadge(
      published ? "Published" : "Unpublished",
      published ? "published" : "neutral",
      published ? "✓" : "○",
    ),
  );
  const actionsCell = document.createElement("div");
  actionsCell.className = "admin-media-cell admin-media-cell--actions";
  actionsCell.setAttribute("role", "cell");
  if (typeof identityAllowsAnalysisPropose === "function" && identityAllowsAnalysisPropose()) {
    const proposeButton = document.createElement("button");
    proposeButton.type = "button";
    proposeButton.className = "admin-media-action";
    proposeButton.textContent = "Propose analysis";
    proposeButton.dataset.mediaId = item.media_id;
    proposeButton.addEventListener("click", () => {
      proposeWorkspaceAnalysis(item, proposeButton);
    });
    actionsCell.appendChild(proposeButton);
  }
  row.append(titleCell, readinessCell, publicationCell, actionsCell);
  return row;
}

async function openWorkspaceMediaDetails(item, opener) {
  try {
    const response = await fetch(`${MEDIA_CATALOG_ENDPOINT}/${encodeURIComponent(item.media_id)}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    openDetailsDialog(payload, opener);
  } catch {
    return;
  }
}

function renderWorkspaceMediaPage(page) {
  workspaceMediaState.items = Array.isArray(page.items) ? page.items : [];
  workspaceMediaState.total = Number.isInteger(page.total) ? page.total : 0;
  workspaceMediaState.limit = Number.isInteger(page.limit) ? page.limit : WORKSPACE_MEDIA_PAGE_SIZE;
  workspaceMediaState.offset = Number.isInteger(page.offset) ? page.offset : 0;
  if (workspaceMediaResults) {
    workspaceMediaResults.replaceChildren(
      ...workspaceMediaState.items.map((item) => renderWorkspaceMediaItem(item)),
    );
  }
  const start = workspaceMediaState.total === 0 ? 0 : workspaceMediaState.offset + 1;
  const end = Math.min(
    workspaceMediaState.offset + workspaceMediaState.items.length,
    workspaceMediaState.total,
  );
  if (workspaceMediaPageSummary) {
    workspaceMediaPageSummary.textContent = workspaceMediaState.total === 0
      ? "No contributions."
      : `Showing ${start}–${end} of ${workspaceMediaState.total}.`;
  }
  if (workspaceMediaPrevButton) workspaceMediaPrevButton.disabled = !page.has_previous;
  if (workspaceMediaNextButton) workspaceMediaNextButton.disabled = !page.has_next;
  setWorkspaceMediaViewState(workspaceMediaState.items.length === 0 ? "empty" : "results");
}

async function loadWorkspaceMedia() {
  if (!identityAllowsWorkspaceSurface() || !workspaceMediaBrowser) return false;
  const owner = claimWorkspaceMediaRequest();
  setWorkspaceMediaViewState("loading");
  try {
    const params = buildWorkspaceMediaQueryParams(owner);
    const response = await fetch(`${WORKSPACE_MEDIA_ENDPOINT}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!workspaceMediaRequestOwnerIsCurrent(owner)) return false;
    if (!response.ok) {
      setWorkspaceMediaViewState("error");
      return false;
    }
    renderWorkspaceMediaPage(payload);
    if (workspaceMediaHeading) workspaceMediaHeading.focus();
    return true;
  } catch {
    if (workspaceMediaRequestOwnerIsCurrent(owner)) {
      setWorkspaceMediaViewState("error");
    }
    return false;
  }
}

function openWorkspaceMediaBrowser() {
  if (!identityAllowsWorkspaceSurface() || !workspaceMediaBrowser) return;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (catalogBrowser) catalogBrowser.hidden = true;
  if (headerSearch) headerSearch.hidden = true;
  if (adminMediaBrowser) adminMediaBrowser.hidden = true;
  if (typeof analysisProposalsBrowser !== "undefined" && analysisProposalsBrowser) {
    analysisProposalsBrowser.hidden = true;
  }
  workspaceMediaBrowser.hidden = false;
  workspaceMediaState.offset = 0;
  loadWorkspaceMedia();
}

function closeWorkspaceMediaBrowser() {
  workspaceMediaRequestToken += 1;
  workspaceMediaState.requestOwner = null;
  if (workspaceMediaBrowser) workspaceMediaBrowser.hidden = true;
  if (catalogBrowser) catalogBrowser.hidden = false;
  if (headerSearch) headerSearch.hidden = false;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (typeof kronikaMarkGalleryCurrent === "function") kronikaMarkGalleryCurrent();
  if (workspaceMediaOpenButton && !workspaceMediaOpenButton.hidden) {
    workspaceMediaOpenButton.focus();
  }
}

async function proposeWorkspaceAnalysis(item, opener) {
  if (!identityAllowsAnalysisPropose() || !item || typeof item.media_id !== "string") return;
  if (opener) opener.disabled = true;
  if (workspaceMediaActionStatus) {
    workspaceMediaActionStatus.textContent = "Recording analysis proposal…";
  }
  try {
    const response = await fetch(
      `${WORKSPACE_MEDIA_ENDPOINT}/${encodeURIComponent(item.media_id)}/analysis-proposals`,
      {
        method: "POST",
        headers: framenestMutationHeaders({ Accept: "application/json" }),
        cache: "no-store",
      },
    );
    if (response.ok) {
      if (workspaceMediaActionStatus) {
        workspaceMediaActionStatus.textContent = "Proposal recorded. Analysis is not started.";
      }
      return;
    }
    if (workspaceMediaActionStatus) {
      workspaceMediaActionStatus.textContent = response.status === 404
        ? "That medium could not be found."
        : "The analysis proposal could not be recorded.";
    }
  } catch {
    if (workspaceMediaActionStatus) {
      workspaceMediaActionStatus.textContent = "The analysis proposal could not reach the server.";
    }
  } finally {
    if (opener) opener.disabled = false;
  }
}

function snapshotAnalysisProposalQueryState() {
  return Object.freeze({
    limit: analysisProposalState.limit,
    offset: analysisProposalState.offset,
  });
}

function buildAnalysisProposalQueryParams(snapshot = snapshotAnalysisProposalQueryState()) {
  const params = new URLSearchParams();
  params.set("limit", String(snapshot.limit));
  params.set("offset", String(snapshot.offset));
  return params;
}

function beginAnalysisProposalRequest() {
  const owner = {
    token: analysisProposalRequestToken + 1,
    ...snapshotAnalysisProposalQueryState(),
  };
  analysisProposalRequestToken = owner.token;
  analysisProposalState.requestOwner = owner;
  return owner;
}

function analysisProposalRequestOwnerIsCurrent(owner) {
  return Boolean(owner)
    && analysisProposalState.requestOwner === owner
    && analysisProposalRequestToken === owner.token
    && analysisProposalState.limit === owner.limit
    && analysisProposalState.offset === owner.offset;
}

function setAnalysisProposalsViewState(state) {
  analysisProposalState.loading = state === "loading";
  analysisProposalState.error = state === "error";
  if (analysisProposalsLoading) analysisProposalsLoading.hidden = state !== "loading";
  if (analysisProposalsEmpty) analysisProposalsEmpty.hidden = state !== "empty";
  if (analysisProposalsError) analysisProposalsError.hidden = state !== "error";
  if (analysisProposalsResults) {
    analysisProposalsResults.hidden = state === "loading" || state === "error" || state === "empty";
    analysisProposalsResults.setAttribute("aria-busy", String(state === "loading"));
  }
}

function renderAnalysisProposalItem(item) {
  const row = document.createElement("article");
  const published = item.content_publication_state === "published";
  row.className = published
    ? "admin-media-row admin-media-row--published"
    : "admin-media-row admin-media-row--unpublished";
  row.setAttribute("role", "row");
  const titleCell = document.createElement("div");
  titleCell.className = "admin-media-cell admin-media-cell--summary";
  titleCell.setAttribute("role", "cell");
  const title = document.createElement("p");
  title.className = "admin-media-row__title";
  title.textContent = typeof item.display_title === "string" && item.display_title.trim()
    ? item.display_title.trim()
    : "Untitled media";
  const meta = document.createElement("p");
  meta.className = "admin-media-row__metadata";
  const proposer = typeof item.proposer_login === "string" ? item.proposer_login : "";
  meta.textContent = proposer ? `Proposed by ${proposer}` : "Proposed by an unknown login";
  titleCell.append(title, meta);
  const readinessCell = document.createElement("div");
  readinessCell.className = "admin-media-cell admin-media-cell--readiness";
  readinessCell.setAttribute("role", "cell");
  readinessCell.appendChild(
    createAdminStateBadge(
      item.publication_ready ? "Ready to publish" : "Incomplete metadata",
      item.publication_ready ? "ready" : "incomplete",
      item.publication_ready ? "✓" : "!",
    ),
  );
  const publicationCell = document.createElement("div");
  publicationCell.className = "admin-media-cell admin-media-cell--publication";
  publicationCell.setAttribute("role", "cell");
  publicationCell.appendChild(
    createAdminStateBadge(
      published ? "Published" : "Unpublished",
      published ? "published" : "neutral",
      published ? "✓" : "○",
    ),
  );
  const statusCell = document.createElement("div");
  statusCell.className = "admin-media-cell";
  statusCell.setAttribute("role", "cell");
  statusCell.appendChild(
    createAdminStateBadge(
      typeof item.status === "string" ? item.status : "open",
      "neutral",
      "○",
    ),
  );
  row.append(titleCell, readinessCell, publicationCell, statusCell);
  return row;
}

function renderAnalysisProposalsPage(page) {
  analysisProposalState.items = Array.isArray(page.items) ? page.items : [];
  analysisProposalState.total = Number.isInteger(page.total) ? page.total : 0;
  analysisProposalState.limit = Number.isInteger(page.limit) ? page.limit : ANALYSIS_PROPOSALS_PAGE_SIZE;
  analysisProposalState.offset = Number.isInteger(page.offset) ? page.offset : 0;
  if (analysisProposalsResults) {
    analysisProposalsResults.replaceChildren(
      ...analysisProposalState.items.map((item) => renderAnalysisProposalItem(item)),
    );
  }
  const start = analysisProposalState.total === 0 ? 0 : analysisProposalState.offset + 1;
  const end = Math.min(
    analysisProposalState.offset + analysisProposalState.items.length,
    analysisProposalState.total,
  );
  if (analysisProposalsPageSummary) {
    analysisProposalsPageSummary.textContent = analysisProposalState.total === 0
      ? "No open proposals."
      : `Showing ${start}–${end} of ${analysisProposalState.total}.`;
  }
  if (analysisProposalsPrevButton) analysisProposalsPrevButton.disabled = !page.has_previous;
  if (analysisProposalsNextButton) analysisProposalsNextButton.disabled = !page.has_next;
  setAnalysisProposalsViewState(analysisProposalState.items.length === 0 ? "empty" : "results");
}

async function loadAnalysisProposals() {
  if (!identityAllowsAdminWorkflow() || !analysisProposalsBrowser) return false;
  const owner = beginAnalysisProposalRequest();
  setAnalysisProposalsViewState("loading");
  try {
    const response = await fetch(
      `${ANALYSIS_PROPOSALS_ENDPOINT}?${buildAnalysisProposalQueryParams(owner).toString()}`,
      {
        headers: { Accept: "application/json" },
        cache: "no-store",
      },
    );
    if (!analysisProposalRequestOwnerIsCurrent(owner)) return false;
    if (!response.ok) {
      setAnalysisProposalsViewState("error");
      return false;
    }
    const page = await response.json();
    if (!analysisProposalRequestOwnerIsCurrent(owner)) return false;
    renderAnalysisProposalsPage(page);
    if (analysisProposalsHeading) analysisProposalsHeading.focus();
    return true;
  } catch {
    if (analysisProposalRequestOwnerIsCurrent(owner)) {
      setAnalysisProposalsViewState("error");
    }
    return false;
  }
}

function openAnalysisProposalsBrowser() {
  if (!identityAllowsAdminWorkflow() || !analysisProposalsBrowser) return;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (catalogBrowser) catalogBrowser.hidden = true;
  if (headerSearch) headerSearch.hidden = true;
  if (adminMediaBrowser) adminMediaBrowser.hidden = true;
  if (workspaceMediaBrowser) workspaceMediaBrowser.hidden = true;
  analysisProposalsBrowser.hidden = false;
  analysisProposalState.offset = 0;
  if (analysisProposalsActionStatus) analysisProposalsActionStatus.textContent = "";
  loadAnalysisProposals();
}

function closeAnalysisProposalsBrowser() {
  analysisProposalRequestToken += 1;
  analysisProposalState.requestOwner = null;
  if (analysisProposalsBrowser) analysisProposalsBrowser.hidden = true;
  if (catalogBrowser) catalogBrowser.hidden = false;
  if (headerSearch) headerSearch.hidden = false;
  if (typeof kronikaHideProductSections === "function") kronikaHideProductSections();
  if (typeof kronikaMarkGalleryCurrent === "function") kronikaMarkGalleryCurrent();
  if (analysisProposalsOpenButton && !analysisProposalsOpenButton.hidden) {
    analysisProposalsOpenButton.focus();
  }
}

function applyAdminCatalogFilters() {
  adminCatalogState.q = adminMediaSearch ? adminMediaSearch.value : "";
  adminCatalogState.publication = adminMediaPublicationFilter
    ? adminMediaPublicationFilter.value
    : "unpublished";
  adminCatalogState.readiness = adminMediaReadinessFilter
    ? adminMediaReadinessFilter.value
    : "all";
  adminCatalogState.analysis = adminMediaAnalysisFilter
    ? adminMediaAnalysisFilter.value
    : "all";
  adminCatalogState.contributor = adminMediaContributorFilter
    ? adminMediaContributorFilter.value
    : "";
  adminCatalogState.offset = 0;
  resetAdminBatchForQueryChange();
  loadAdminCatalog();
}

const MOVIE_GENRE_OPTIONS = [
  "Drama", "Comedy", "Sci-Fi", "Thriller", "Horror", "Action", "Adventure",
  "Documentary", "Animation", "Family", "Romance", "Crime", "Fantasy", "Mystery",
];

function syncClassificationControlsFromWorkspace() {
  const aliasMode = metadataWorkspaceIsAliasMode();
  const categorySelect = document.querySelector("#metadata-content-category");
  const sourceSelect = document.querySelector("#metadata-acquisition-source");
  const genresFieldset = document.querySelector("#metadata-genres-fieldset");
  const genresContainer = document.querySelector("#metadata-genres");
  const identifyButton = document.querySelector("#metadata-movie-identify-button");
  const classificationRow = document.querySelector(".metadata-classification-row");
  if (classificationRow) {
    classificationRow.hidden = true;
  }
  if (categorySelect) {
    categorySelect.value = metadataWorkspace.current.contentCategory || "general";
    const xAcquired = metadataWorkspace.current.acquisitionSource === "x_manual_claim";
    categorySelect.disabled = aliasMode || xAcquired;
  }
  if (sourceSelect) {
    sourceSelect.value = metadataWorkspace.current.acquisitionSource || "unknown";
    sourceSelect.disabled = true;
  }
  const isMovie = (metadataWorkspace.current.contentCategory || "general") === "movie";
  if (genresFieldset) {
    genresFieldset.hidden = aliasMode || !isMovie;
  }
  if (genresContainer) {
    const selected = new Set(metadataWorkspace.current.genres || []);
    genresContainer.replaceChildren();
    MOVIE_GENRE_OPTIONS.forEach((genre) => {
      const label = document.createElement("label");
      label.className = "metadata-genre-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = genre;
      input.checked = selected.has(genre);
      input.addEventListener("change", () => {
        metadataWorkspace.current.genres = MOVIE_GENRE_OPTIONS.filter((name) => {
          const box = genresContainer.querySelector(`input[value="${CSS.escape(name)}"]`);
          return box && box.checked;
        });
        updateMetadataControls();
      });
      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${genre}`));
      genresContainer.appendChild(label);
    });
  }
  if (identifyButton) {
    identifyButton.hidden = aliasMode || !isMovie;
    identifyButton.disabled = aliasMode || !isMovie || !aiCapability.configured;
  }
}

function catalogHasNarrowingFilters() {
  return Boolean(typeof catalogState.q === "string" ? catalogState.q.trim() : "")
    || catalogState.tagKeys.length > 0
    || Boolean(catalogState.contentCategory)
    || Boolean(catalogState.acquisitionSource)
    || Boolean(catalogState.creatorAttributionKind)
    || Boolean(catalogState.creatorStableId)
    || Boolean(catalogState.creatorHandle);
}

function catalogIsUnfilteredAllMedia() {
  return catalogState.collection === "" && !catalogHasNarrowingFilters();
}

function syncCatalogFilterControls() {
  const allButton = document.querySelector("#catalog-scope-all");
  const processedButton = document.querySelector("#catalog-scope-processed");
  if (allButton) {
    allButton.classList.toggle("scope-active", catalogIsUnfilteredAllMedia());
  }
  if (processedButton) {
    processedButton.classList.toggle("scope-active", catalogState.collection === PROCESSED_COLLECTION);
  }
  const memes = document.querySelector("#catalog-filter-memes");
  const movies = document.querySelector("#catalog-filter-movies");
  const youtube = document.querySelector("#catalog-filter-youtube");
  if (memes) memes.setAttribute("aria-pressed", String(catalogState.contentCategory === "meme"));
  if (movies) movies.setAttribute("aria-pressed", String(catalogState.contentCategory === "movie"));
  if (youtube) youtube.setAttribute("aria-pressed", String(catalogState.contentCategory === "youtube"));
}

function setCatalogClassificationFilter({ contentCategory = null, acquisitionSource = null } = {}) {
  if (contentCategory !== null) {
    catalogState.contentCategory = catalogState.contentCategory === contentCategory ? "" : contentCategory;
  }
  if (acquisitionSource !== null) {
    catalogState.acquisitionSource = catalogState.acquisitionSource === acquisitionSource ? "" : acquisitionSource;
  }
  catalogState.offset = 0;
  syncCatalogFilterControls();
  loadCatalog();
}

function setCatalogCreatorFilter(options) {
  const opts = options || {};
  const nextKind = opts.kind || "";
  const nextStableId = opts.stableId || "";
  const nextHandle = nextStableId ? "" : (opts.handle || "");
  const alreadyActive = Boolean(nextKind)
    && Boolean(nextStableId || nextHandle)
    && catalogState.creatorAttributionKind === nextKind
    && catalogState.creatorStableId === nextStableId
    && catalogState.creatorHandle === nextHandle;
  if (alreadyActive) {
    catalogState.creatorAttributionKind = "";
    catalogState.creatorStableId = "";
    catalogState.creatorHandle = "";
  } else {
    catalogState.creatorAttributionKind = nextKind;
    catalogState.creatorStableId = nextStableId;
    catalogState.creatorHandle = nextHandle;
  }
  catalogState.offset = 0;
  syncCatalogFilterControls();
  loadCatalog();
}

function setCatalogScope(collection) {
  if (catalogState.collection !== collection) {
    advanceMetadataWorkspaceRevision();
  }
  catalogState.collection = collection;
  catalogState.offset = 0;
  syncCatalogFilterControls();
  loadCatalog();
}

function resetCatalogToAllMedia() {
  if (catalogState.collection !== "") {
    advanceMetadataWorkspaceRevision();
  }
  resetCatalogSearchState();
  catalogRequestToken += 1;
  catalogRequestOwner = null;
  catalogState.collection = "";
  catalogState.tagKeys = [];
  catalogState.contentCategory = "";
  catalogState.acquisitionSource = "";
  catalogState.creatorAttributionKind = "";
  catalogState.creatorStableId = "";
  catalogState.creatorHandle = "";
  catalogState.offset = 0;
  renderActiveCatalogTagFilters();
  renderCatalogTagFilterStates();
  syncCatalogFilterControls();
  loadCatalog();
}

document.querySelector("#catalog-scope-all").addEventListener("click", async () => {
  const targetScope = "";
  const discardContext = await confirmDiscardDirtyMetadata({ action: "change-scope", targetScope });
  if (!discardContext) return;
  if (metadataWorkspace.openMediaId !== null) {
    if (!closeMetadataWorkspaceWithContext(discardContext, { reloadCatalog: false })) return;
  } else if (!metadataDiscardContextIsCurrent(discardContext)) {
    return;
  }
  resetCatalogToAllMedia();
});

document.querySelector("#catalog-scope-processed").addEventListener("click", async () => {
  const targetScope = PROCESSED_COLLECTION;
  const discardContext = await confirmDiscardDirtyMetadata({ action: "change-scope", targetScope });
  if (!discardContext) return;
  if (metadataWorkspace.openMediaId !== null) {
    if (!closeMetadataWorkspaceWithContext(discardContext, { reloadCatalog: false })) return;
  } else if (!metadataDiscardContextIsCurrent(discardContext)) {
    return;
  }
  setCatalogScope(PROCESSED_COLLECTION);
});

document.querySelector("#catalog-filter-memes")?.addEventListener("click", () => {
  setCatalogClassificationFilter({ contentCategory: "meme" });
});
document.querySelector("#catalog-filter-movies")?.addEventListener("click", () => {
  setCatalogClassificationFilter({ contentCategory: "movie" });
});
document.querySelector("#catalog-filter-youtube")?.addEventListener("click", () => {
  setCatalogClassificationFilter({ contentCategory: "youtube" });
});

document.querySelector("#metadata-content-category")?.addEventListener("change", (event) => {
  metadataWorkspace.current.contentCategory = event.target.value;
  if (metadataWorkspace.current.contentCategory !== "movie") {
    metadataWorkspace.current.genres = [];
  }
  syncClassificationControlsFromWorkspace();
  updateMetadataControls();
});
document.querySelector("#metadata-acquisition-source")?.addEventListener("change", () => {
  // Acquisition source is read-only provenance; the control stays disabled.
});

document.querySelector("#metadata-movie-identify-button")?.addEventListener("click", async () => {
  const mediaId = metadataWorkspace.openMediaId;
  const location = metadataAiLocation();
  if (!mediaId || !location || !aiCapability.configured) return;
  const button = document.querySelector("#metadata-movie-identify-button");
  if (button) button.disabled = true;
  metadataAiStatus.textContent = "Running movie identification...";
  try {
    const response = await fetch(
      `/api/media/${encodeURIComponent(mediaId)}/locations/${encodeURIComponent(location.location_id)}/movie-identification`,
      {
        method: "POST",
        headers: framenestMutationHeaders({ Accept: "application/json", "Content-Type": "application/json" }),
        body: JSON.stringify({ confirm_cloud_upload: true }),
      },
    );
    const payload = await response.json();
    if (!response.ok) {
      metadataAiStatus.textContent = (payload.error && payload.error.message) || "Movie identification failed.";
      return;
    }
    metadataDurableAnalysis = applyAnalysisStatusPayload(mediaId, payload, true);
    metadataAiStatus.textContent = metadataDurableAnalysis.statusMessage
      || metadataDurableAnalysis.errorMessage
      || `Movie identification state: ${payload.state}.`;
    renderMetadataWorkspace();
  } catch {
    metadataAiStatus.textContent = "Movie identification failed.";
  } finally {
    syncClassificationControlsFromWorkspace();
  }
});

let commandSearchDebounceTimer = null;
let commandSearchRequestToken = 0;
let commandSearchActiveIndex = -1;
let commandSearchCurrentSuggestions = [];

function closeCommandSearchSuggestions() {
  commandSearchActiveIndex = -1;
  commandSearchCurrentSuggestions = [];
  if (!commandSearchSuggestions) return;
  commandSearchSuggestions.hidden = true;
  commandSearchSuggestions.replaceChildren();
  if (commandSearchInput) commandSearchInput.setAttribute("aria-expanded", "false");
}

function resetCatalogSearchState() {
  if (commandSearchDebounceTimer) {
    clearTimeout(commandSearchDebounceTimer);
    commandSearchDebounceTimer = null;
  }
  commandSearchRequestToken += 1;
  if (commandSearchInput) commandSearchInput.value = "";
  if (commandSearchClear) commandSearchClear.hidden = true;
  closeCommandSearchSuggestions();
  setCatalogSearchText("");
}

function renderCommandSearchSuggestions(titleItems, tagMatches, fallbackItems) {
  if (!commandSearchSuggestions) return;
  const hasRealSuggestions = (titleItems && titleItems.length > 0) || (tagMatches && tagMatches.length > 0) || (fallbackItems && fallbackItems.length > 0);
  if (!hasRealSuggestions) {
    closeCommandSearchSuggestions();
    return;
  }
  commandSearchSuggestions.replaceChildren();
  commandSearchCurrentSuggestions = [];
  const maxTitles = 5;
  const maxTags = 5;
  const maxFallback = 3;
  let count = 0;
  titleItems.forEach((item) => {
    if (count >= maxTitles) return;
    const li = document.createElement("li");
    li.className = "command-search-suggestion";
    li.setAttribute("role", "option");
    li.dataset.suggestionType = "title";
    li.dataset.suggestionMediaId = item.media_id;
    const typeSpan = document.createElement("span");
    typeSpan.className = "command-search-suggestion__type";
    typeSpan.textContent = "Title";
    const labelSpan = document.createElement("span");
    labelSpan.className = "command-search-suggestion__label";
    labelSpan.textContent = item.display_title || item.media_id;
    li.appendChild(typeSpan);
    li.appendChild(labelSpan);
    li.addEventListener("click", () => {
      commandSearchRequestToken += 1;
      commandSearchInput.value = item.display_title || "";
      setCatalogSearchText(item.display_title || "");
      catalogState.offset = 0;
      closeCommandSearchSuggestions();
      loadCatalog();
    });
    commandSearchSuggestions.appendChild(li);
    commandSearchCurrentSuggestions.push(li);
    count++;
  });
  if (fallbackItems) {
    fallbackItems.forEach((item) => {
      if (commandSearchCurrentSuggestions.length >= maxTitles + maxFallback) return;
      const li = document.createElement("li");
      li.className = "command-search-suggestion";
      li.setAttribute("role", "option");
      li.dataset.suggestionType = "title";
      li.dataset.suggestionMediaId = item.media_id;
      const typeSpan = document.createElement("span");
      typeSpan.className = "command-search-suggestion__type";
      typeSpan.textContent = "File";
      const labelSpan = document.createElement("span");
      labelSpan.className = "command-search-suggestion__label";
      labelSpan.textContent = deriveCatalogFallbackTitle(item);
      li.appendChild(typeSpan);
      li.appendChild(labelSpan);
      li.addEventListener("click", () => {
        closeCommandSearchSuggestions();
        const card = document.querySelector(`[data-media-id="${item.media_id}"]`);
        if (card) {
          card.scrollIntoView({ block: "center", behavior: "smooth" });
          card.classList.add("catalog-card--flash");
          setTimeout(() => card.classList.remove("catalog-card--flash"), 1500);
        }
      });
      commandSearchSuggestions.appendChild(li);
      commandSearchCurrentSuggestions.push(li);
    });
  }
  tagMatches.forEach((tag) => {
    if (commandSearchCurrentSuggestions.length >= maxTitles + maxTags + maxFallback) return;
    const li = document.createElement("li");
    li.className = "command-search-suggestion";
    li.setAttribute("role", "option");
    li.dataset.suggestionType = "tag";
    li.dataset.suggestionTagKey = tag.key;
    const typeSpan = document.createElement("span");
    typeSpan.className = "command-search-suggestion__type";
    typeSpan.textContent = "Tag";
    const labelSpan = document.createElement("span");
    labelSpan.className = "command-search-suggestion__label";
    labelSpan.textContent = tag.display_name;
    li.appendChild(typeSpan);
    li.appendChild(labelSpan);
    li.addEventListener("click", (event) => {
      commandSearchRequestToken += 1;
      commandSearchInput.value = "";
      if (commandSearchClear) commandSearchClear.hidden = true;
      setCatalogSearchText("");
      catalogState.offset = 0;
      closeCommandSearchSuggestions();
      if (!activateCatalogTagFilter(tag.key, { focusChip: catalogTagActivationShouldFocusChip(event) })) {
        loadCatalog();
      }
    });
    commandSearchSuggestions.appendChild(li);
    commandSearchCurrentSuggestions.push(li);
  });
  commandSearchSuggestions.hidden = false;
  commandSearchInput.setAttribute("aria-expanded", "true");
  commandSearchActiveIndex = -1;
}

function updateSuggestionActiveState() {
  commandSearchCurrentSuggestions.forEach((li, index) => {
    li.classList.toggle("command-search-suggestion--active", index === commandSearchActiveIndex);
  });
  if (commandSearchActiveIndex >= 0 && commandSearchCurrentSuggestions[commandSearchActiveIndex]) {
    commandSearchCurrentSuggestions[commandSearchActiveIndex].scrollIntoView({ block: "nearest" });
  }
}

async function performCommandSearch(query, token = ++commandSearchRequestToken) {
  if (!query || query.trim().length === 0) {
    closeCommandSearchSuggestions();
    return;
  }
  const lowerQuery = query.trim().toLocaleLowerCase();
  const tagMatches = canonicalTagDefinitions
    .filter((tag) =>
      tag.key.toLowerCase().includes(lowerQuery) ||
      tag.display_name.toLowerCase().includes(lowerQuery)
    )
    .slice(0, 5);
  const fallbackMatches = [];
  const cardElements = catalogResults.querySelectorAll(".catalog-card");
  cardElements.forEach((card) => {
    const mediaId = card.dataset.mediaId;
    if (!mediaId) return;
    const titleEl = card.querySelector("h3");
    if (!titleEl) return;
    const title = titleEl.textContent || "";
    if (title.toLowerCase().includes(lowerQuery)) {
      fallbackMatches.push({ media_id: mediaId, _fallbackTitle: title });
    }
  });
  try {
    const params = new URLSearchParams();
    params.set("q", query);
    params.set("limit", "5");
    params.set("offset", "0");
    const response = await fetch(`${MEDIA_CATALOG_ENDPOINT}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (token !== commandSearchRequestToken) return;
    if (!response.ok) {
      renderCommandSearchSuggestions([], tagMatches, fallbackMatches.slice(0, 3));
      return;
    }
    const payload = await response.json();
    if (token !== commandSearchRequestToken) return;
    const titleItems = (payload.items || []).filter((item) => item.display_title);
    const filteredFallback = fallbackMatches.filter((fm) =>
      !titleItems.some((ti) => ti.media_id === fm.media_id)
    ).slice(0, 3);
    renderCommandSearchSuggestions(titleItems, tagMatches, filteredFallback);
  } catch {
    if (token !== commandSearchRequestToken) return;
    renderCommandSearchSuggestions([], tagMatches, fallbackMatches.slice(0, 3));
  }
}

if (commandSearchInput) {
  commandSearchInput.addEventListener("input", () => {
    if (commandSearchClear) commandSearchClear.hidden = commandSearchInput.value.length === 0;
    if (commandSearchDebounceTimer) clearTimeout(commandSearchDebounceTimer);
    const query = commandSearchInput.value;
    const token = ++commandSearchRequestToken;
    setCatalogSearchText(query);
    if (query.trim().length === 0) {
      closeCommandSearchSuggestions();
      loadCatalog();
      return;
    }
    commandSearchDebounceTimer = setTimeout(() => {
      commandSearchDebounceTimer = null;
      if (token !== commandSearchRequestToken) return;
      performCommandSearch(query, token);
      loadCatalog();
    }, 200);
  });

  commandSearchInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (commandSearchCurrentSuggestions.length === 0) return;
      commandSearchActiveIndex = Math.min(commandSearchActiveIndex + 1, commandSearchCurrentSuggestions.length - 1);
      updateSuggestionActiveState();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (commandSearchCurrentSuggestions.length === 0) return;
      commandSearchActiveIndex = Math.max(commandSearchActiveIndex - 1, -1);
      updateSuggestionActiveState();
    } else if (event.key === "Enter") {
      if (commandSearchActiveIndex >= 0 && commandSearchCurrentSuggestions[commandSearchActiveIndex]) {
        event.preventDefault();
        commandSearchCurrentSuggestions[commandSearchActiveIndex].click();
      } else {
        if (commandSearchDebounceTimer) clearTimeout(commandSearchDebounceTimer);
        commandSearchRequestToken += 1;
        setCatalogSearchText(commandSearchInput.value);
        catalogState.offset = 0;
        closeCommandSearchSuggestions();
        loadCatalog();
      }
    } else if (event.key === "Escape") {
      commandSearchRequestToken += 1;
      closeCommandSearchSuggestions();
    }
  });

  commandSearchInput.addEventListener("blur", () => {
    setTimeout(() => {
      commandSearchRequestToken += 1;
      closeCommandSearchSuggestions();
    }, 150);
  });
}

if (commandSearchClear) {
  commandSearchClear.addEventListener("click", () => {
    resetCatalogSearchState();
    loadCatalog();
    commandSearchInput.focus();
  });
}

catalogPrevButton.addEventListener("click", () => {
  catalogState.offset = Math.max(0, catalogState.offset - catalogState.limit);
  loadCatalog();
});

catalogNextButton.addEventListener("click", () => {
  if (catalogState.offset + catalogState.limit >= catalogState.total) {
    return;
  }
  catalogState.offset += catalogState.limit;
  loadCatalog();
});

if (catalogPageSizeSelect) {
  syncCatalogPageSizeControl();
  catalogPageSizeSelect.addEventListener("change", () => {
    const nextLimit = Number(catalogPageSizeSelect.value);
    catalogState.limit = CATALOG_PAGE_SIZE_OPTIONS.includes(nextLimit) ? nextLimit : CATALOG_PAGE_SIZE;
    catalogState.offset = 0;
    syncCatalogPageSizeControl();
    try {
      window.localStorage.setItem(KRONIKA_CATALOG_PAGE_SIZE_STORAGE_KEY, String(catalogState.limit));
    } catch {
      // Ignore unavailable localStorage; the in-memory selection still applies.
    }
    loadCatalog();
  });
}

metadataTitleInput.addEventListener("input", () => {
  metadataWorkspace.current.displayTitle = metadataTitleInput.value;
  metadataWorkspace.statusOverride = null;
  advanceMetadataWorkspaceRevision();
  updateMetadataControls();
});

metadataTagSearchInput.addEventListener("input", () => {
  metadataTagSuggestionState.activeIndex = -1;
  renderMetadataTagSuggestions();
});
metadataTagSearchInput.addEventListener("keydown", handleMetadataTagSearchKeydown);
metadataTagSearchInput.addEventListener("blur", () => {
  window.setTimeout(() => {
    metadataTagSuggestionState = { items: [], activeIndex: -1 };
    metadataTagSuggestions.replaceChildren();
    metadataTagSearchInput.setAttribute("aria-expanded", "false");
  }, 120);
});
metadataDescriptionInput.addEventListener("input", () => {
  metadataWorkspace.current.description = metadataDescriptionInput.value;
  metadataWorkspace.statusOverride = null;
  advanceMetadataWorkspaceRevision();
  updateDescriptionStatus();
  updateMetadataControls();
});
metadataSaveButton.addEventListener("click", handleSaveMetadata);
metadataDiscardButton.addEventListener("click", handleDiscardMetadataChanges);
metadataAiAnalyzeButton.addEventListener("click", handleAnalyzeMetadataByAi);
if (metadataAiSuggestionToggle) {
  metadataAiSuggestionToggle.addEventListener("click", (event) => {
    event.preventDefault();
    toggleMetadataSuggestionDropdown();
  });
  metadataAiSuggestionToggle.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleMetadataSuggestionDropdown();
    }
  });
}
if (metadataAiSuggestionList) {
  metadataAiSuggestionList.addEventListener("keydown", (event) => {
    const options = [...metadataAiSuggestionList.querySelectorAll(".metadata-ai-suggestion-option")];
    const current = options.indexOf(document.activeElement);
    if (event.key === "Escape") {
      event.preventDefault();
      closeMetadataSuggestionDropdown();
      metadataAiSuggestionToggle?.focus();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      const next = options[Math.min(options.length - 1, current + 1)] || options[0];
      next?.focus();
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      const previous = options[Math.max(0, current - 1)] || options[0];
      previous?.focus();
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      options[0]?.focus();
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      options[options.length - 1]?.focus();
    }
  });
}
document.addEventListener("click", (event) => {
  if (!metadataAiSuggestionDropdown) return;
  if (metadataAiSuggestionDropdown.contains(event.target)) return;
  closeMetadataSuggestionDropdown();
});

function setActiveStatusTab(tabName, { focusTab = false, refreshAiStatus = false } = {}) {
  const normalized =
    tabName === "cloud" || tabName === "tailscale" || tabName === "ai" ? tabName : "ai";
  if (!statusTabAi || !statusTabCloud || !statusTabTailscale) return;
  if (!statusPanelAi || !statusPanelCloud || !statusPanelTailscale) return;

  const tabs = [
    { name: "ai", tab: statusTabAi, panel: statusPanelAi },
    { name: "cloud", tab: statusTabCloud, panel: statusPanelCloud },
    { name: "tailscale", tab: statusTabTailscale, panel: statusPanelTailscale },
  ];
  for (const entry of tabs) {
    const selected = entry.name === normalized;
    entry.tab.classList.toggle("settings-dialog__tab--active", selected);
    entry.tab.setAttribute("aria-selected", String(selected));
    entry.tab.tabIndex = selected ? 0 : -1;
    entry.panel.hidden = !selected;
  }

  if (normalized === "cloud") {
    loadCloudStatus();
  } else if (normalized === "tailscale") {
    loadTailscaleStatus();
  } else if (refreshAiStatus) {
    loadAiCapability();
  }

  if (focusTab) {
    const active = tabs.find((entry) => entry.name === normalized);
    if (active) active.tab.focus();
  }
}

function openStatusDialog(tabName = "ai", { refreshAiStatus = false } = {}) {
  if (!statusDialog) return;
  lastFocusedElementBeforeStatus = document.activeElement;
  setActiveStatusTab(tabName, { refreshAiStatus: tabName === "ai" && refreshAiStatus });
  if (typeof statusDialog.showModal === "function") {
    statusDialog.showModal();
  } else {
    statusDialog.setAttribute("open", "");
  }
  const panel =
    tabName === "cloud"
      ? statusPanelCloud
      : tabName === "tailscale"
        ? statusPanelTailscale
        : statusPanelAi;
  if (panel) panel.focus();
}

function closeStatusDialog() {
  if (!statusDialog) return;
  if (typeof statusDialog.close === "function") {
    statusDialog.close();
  } else {
    statusDialog.removeAttribute("open");
  }
  if (lastFocusedElementBeforeStatus) {
    lastFocusedElementBeforeStatus.focus();
    lastFocusedElementBeforeStatus = null;
  } else {
    aiStatusButton.focus();
  }
}

function handleStatusTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const order = ["ai", "cloud", "tailscale"];
  const currentIndex = order.findIndex((name) => {
    if (name === "ai") return event.currentTarget === statusTabAi;
    if (name === "cloud") return event.currentTarget === statusTabCloud;
    return event.currentTarget === statusTabTailscale;
  });
  let nextName = "ai";
  if (event.key === "Home") {
    nextName = "ai";
  } else if (event.key === "End") {
    nextName = "tailscale";
  } else if (event.key === "ArrowRight") {
    nextName = order[(Math.max(currentIndex, 0) + 1) % order.length];
  } else {
    nextName = order[(Math.max(currentIndex, 0) - 1 + order.length) % order.length];
  }
  setActiveStatusTab(nextName, {
    focusTab: true,
    refreshAiStatus: nextName === "ai",
  });
}

if (serverHealthButton) {
  serverHealthButton.addEventListener("click", () => {
    retryHealth();
    openStatusDialog("cloud");
  });
}

if (aiStatusButton) {
  aiStatusButton.addEventListener("click", () => {
    openStatusDialog("ai", { refreshAiStatus: true });
  });
}

if (identityBadge) {
  identityBadge.addEventListener("click", () => {
    openStatusDialog("tailscale");
  });
}

if (statusTabAi) {
  statusTabAi.addEventListener("click", () => setActiveStatusTab("ai", { refreshAiStatus: true }));
  statusTabAi.addEventListener("keydown", handleStatusTabKeydown);
}

if (statusTabCloud) {
  statusTabCloud.addEventListener("click", () => setActiveStatusTab("cloud"));
  statusTabCloud.addEventListener("keydown", handleStatusTabKeydown);
}

if (statusTabTailscale) {
  statusTabTailscale.addEventListener("click", () => setActiveStatusTab("tailscale"));
  statusTabTailscale.addEventListener("keydown", handleStatusTabKeydown);
}

if (statusCloseButton) {
  statusCloseButton.addEventListener("click", () => {
    closeStatusDialog();
  });
}

if (statusDialog) {
  statusDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeStatusDialog();
    }
  });
  statusDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeStatusDialog);
  });
  statusDialog.addEventListener("click", (event) => {
    if (event.target === statusDialog) {
      closeStatusDialog();
    }
  });
}

if (uploadOpenButton) {
  uploadOpenButton.addEventListener("click", openUploadDialog);
}

if (youtubeClaimOpenButton) {
  youtubeClaimOpenButton.addEventListener("click", openYouTubeClaimDialog);
}

if (youtubeClaimCloseButton) {
  youtubeClaimCloseButton.addEventListener("click", closeYouTubeClaimDialog);
}

if (youtubeClaimForm) {
  youtubeClaimForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitYouTubeClaim();
  });
}

if (youtubeClaimUrlInput) {
  youtubeClaimUrlInput.addEventListener("input", () => {
    if (!youtubeClaimState.urlError) return;
    youtubeClaimState.urlError = "";
    renderYouTubeClaimCockpit();
  });
  youtubeClaimUrlInput.addEventListener("invalid", (event) => {
    event.preventDefault();
    youtubeClaimState.urlError = validateYouTubeClaimUrl(youtubeClaimUrlInput.value).message;
    renderYouTubeClaimCockpit();
  });
}

if (youtubeClaimRetryButton) {
  youtubeClaimRetryButton.addEventListener("click", retryYouTubeClaim);
}

if (youtubeClaimResetButton) {
  youtubeClaimResetButton.addEventListener("click", resetYouTubeClaimState);
}

if (youtubeClaimManageMediaButton) {
  youtubeClaimManageMediaButton.addEventListener("click", handoffYouTubeClaimToManageMedia);
}

if (youtubeClaimDialog) {
  youtubeClaimDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeYouTubeClaimDialog();
    }
  });
  youtubeClaimDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeYouTubeClaimDialog);
  });
  youtubeClaimDialog.addEventListener("click", (event) => {
    if (event.target === youtubeClaimDialog) closeYouTubeClaimDialog();
  });
}

if (uploadCloseButton) {
  uploadCloseButton.addEventListener("click", closeUploadDialog);
}

if (uploadDialog) {
  uploadDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeUploadDialog();
    }
  });
  uploadDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeUploadDialog);
  });
  uploadDialog.addEventListener("click", (event) => {
    if (event.target === uploadDialog) {
      closeUploadDialog();
    }
  });
}

if (uploadFileInput) {
  uploadFileInput.addEventListener("change", handleUploadFileSelection);
}

if (uploadStartButton) {
  uploadStartButton.addEventListener("click", handleStartUpload);
}

if (uploadPauseButton) {
  uploadPauseButton.addEventListener("click", handlePauseUpload);
}

if (uploadResumeButton) {
  uploadResumeButton.addEventListener("click", handleResumeUpload);
}

if (uploadDuplicateKeepButton) {
  uploadDuplicateKeepButton.addEventListener("click", handleKeepDuplicate);
}

if (uploadDuplicateDiscardButton) {
  uploadDuplicateDiscardButton.addEventListener("click", handleDiscardDuplicate);
}

if (uploadCancelButton) {
  uploadCancelButton.addEventListener("click", handleCancelUpload);
}

if (confirmationDialog) {
  confirmationDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    settleConfirmationEscape(activeConfirmationRequest);
  });
  confirmationDialog.addEventListener("keydown", handleConfirmationKeydown);
  confirmationDialog.addEventListener("click", (event) => {
    if (event.target === confirmationDialog) {
      settleConfirmation(activeConfirmationRequest, false);
    }
  });
}

if (confirmationDismissButton) {
  confirmationDismissButton.addEventListener("click", () => {
    settleConfirmation(activeConfirmationRequest, false);
  });
}

if (confirmationConfirmButton) {
  confirmationConfirmButton.addEventListener("click", () => {
    settleConfirmation(activeConfirmationRequest, true);
  });
}

// --- Durable manual cover chooser -----------------------------------------

let coverOpenerElement = null;
let coverPreviewAbortController = null;
let coverDialogState = {
  available: false,
  openItem: null,
  currentLocation: null,
  durationMs: 0,
  sourceVersion: "",
  currentCover: null,
  requestToken: 0,
  previewToken: 0,
  acceptToken: 0,
  selectedTimestampMs: 0,
  isImage: false,
  confirmingReplace: false,
  submitting: false,
};

function coverContextStillCurrent(owner) {
  return Boolean(owner)
    && coverDialogState.available
    && coverDialogState.openItem
    && coverDialogState.openItem.media_id === owner.mediaId
    && coverDialogState.requestToken === owner.token;
}

function coverReadoutText(ms) {
  const safe = Number.isFinite(ms) && ms >= 0 ? Math.floor(ms) : 0;
  const hours = Math.floor(safe / 3600000);
  const minutes = Math.floor((safe % 3600000) / 60000);
  const seconds = Math.floor((safe % 60000) / 1000);
  const millis = safe % 1000;
  const pad = (value, width) => String(value).padStart(width, "0");
  return `${pad(hours, 2)}:${pad(minutes, 2)}:${pad(seconds, 2)}.${pad(millis, 3)}`;
}

function coverDurationText(ms) {
  return `/ ${coverReadoutText(ms)}`;
}

function resetCoverDialogView() {
  if (coverTimeline) coverTimeline.hidden = false;
  if (coverCurrentTimestamp) coverCurrentTimestamp.hidden = false;
  if (coverDialogLoading) coverDialogLoading.hidden = false;
  if (coverDialogError) coverDialogError.hidden = true;
  if (coverDialogContent) coverDialogContent.hidden = true;
  if (coverCurrent) coverCurrent.hidden = true;
  if (coverReplaceConfirm) coverReplaceConfirm.hidden = true;
  if (coverPreviewContainer) {
    coverPreviewContainer.replaceChildren();
    coverPreviewContainer.hidden = true;
  }
  hideCoverPreviewStatus();
  clearCoverStatus();
  if (coverSetButton) coverSetButton.disabled = true;
  if (coverPreviewButton) coverPreviewButton.disabled = true;
  if (coverTimelineRange) coverTimelineRange.value = "0";
  if (coverTimestampReadout) coverTimestampReadout.textContent = "00:00:00.000";
  if (coverDurationReadout) coverDurationReadout.textContent = "";
}

function coverTimelineFromPayload(payload) {
  return Boolean(payload) && payload.media_kind === "image";
}

function coverSubmittedTimestampMs(isImage, selectedTimestampMs) {
  return isImage ? 0 : selectedTimestampMs;
}

function applyCoverImageMode(isImage) {
  coverDialogState.isImage = Boolean(isImage);
  if (coverTimeline) coverTimeline.hidden = coverDialogState.isImage;
  if (coverCurrentTimestamp) {
    coverCurrentTimestamp.hidden = coverDialogState.isImage;
  }
}

function showCoverLoading() {
  if (coverDialogLoading) coverDialogLoading.hidden = false;
  if (coverDialogError) coverDialogError.hidden = true;
  if (coverDialogContent) coverDialogContent.hidden = true;
}

function showCoverContent() {
  if (coverDialogLoading) coverDialogLoading.hidden = true;
  if (coverDialogError) coverDialogError.hidden = true;
  if (coverDialogContent) coverDialogContent.hidden = false;
  if (coverPreviewButton) coverPreviewButton.disabled = false;
}

function showCoverError(message) {
  if (coverDialogLoading) coverDialogLoading.hidden = true;
  if (coverDialogError) {
    coverDialogError.hidden = false;
    coverDialogError.textContent = message;
  }
  if (coverDialogContent) coverDialogContent.hidden = true;
}

function showCoverPreviewStatus(message) {
  if (!coverPreviewStatus) return;
  coverPreviewStatus.hidden = false;
  coverPreviewStatus.textContent = message;
}

function hideCoverPreviewStatus() {
  if (coverPreviewStatus) coverPreviewStatus.hidden = true;
  coverDialogState.previewReady = false;
}

function showCoverStatus(message) {
  if (coverDialogStatus) {
    coverDialogStatus.hidden = false;
    coverDialogStatus.textContent = message;
  }
}

function clearCoverStatus() {
  if (coverDialogStatus) {
    coverDialogStatus.hidden = true;
    coverDialogStatus.textContent = "";
  }
}

function hideCoverReplaceConfirm() {
  coverDialogState.confirmingReplace = false;
  if (coverReplaceConfirm) coverReplaceConfirm.hidden = true;
}

async function handleCoverErrorResponse(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  return payload;
}

function sanitizedCoverMessage(payload) {
  if (payload && payload.error && typeof payload.error.message === "string" && payload.error.message) {
    return payload.error.message;
  }
  return "The cover operation could not be completed.";
}

async function openCoverDialog(item, openerElement) {
  if (!coverDialog) return;
  const targetMediaId = item.media_id;
  const location = selectSupportedAvailableLocation(item);
  if (!location) {
    showCoverStatus("No available source location is present for a cover.");
    return;
  }
  coverOpenerElement = openerElement || document.activeElement;
  const token = ++coverDialogState.requestToken;
  coverDialogState.available = true;
  coverDialogState.openItem = item;
  coverDialogState.currentLocation = location;
  coverDialogState.currentCover = null;
  coverDialogState.durationMs = 0;
  coverDialogState.sourceVersion = "";
  coverDialogState.selectedTimestampMs = 0;
  coverDialogState.previewReady = false;
  coverDialogState.confirmingReplace = false;
  coverDialogState.submitting = false;
  resetCoverDialogView();
  applyCoverImageMode(Boolean(item && item.media_kind === "image"));
  if (typeof coverDialog.showModal === "function") {
    coverDialog.showModal();
  } else {
    coverDialog.setAttribute("open", "");
  }
  if (coverDialogTitle) {
    coverDialogTitle.focus();
  }
  await loadCoverTimeline(token);
}

function closeCoverDialog({ restoreFocus = true } = {}) {
  if (coverPreviewAbortController) {
    coverPreviewAbortController.abort();
    coverPreviewAbortController = null;
  }
  coverDialogState.available = false;
  coverDialogState.openItem = null;
  coverDialogState.currentLocation = null;
  coverDialogState.sourceVersion = "";
  coverDialogState.currentCover = null;
  coverDialogState.requestToken += 1;
  coverDialogState.previewToken += 1;
  coverDialogState.acceptToken += 1;
  if (coverDialog && typeof coverDialog.close === "function") {
    coverDialog.close();
  } else if (coverDialog) {
    coverDialog.removeAttribute("open");
  }
  if (restoreFocus && coverOpenerElement) {
    coverOpenerElement.focus();
  }
  coverOpenerElement = null;
}

async function loadCoverTimeline(token) {
  const item = coverDialogState.openItem;
  const location = coverDialogState.currentLocation;
  if (!item || !location) return;
  showCoverLoading();
  try {
    const response = await fetch(
      coverTimelineEndpoint(item.media_id, location.location_id),
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    const payload = await response.json();
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    if (!response.ok) {
      showCoverError(sanitizedCoverMessage(payload));
      return;
    }
    coverDialogState.durationMs = Number(payload.duration_ms) || 0;
    coverDialogState.sourceVersion = typeof payload.source_version === "string"
      ? payload.source_version
      : "";
    applyCoverImageMode(coverTimelineFromPayload(payload));
    if (!coverDialogState.isImage && coverTimelineRange) {
      coverTimelineRange.max = String(Math.max(0, coverDialogState.durationMs - 1));
    }
    updateCoverTimestampReadout(0);
    if (coverTimestampReadout && coverDialogState.isImage) {
      coverTimestampReadout.textContent = "";
    }
    if (coverDurationReadout) {
      coverDurationReadout.textContent = coverDialogState.isImage
        ? ""
        : coverDurationText(coverDialogState.durationMs);
    }
    if (coverSetButton) coverSetButton.disabled = true;
    await loadCoverState(token);
    if (coverContextStillCurrent({ mediaId: item.media_id, token })) {
      showCoverContent();
      if (coverDialogState.isImage) {
        void requestCoverPreview();
      }
    }
  } catch {
    if (coverContextStillCurrent({ mediaId: item.media_id, token })) {
      showCoverError("Cover options could not be loaded.");
    }
  }
}

async function loadCoverState(token) {
  const item = coverDialogState.openItem;
  if (!item) return;
  try {
    const response = await fetch(coverAdminStateEndpoint(item.media_id), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    const payload = await response.json();
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    if (response.ok && payload && payload.has_cover) {
      coverDialogState.currentCover = {
        revision: payload.revision,
        timestamp_ms: payload.timestamp_ms,
        artifact_digest: payload.artifact_digest,
        source_reference: payload.source_reference,
        source_kind: payload.source_kind,
        accepted_at_ms: payload.accepted_at_ms,
        thumbnail_state: payload.thumbnail_state,
        artifact_state: payload.artifact_state,
      };
    } else {
      coverDialogState.currentCover = null;
    }
    renderCoverCurrent();
  } catch {
    coverDialogState.currentCover = null;
    renderCoverCurrent();
  }
}

function renderCoverCurrent() {
  if (!coverCurrent) return;
  const cover = coverDialogState.currentCover;
  if (!cover) {
    coverCurrent.hidden = true;
    return;
  }
  coverCurrent.hidden = false;
  if (coverCurrentThumbnail) {
    coverCurrentThumbnail.replaceChildren();
    if (cover.thumbnail_state === "ready" && coverDialogState.openItem) {
      const image = document.createElement("img");
      image.src = mediaCoverThumbnailUrl(coverDialogState.openItem.media_id);
      image.alt = "Current cover";
      image.loading = "lazy";
      image.onerror = () => {
        image.remove();
      };
      coverCurrentThumbnail.appendChild(image);
    } else {
      const note = document.createElement("span");
      note.textContent = "Cover selected";
      coverCurrentThumbnail.appendChild(note);
    }
  }
  if (coverCurrentTimestamp) {
    if (coverDialogState.isImage) {
      coverCurrentTimestamp.hidden = true;
      coverCurrentTimestamp.textContent = "";
    } else {
      coverCurrentTimestamp.hidden = false;
      coverCurrentTimestamp.textContent = `Set at ${coverReadoutText(cover.timestamp_ms)}`;
    }
  }
}

function updateCoverTimestampReadout(ms) {
  const maximum = Math.max(0, coverDialogState.durationMs - 1);
  const bounded = Math.max(0, Math.min(Number.isFinite(ms) ? ms : 0, maximum));
  coverDialogState.selectedTimestampMs = bounded;
  if (coverTimelineRange) coverTimelineRange.value = String(bounded);
  if (coverTimestampReadout) coverTimestampReadout.textContent = coverReadoutText(bounded);
  if (coverSetButton) {
    coverSetButton.disabled = coverDialogState.submitting || !coverDialogState.sourceVersion;
  }
}

function handleCoverRangeInput() {
  if (!coverTimelineRange) return;
  updateCoverTimestampReadout(Number(coverTimelineRange.value) || 0);
  hideCoverReplaceConfirm();
}

function stepCoverTimestamp(deltaMs) {
  updateCoverTimestampReadout(coverDialogState.selectedTimestampMs + deltaMs);
  hideCoverReplaceConfirm();
}

async function requestCoverPreview() {
  const item = coverDialogState.openItem;
  const location = coverDialogState.currentLocation;
  if (!item || !location || !coverDialogState.sourceVersion) return;
  const token = coverDialogState.requestToken;
  const previewToken = ++coverDialogState.previewToken;
  if (coverPreviewAbortController) coverPreviewAbortController.abort();
  const controller = new AbortController();
  coverPreviewAbortController = controller;
  if (coverPreviewButton) coverPreviewButton.disabled = true;
  showCoverPreviewStatus("Preparing preview…");
  const params = new URLSearchParams({
    timestamp_ms: String(coverDialogState.selectedTimestampMs),
    source_version: coverDialogState.sourceVersion,
  });
  try {
    const response = await fetch(
      `${coverFrameEndpoint(item.media_id, location.location_id)}?${params.toString()}`,
      { headers: { Accept: "image/png" }, cache: "no-store", signal: controller.signal },
    );
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    if (previewToken !== coverDialogState.previewToken) return;
    if (!response.ok) {
      const payload = await handleCoverErrorResponse(response);
      if (previewToken !== coverDialogState.previewToken) return;
      showCoverPreviewStatus(sanitizedCoverMessage(payload));
      if (payload && payload.error && payload.error.code === "COVER_SOURCE_CHANGED") {
        void reloadCoverContext();
      }
      return;
    }
    const blob = await response.blob();
    if (previewToken !== coverDialogState.previewToken) return;
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    renderCoverPreview(URL.createObjectURL(blob));
    coverDialogState.previewReady = true;
    showCoverPreviewStatus("Frame preview is available but not saved. Use Set as cover to accept it.");
    if (coverSetButton) coverSetButton.disabled = coverDialogState.submitting;
  } catch {
    if (previewToken === coverDialogState.previewToken && coverContextStillCurrent({ mediaId: item.media_id, token })) {
      showCoverPreviewStatus("Frame preview is not available.");
    }
  } finally {
    if (coverPreviewAbortController === controller) {
      coverPreviewAbortController = null;
    }
    if (coverContextStillCurrent({ mediaId: item.media_id, token })) {
      if (coverPreviewButton) coverPreviewButton.disabled = false;
    }
  }
}

function renderCoverPreview(objectUrl) {
  if (!coverPreviewContainer) return;
  coverPreviewContainer.replaceChildren();
  const image = document.createElement("img");
  image.className = "cover-preview__image";
  image.src = objectUrl;
  image.alt = "Selected cover frame preview";
  image.onload = () => {
    if (coverPreviewContainer) coverPreviewContainer.hidden = false;
  };
  image.onerror = () => {
    if (coverPreviewContainer) coverPreviewContainer.hidden = true;
    showCoverPreviewStatus("Frame preview is not available.");
  };
  coverPreviewContainer.appendChild(image);
}

function handleSetAsCover() {
  if (coverDialogState.submitting) return;
  if (!coverDialogState.sourceVersion) return;
  if (coverDialogState.currentCover && !coverDialogState.confirmingReplace) {
    coverDialogState.confirmingReplace = true;
    if (coverReplaceConfirm) coverReplaceConfirm.hidden = false;
    return;
  }
  void submitCoverAccept();
}

function confirmCoverReplace() {
  coverDialogState.confirmingReplace = true;
  void submitCoverAccept();
}

function cancelCoverReplace() {
  hideCoverReplaceConfirm();
}

async function submitCoverAccept() {
  const item = coverDialogState.openItem;
  const location = coverDialogState.currentLocation;
  if (!item || !location || coverDialogState.submitting) return;
  const token = coverDialogState.requestToken;
  const acceptToken = ++coverDialogState.acceptToken;
  coverDialogState.submitting = true;
  if (coverSetButton) coverSetButton.disabled = true;
  showCoverStatus("Setting cover…");
  const expectedCover = coverDialogState.currentCover
    ? coverDialogState.currentCover.revision
    : 0;
  try {
    const response = await fetch(
      coverMutationEndpoint(item.media_id, location.location_id),
      {
        method: "PUT",
        headers: framenestMutationHeaders({
          "Content-Type": "application/json",
          Accept: "application/json",
        }),
        body: JSON.stringify({
          timestamp_ms: coverSubmittedTimestampMs(
            coverDialogState.isImage,
            coverDialogState.selectedTimestampMs,
          ),
          expected_revision: expectedCover,
          expected_source_version: coverDialogState.sourceVersion,
        }),
      },
    );
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    if (acceptToken !== coverDialogState.acceptToken) return;
    if (response.status === 201 || response.status === 200) {
      const payload = await response.json();
      if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
      handleCoverAcceptSuccess(payload);
      return;
    }
    const payload = await handleCoverErrorResponse(response);
    if (!coverContextStillCurrent({ mediaId: item.media_id, token })) return;
    handleCoverAcceptFailure(payload);
  } catch {
    if (coverContextStillCurrent({ mediaId: item.media_id, token }) && acceptToken === coverDialogState.acceptToken) {
      coverDialogState.submitting = false;
      if (coverSetButton) coverSetButton.disabled = false;
      showCoverStatus("The cover could not be set. Check the server connection and retry.");
    }
  }
}

function handleCoverAcceptSuccess(payload) {
  coverDialogState.submitting = false;
  coverDialogState.confirmingReplace = false;
  if (coverReplaceConfirm) coverReplaceConfirm.hidden = true;
  const replaced = payload && payload.status === "replaced";
  const created = payload && payload.status === "created";
  showCoverStatus(
    created ? "Cover set."
      : replaced ? "Cover replaced."
        : "Cover unchanged.",
  );
  coverDialogState.currentCover = {
    revision: payload ? payload.revision : null,
    timestamp_ms: payload ? payload.timestamp_ms : null,
    artifact_digest: payload ? payload.artifact_digest : null,
    thumbnail_state: payload ? payload.thumbnail_state : "missing",
    source_reference: null,
    source_kind: null,
    accepted_at_ms: null,
    artifact_state: null,
  };
  renderCoverCurrent();
  if (typeof loadCatalog === "function") {
    loadCatalog();
  }
  window.setTimeout(() => {
    if (coverDialogState.available) closeCoverDialog();
  }, 480);
}

function handleCoverAcceptFailure(payload) {
  coverDialogState.submitting = false;
  coverDialogState.confirmingReplace = false;
  if (coverReplaceConfirm) coverReplaceConfirm.hidden = true;
  const code = payload && payload.error && payload.error.code;
  if (code === "COVER_CONFLICT" || code === "COVER_SOURCE_CHANGED") {
    showCoverStatus(sanitizedCoverMessage(payload));
    void reloadCoverContext();
    return;
  }
  showCoverStatus(sanitizedCoverMessage(payload));
  if (code === "COVER_TIMESTAMP_INVALID" || code === "COVER_SOURCE_UNAVAILABLE") {
    if (coverSetButton) coverSetButton.disabled = true;
    return;
  }
  if (coverSetButton) coverSetButton.disabled = false;
}

async function reloadCoverContext() {
  const token = ++coverDialogState.requestToken;
  await loadCoverTimeline(token);
}

if (detailsChooseCoverButton) {
  detailsChooseCoverButton.addEventListener("click", () => {
    if (detailsCurrentItem) {
      openCoverDialog(detailsCurrentItem, detailsChooseCoverButton);
    }
  });
}

if (coverDialog) {
  coverDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeCoverDialog();
    }
  });
  coverDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeCoverDialog);
  });
  coverDialog.addEventListener("click", (event) => {
    if (event.target === coverDialog) {
      closeCoverDialog();
    }
  });
}

if (coverDialogCloseButton) {
  coverDialogCloseButton.addEventListener("click", () => closeCoverDialog());
}

if (coverCancelButton) {
  coverCancelButton.addEventListener("click", () => closeCoverDialog());
}

if (coverTimelineRange) {
  coverTimelineRange.addEventListener("input", handleCoverRangeInput);
}

if (coverStepBackButton) {
  coverStepBackButton.addEventListener("click", () => stepCoverTimestamp(-250));
}

if (coverStepForwardButton) {
  coverStepForwardButton.addEventListener("click", () => stepCoverTimestamp(250));
}

if (coverPreviewButton) {
  coverPreviewButton.addEventListener("click", () => {
    void requestCoverPreview();
  });
}

if (coverSetButton) {
  coverSetButton.addEventListener("click", handleSetAsCover);
}

if (coverReplaceYesButton) {
  coverReplaceYesButton.addEventListener("click", confirmCoverReplace);
}

if (coverReplaceNoButton) {
  coverReplaceNoButton.addEventListener("click", cancelCoverReplace);
}

if (detailsCloseButton) {
  detailsCloseButton.addEventListener("click", () => closeDetailsDialog());
}

if (detailsDialog) {
  detailsDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeDetailsDialog();
    }
  });
  detailsDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeDetailsDialog);
  });
  detailsDialog.addEventListener("click", (event) => {
    if (event.target === detailsDialog) {
      closeDetailsDialog();
    }
  });
}

if (detailsEditButton) {
  detailsEditButton.addEventListener("click", () => {
    if (detailsCurrentItem) {
      const item = detailsCurrentItem;
      closeDetailsDialog();
      handleOpenMetadataWorkspace(item, null);
    }
  });
}

if (metadataDialog) {
  metadataDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      closeMetadataWorkspace();
    }
  });
  metadataDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeMetadataWorkspace);
  });
  metadataDialog.addEventListener("click", (event) => {
    if (event.target === metadataDialog) {
      closeMetadataWorkspace();
    }
  });
}

if (metadataCloseButton) {
  metadataCloseButton.addEventListener("click", () => closeMetadataWorkspace());
}

if (catalogRetryButton) {
  catalogRetryButton.addEventListener("click", () => {
    loadCatalog();
  });
}

if (adminMediaOpenButton) {
  adminMediaOpenButton.addEventListener("click", openAdminMediaBrowser);
}

if (workspaceMediaOpenButton) {
  workspaceMediaOpenButton.addEventListener("click", openWorkspaceMediaBrowser);
}

if (workspaceMediaCloseButton) {
  workspaceMediaCloseButton.addEventListener("click", closeWorkspaceMediaBrowser);
}

if (workspaceMediaRetryButton) {
  workspaceMediaRetryButton.addEventListener("click", () => {
    loadWorkspaceMedia();
  });
}

if (workspaceMediaPrevButton) {
  workspaceMediaPrevButton.addEventListener("click", () => {
    workspaceMediaState.offset = Math.max(0, workspaceMediaState.offset - workspaceMediaState.limit);
    loadWorkspaceMedia();
  });
}

if (workspaceMediaNextButton) {
  workspaceMediaNextButton.addEventListener("click", () => {
    const nextOffset = workspaceMediaState.offset + workspaceMediaState.limit;
    if (nextOffset >= workspaceMediaState.total) return;
    workspaceMediaState.offset = nextOffset;
    loadWorkspaceMedia();
  });
}

if (analysisProposalsOpenButton) {
  analysisProposalsOpenButton.addEventListener("click", openAnalysisProposalsBrowser);
}

if (analysisProposalsCloseButton) {
  analysisProposalsCloseButton.addEventListener("click", closeAnalysisProposalsBrowser);
}

if (analysisProposalsRetryButton) {
  analysisProposalsRetryButton.addEventListener("click", () => {
    loadAnalysisProposals();
  });
}

if (analysisProposalsPrevButton) {
  analysisProposalsPrevButton.addEventListener("click", () => {
    analysisProposalState.offset = Math.max(
      0,
      analysisProposalState.offset - analysisProposalState.limit,
    );
    loadAnalysisProposals();
  });
}

if (analysisProposalsNextButton) {
  analysisProposalsNextButton.addEventListener("click", () => {
    const nextOffset = analysisProposalState.offset + analysisProposalState.limit;
    if (nextOffset >= analysisProposalState.total) return;
    analysisProposalState.offset = nextOffset;
    loadAnalysisProposals();
  });
}

if (adminMediaCloseButton) {
  adminMediaCloseButton.addEventListener("click", closeAdminMediaBrowser);
}

if (adminMediaFilters) {
  adminMediaFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    applyAdminCatalogFilters();
  });
}

if (adminMediaRetryButton) {
  adminMediaRetryButton.addEventListener("click", () => {
    loadAdminCatalog();
  });
}

if (adminCatalogCleanupRetryButton) {
  adminCatalogCleanupRetryButton.addEventListener("click", () => {
    const receiptId = adminCatalogState.pendingCleanupReceiptId
      || adminCatalogCleanupRetryButton.dataset.receiptId;
    retryAdminCatalogRemovalCleanup(receiptId, adminCatalogCleanupRetryButton);
  });
}

if (adminMediaPrevButton) {
  adminMediaPrevButton.addEventListener("click", () => {
    adminCatalogState.offset = Math.max(0, adminCatalogState.offset - adminCatalogState.limit);
    resetAdminBatchForQueryChange();
    loadAdminCatalog();
  });
}

if (adminMediaNextButton) {
  adminMediaNextButton.addEventListener("click", () => {
    const nextOffset = adminCatalogState.offset + adminCatalogState.limit;
    if (nextOffset >= adminCatalogState.total) return;
    adminCatalogState.offset = nextOffset;
    resetAdminBatchForQueryChange();
    loadAdminCatalog();
  });
}

if (adminBatchSelectAll) {
  adminBatchSelectAll.addEventListener("change", () => {
    setAdminPageSelection(adminBatchSelectAll.checked);
  });
}

if (adminBatchPublishButton) {
  adminBatchPublishButton.addEventListener("click", () => {
    startAdminPublishBatch(adminBatchPublishButton);
  });
}

if (adminBatchAnalyzeButton) {
  adminBatchAnalyzeButton.addEventListener("click", () => {
    startAdminAnalysisBatch(adminBatchAnalyzeButton);
  });
}

if (adminBatchClearButton) {
  adminBatchClearButton.addEventListener("click", () => {
    clearAdminBatchSelection();
  });
}

if (adminBatchStopButton) {
  adminBatchStopButton.addEventListener("click", () => {
    requestAdminBatchStop();
  });
}

const identityReady = loadIdentity();
identityReady.then(() => {
  if (!isPublicPublishedAudience()) {
    checkHealth();
    loadAiCapability();
    loadUploadCapability();
    restoreUploadRecovery();
    renderYouTubeClaimCockpit();
    if (typeof kronikaStartNavigation === "function") {
      kronikaStartNavigation();
    } else {
      loadCatalogTags();
      loadCatalog();
    }
  } else {
    loadCatalogTags();
    loadCatalog();
    if (commandSearchInput) commandSearchInput.focus({ preventScroll: true });
  }
});
if (
  globalThis.FrameNestCompanionWeb
  && typeof globalThis.FrameNestCompanionWeb.onHostedChange === "function"
) {
  globalThis.FrameNestCompanionWeb.onHostedChange(() => {
    void loadCatalog();
  });
}
if (
  globalThis.FrameNestCompanionWeb
  && typeof globalThis.FrameNestCompanionWeb.onOpenDetails === "function"
) {
  globalThis.FrameNestCompanionWeb.onOpenDetails((mediaId) => {
    openDetailsDialog({ media_id: mediaId }, detailsCloseButton);
  });
}
identityReady.then(() => {
  restoreYouTubeClaim();
});
window.addEventListener("pagehide", revokePreviewObjectUrls);
window.addEventListener("pagehide", cleanupUploadRuntime);
window.addEventListener("pagehide", invalidateAdminBatchOnTeardown);
window.addEventListener("pagehide", invalidateYouTubeClaimOwnership);

let youtubeRequestState = {
  items: [],
  submitting: false,
  pollTimer: null,
  urlError: "",
  statusMessage: "",
  focusedRequestId: "",
};

function youtubeRequestDialogIsOpen() {
  return Boolean(
    youtubeRequestDialog
    && (
      (typeof youtubeRequestDialog.hasAttribute === "function"
        && youtubeRequestDialog.hasAttribute("open"))
      || youtubeRequestDialog.open === true
    ),
  );
}

function youtubeRequestPhaseLabel(phase) {
  switch (phase) {
    case "queued": return "Queued";
    case "processing": return "Processing";
    case "downloading": return "Downloading";
    case "completed_private": return "Completed (private)";
    case "completed": return "Completed";
    case "failed": return "Failed";
    case "unavailable": return "Unavailable";
    default: return "Unknown";
  }
}

function clearYouTubeRequestPollTimer() {
  if (youtubeRequestState.pollTimer != null) {
    window.clearTimeout(youtubeRequestState.pollTimer);
    youtubeRequestState.pollTimer = null;
  }
}

function stopYouTubeRequestPolling() {
  clearYouTubeRequestPollTimer();
}

function openYouTubeRequestDialog() {
  if (!identityAllowsYouTubeRequest() || !youtubeRequestDialog) return;
  if (typeof youtubeRequestDialog.showModal === "function") {
    youtubeRequestDialog.showModal();
  } else {
    youtubeRequestDialog.setAttribute("open", "");
  }
  youtubeRequestState.statusMessage = "";
  renderYouTubeRequestCockpit();
  refreshYouTubeRequests({ focusSubmit: true });
  if (youtubeRequestDialogTitle) youtubeRequestDialogTitle.focus();
}

function closeYouTubeRequestDialog() {
  stopYouTubeRequestPolling();
  if (!youtubeRequestDialog) return;
  if (typeof youtubeRequestDialog.close === "function") {
    youtubeRequestDialog.close();
  } else {
    youtubeRequestDialog.removeAttribute("open");
  }
}

function renderYouTubeRequestCockpit() {
  const busy = youtubeRequestState.submitting;
  const urlError = youtubeRequestState.urlError || "";
  if (youtubeRequestUrlInput) {
    youtubeRequestUrlInput.disabled = busy;
    youtubeRequestUrlInput.setAttribute("aria-invalid", String(Boolean(urlError)));
    youtubeRequestUrlInput.setAttribute(
      "aria-describedby",
      urlError ? "youtube-request-url-note youtube-request-url-error" : "youtube-request-url-note",
    );
  }
  if (youtubeRequestUrlError) {
    youtubeRequestUrlError.hidden = !urlError;
    youtubeRequestUrlError.textContent = urlError;
  }
  if (youtubeRequestSubmitButton) youtubeRequestSubmitButton.disabled = busy;
  if (youtubeRequestStatus) {
    youtubeRequestStatus.textContent = youtubeRequestState.statusMessage || "";
  }
  if (!youtubeRequestList) return;
  youtubeRequestList.replaceChildren();
  for (const item of youtubeRequestState.items) {
    const li = document.createElement("li");
    li.className = "youtube-request-item";
    li.dataset.phase = item.phase || "unknown";
    li.dataset.requestId = item.request_id || "";
    li.tabIndex = -1;
    const phase = document.createElement("p");
    phase.className = "youtube-request-item__phase";
    phase.textContent = youtubeRequestPhaseLabel(item.phase);
    const url = document.createElement("p");
    url.textContent = item.canonical_url || item.submitted_url || "";
    li.append(phase, url);
    if (item.phase === "completed_private") {
      const privateLabel = document.createElement("p");
      privateLabel.className = "youtube-request-item__private";
      privateLabel.textContent = "Private until an administrator publishes it.";
      li.append(privateLabel);
    }
    if (item.failure_code) {
      const failure = document.createElement("p");
      failure.setAttribute("role", "alert");
      failure.textContent = `Failure: ${item.failure_code}`;
      li.append(failure);
    }
    const actions = document.createElement("div");
    actions.className = "youtube-request-item__actions";
    if (item.phase === "failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.setAttribute("aria-label", `Retry YouTube request ${item.request_id}`);
      retry.addEventListener("click", () => {
        retryYouTubeRequest(item.request_id);
      });
      actions.append(retry);
    }
    if (
      item.media_id
      && (item.phase === "completed" || item.phase === "completed_private")
    ) {
      const openDetails = document.createElement("button");
      openDetails.type = "button";
      openDetails.textContent = "Open Details";
      openDetails.setAttribute("aria-label", `Open details for ${item.request_id}`);
      openDetails.addEventListener("click", () => {
        closeYouTubeRequestDialog();
        const mediaItem = { media_id: item.media_id, id: item.media_id };
        if (typeof openDetailsDialog === "function") {
          openDetailsDialog(mediaItem, openDetails);
        }
      });
      actions.append(openDetails);
    }
    if (actions.childNodes.length) li.append(actions);
    youtubeRequestList.append(li);
  }
}

async function refreshYouTubeRequests({ focusSubmit = false } = {}) {
  if (!identityAllowsYouTubeRequest()) return;
  try {
    const response = await fetch(YOUTUBE_REQUESTS_ENDPOINT, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      youtubeRequestState.statusMessage = "Unable to load YouTube requests.";
      renderYouTubeRequestCockpit();
      return;
    }
    const payload = await response.json();
    youtubeRequestState.items = Array.isArray(payload.items) ? payload.items : [];
    youtubeRequestState.statusMessage = "";
    renderYouTubeRequestCockpit();
    const active = youtubeRequestState.items.some((item) => (
      item.phase === "queued"
      || item.phase === "processing"
      || item.phase === "downloading"
    ));
    if (active && youtubeRequestDialogIsOpen()) {
      clearYouTubeRequestPollTimer();
      youtubeRequestState.pollTimer = window.setTimeout(() => {
        youtubeRequestState.pollTimer = null;
        refreshYouTubeRequests();
      }, 1000);
    } else {
      stopYouTubeRequestPolling();
    }
    if (focusSubmit && youtubeRequestSubmitButton) youtubeRequestSubmitButton.focus();
  } catch {
    youtubeRequestState.statusMessage = "Unable to load YouTube requests.";
    renderYouTubeRequestCockpit();
  }
}

async function submitYouTubeRequest(event) {
  if (event) event.preventDefault();
  if (!identityAllowsYouTubeRequest() || youtubeRequestState.submitting) return;
  const rawUrl = youtubeRequestUrlInput ? youtubeRequestUrlInput.value.trim() : "";
  const validation = validateYouTubeClaimUrl(rawUrl);
  if (!validation.supported) {
    youtubeRequestState.urlError = validation.message || "Enter a supported YouTube URL.";
    renderYouTubeRequestCockpit();
    if (youtubeRequestUrlInput) youtubeRequestUrlInput.focus();
    return;
  }
  youtubeRequestState.urlError = "";
  youtubeRequestState.submitting = true;
  youtubeRequestState.statusMessage = "Submitting YouTube request...";
  renderYouTubeRequestCockpit();
  try {
    const response = await fetch(YOUTUBE_REQUESTS_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        url: rawUrl,
        confirmation_method: "interactive",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = payload && payload.error && payload.error.code;
      youtubeRequestState.statusMessage = (payload && payload.error && payload.error.message)
        || "YouTube request failed.";
      if (code === "YOUTUBE_REQUEST_INVALID_URL") {
        youtubeRequestState.urlError = "Enter a supported YouTube URL.";
      }
      return;
    }
    youtubeRequestState.focusedRequestId = payload.request_id || "";
    youtubeRequestState.statusMessage = response.status === 201
      ? "YouTube request accepted."
      : "Existing YouTube request reused.";
    if (youtubeRequestUrlInput) youtubeRequestUrlInput.value = "";
    await refreshYouTubeRequests();
  } catch {
    youtubeRequestState.statusMessage = "YouTube request failed.";
  } finally {
    youtubeRequestState.submitting = false;
    renderYouTubeRequestCockpit();
    if (youtubeRequestSubmitButton) youtubeRequestSubmitButton.focus();
  }
}

async function retryYouTubeRequest(requestId) {
  if (!identityAllowsYouTubeRequest() || youtubeRequestState.submitting || !requestId) return;
  youtubeRequestState.submitting = true;
  youtubeRequestState.statusMessage = "Retrying YouTube request...";
  renderYouTubeRequestCockpit();
  try {
    const response = await fetch(
      `${YOUTUBE_REQUESTS_ENDPOINT}/${encodeURIComponent(requestId)}/retry`,
      {
        method: "POST",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({ confirmation_method: "interactive" }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      youtubeRequestState.statusMessage = (payload && payload.error && payload.error.message)
        || "YouTube request retry failed.";
      return;
    }
    youtubeRequestState.focusedRequestId = payload.request_id || requestId;
    youtubeRequestState.statusMessage = "YouTube request retry accepted.";
    await refreshYouTubeRequests();
  } catch {
    youtubeRequestState.statusMessage = "YouTube request retry failed.";
  } finally {
    youtubeRequestState.submitting = false;
    renderYouTubeRequestCockpit();
  }
}

if (youtubeRequestOpenButton) {
  youtubeRequestOpenButton.addEventListener("click", () => openYouTubeRequestDialog());
}
if (youtubeRequestCloseButton) {
  youtubeRequestCloseButton.addEventListener("click", () => closeYouTubeRequestDialog());
}
if (youtubeRequestForm) {
  youtubeRequestForm.addEventListener("submit", (event) => {
    submitYouTubeRequest(event);
  });
}
if (youtubeRequestDialog) {
  youtubeRequestDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeYouTubeRequestDialog();
  });
}


/* ------------------------------------------------------------------ X cockpit */
const X_REQUESTS_ENDPOINT = "/api/x/requests";
const X_ADMIN_ENDPOINT = "/api/admin/x/requests";

function framenestJSONHeaders() {
  return { Accept: "application/json", "Content-Type": "application/json" };
}

const xRequestOpenButton = document.querySelector("#x-request-open-button");
const xRequestDialog = document.querySelector("#x-request-dialog");
const xRequestCloseButton = document.querySelector("#x-request-close-button");
const xRequestForm = document.querySelector("#x-request-form");
const xRequestUrlInput = document.querySelector("#x-request-url");
const xRequestUrlError = document.querySelector("#x-request-url-error");
const xRequestStatus = document.querySelector("#x-request-status");
const xRequestList = document.querySelector("#x-request-list");
const xRequestSubmitButton = document.querySelector("#x-request-submit-button");

const xAdminOpenButton = document.querySelector("#x-admin-open-button");
const xAdminDialog = document.querySelector("#x-admin-dialog");
const xAdminCloseButton = document.querySelector("#x-admin-close-button");
const xAdminForm = document.querySelector("#x-admin-form");
const xAdminClaimId = document.querySelector("#x-admin-claim-id");
const xAdminError = document.querySelector("#x-admin-error");
const xAdminReview = document.querySelector("#x-admin-review");

const xRequestState = { items: [], statusMessage: "", submitting: false, pollTimer: null };

function identityAllowsXRequest() {
  return isWorkspaceAudience() && identityHasCapability("x.request");
}
function identityAllowsXAdmin() {
  return isWorkspaceAudience() && identityHasCapability("x.acquire");
}

function xPhaseLabel(phase) {
  switch (phase) {
    case "queued": return "Queued";
    case "processing": return "Processing";
    case "failed": return "Failed";
    case "completed": return "Completed";
    case "completed_private": return "Completed (private)";
    case "unavailable": return "Unavailable";
    default: return phase || "Unknown";
  }
}

function setXRequestStatus(message) {
  if (xRequestStatus) xRequestStatus.textContent = message || "";
}

function renderXRequestCockpit() {
  if (!xRequestList) return;
  xRequestStatus && (xRequestStatus.textContent = xRequestState.statusMessage || "");
  xRequestList.textContent = "";
  if (!xRequestState.items.length) {
    const empty = document.createElement("li");
    empty.className = "youtube-request-empty";
    empty.textContent = "No X requests yet.";
    xRequestList.appendChild(empty);
    return;
  }
  for (const item of xRequestState.items) {
    const li = document.createElement("li");
    li.className = "youtube-request-item";
    li.dataset.state = item.state || "";
    const header = document.createElement("div");
    header.className = "youtube-request-row";
    const phase = document.createElement("span");
    phase.className = "youtube-request-phase";
    phase.textContent = xPhaseLabel(item.phase);
    phase.setAttribute("data-phase", item.phase || "");
    const url = document.createElement("span");
    url.className = "youtube-request-url";
    url.textContent = item.canonical_url || item.submitted_url || "X post";
    header.appendChild(phase);
    header.appendChild(url);
    li.appendChild(header);
    if (item.title) {
      const t = document.createElement("div");
      t.className = "youtube-request-meta";
      t.textContent = item.title;
      li.appendChild(t);
    }
    if (Array.isArray(item.assets) && item.assets.length) {
      const assets = document.createElement("ul");
      assets.className = "x-asset-list";
      for (const asset of item.assets) {
        const a = document.createElement("li");
        a.className = "x-asset-item";
        a.dataset.state = asset.state || "";
        const label = document.createElement("span");
        label.textContent = `#${asset.ordinal + 1} ${asset.media_type} — ${asset.state}`;
        a.appendChild(label);
        if (asset.failure_code) {
          const err = document.createElement("span");
          err.className = "youtube-claim-errortext";
          err.textContent = asset.failure_code;
          a.appendChild(err);
        }
        if (asset.media_id) {
          const open = document.createElement("button");
          open.type = "button";
          open.className = "youtube-request-action";
          open.textContent = "Open result";
          open.addEventListener("click", () => openPrivateDetails(asset.media_id));
          a.appendChild(open);
        }
        assets.appendChild(a);
      }
      li.appendChild(assets);
    }
    const actions = document.createElement("div");
    actions.className = "youtube-request-actions";
    if (item.can_retry) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "youtube-request-action";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => retryXRequest(item.request_id));
      actions.appendChild(retry);
    }
    li.appendChild(actions);
    xRequestList.appendChild(li);
  }
}

function openPrivateDetails(mediaId, openerElement) {
  if (!mediaId) return;
  if (xRequestDialog && typeof xRequestDialog.close === "function") {
    xRequestDialog.close();
  }
  if (typeof openDetailsDialog === "function") {
    const element = openerElement || document.activeElement;
    openDetailsDialog({ media_id: mediaId }, element, { playWhenReady: false });
  } else {
    window.location.hash = `#/details/${encodeURIComponent(mediaId)}`;
  }
}

async function refreshXRequests({ focusSubmit } = {}) {
  if (!identityAllowsXRequest() || !xRequestList) return;
  try {
    const response = await fetch(X_REQUESTS_ENDPOINT + "?limit=20", { headers: framenestJSONHeaders() });
    if (!response.ok) {
      setXRequestStatus("Could not load X requests.");
      return;
    }
    const payload = await response.json();
    xRequestState.items = Array.isArray(payload.items) ? payload.items : [];
    renderXRequestCockpit();
    if (focusSubmit && xRequestUrlInput) xRequestUrlInput.focus();
  } catch (_err) {
    setXRequestStatus("Could not load X requests.");
  }
}

async function submitXRequest(event) {
  event.preventDefault();
  if (!identityAllowsXRequest() || xRequestState.submitting) return;
  const url = xRequestUrlInput && xRequestUrlInput.value.trim();
  if (!url) return;
  xRequestState.submitting = true;
  if (xRequestSubmitButton) xRequestSubmitButton.disabled = true;
  if (xRequestUrlError) xRequestUrlError.hidden = true;
  try {
    const response = await fetch(X_REQUESTS_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders(framenestJSONHeaders()),
      body: JSON.stringify({ url }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = (payload.error && payload.error.code) || "X_REQUEST_ERROR";
      setXRequestStatus(typeof mapXRequestError === "function" ? mapXRequestError(code) : code || "Request failed.");
      return;
    }
    xRequestState.statusMessage = "";
    setXRequestStatus("Submitted. Processing locally.");
    await refreshXRequests({ focusSubmit: false });
  } catch (_err) {
    setXRequestStatus("Could not submit X request.");
  } finally {
    xRequestState.submitting = false;
    if (xRequestSubmitButton) xRequestSubmitButton.disabled = false;
  }
}

async function retryXRequest(requestId) {
  if (!requestId || !identityAllowsXRequest()) return;
  try {
    await fetch(`${X_REQUESTS_ENDPOINT}/${encodeURIComponent(requestId)}/retry`, {
      method: "POST",
      headers: framenestMutationHeaders(framenestJSONHeaders()),
    });
    setXRequestStatus("Retrying.");
    await refreshXRequests({ focusSubmit: false });
  } catch (_err) {
    setXRequestStatus("Could not retry X request.");
  }
}

function openXRequestDialog() {
  if (!identityAllowsXRequest() || !xRequestDialog) return;
  if (typeof xRequestDialog.showModal === "function") {
    xRequestDialog.showModal();
  } else {
    xRequestDialog.setAttribute("open", "");
  }
  xRequestState.statusMessage = "";
  renderXRequestCockpit();
  refreshXRequests({ focusSubmit: true });
  const title = xRequestDialog.querySelector(".upload-dialog__title");
  if (title) title.focus();
}
function closeXRequestDialog() {
  if (!xRequestDialog) return;
  if (typeof xRequestDialog.close === "function") {
    xRequestDialog.close();
  } else {
    xRequestDialog.removeAttribute("open");
  }
}

function openXAdminDialog() {
  if (!identityAllowsXAdmin() || !xAdminDialog) return;
  if (typeof xAdminDialog.showModal === "function") {
    xAdminDialog.showModal();
  } else {
    xAdminDialog.setAttribute("open", "");
  }
  if (xAdminError) xAdminError.hidden = true;
  if (xAdminReview) xAdminReview.textContent = "";
  if (xAdminClaimId) xAdminClaimId.focus();
}
function closeXAdminDialog() {
  if (!xAdminDialog) return;
  if (typeof xAdminDialog.close === "function") {
    xAdminDialog.close();
  } else {
    xAdminDialog.removeAttribute("open");
  }
}

async function reviewXClaim(event) {
  event.preventDefault();
  if (!identityAllowsXAdmin() || !xAdminClaimId) return;
  const claimId = xAdminClaimId.value.trim();
  if (!claimId) return;
  if (xAdminError) xAdminError.hidden = true;
  if (xAdminReview) xAdminReview.textContent = "Reviewing…";
  try {
    const response = await fetch(`${X_ADMIN_ENDPOINT}/${encodeURIComponent(claimId)}`, {
      headers: framenestJSONHeaders(),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (xAdminError) {
        xAdminError.textContent = (payload.error && payload.error.message) || "Claim not found.";
        xAdminError.hidden = false;
      }
      if (xAdminReview) xAdminReview.textContent = "";
      return;
    }
    renderXAdminReview(payload, claimId);
  } catch (_err) {
    if (xAdminError) {
      xAdminError.textContent = "Could not review X claim.";
      xAdminError.hidden = false;
    }
    if (xAdminReview) xAdminReview.textContent = "";
  }
}

function renderXAdminReview(claim, claimId) {
  if (!xAdminReview) return;
  const parts = [];
  const h = document.createElement("h3");
  h.className = "x-admin-heading";
  h.textContent = "X claim " + (claim.claim_id || claimId);
  parts.push(h);
  const meta = document.createElement("dl");
  meta.className = "x-admin-meta";
  const add = (k, v) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v || "—";
    meta.appendChild(dt); meta.appendChild(dd);
  };
  add("State", claim.state);
  add("Post ID", claim.x_post_id);
  add("Acquisition source", "x_manual_claim");
  add("Canonical URL", claim.canonical_url);
  add("Status", xPhaseLabel(claim.phase));
  add("Assets", `${claim.success_count || 0} / ${claim.discovered_asset_count || 0}`);
  add("Failure", claim.failure_code || "none");
  parts.push(meta);
  if (claim.source_author_handle || claim.source_author_display_name) {
    const creator = document.createElement("div");
    creator.className = "x-admin-creator";
    const buffer = [claim.source_author_handle || ""];
    if (claim.source_author_handle && claim.source_author_display_name) buffer.unshift("Creator");
    creator.textContent = "Creator: " + (claim.source_author_display_name || claim.source_author_handle);
    parts.push(creator);
  }
  if (Array.isArray(claim.assets) && claim.assets.length) {
    const list = document.createElement("ul");
    list.className = "x-admin-assets";
    for (const asset of claim.assets) {
      const li = document.createElement("li");
      li.dataset.state = asset.state || "";
      const text = `#${asset.ordinal + 1} ${asset.media_type} — ${asset.state}` + (asset.failure_code ? ` (${asset.failure_code})` : "");
      const label = document.createElement("span");
      label.textContent = text;
      li.appendChild(label);
      if (asset.media_id) {
        const open = document.createElement("button");
        open.type = "button";
        open.className = "youtube-request-action";
        open.textContent = "Open media";
        open.addEventListener("click", () => openPrivateDetails(asset.media_id));
        li.appendChild(open);
      }
      list.appendChild(li);
    }
    parts.push(list);
  }
  xAdminReview.textContent = "";
  for (const p of parts) xAdminReview.appendChild(p);
}

if (xRequestOpenButton) xRequestOpenButton.addEventListener("click", openXRequestDialog);
if (xRequestCloseButton) xRequestCloseButton.addEventListener("click", closeXRequestDialog);
if (xRequestForm) xRequestForm.addEventListener("submit", submitXRequest);
if (xAdminOpenButton) xAdminOpenButton.addEventListener("click", openXAdminDialog);
if (xAdminCloseButton) xAdminCloseButton.addEventListener("click", closeXAdminDialog);
if (xAdminForm) xAdminForm.addEventListener("submit", reviewXClaim);

/* --- Administrator AI provider surface --- */

const AI_ADMIN_PROVIDERS_ENDPOINT = "/api/admin/ai/providers";
const AI_ADMIN_SELECTION_ENDPOINT = "/api/admin/ai/active-selection";
const AI_ADMIN_PING_ENDPOINT = "/api/admin/ai/ping";
const AI_ADMIN_PONG_ENDPOINT = "/api/admin/ai/pong";
const AI_PROVIDER_DECLARED_PROTOCOL = "openai-chat-completions";
const AI_PROVIDER_BUILTIN_IDS = new Set(["nvidia-nim", "vercel-ai-gateway"]);

let lastFocusedElementBeforeAiProviders = null;
let aiProviderPongArmed = false;
let aiProvidersState = {
  loaded: false,
  providers: [],
  activeProviderId: null,
  activeModelId: null,
  configurationSource: "",
  selectedProviderId: "",
  message: "",
  errorMessage: "",
  busy: false,
  revision: "",
};

function identityAllowsProviderAdministration() {
  return identityState.resolved
    && isWorkspaceAudience()
    && identityHasCapability("provider.operate")
    && (identityState.available || identityState.audience === "trusted_loopback");
}

function aiProviderSourceLabel(source) {
  return source === "declared" ? "Declared" : "Built-in";
}

function aiProviderCredentialHint(provider) {
  const envName = provider && typeof provider.credential_env === "string"
    ? provider.credential_env.trim()
    : "";
  const suffix = envName ? ` Set ${envName} for the Kronika server process.` : "";
  return `Credential available to this process: no.${suffix}`;
}

function aiProviderLastTestLabel(provider) {
  const lastTest = provider && provider.last_test;
  if (lastTest && typeof lastTest.status === "string") {
    return `Last ping: ${lastTest.status}`;
  }
  return "Last ping: not tested";
}

function aiProviderLastProbeLabel(provider) {
  const probe = provider && provider.last_vision_probe;
  if (!probe || typeof probe.status !== "string") {
    return "Last vision test: not run";
  }
  if (probe.status === "mismatch") {
    const observed = typeof probe.observed_color === "string" && probe.observed_color
      ? ` (observed ${probe.observed_color})`
      : "";
    return `Last vision test: mismatch${observed}`;
  }
  return `Last vision test: ${probe.status}`;
}

function aiProviderRowActions(provider, activeProviderId) {
  if (!provider || typeof provider !== "object") {
    return {
      canEdit: false,
      canDelete: false,
      canActivate: false,
      canPing: false,
      canTestVision: false,
    };
  }
  const declared = provider.source === "declared";
  const credentialAvailable = provider.credential_available === true;
  return {
    canEdit: declared,
    canDelete: declared && provider.provider_id !== activeProviderId,
    canActivate: true,
    canPing: credentialAvailable,
    canTestVision: credentialAvailable && provider.supports_vision === true,
  };
}

function aiProviderStatusMessage(code, fallback) {
  switch (code) {
    case "AI_PROVIDER_AUTHENTICATION_FAILED":
      return "The provider rejected the credential or this model is not included in your subscription.";
    case "AI_PROVIDER_RATE_LIMITED":
      return "The provider rate limit was reached. Try again later.";
    case "AI_PROVIDER_MODEL_UNAVAILABLE":
      return "The configured model is not available to this provider.";
    case "AI_PROVIDER_UNAVAILABLE":
      return "The provider is not reachable right now.";
    case "AI_PROVIDER_INVALID_RESPONSE":
      return "The provider returned an invalid response.";
    case "AI_PROVIDER_FAILED":
      return "The provider request failed.";
    case "AI_PROVIDER_BUSY":
      return "Another AI provider operation is already running.";
    case "AI_PROVIDER_BUILTIN":
      return "Built-in providers cannot be edited or deleted.";
    case "AI_PROVIDER_ACTIVE":
      return "The active provider cannot be deleted; select another provider first.";
    case "AI_PROVIDER_NOT_CONFIGURED":
      return "The provider credential is not available to this process.";
    case "AI_MODEL_CAPABILITY_MISSING":
      return "The selected model does not declare vision support.";
    case "CLOUD_CONFIRMATION_REQUIRED":
      return "Explicit color-test confirmation is required.";
    case "AI_CONFIG_UNAVAILABLE":
      return "The AI provider configuration could not be read or saved.";
    default:
      return fallback || "The AI provider request failed.";
  }
}

function aiProviderResponseMessage(payload, fallback) {
  const error = payload && payload.error;
  if (error && typeof error.message === "string" && error.message.trim()) {
    return error.message.trim();
  }
  const code = error && typeof error.code === "string" ? error.code : "";
  return aiProviderStatusMessage(code, fallback);
}

function sortedJsonValue(value) {
  if (Array.isArray(value)) {
    return value.map((entry) => sortedJsonValue(entry));
  }
  if (value && typeof value === "object") {
    const sorted = {};
    for (const key of Object.keys(value).sort()) {
      sorted[key] = sortedJsonValue(value[key]);
    }
    return sorted;
  }
  return value;
}

function buildAiProviderRecordPayload({ name, baseUrl, credentialEnv, models }) {
  const normalizedModels = {};
  for (const model of Array.isArray(models) ? models : []) {
    const modelId = model && typeof model.modelId === "string" ? model.modelId.trim() : "";
    if (!modelId) continue;
    const declaredName = model && typeof model.displayName === "string" && model.displayName.trim()
      ? model.displayName.trim()
      : modelId;
    normalizedModels[modelId] = {
      name: declaredName,
      capabilities: model && model.visionInput ? ["vision_input"] : [],
    };
  }
  return {
    name: typeof name === "string" ? name.trim() : "",
    protocol: AI_PROVIDER_DECLARED_PROTOCOL,
    base_url: typeof baseUrl === "string" ? baseUrl.trim() : "",
    credential_env: typeof credentialEnv === "string" ? credentialEnv.trim() : "",
    models: normalizedModels,
  };
}

function formatAiProviderJsonPreview(record) {
  return JSON.stringify(sortedJsonValue(record), null, 2);
}

function aiProviderModelRows() {
  if (!aiProviderModelsList || typeof aiProviderModelsList.querySelectorAll !== "function") {
    return [];
  }
  const rows = [];
  for (const row of aiProviderModelsList.querySelectorAll("[data-ai-provider-model-row]")) {
    const modelIdInput = row.querySelector("[data-ai-provider-model-id]");
    const nameInput = row.querySelector("[data-ai-provider-model-name]");
    const visionInput = row.querySelector("[data-ai-provider-model-vision]");
    rows.push({
      modelId: modelIdInput ? modelIdInput.value : "",
      displayName: nameInput ? nameInput.value : "",
      visionInput: Boolean(visionInput && visionInput.checked),
    });
  }
  return rows;
}

function collectAiProviderFormRecord() {
  return buildAiProviderRecordPayload({
    name: aiProviderNameInput ? aiProviderNameInput.value : "",
    baseUrl: aiProviderBaseUrlInput ? aiProviderBaseUrlInput.value : "",
    credentialEnv: aiProviderCredentialEnvInput ? aiProviderCredentialEnvInput.value : "",
    models: aiProviderModelRows(),
  });
}

function renderAiProviderJsonPreview() {
  if (!aiProvidersJsonPreview) return;
  aiProvidersJsonPreview.textContent = formatAiProviderJsonPreview(collectAiProviderFormRecord());
}

function handleAiProviderFormInput() {
  renderAiProviderJsonPreview();
}

function providerById(providerId) {
  if (!providerId) return null;
  return aiProvidersState.providers.find(
    (provider) => provider && provider.provider_id === providerId,
  ) || null;
}

function activeProviderEntry() {
  return providerById(aiProvidersState.activeProviderId);
}

function framenestResponseRevision(response) {
  if (!response || !response.headers || typeof response.headers.get !== "function") {
    return "";
  }
  const raw = response.headers.get("etag");
  if (typeof raw !== "string") return "";
  const trimmed = raw.trim();
  if (!trimmed.startsWith('"') || !trimmed.endsWith('"') || trimmed.length < 2) {
    return "";
  }
  return trimmed.slice(1, -1);
}

function framenestRevisionHeader(revision) {
  return typeof revision === "string" && revision ? { "If-Match": `"${revision}"` } : {};
}

function invalidateAiProvidersRevision() {
  aiProvidersState.revision = "";
}

function applyAiProvidersRevision(response) {
  const revision = framenestResponseRevision(response);
  if (revision) {
    aiProvidersState.revision = revision;
  }
  return revision;
}

function invalidateResearchSettingsRevision() {
  if (typeof researchSettingsState === "undefined") return;
  researchSettingsState.revision = "";
  researchSettingsState.stale = true;
}

function applyAiProvidersPayload(payload, revision) {
  const providers = payload && Array.isArray(payload.providers) ? payload.providers : [];
  aiProvidersState.loaded = true;
  aiProvidersState.providers = providers;
  if (typeof revision === "string" && revision) {
    aiProvidersState.revision = revision;
  }
  aiProvidersState.activeProviderId = payload && typeof payload.active_provider_id === "string"
    ? payload.active_provider_id
    : null;
  aiProvidersState.activeModelId = payload && typeof payload.active_model_id === "string"
    ? payload.active_model_id
    : null;
  aiProvidersState.configurationSource = payload && typeof payload.configuration_source === "string"
    ? payload.configuration_source
    : "";
  if (!aiProvidersState.selectedProviderId && aiProvidersState.activeProviderId) {
    aiProvidersState.selectedProviderId = aiProvidersState.activeProviderId;
  }
}

async function fetchAiProvidersList() {
  const response = await fetch(AI_ADMIN_PROVIDERS_ENDPOINT, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(aiProviderResponseMessage(payload, "The AI provider list is unavailable."));
  }
  return { payload, revision: framenestResponseRevision(response) };
}

async function loadAiProviders() {
  aiProvidersState.errorMessage = "";
  try {
    const loaded = await fetchAiProvidersList();
    applyAiProvidersPayload(loaded.payload, loaded.revision);
  } catch (error) {
    aiProvidersState.loaded = false;
    aiProvidersState.providers = [];
    aiProvidersState.activeProviderId = null;
    aiProvidersState.activeModelId = null;
    aiProvidersState.errorMessage = error && error.message
      ? error.message
      : "The AI provider list is unavailable.";
  }
  renderAiProvidersState();
}

function setAiProviderButtonDisabled(button, disabled) {
  if (button) button.disabled = Boolean(disabled);
}

function setAiProvidersBusy(busy, message) {
  aiProvidersState.busy = Boolean(busy);
  if (aiProvidersDialog && typeof aiProvidersDialog.setAttribute === "function") {
    aiProvidersDialog.setAttribute("aria-busy", aiProvidersState.busy ? "true" : "false");
  }
  setAiProviderButtonDisabled(aiProviderPingButton, aiProvidersState.busy);
  setAiProviderButtonDisabled(aiProviderPongButton, aiProvidersState.busy);
  if (message !== undefined) {
    aiProvidersState.message = aiProvidersState.busy ? message : "";
  }
  renderAiProvidersStatusLine();
  if (!aiProvidersState.busy) {
    renderAiProviderActionControls();
  }
}

function renderAiProvidersSummary() {
  if (!aiProvidersActiveSummary) return;
  const lines = [];
  const active = activeProviderEntry();
  if (active) {
    lines.push(`Active provider: ${active.display_name || active.provider_id} (${active.provider_id})`);
    lines.push(`Model: ${active.selected_model_id || aiProvidersState.activeModelId || "not selected"}`);
    lines.push(`Credential available to this process: ${active.credential_available ? "yes" : "no"}`);
  } else {
    lines.push("Active provider: none");
    lines.push("Credential available to this process: no");
  }
  if (aiProvidersState.configurationSource === "environment") {
    lines.push("Warning: an environment override currently shadows these settings.");
  }
  aiProvidersActiveSummary.textContent = lines.join("\n");
}

function renderAiProvidersStatusLine() {
  if (!aiProvidersStatus) return;
  if (aiProvidersState.errorMessage) {
    aiProvidersStatus.textContent = aiProvidersState.errorMessage;
  } else if (aiProvidersState.message) {
    aiProvidersStatus.textContent = aiProvidersState.message;
  } else {
    aiProvidersStatus.textContent = aiProvidersState.loaded ? "" : "Loading providers…";
  }
}

function aiProviderFact(text) {
  const line = document.createElement("span");
  line.className = "ai-provider-fact";
  line.textContent = text;
  return line;
}

function aiProviderActionButton(label, handler, disabled, destructive) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = destructive
    ? "ai-provider-button ai-provider-button--danger"
    : "ai-provider-button";
  button.textContent = label;
  button.disabled = Boolean(disabled);
  button.addEventListener("click", handler);
  return button;
}

function buildAiProviderRow(provider) {
  const row = document.createElement("div");
  row.className = "ai-provider-row";
  row.dataset.providerId = provider.provider_id;
  if (provider.provider_id === aiProvidersState.selectedProviderId) {
    row.classList.add("ai-provider-row--selected");
  }
  const head = document.createElement("div");
  head.className = "ai-provider-row__head";
  const name = document.createElement("span");
  name.className = "ai-provider-row__name";
  name.textContent = provider.display_name || provider.provider_id;
  head.appendChild(name);
  const identifier = document.createElement("span");
  identifier.className = "ai-provider-row__id";
  identifier.textContent = provider.provider_id;
  head.appendChild(identifier);
  const badge = document.createElement("span");
  badge.className = provider.source === "declared"
    ? "ai-provider-badge ai-provider-badge--declared"
    : "ai-provider-badge";
  badge.textContent = aiProviderSourceLabel(provider.source);
  head.appendChild(badge);
  if (provider.provider_id === aiProvidersState.activeProviderId) {
    const activeBadge = document.createElement("span");
    activeBadge.className = "ai-provider-badge ai-provider-badge--active";
    activeBadge.textContent = "Active";
    head.appendChild(activeBadge);
  }
  row.appendChild(head);
  const facts = document.createElement("div");
  facts.className = "ai-provider-facts";
  facts.appendChild(aiProviderFact(`Protocol: ${provider.protocol || "unknown"}`));
  facts.appendChild(aiProviderFact(`Base URL: ${provider.base_url || "unknown"}`));
  facts.appendChild(
    aiProviderFact(`Credential environment variable: ${provider.credential_env || "unknown"}`),
  );
  facts.appendChild(
    aiProviderFact(
      provider.credential_available
        ? "Credential available to this process: yes"
        : aiProviderCredentialHint(provider),
    ),
  );
  facts.appendChild(aiProviderFact(aiProviderLastTestLabel(provider)));
  facts.appendChild(aiProviderFact(aiProviderLastProbeLabel(provider)));
  row.appendChild(facts);
  const models = document.createElement("div");
  models.className = "ai-provider-model-chips";
  for (const model of Array.isArray(provider.models) ? provider.models : []) {
    const chip = document.createElement("span");
    const selected = model.model_id === provider.selected_model_id;
    chip.className = selected
      ? "ai-provider-chip ai-provider-chip--selected"
      : "ai-provider-chip";
    const capabilities = Array.isArray(model.capabilities) && model.capabilities.length
      ? ` [${model.capabilities.join(", ")}]`
      : "";
    chip.textContent = `${model.model_id}${selected ? " (selected)" : ""}${capabilities}`;
    models.appendChild(chip);
  }
  row.appendChild(models);
  const actions = document.createElement("div");
  actions.className = "ai-provider-actions";
  const availability = aiProviderRowActions(provider, aiProvidersState.activeProviderId);
  actions.appendChild(
    aiProviderActionButton(
      "Use for analysis",
      () => activateAiProvider(provider.provider_id),
      !availability.canActivate,
    ),
  );
  actions.appendChild(
    aiProviderActionButton(
      "Ping",
      () => runAiProviderPing(provider.provider_id),
      !availability.canPing,
    ),
  );
  actions.appendChild(
    aiProviderActionButton(
      "Test vision",
      () => requestAiProviderPong(provider.provider_id),
      !availability.canTestVision,
    ),
  );
  if (availability.canEdit) {
    actions.appendChild(
      aiProviderActionButton("Edit", () => editAiProvider(provider.provider_id), false),
    );
  }
  if (availability.canDelete) {
    actions.appendChild(
      aiProviderActionButton("Delete", () => deleteAiProvider(provider.provider_id), false, true),
    );
  }
  row.appendChild(actions);
  return row;
}

function renderAiProvidersList() {
  if (!aiProvidersList) return;
  if (typeof aiProvidersList.replaceChildren === "function") {
    aiProvidersList.replaceChildren();
  } else {
    aiProvidersList.textContent = "";
  }
  if (aiProvidersState.errorMessage || aiProvidersState.providers.length === 0) {
    return;
  }
  for (const provider of aiProvidersState.providers) {
    aiProvidersList.appendChild(buildAiProviderRow(provider));
  }
}

function renderAiProviderActionControls() {
  const selection = providerById(aiProvidersState.selectedProviderId);
  const availability = aiProviderRowActions(selection, aiProvidersState.activeProviderId);
  if (!aiProvidersState.busy) {
    setAiProviderButtonDisabled(aiProviderPingButton, !availability.canPing);
    setAiProviderButtonDisabled(aiProviderPongButton, !availability.canTestVision);
  }
  setAiProviderButtonDisabled(aiProviderActivateButton, !selection);
  setAiProviderButtonDisabled(aiProviderDeleteButton, !availability.canDelete);
}

function renderAiProvidersState() {
  renderAiProvidersSummary();
  renderAiProvidersList();
  renderAiProvidersStatusLine();
  renderAiProviderActionControls();
}

async function openAiProvidersDialog() {
  if (!identityAllowsProviderAdministration() || !aiProvidersDialog) return;
  lastFocusedElementBeforeAiProviders = document.activeElement;
  aiProvidersState.selectedProviderId = "";
  aiProvidersState.message = "";
  aiProvidersState.errorMessage = "";
  cancelAiProviderPong();
  resetAiProviderForm();
  renderAiProvidersState();
  if (typeof aiProvidersDialog.showModal === "function") {
    aiProvidersDialog.showModal();
  } else {
    aiProvidersDialog.setAttribute("open", "");
  }
  const title = typeof aiProvidersDialog.querySelector === "function"
    ? aiProvidersDialog.querySelector(".settings-dialog__title")
    : null;
  if (title && typeof title.focus === "function") title.focus();
  await loadAiProviders();
  if (typeof openResearchSettings === "function") {
    openResearchSettings();
  }
}

function closeAiProvidersDialog() {
  if (!aiProvidersDialog) return;
  cancelAiProviderPong();
  if (typeof closeResearchSettings === "function") {
    closeResearchSettings();
  }
  if (typeof aiProvidersDialog.close === "function") {
    aiProvidersDialog.close();
  } else {
    aiProvidersDialog.removeAttribute("open");
  }
  if (lastFocusedElementBeforeAiProviders && typeof lastFocusedElementBeforeAiProviders.focus === "function") {
    lastFocusedElementBeforeAiProviders.focus();
  } else if (aiProvidersButton && typeof aiProvidersButton.focus === "function") {
    aiProvidersButton.focus();
  }
  lastFocusedElementBeforeAiProviders = null;
}

function aiProviderModelRow({ modelId, displayName, visionInput }) {
  const row = document.createElement("div");
  row.className = "ai-provider-model-row";
  row.dataset.aiProviderModelRow = "1";
  const idField = document.createElement("input");
  idField.type = "text";
  idField.placeholder = "Model ID";
  idField.autocomplete = "off";
  idField.spellcheck = false;
  idField.dataset.aiProviderModelId = "1";
  idField.setAttribute("aria-label", "Model ID");
  idField.value = modelId || "";
  idField.addEventListener("input", handleAiProviderFormInput);
  const nameField = document.createElement("input");
  nameField.type = "text";
  nameField.placeholder = "Model display name";
  nameField.autocomplete = "off";
  nameField.dataset.aiProviderModelName = "1";
  nameField.setAttribute("aria-label", "Model display name");
  nameField.value = displayName || "";
  nameField.addEventListener("input", handleAiProviderFormInput);
  const visionLabel = document.createElement("label");
  visionLabel.className = "ai-provider-model-row__check";
  const visionBox = document.createElement("input");
  visionBox.type = "checkbox";
  visionBox.dataset.aiProviderModelVision = "1";
  visionBox.checked = Boolean(visionInput);
  visionBox.addEventListener("change", handleAiProviderFormInput);
  visionLabel.appendChild(visionBox);
  const visionText = document.createElement("span");
  visionText.textContent = "vision_input";
  visionLabel.appendChild(visionText);
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "ai-provider-button ai-provider-button--danger";
  removeButton.textContent = "Remove";
  removeButton.addEventListener("click", () => {
    if (typeof row.remove === "function") row.remove();
    renderAiProviderJsonPreview();
  });
  row.appendChild(idField);
  row.appendChild(nameField);
  row.appendChild(visionLabel);
  row.appendChild(removeButton);
  return row;
}

function renderAiProviderModelRows(models) {
  if (!aiProviderModelsList) return;
  if (typeof aiProviderModelsList.replaceChildren === "function") {
    aiProviderModelsList.replaceChildren();
  } else {
    aiProviderModelsList.textContent = "";
  }
  const entries = Array.isArray(models) && models.length
    ? models
    : [{ model_id: "", display_name: "", capabilities: [] }];
  for (const model of entries) {
    const capabilities = Array.isArray(model.capabilities) ? model.capabilities : [];
    aiProviderModelsList.appendChild(
      aiProviderModelRow({
        modelId: model.model_id || "",
        displayName: model.display_name || "",
        visionInput: capabilities.includes("vision_input"),
      }),
    );
  }
  renderAiProviderJsonPreview();
}

function addAiProviderModelRow() {
  if (!aiProviderModelsList) return;
  aiProviderModelsList.appendChild(
    aiProviderModelRow({ modelId: "", displayName: "", visionInput: true }),
  );
  renderAiProviderJsonPreview();
}

function resetAiProviderForm() {
  if (aiProviderIdInput) aiProviderIdInput.value = "";
  if (aiProviderNameInput) aiProviderNameInput.value = "";
  if (aiProviderBaseUrlInput) aiProviderBaseUrlInput.value = "";
  if (aiProviderCredentialEnvInput) aiProviderCredentialEnvInput.value = "";
  if (aiProviderProtocolInput) aiProviderProtocolInput.value = AI_PROVIDER_DECLARED_PROTOCOL;
  renderAiProviderModelRows([]);
}

function editAiProvider(providerId) {
  const provider = providerById(providerId);
  if (!provider || provider.source !== "declared") {
    aiProvidersState.errorMessage = "Built-in providers cannot be edited.";
    renderAiProvidersState();
    return;
  }
  aiProvidersState.selectedProviderId = provider.provider_id;
  if (aiProviderIdInput) aiProviderIdInput.value = provider.provider_id;
  if (aiProviderNameInput) aiProviderNameInput.value = provider.display_name || "";
  if (aiProviderBaseUrlInput) aiProviderBaseUrlInput.value = provider.base_url || "";
  if (aiProviderCredentialEnvInput) aiProviderCredentialEnvInput.value = provider.credential_env || "";
  if (aiProviderProtocolInput) aiProviderProtocolInput.value = AI_PROVIDER_DECLARED_PROTOCOL;
  renderAiProviderModelRows(provider.models || []);
  renderAiProviderActionControls();
}

function requestAiProviderPong(providerId) {
  const provider = providerById(providerId) || activeProviderEntry();
  if (!provider) {
    aiProvidersState.errorMessage = "Select a provider first.";
    renderAiProvidersState();
    return;
  }
  const availability = aiProviderRowActions(provider, aiProvidersState.activeProviderId);
  if (!availability.canTestVision) {
    aiProvidersState.errorMessage = provider.credential_available
      ? "The selected model does not declare vision support."
      : aiProviderCredentialHint(provider);
    renderAiProvidersState();
    return;
  }
  aiProvidersState.selectedProviderId = provider.provider_id;
  aiProviderPongArmed = true;
  if (aiProviderPongConfirm) aiProviderPongConfirm.hidden = false;
  if (aiProviderPongConfirmNote) {
    aiProviderPongConfirmNote.textContent =
      `Send the color test through ${provider.display_name || provider.provider_id}? `
      + "Kronika sends only a tiny solid-red test square made by Kronika. "
      + "The provider bills image tokens for this request, and no catalog media is used.";
  }
  renderAiProviderActionControls();
}

function cancelAiProviderPong() {
  aiProviderPongArmed = false;
  if (aiProviderPongConfirm) aiProviderPongConfirm.hidden = true;
}

async function activateAiProvider(providerId) {
  const provider = providerById(providerId) || activeProviderEntry();
  if (!provider) return;
  const models = Array.isArray(provider.models) ? provider.models : [];
  const modelId = provider.selected_model_id
    || (models.length ? models[0].model_id : "");
  if (!modelId) {
    aiProvidersState.errorMessage = "The provider declares no model to activate.";
    renderAiProvidersState();
    return;
  }
  aiProvidersState.selectedProviderId = provider.provider_id;
  setAiProvidersBusy(true, "Activating provider…");
  try {
    const response = await fetch(AI_ADMIN_SELECTION_ENDPOINT, {
      method: "PUT",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
        ...framenestRevisionHeader(aiProvidersState.revision),
      }),
      body: JSON.stringify({ provider_id: provider.provider_id, model_id: modelId }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      aiProvidersState.errorMessage = aiProviderResponseMessage(
        payload,
        "The provider selection could not be saved.",
      );
    } else {
      applyAiProvidersRevision(response);
      invalidateResearchSettingsRevision();
      aiProvidersState.activeProviderId = provider.provider_id;
      aiProvidersState.activeModelId = modelId;
      aiProvidersState.message =
        `Active provider: ${provider.display_name || provider.provider_id} (${modelId}).`;
      aiProvidersState.errorMessage = "";
    }
  } catch {
    aiProvidersState.errorMessage = "The server is unreachable. Try activating the provider again.";
  } finally {
    setAiProvidersBusy(false);
  }
  renderAiProvidersState();
  await loadAiProviders();
}

async function saveAiProviderRecord(event) {
  if (event && typeof event.preventDefault === "function") event.preventDefault();
  const providerId = aiProviderIdInput ? aiProviderIdInput.value.trim() : "";
  if (!providerId) {
    aiProvidersState.errorMessage = "A provider ID is required.";
    renderAiProvidersState();
    return;
  }
  if (AI_PROVIDER_BUILTIN_IDS.has(providerId)) {
    aiProvidersState.errorMessage = "Built-in providers cannot be edited.";
    renderAiProvidersState();
    return;
  }
  const record = collectAiProviderFormRecord();
  setAiProvidersBusy(true, "Saving provider record…");
  try {
    const response = await fetch(
      `${AI_ADMIN_PROVIDERS_ENDPOINT}/${encodeURIComponent(providerId)}`,
      {
        method: "PUT",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          "Content-Type": "application/json",
          ...framenestRevisionHeader(aiProvidersState.revision),
        }),
        body: JSON.stringify(record),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      aiProvidersState.errorMessage = aiProviderResponseMessage(
        payload,
        "The provider record could not be saved.",
      );
    } else {
      applyAiProvidersRevision(response);
      invalidateResearchSettingsRevision();
      aiProvidersState.selectedProviderId = providerId;
      aiProvidersState.message = `Provider record saved: ${providerId}.`;
      aiProvidersState.errorMessage = "";
    }
  } catch {
    aiProvidersState.errorMessage = "The server is unreachable. Try saving the record again.";
  } finally {
    setAiProvidersBusy(false);
  }
  renderAiProvidersState();
  await loadAiProviders();
}

async function deleteAiProvider(providerId) {
  const provider = providerById(providerId);
  if (!provider || provider.source !== "declared") {
    aiProvidersState.errorMessage = "Built-in providers cannot be deleted.";
    renderAiProvidersState();
    return;
  }
  if (provider.provider_id === aiProvidersState.activeProviderId) {
    aiProvidersState.errorMessage =
      "The active provider cannot be deleted; select another provider first.";
    renderAiProvidersState();
    return;
  }
  setAiProvidersBusy(true, "Removing provider record…");
  try {
    const response = await fetch(
      `${AI_ADMIN_PROVIDERS_ENDPOINT}/${encodeURIComponent(provider.provider_id)}`,
      {
        method: "DELETE",
        headers: framenestMutationHeaders({
          Accept: "application/json",
          ...framenestRevisionHeader(aiProvidersState.revision),
        }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      aiProvidersState.errorMessage = aiProviderResponseMessage(
        payload,
        "The provider record could not be removed.",
      );
    } else {
      applyAiProvidersRevision(response);
      invalidateResearchSettingsRevision();
      if (aiProvidersState.selectedProviderId === provider.provider_id) {
        aiProvidersState.selectedProviderId = "";
      }
      aiProvidersState.message = `Provider record removed: ${provider.provider_id}.`;
      aiProvidersState.errorMessage = "";
    }
  } catch {
    aiProvidersState.errorMessage = "The server is unreachable. Try removing the record again.";
  } finally {
    setAiProvidersBusy(false);
  }
  renderAiProvidersState();
  await loadAiProviders();
}

async function runAiProviderPing(providerId) {
  const provider = providerById(providerId) || activeProviderEntry();
  if (!provider) {
    aiProvidersState.errorMessage = "Select a provider first.";
    renderAiProvidersState();
    return;
  }
  if (provider.credential_available !== true) {
    aiProvidersState.errorMessage = aiProviderCredentialHint(provider);
    renderAiProvidersState();
    return;
  }
  aiProvidersState.selectedProviderId = provider.provider_id;
  setAiProvidersBusy(true, "Testing connection…");
  try {
    const response = await fetch(AI_ADMIN_PING_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      aiProvidersState.errorMessage = aiProviderResponseMessage(
        payload,
        "The provider connection test failed.",
      );
    } else {
      aiProvidersState.message = `Connection test: ${payload.status || "success"}.`;
      aiProvidersState.errorMessage = "";
    }
  } catch {
    aiProvidersState.errorMessage = "The server is unreachable. Try the connection test again.";
  } finally {
    setAiProvidersBusy(false);
  }
  renderAiProvidersState();
  await loadAiProviders();
}

async function confirmAiProviderPong() {
  if (!aiProviderPongArmed) return;
  const provider = providerById(aiProvidersState.selectedProviderId) || activeProviderEntry();
  if (!provider) return;
  cancelAiProviderPong();
  setAiProvidersBusy(true, "Sending the color test…");
  try {
    const response = await fetch(AI_ADMIN_PONG_ENDPOINT, {
      method: "POST",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ confirm_cloud_upload: true }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      aiProvidersState.errorMessage = aiProviderResponseMessage(
        payload,
        "The color test failed.",
      );
    } else if (payload.status === "mismatch") {
      const observed = typeof payload.observed_color === "string" && payload.observed_color
        ? ` Observed: ${payload.observed_color}.`
        : "";
      aiProvidersState.message = `The model answered, but not with the expected color.${observed}`;
      aiProvidersState.errorMessage = "";
    } else {
      aiProvidersState.message =
        `Vision probe: success. Expected ${payload.expected_color || "red"}, `
        + `observed ${payload.observed_color || "red"}.`;
      aiProvidersState.errorMessage = "";
    }
  } catch {
    aiProvidersState.errorMessage = "The server is unreachable. Try the color test again.";
  } finally {
    setAiProvidersBusy(false);
  }
  renderAiProvidersState();
  await loadAiProviders();
}

if (aiProvidersButton) {
  aiProvidersButton.addEventListener("click", () => {
    void openAiProvidersDialog();
  });
}

if (aiProvidersCloseButton) {
  aiProvidersCloseButton.addEventListener("click", () => closeAiProvidersDialog());
}

if (aiProvidersForm) {
  aiProvidersForm.addEventListener("submit", (event) => {
    void saveAiProviderRecord(event);
  });
}

if (aiProviderAddModelButton) {
  aiProviderAddModelButton.addEventListener("click", addAiProviderModelRow);
}

if (aiProviderSaveButton) {
  aiProviderSaveButton.addEventListener("click", (event) => {
    if (typeof event.preventDefault === "function") event.preventDefault();
    void saveAiProviderRecord(event);
  });
}

if (aiProviderActivateButton) {
  aiProviderActivateButton.addEventListener("click", () => {
    void activateAiProvider(aiProvidersState.selectedProviderId);
  });
}

if (aiProviderPingButton) {
  aiProviderPingButton.addEventListener("click", () => {
    void runAiProviderPing(aiProvidersState.selectedProviderId);
  });
}

if (aiProviderPongButton) {
  aiProviderPongButton.addEventListener("click", () => {
    requestAiProviderPong(aiProvidersState.selectedProviderId);
  });
}

if (aiProviderDeleteButton) {
  aiProviderDeleteButton.addEventListener("click", () => {
    void deleteAiProvider(aiProvidersState.selectedProviderId);
  });
}

if (aiProviderPongCancelButton) {
  aiProviderPongCancelButton.addEventListener("click", cancelAiProviderPong);
}

if (aiProviderPongConfirmButton) {
  aiProviderPongConfirmButton.addEventListener("click", () => {
    void confirmAiProviderPong();
  });
}

for (const aiProviderTextInput of [
  aiProviderIdInput,
  aiProviderNameInput,
  aiProviderBaseUrlInput,
  aiProviderCredentialEnvInput,
]) {
  if (aiProviderTextInput) {
    aiProviderTextInput.addEventListener("input", handleAiProviderFormInput);
  }
}

if (aiProvidersDialog) {
  aiProvidersDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (confirmationOwnsTopmostModal()) return;
      if (aiProviderPongArmed) {
        cancelAiProviderPong();
        return;
      }
      closeAiProvidersDialog();
    }
  });
  aiProvidersDialog.addEventListener("cancel", (event) => {
    handleParentDialogCancel(event, closeAiProvidersDialog);
  });
  aiProvidersDialog.addEventListener("click", (event) => {
    if (event.target === aiProvidersDialog) {
      closeAiProvidersDialog();
    }
  });
}

/* --- Administrator research settings surface --- */

const RESEARCH_SETTINGS_ENDPOINT = "/api/admin/ai/research-settings";
const RESEARCH_SETTINGS_NOTICE =
  "These server settings apply to new Search and Research requests. "
  + "People submitting questions cannot choose a model, endpoint or tools.";
const RESEARCH_SETTINGS_HISTORY_NOTE =
  "Changing the model does not change requests already admitted. "
  + "Disabling pauses new generation; existing requests can still be checked or cancelled.";
const RESEARCH_SETTINGS_CREDENTIAL_NOTE =
  "Credentials are managed on the server. A saved configuration does not prove account access.";

let lastFocusedElementBeforeResearch = null;
let researchSettingsState = {
  loaded: false,
  server: null,
  revision: "",
  configurationPresent: false,
  credentialAvailable: false,
  models: [],
  limits: null,
  draft: null,
  dirty: false,
  stale: false,
  loading: false,
  saving: false,
  confirmArmed: false,
  pendingDraft: null,
  message: "",
  errorMessage: "",
  responseGeneration: 0,
};

function identityAllowsResearchSettings() {
  return identityState.resolved
    && identityState.available
    && isWorkspaceAudience()
    && identityHasCapability("provider.operate");
}

function researchSettingsModelById(modelId) {
  return researchSettingsState.models.find((entry) => entry.model_id === modelId) || null;
}

function researchSettingsFormatUsd(micros) {
  const value = Number(micros);
  if (!Number.isInteger(value) || value < 0) return "";
  const whole = Math.floor(value / 1000000);
  const remainder = String(value % 1000000).padStart(6, "0").replace(/0+$/, "");
  return remainder ? `${whole}.${remainder}` : String(whole);
}

function researchSettingsParseUsd(text) {
  const value = String(text == null ? "" : text).trim();
  const match = /^([0-9]+)(?:\.([0-9]{0,6}))?$/.exec(value);
  if (!match) return null;
  const whole = BigInt(match[1]);
  const fraction = (match[2] || "").padEnd(6, "0");
  const micros = whole * 1000000n + BigInt(fraction || "0");
  if (micros > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(micros);
}

function researchSettingsDraftFromServer(settings) {
  return {
    enabled: settings.enabled === true,
    model_id: typeof settings.model_id === "string" ? settings.model_id : "",
    daily_budget_usd: researchSettingsFormatUsd(settings.daily_budget_usd_micros),
    monthly_budget_usd: researchSettingsFormatUsd(settings.monthly_budget_usd_micros),
    search_reservation_usd: researchSettingsFormatUsd(
      settings.search_budget_reservation_usd_micros,
    ),
    research_reservation_usd: researchSettingsFormatUsd(
      settings.research_budget_reservation_usd_micros,
    ),
  };
}

function researchSettingsPayloadFromDraft(draft) {
  const daily = researchSettingsParseUsd(draft.daily_budget_usd);
  const monthly = researchSettingsParseUsd(draft.monthly_budget_usd);
  const searchReservation = researchSettingsParseUsd(draft.search_reservation_usd);
  const researchReservation = researchSettingsParseUsd(draft.research_reservation_usd);
  if (
    daily === null
    || monthly === null
    || searchReservation === null
    || researchReservation === null
  ) {
    return null;
  }
  return {
    enabled: draft.enabled === true,
    model_id: draft.model_id,
    daily_budget_usd_micros: daily,
    monthly_budget_usd_micros: monthly,
    search_budget_reservation_usd_micros: searchReservation,
    research_budget_reservation_usd_micros: researchReservation,
  };
}

function researchSettingsRequiresConfirmation(previous, next) {
  if (!previous || !next) return false;
  return previous.model_id !== next.model_id
    || previous.daily_budget_usd !== next.daily_budget_usd
    || previous.monthly_budget_usd !== next.monthly_budget_usd
    || previous.search_reservation_usd !== next.search_reservation_usd
    || previous.research_reservation_usd !== next.research_reservation_usd;
}

function researchSettingsStatusMessage(code, fallback) {
  switch (code) {
    case "AI_CONFIG_CONFLICT":
      return "AI settings changed. Reload and review before saving again.";
    case "AI_CONFIG_UNAVAILABLE":
      return "The AI provider configuration could not be read or saved.";
    case "AI_PROVIDER_BUSY":
      return "Another AI provider operation is already running.";
    case "E_CAPABILITY_UNAVAILABLE":
      return "Choose a supported research model from the list.";
    case "E_NOT_CONFIGURED":
      return "Research credentials are not configured on the server.";
    case "VALIDATION_FAILED":
      return "Request validation failed.";
    case "IDENTITY_REQUIRED":
      return "A verified identity is required.";
    case "CAPABILITY_DENIED":
      return "You do not have permission to manage research settings.";
    default:
      return fallback || "The research settings request failed.";
  }
}

function researchSettingsResponseMessage(payload, fallback) {
  const error = payload && payload.error;
  if (error && typeof error.message === "string" && error.message.trim()) {
    return error.message.trim();
  }
  const code = error && typeof error.code === "string" ? error.code : "";
  return researchSettingsStatusMessage(code, fallback);
}

function applyResearchSettingsPayload(payload) {
  researchSettingsState.server = payload;
  researchSettingsState.revision = typeof payload.revision === "string" ? payload.revision : "";
  researchSettingsState.configurationPresent = payload.configuration_present === true;
  researchSettingsState.credentialAvailable = payload.credential_available === true;
  researchSettingsState.models = Array.isArray(payload.models) ? payload.models : [];
  researchSettingsState.limits = payload.limits || null;
  if (payload.settings && typeof payload.settings === "object") {
    researchSettingsState.draft = researchSettingsDraftFromServer(payload.settings);
  }
  researchSettingsState.dirty = false;
  researchSettingsState.stale = false;
  researchSettingsState.loaded = true;
}

function clearResearchSettingsProtectedState() {
  researchSettingsState = {
    loaded: false,
    server: null,
    revision: "",
    configurationPresent: false,
    credentialAvailable: false,
    models: [],
    limits: null,
    draft: null,
    dirty: false,
    stale: false,
    loading: false,
    saving: false,
    confirmArmed: false,
    pendingDraft: null,
    message: "",
    errorMessage: "",
    responseGeneration: 0,
  };
}

function renderResearchSettings() {
  if (!researchSettingsSection) return;
  if (researchSettingsStatus) {
    researchSettingsStatus.textContent = researchSettingsState.errorMessage
      || researchSettingsState.message
      || "";
  }
  if (researchSettingsLoading) {
    researchSettingsLoading.hidden = !researchSettingsState.loading;
  }
  if (researchSettingsModel) {
    researchSettingsModel.textContent = "";
    for (const model of researchSettingsState.models) {
      const option = document.createElement("option");
      option.value = model.model_id;
      option.textContent = `${model.display_name || model.model_id}`
        + (model.selectable ? "" : " (unavailable)");
      researchSettingsModel.appendChild(option);
    }
  }
  const draft = researchSettingsState.draft;
  if (researchSettingsEnabled) draft
    ? (researchSettingsEnabled.checked = draft.enabled === true)
    : (researchSettingsEnabled.checked = false);
  if (researchSettingsModel) draft
    ? (researchSettingsModel.value = draft.model_id)
    : (researchSettingsModel.value = "");
  if (researchSettingsDailyBudget) draft
    ? (researchSettingsDailyBudget.value = draft.daily_budget_usd)
    : (researchSettingsDailyBudget.value = "");
  if (researchSettingsMonthlyBudget) draft
    ? (researchSettingsMonthlyBudget.value = draft.monthly_budget_usd)
    : (researchSettingsMonthlyBudget.value = "");
  if (researchSettingsSearchReservation) draft
    ? (researchSettingsSearchReservation.value = draft.search_reservation_usd)
    : (researchSettingsSearchReservation.value = "");
  if (researchSettingsResearchReservation) draft
    ? (researchSettingsResearchReservation.value = draft.research_reservation_usd)
    : (researchSettingsResearchReservation.value = "");
  if (researchSettingsCatalog) {
    researchSettingsCatalog.textContent = "";
    for (const model of researchSettingsState.models) {
      const row = document.createElement("li");
      row.className = "research-settings-model";
      row.textContent = `${model.display_name || model.model_id} (${model.model_id}) `
        + `— ${model.pinning}, ${model.selectable ? "selectable" : "unavailable"}`;
      researchSettingsCatalog.appendChild(row);
    }
  }
  if (researchSettingsCredential) {
    researchSettingsCredential.textContent = researchSettingsState.credentialAvailable
      ? "Research credential available to the server."
      : "Research credential is not configured on the server.";
  }
  if (researchSettingsConfirm) {
    researchSettingsConfirm.hidden = !researchSettingsState.confirmArmed;
  }
}

async function loadResearchSettings() {
  clearResearchSettingsProtectedState();
  researchSettingsState.loading = true;
  researchSettingsState.draft = null;
  renderResearchSettings();
  const generation = researchSettingsState.responseGeneration;
  try {
    const response = await fetch(RESEARCH_SETTINGS_ENDPOINT, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (generation !== researchSettingsState.responseGeneration) return;
    if (!response.ok) {
      researchSettingsState.errorMessage = researchSettingsResponseMessage(
        payload,
        "Research settings could not be loaded.",
      );
    } else {
      applyResearchSettingsPayload(payload);
      researchSettingsState.errorMessage = "";
    }
  } catch {
    if (generation !== researchSettingsState.responseGeneration) return;
    researchSettingsState.errorMessage = "The server is unreachable. Try loading research settings again.";
  } finally {
    if (generation === researchSettingsState.responseGeneration) {
      researchSettingsState.loading = false;
      renderResearchSettings();
    }
  }
}

function collectResearchSettingsDraft() {
  return {
    enabled: researchSettingsEnabled ? researchSettingsEnabled.checked : false,
    model_id: researchSettingsModel ? researchSettingsModel.value : "",
    daily_budget_usd: researchSettingsDailyBudget ? researchSettingsDailyBudget.value : "",
    monthly_budget_usd: researchSettingsMonthlyBudget ? researchSettingsMonthlyBudget.value : "",
    search_reservation_usd: researchSettingsSearchReservation
      ? researchSettingsSearchReservation.value
      : "",
    research_reservation_usd: researchSettingsResearchReservation
      ? researchSettingsResearchReservation.value
      : "",
  };
}

function researchSettingsConfirmNoteText(previous, next) {
  return `Change the research model from ${previous.model_id} to ${next.model_id}? `
    + `Daily budget ${next.daily_budget_usd} USD, monthly budget ${next.monthly_budget_usd} USD.`;
}

async function requestSaveResearchSettings() {
  if (!identityAllowsResearchSettings() || researchSettingsState.saving) return;
  const draft = collectResearchSettingsDraft();
  const previous = researchSettingsState.draft;
  if (researchSettingsRequiresConfirmation(previous, draft)) {
    researchSettingsState.pendingDraft = draft;
    researchSettingsState.confirmArmed = true;
    if (researchSettingsConfirmNote) {
      researchSettingsConfirmNote.textContent = researchSettingsConfirmNoteText(previous, draft);
    }
    renderResearchSettings();
    return;
  }
  await saveResearchSettings(draft);
}

function cancelResearchSettingsConfirmation() {
  researchSettingsState.confirmArmed = false;
  researchSettingsState.pendingDraft = null;
}

async function confirmResearchSettingsChange() {
  const draft = researchSettingsState.pendingDraft;
  researchSettingsState.confirmArmed = false;
  researchSettingsState.pendingDraft = null;
  if (draft) {
    await saveResearchSettings(draft);
  }
}

async function saveResearchSettings(draft) {
  const payload = researchSettingsPayloadFromDraft(draft);
  if (payload === null) {
    researchSettingsState.errorMessage = "Enter each budget as a decimal USD amount with at most six decimals.";
    renderResearchSettings();
    return;
  }
  if (!researchSettingsState.revision) {
    // A sibling media write invalidated this revision. Never silently adopt a
    // new revision or send a save without one; require an explicit reload.
    researchSettingsState.stale = true;
    researchSettingsState.dirty = true;
    researchSettingsState.errorMessage =
      "AI settings changed. Reload and review before saving again.";
    renderResearchSettings();
    return;
  }
  researchSettingsState.saving = true;
  const generation = researchSettingsState.responseGeneration;
  try {
    const response = await fetch(RESEARCH_SETTINGS_ENDPOINT, {
      method: "PUT",
      headers: framenestMutationHeaders({
        Accept: "application/json",
        "Content-Type": "application/json",
        "If-Match": `"${researchSettingsState.revision}"`,
      }),
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (generation !== researchSettingsState.responseGeneration) return;
    if (response.status === 409) {
      researchSettingsState.errorMessage = researchSettingsResponseMessage(
        body,
        "AI settings changed. Reload and review before saving again.",
      );
      researchSettingsState.dirty = true;
    } else if (!response.ok) {
      researchSettingsState.errorMessage = researchSettingsResponseMessage(
        body,
        "Research settings could not be saved.",
      );
    } else {
      applyResearchSettingsPayload(body);
      if (typeof invalidateAiProvidersRevision === "function") {
        // A research write changes the shared configuration file, so the media
        // section's captured revision is stale until it reloads.
        invalidateAiProvidersRevision();
      }
      researchSettingsState.message = body.changed
        ? "Research settings saved."
        : "Research settings unchanged.";
      researchSettingsState.errorMessage = "";
    }
  } catch {
    if (generation !== researchSettingsState.responseGeneration) return;
    researchSettingsState.errorMessage =
      "The server is unreachable. Reload before trying another save.";
  } finally {
    if (generation === researchSettingsState.responseGeneration) {
      researchSettingsState.saving = false;
      renderResearchSettings();
    }
  }
}

function openResearchSettings() {
  if (!identityAllowsResearchSettings() || !researchSettingsSection) return;
  lastFocusedElementBeforeResearch = document.activeElement;
  if (typeof researchSettingsSection.setAttribute === "function") {
    researchSettingsSection.removeAttribute("hidden");
  }
  void loadResearchSettings();
}

function closeResearchSettings() {
  researchSettingsState.responseGeneration += 1;
  clearResearchSettingsProtectedState();
  if (lastFocusedElementBeforeResearch && typeof lastFocusedElementBeforeResearch.focus === "function") {
    lastFocusedElementBeforeResearch.focus();
  }
}

if (researchSettingsSaveButton) {
  researchSettingsSaveButton.addEventListener("click", (event) => {
    if (typeof event.preventDefault === "function") event.preventDefault();
    void requestSaveResearchSettings();
  });
}

if (researchSettingsReloadButton) {
  researchSettingsReloadButton.addEventListener("click", () => {
    void loadResearchSettings();
  });
}

if (researchSettingsConfirmCancel) {
  researchSettingsConfirmCancel.addEventListener("click", cancelResearchSettingsConfirmation);
}

if (researchSettingsConfirmButton) {
  researchSettingsConfirmButton.addEventListener("click", () => {
    void confirmResearchSettingsChange();
  });
}

for (const researchTextInput of [
  researchSettingsDailyBudget,
  researchSettingsMonthlyBudget,
  researchSettingsSearchReservation,
  researchSettingsResearchReservation,
]) {
  if (researchTextInput) {
    researchTextInput.addEventListener("input", () => {
      researchSettingsState.dirty = true;
      researchSettingsState.confirmArmed = false;
      researchSettingsState.pendingDraft = null;
    });
  }
}

if (researchSettingsModel) {
  researchSettingsModel.addEventListener("change", () => {
    researchSettingsState.dirty = true;
    researchSettingsState.confirmArmed = false;
    researchSettingsState.pendingDraft = null;
  });
}

/* KRONIKA_SHELL_START */
const KRONIKA_ACTIVE_STATES = Object.freeze([
  "admitted",
  "submitting",
  "running",
  "validating",
  "cancel_requested",
]);
const KRONIKA_TERMINAL_STATES = Object.freeze([
  "saved",
  "refused",
  "failed",
  "incomplete",
  "cancelled",
  "timeout",
  "submission_unknown",
]);
const KRONIKA_FILTERS = Object.freeze([
  "all",
  "search",
  "research",
  "media",
  "general",
  "meme",
  "movie",
  "youtube",
]);
const KRONIKA_ATTEMPT_KEY = "kronika.research.attempt.v1";
const KRONIKA_CONSENT_VERSION = "kronika-research-v1";
const KRONIKA_POLL_DELAY_MS = 5000;

function kronikaBlankList() {
  return {
    items: [],
    total: 0,
    offset: 0,
    queryKey: "",
    generation: 0,
    stale: false,
    error: "",
  };
}

const kronikaLists = {
  timeline: kronikaBlankList(),
  questions: kronikaBlankList(),
  records: kronikaBlankList(),
  adminRecords: kronikaBlankList(),
  adminRequests: kronikaBlankList(),
};

const kronikaAttempt = {
  id: "",
  kind: "",
  prompt: "",
  consentVersion: "",
  operationId: "",
  submitting: false,
};

const kronikaReview = {
  recordId: "",
  expectedVersion: null,
  visibility: "",
  kind: "",
  completed: false,
  needsReload: false,
  pending: false,
};

const kronikaRuntime = {
  bound: false,
  routeName: "timeline",
  historyMode: "questions",
  reviewMode: "private",
  questionKind: "search",
  activeOperationId: "",
  pollTimer: null,
  pollInFlight: false,
  pollPaused: false,
  pollFailures: 0,
  hiddenHold: false,
  cancelSent: false,
  lastAnnouncedState: "",
  galleryLoaded: false,
};

let kronikaIdentityKey = null;
let kronikaIdentityGeneration = 0;
let kronikaNavToken = 0;
let kronikaSuppressHash = false;
let kronikaApplyingRoute = false;
let kronikaClosingFromRoute = false;
let kronikaCurrentHash = "";
let kronikaDetailsReturn = "#/gallery";
let kronikaOpener = null;

function kronikaLogin() {
  if (typeof identityState === "undefined" || !identityState) return "";
  return typeof identityState.login === "string" ? identityState.login : "";
}

function kronikaAudience() {
  if (typeof identityState === "undefined" || !identityState) return "";
  return typeof identityState.audience === "string" ? identityState.audience : "";
}

function kronikaVerified() {
  return Boolean(
    typeof identityState !== "undefined"
    && identityState
    && identityState.resolved
    && kronikaAudience()
    && kronikaAudience() !== "public_published"
    && kronikaLogin(),
  );
}

function kronikaCanResearch() {
  return kronikaVerified()
    && typeof identityHasCapability === "function"
    && identityHasCapability("research.run");
}

function kronikaCanApprove() {
  return kronikaVerified()
    && typeof identityHasCapability === "function"
    && identityHasCapability("records.approve");
}

function kronikaErrorCopy(code) {
  switch (code) {
    case "E_DISABLED":
      return "Search and Research are turned off. Your history is still available.";
    case "E_NOT_CONFIGURED":
      return "Search and Research are not ready. An administrator needs to finish setup.";
    case "E_BUSY":
      return "Another request is running. Try again when it finishes.";
    case "E_IDEMPOTENCY_CONFLICT":
      return "This submission ID belongs to different content. Check your history before starting a new request.";
    case "E_BUDGET_EXCEEDED":
      return "The research budget has been reached. Try again after it resets.";
    case "IDENTITY_REQUIRED":
      return "A verified account is required. Reconnect to your workspace.";
    case "CAPABILITY_DENIED":
      return "Your account is not allowed to perform this action.";
    case "RECORD_CONFLICT":
      return "This record changed or is not ready for approval. Reload and review it before trying again.";
    case "NOT_FOUND":
      return "This item is unavailable.";
    default:
      return "The request could not be completed.";
  }
}

function kronikaQuestionBytes(value) {
  return new TextEncoder().encode(String(value)).length;
}

function kronikaSafeCitationUrl(value) {
  if (typeof value !== "string" || !value) return "";
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:" && parsed.protocol !== "mailto:") {
      return "";
    }
    return parsed.href;
  } catch {
    return "";
  }
}

function kronikaSrcdoc(html) {
  return "<!DOCTYPE html><html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'\"><style>body{margin:0;padding:16px;background:#0a0e0a;color:#e8f0e8;font:16px/1.5 sans-serif;overflow-wrap:anywhere}</style></head><body>"
    + String(html || "")
    + "</body></html>";
}

function kronikaParseOffset(value) {
  if (value == null || value === "") return 0;
  if (!/^[0-9]+$/.test(String(value))) return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) return null;
  return parsed;
}

function kronikaParseFilter(value) {
  if (value == null || value === "" || value === "all") return "all";
  return KRONIKA_FILTERS.includes(value) ? value : null;
}

function kronikaValidToken(value, maximum) {
  return typeof value === "string"
    && value.length > 0
    && value.length <= maximum
    && !/[\u0000-\u001f\u007f/\\]/.test(value);
}

function kronikaParseRoute(hash) {
  const raw = String(hash || "");
  const body = raw.startsWith("#") ? raw.slice(1) : raw;
  const splitAt = body.indexOf("?");
  const path = splitAt === -1 ? body : body.slice(0, splitAt);
  const query = new URLSearchParams(splitAt === -1 ? "" : body.slice(splitAt + 1));
  const offset = kronikaParseOffset(query.get("offset"));
  const filter = kronikaParseFilter(query.get("filter"));
  const unavailable = {
    name: "unavailable",
    path: path || "/",
    id: "",
    offset: 0,
    filter: "all",
    headingId: "kronika-unknown-heading",
    view: "unknown",
  };
  if (offset === null || filter === null) return unavailable;
  const normalized = path === "" || path === "/" ? "/timeline" : path;
  if (normalized === "/timeline") {
    return {
      name: "timeline",
      path: "/timeline",
      id: "",
      offset,
      filter,
      headingId: "kronika-timeline-heading",
      view: "timeline",
    };
  }
  if (normalized === "/gallery") {
    return {
      name: "gallery",
      path: "/gallery",
      id: "",
      offset: 0,
      filter: "all",
      headingId: "",
      view: "gallery",
    };
  }
  if (normalized === "/history") {
    return {
      name: "history",
      path: "/history",
      id: "",
      offset,
      filter: "all",
      headingId: "kronika-history-heading",
      view: "history",
    };
  }
  if (normalized === "/history/records") {
    return {
      name: "history-records",
      path: "/history/records",
      id: "",
      offset,
      filter: "all",
      headingId: "kronika-history-heading",
      view: "history",
    };
  }
  if (normalized === "/search" || normalized === "/research") {
    return {
      name: normalized.slice(1),
      path: normalized,
      id: "",
      offset: 0,
      filter: "all",
      headingId: "kronika-question-heading",
      view: "question",
    };
  }
  if (normalized === "/review" || normalized === "/review/shared" || normalized === "/review/requests") {
    return {
      name: normalized === "/review" ? "review" : normalized.slice("/review/".length) === "shared" ? "review-shared" : "review-requests",
      path: normalized,
      id: "",
      offset,
      filter: "all",
      headingId: "kronika-review-heading",
      view: "review",
    };
  }
  const recordPrefix = "/records/";
  if (normalized.startsWith(recordPrefix)) {
    const id = normalized.slice(recordPrefix.length);
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
      return unavailable;
    }
    return {
      name: "record",
      path: recordPrefix + id,
      id,
      offset: 0,
      filter: "all",
      headingId: "kronika-record-heading",
      view: "record",
    };
  }
  const requestPrefix = "/requests/";
  if (normalized.startsWith(requestPrefix)) {
    const id = decodeURIComponent(normalized.slice(requestPrefix.length));
    if (!kronikaValidToken(id, 128)) return unavailable;
    return {
      name: "request",
      path: requestPrefix + id,
      id,
      offset: 0,
      filter: "all",
      headingId: "kronika-request-heading",
      view: "request",
    };
  }
  const detailsPrefix = "/details/";
  if (normalized.startsWith(detailsPrefix)) {
    const id = decodeURIComponent(normalized.slice(detailsPrefix.length));
    if (!kronikaValidToken(id, 128)) return unavailable;
    return {
      name: "details",
      path: detailsPrefix + id,
      id,
      offset: 0,
      filter: "all",
      headingId: "",
      view: "gallery",
    };
  }
  return {
    name: "unknown",
    path: normalized,
    id: "",
    offset: 0,
    filter: "all",
    headingId: "kronika-unknown-heading",
    view: "unknown",
  };
}

function kronikaHashForRoute(route) {
  const params = new URLSearchParams();
  if (route.filter && route.filter !== "all") params.set("filter", route.filter);
  if (route.offset) params.set("offset", String(route.offset));
  const query = params.toString();
  return `#${route.path}${query ? `?${query}` : ""}`;
}

function kronikaLocationHash() {
  const locationObject = globalThis.location;
  return locationObject && typeof locationObject.hash === "string" ? locationObject.hash : "";
}

function kronikaAssignHash(next) {
  const locationObject = globalThis.location;
  if (!locationObject) return;
  if (kronikaLocationHash() === next) {
    void kronikaFollowHash();
    return;
  }
  locationObject.hash = next;
}

function kronikaSetHashSuppressed(next) {
  const locationObject = globalThis.location;
  if (!locationObject || kronikaLocationHash() === next) return;
  kronikaSuppressHash = true;
  locationObject.hash = next;
}

function kronikaSetText(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

function kronikaNoteIdentity() {
  const key = `${kronikaAudience()}|${kronikaLogin()}`;
  if (key === kronikaIdentityKey) return;
  const previous = kronikaIdentityKey;
  kronikaIdentityKey = key;
  kronikaIdentityGeneration += 1;
  if (previous !== null) {
    kronikaClearPrivate();
    kronikaStopPoll();
  }
  kronikaSyncReviewNav();
}

function kronikaDocumentReady() {
  return typeof document !== "undefined"
    && document !== null
    && typeof document.getElementById === "function"
    && typeof document.querySelectorAll === "function";
}

function kronikaClearPrivate() {
  Object.keys(kronikaLists).forEach((key) => {
    kronikaLists[key] = kronikaBlankList();
  });
  kronikaAttempt.id = "";
  kronikaAttempt.prompt = "";
  kronikaAttempt.operationId = "";
  kronikaAttempt.submitting = false;
  kronikaReview.recordId = "";
  kronikaReview.expectedVersion = null;
  kronikaReview.needsReload = false;
  kronikaReview.pending = false;
  if (!kronikaDocumentReady()) return;
  ["kronika-timeline-results", "kronika-history-results", "kronika-review-results", "kronika-record-citations", "kronika-review-media"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.replaceChildren();
  });
  kronikaSetText("kronika-record-question", "");
  kronikaSetText("kronika-timeline-status", "");
  kronikaSetText("kronika-history-status", "");
  kronikaSetText("kronika-review-status", "");
  kronikaSetText("kronika-request-status", "");
  const frame = document.getElementById("kronika-document-frame");
  if (frame) {
    frame.srcdoc = "";
    if (typeof frame.removeAttribute === "function") frame.removeAttribute("src");
  }
  const input = document.getElementById("kronika-question-input");
  if (input) input.value = "";
  const consent = document.getElementById("kronika-consent");
  if (consent) consent.checked = false;
}

function kronikaStopPoll() {
  if (kronikaRuntime.pollTimer != null) {
    clearTimeout(kronikaRuntime.pollTimer);
    kronikaRuntime.pollTimer = null;
  }
  kronikaRuntime.activeOperationId = "";
  kronikaRuntime.pollInFlight = false;
  kronikaRuntime.pollPaused = false;
  kronikaRuntime.pollFailures = 0;
  kronikaRuntime.cancelSent = false;
  kronikaRuntime.lastAnnouncedState = "";
}

function kronikaClearPollTimer() {
  if (kronikaRuntime.pollTimer != null) {
    clearTimeout(kronikaRuntime.pollTimer);
    kronikaRuntime.pollTimer = null;
  }
}

function kronikaHideProductSections() {
  document.querySelectorAll("[data-kronika-view]").forEach((node) => {
    node.hidden = true;
  });
}

function kronikaHideLegacyBrowsers() {
  ["admin-media-browser", "workspace-media-browser", "analysis-proposals-browser"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.hidden = true;
  });
}

function kronikaLeaveGallery() {
  if (typeof stopCardPreviewTimer === "function") stopCardPreviewTimer();
  if (typeof captureActiveCardVideoPlaybackPosition === "function") {
    captureActiveCardVideoPlaybackPosition();
  }
}

function kronikaMarkCurrent(name) {
  const current = name === "details" ? "gallery"
    : name === "history-records" || name === "request" || name === "record" ? "history"
      : name === "review-shared" || name === "review-requests" ? "review"
        : name;
  document.querySelectorAll(".kronika-nav a[data-kronika-nav]").forEach((link) => {
    if (link.getAttribute("data-kronika-nav") === current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function kronikaMarkGalleryCurrent() {
  kronikaMarkCurrent("gallery");
  const hash = kronikaLocationHash();
  if (hash === "#/gallery" || hash.startsWith("#/details/")) return;
  kronikaSetHashSuppressed("#/gallery");
}

function kronikaSyncReviewNav() {
  if (!kronikaDocumentReady()) return;
  const review = document.getElementById("kronika-nav-review");
  if (review) review.hidden = !kronikaCanApprove();
}

function kronikaShowShell(view) {
  document.querySelectorAll("[data-kronika-view]").forEach((node) => {
    node.hidden = node.getAttribute("data-kronika-view") !== view;
  });
  const catalog = document.getElementById("catalog-browser");
  const search = document.querySelector(".header-search");
  const gallery = view === "gallery";
  if (!gallery) kronikaLeaveGallery();
  if (catalog) catalog.hidden = !gallery;
  if (search) search.hidden = !gallery;
  if (gallery) kronikaHideLegacyBrowsers();
}

function kronikaFocusRoute(route) {
  const opener = kronikaOpener;
  kronikaOpener = null;
  const openerConnected = opener && opener.isConnected !== false && typeof opener.focus === "function";
  if (openerConnected && (typeof document.contains !== "function" || document.contains(opener))) {
    opener.focus();
    return;
  }
  const heading = route.headingId ? document.getElementById(route.headingId) : null;
  if (heading && typeof heading.focus === "function") {
    heading.focus();
    return;
  }
  const catalog = document.getElementById("catalog-browser");
  if (catalog && typeof catalog.focus === "function") catalog.focus();
}

function kronikaEnsureGalleryLoaded() {
  if (kronikaRuntime.galleryLoaded) return;
  kronikaRuntime.galleryLoaded = true;
  if (typeof loadCatalogTags === "function") loadCatalogTags();
  if (typeof loadCatalog === "function") loadCatalog();
  const input = typeof commandSearchInput !== "undefined" ? commandSearchInput : null;
  if (input && typeof input.focus === "function") input.focus({ preventScroll: true });
}

async function kronikaConfirmNavigation() {
  if (typeof metadataDirtyForBeforeUnload !== "function" || !metadataDirtyForBeforeUnload()) {
    return true;
  }
  if (typeof confirmDiscardDirtyMetadata !== "function") return false;
  const context = await confirmDiscardDirtyMetadata({ action: "kronika-navigate" });
  if (!context) return false;
  if (typeof closeMetadataWorkspaceWithContext === "function") {
    closeMetadataWorkspaceWithContext(context);
  }
  return true;
}

function kronikaOnHashChange() {
  if (kronikaSuppressHash) {
    kronikaSuppressHash = false;
    return;
  }
  void kronikaFollowHash();
}

async function kronikaFollowHash() {
  const token = ++kronikaNavToken;
  const route = kronikaParseRoute(kronikaLocationHash());
  const allowed = await kronikaConfirmNavigation();
  if (token !== kronikaNavToken) return;
  if (!allowed) {
    kronikaSetHashSuppressed(kronikaCurrentHash || "#/timeline");
    return;
  }
  kronikaApplyingRoute = true;
  try {
    if (route.name !== "details") {
      kronikaClosingFromRoute = true;
      if (typeof closeDetailsDialog === "function") closeDetailsDialog({ restoreFocus: false });
      kronikaClosingFromRoute = false;
    }
    kronikaRuntime.routeName = route.name;
    kronikaMarkCurrent(route.name);
    kronikaShowShell(route.view);
    if (route.name === "timeline") await kronikaLoadTimeline(route);
    else if (route.name === "gallery") kronikaEnsureGalleryLoaded();
    else if (route.name === "details") {
      kronikaEnsureGalleryLoaded();
      if (typeof openDetailsDialog === "function") {
        openDetailsDialog({ media_id: route.id }, kronikaOpener || document.getElementById("catalog-browser"));
      }
    } else if (route.name === "history" || route.name === "history-records") {
      await kronikaLoadHistory(route);
    } else if (route.name === "search" || route.name === "research") {
      await kronikaShowQuestion(route);
    } else if (route.name === "request") {
      await kronikaShowRequest(route.id);
    } else if (route.name === "record") {
      await kronikaLoadRecord(route.id);
    } else if (route.name === "review" || route.name === "review-shared" || route.name === "review-requests") {
      await kronikaLoadReview(route);
    } else {
      kronikaSetText("kronika-unknown-heading", route.name === "unavailable"
        ? "This address is unavailable"
        : "This address is unavailable");
    }
    kronikaCurrentHash = kronikaLocationHash();
    kronikaFocusRoute(route);
  } finally {
    kronikaApplyingRoute = false;
  }
}

function kronikaFilterQuery(filter) {
  if (filter === "search" || filter === "research" || filter === "media") return { kind: filter };
  if (filter === "general" || filter === "meme" || filter === "movie" || filter === "youtube") {
    return { kind: "media", content_category: filter };
  }
  return {};
}

function kronikaListUrl(path, offset, extra) {
  const params = new URLSearchParams();
  params.set("limit", "24");
  params.set("offset", String(offset || 0));
  Object.keys(extra || {}).forEach((key) => {
    if (extra[key]) params.set(key, extra[key]);
  });
  return `${path}?${params.toString()}`;
}

async function kronikaReadJson(url, generation) {
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (generation !== kronikaIdentityGeneration) return { stale: true };
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (generation !== kronikaIdentityGeneration) return { stale: true };
    const code = payload && payload.error ? payload.error.code : "";
    return { stale: false, ok: response.ok, status: response.status, payload, code };
  } catch {
    if (generation !== kronikaIdentityGeneration) return { stale: true };
    return { stale: false, ok: false, status: 0, payload: null, code: "" };
  }
}

function kronikaAcceptList(list, queryKey, result) {
  if (!result || result.stale) return false;
  if (result.code === "IDENTITY_REQUIRED") {
    kronikaClearPrivate();
    list.error = kronikaErrorCopy("IDENTITY_REQUIRED");
    return true;
  }
  if (!result.ok || !result.payload || !Array.isArray(result.payload.items)) {
    if (list.queryKey === queryKey && list.items.length) {
      list.stale = true;
      list.error = "";
      return true;
    }
    list.items = [];
    list.total = 0;
    list.stale = false;
    list.error = kronikaErrorCopy(result.code || "NETWORK");
    return true;
  }
  list.items = result.payload.items;
  list.total = Number(result.payload.total) || 0;
  list.offset = Number(result.payload.offset) || 0;
  list.queryKey = queryKey;
  list.stale = false;
  list.error = "";
  return true;
}

function kronikaCardTitle(item) {
  if (item && typeof item.display_title === "string" && item.display_title.trim()) return item.display_title;
  if (item && item.kind === "media") return "Untitled media";
  if (item && typeof item.prompt === "string" && item.prompt.trim()) return item.prompt;
  return "Untitled";
}

function kronikaKindLabel(item) {
  if (!item) return "Record";
  if (item.kind === "search") return "Search";
  if (item.kind === "research") return "Research";
  if (item.kind === "media") {
    if (item.content_category === "meme") return "Memes";
    if (item.content_category === "movie") return "Movies";
    if (item.content_category === "youtube") return "YouTube";
    if (item.content_category === "general") return "General";
    return "Media";
  }
  return "Record";
}

function kronikaFormatDay(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  try {
    return new Date(value).toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

function kronikaVisibilityLabel(visibility) {
  return visibility === "family"
    ? "On the household Timeline"
    : "Private — visible to you and administrators";
}

function kronikaRenderCards(container, items, hrefFor) {
  if (!container) return;
  container.replaceChildren();
  items.forEach((item) => {
    const link = document.createElement("a");
    link.className = "kronika-card";
    link.href = hrefFor(item);
    const title = document.createElement("strong");
    title.textContent = kronikaCardTitle(item);
    const meta = document.createElement("small");
    meta.textContent = kronikaKindLabel(item);
    link.appendChild(title);
    link.appendChild(meta);
    container.appendChild(link);
  });
}

function kronikaPager(prevId, nextId, list) {
  const prev = document.getElementById(prevId);
  const next = document.getElementById(nextId);
  if (prev) prev.disabled = list.offset <= 0;
  if (next) next.disabled = list.offset + 24 >= list.total;
}

async function kronikaLoadTimeline(route) {
  const status = document.getElementById("kronika-timeline-status");
  const results = document.getElementById("kronika-timeline-results");
  const section = document.getElementById("kronika-timeline");
  if (!kronikaVerified()) {
    kronikaSetText("kronika-timeline-status", kronikaErrorCopy("IDENTITY_REQUIRED"));
    if (results) results.replaceChildren();
    return;
  }
  const extra = kronikaFilterQuery(route.filter);
  const queryKey = JSON.stringify({ offset: route.offset, extra, login: kronikaLogin() });
  const generation = kronikaIdentityGeneration;
  kronikaLists.timeline.generation += 1;
  const requestGeneration = kronikaLists.timeline.generation;
  if (section) section.setAttribute("aria-busy", "true");
  if (!kronikaLists.timeline.items.length) kronikaSetText("kronika-timeline-status", "Loading…");
  const result = await kronikaReadJson(
    kronikaListUrl("/api/timeline", route.offset, extra),
    generation,
  );
  if (requestGeneration !== kronikaLists.timeline.generation || generation !== kronikaIdentityGeneration) return;
  if (section) section.setAttribute("aria-busy", "false");
  if (!kronikaAcceptList(kronikaLists.timeline, queryKey, result)) return;
  const list = kronikaLists.timeline;
  if (list.error) {
    kronikaSetText("kronika-timeline-status", list.error);
    if (results) results.replaceChildren();
    const retry = document.getElementById("kronika-timeline-retry");
    if (retry) retry.hidden = false;
    return;
  }
  const retry = document.getElementById("kronika-timeline-retry");
  if (retry) retry.hidden = true;
  if (list.stale) {
    if (status) status.textContent = "Showing the last loaded page. It was not refreshed.";
  } else if (!list.total) {
    kronikaSetText(
      "kronika-timeline-status",
      route.filter === "all"
        ? "No records have been approved for the Timeline yet."
        : "No approved records match this filter.",
    );
  } else {
    kronikaSetText("kronika-timeline-status", "");
  }
  kronikaRenderCards(results, list.items, (item) => (
    item.kind === "media" && item.media_id
      ? `#/details/${encodeURIComponent(item.media_id)}`
      : `#/records/${encodeURIComponent(item.record_id)}`
  ));
  if (results) {
    results.querySelectorAll(".kronika-card").forEach((card, index) => {
      const item = list.items[index];
      const meta = card.querySelector("small");
      if (!meta || !item) return;
      const when = kronikaFormatDay(item.timeline_entered_at_ms);
      meta.textContent = when ? `${kronikaKindLabel(item)} · ${when}` : kronikaKindLabel(item);
    });
  }
  kronikaPager("kronika-timeline-prev", "kronika-timeline-next", list);
  document.querySelectorAll("#kronika-timeline-filters [data-kronika-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", button.getAttribute("data-kronika-filter") === route.filter ? "true" : "false");
  });
}

async function kronikaLoadHistory(route) {
  kronikaRuntime.historyMode = route.name === "history-records" ? "records" : "questions";
  const questions = document.getElementById("kronika-history-questions");
  const recordsTab = document.getElementById("kronika-history-records-tab");
  if (questions) {
    if (kronikaRuntime.historyMode === "questions") questions.setAttribute("aria-current", "page");
    else questions.removeAttribute("aria-current");
  }
  if (recordsTab) {
    if (kronikaRuntime.historyMode === "records") recordsTab.setAttribute("aria-current", "page");
    else recordsTab.removeAttribute("aria-current");
  }
  if (!kronikaVerified()) {
    kronikaSetText("kronika-history-status", kronikaErrorCopy("IDENTITY_REQUIRED"));
    return;
  }
  if (kronikaRuntime.historyMode === "questions" && !kronikaCanResearch()) {
    kronikaSetText("kronika-history-status", kronikaErrorCopy("CAPABILITY_DENIED"));
    return;
  }
  const list = kronikaRuntime.historyMode === "records" ? kronikaLists.records : kronikaLists.questions;
  const path = kronikaRuntime.historyMode === "records" ? "/api/my/records" : "/api/research-requests";
  const queryKey = JSON.stringify({ mode: kronikaRuntime.historyMode, offset: route.offset, login: kronikaLogin() });
  const generation = kronikaIdentityGeneration;
  list.generation += 1;
  const requestGeneration = list.generation;
  const result = await kronikaReadJson(kronikaListUrl(path, route.offset, {}), generation);
  if (requestGeneration !== list.generation || generation !== kronikaIdentityGeneration) return;
  if (!kronikaAcceptList(list, queryKey, result)) return;
  const results = document.getElementById("kronika-history-results");
  const retry = document.getElementById("kronika-history-retry");
  if (list.error) {
    kronikaSetText("kronika-history-status", list.error);
    if (results) results.replaceChildren();
    if (retry) retry.hidden = false;
    return;
  }
  if (retry) retry.hidden = true;
  if (list.stale) kronikaSetText("kronika-history-status", "Showing the last loaded page. It was not refreshed.");
  else kronikaSetText("kronika-history-status", "");
  if (!results) return;
  results.replaceChildren();
  list.items.forEach((item) => {
    const link = document.createElement("a");
    link.className = "kronika-card";
    if (kronikaRuntime.historyMode === "records") {
      link.href = item.kind === "media" && item.media_id
        ? `#/details/${encodeURIComponent(item.media_id)}`
        : `#/records/${encodeURIComponent(item.record_id)}`;
      const title = document.createElement("strong");
      title.textContent = kronikaCardTitle(item);
      const meta = document.createElement("small");
      meta.textContent = `${kronikaKindLabel(item)} · ${kronikaVisibilityLabel(item.visibility)}`;
      link.appendChild(title);
      link.appendChild(meta);
    } else {
      link.href = item.record_id
        ? `#/records/${encodeURIComponent(item.record_id)}`
        : `#/requests/${encodeURIComponent(item.operation_id)}`;
      const title = document.createElement("strong");
      title.textContent = kronikaCardTitle(item);
      const meta = document.createElement("small");
      meta.textContent = `${kronikaKindLabel(item)} · ${item.state || "unknown"}`;
      link.appendChild(title);
      link.appendChild(meta);
    }
    results.appendChild(link);
  });
  kronikaPager("kronika-history-prev", "kronika-history-next", list);
}

function kronikaBudgetText(payload) {
  const parts = [];
  if (payload && typeof payload.retention_notice === "string") parts.push(payload.retention_notice);
  const search = payload && payload.search;
  const research = payload && payload.research;
  const reservation = kronikaRuntime.questionKind === "research" ? research : search;
  if (reservation && typeof reservation.budget_reservation_usd_micros === "number") {
    parts.push(`Budget reservation: ${reservation.budget_reservation_usd_micros} micros. A reservation is not a guaranteed invoice cap.`);
  }
  if (payload && typeof payload.daily_budget_usd_micros === "number") {
    parts.push(`Daily budget: ${payload.daily_budget_usd_micros} micros.`);
  }
  parts.push("This does not confirm that the provider is ready.");
  return parts.join(" ");
}

async function kronikaShowQuestion(route) {
  kronikaRuntime.questionKind = route.name === "research" ? "research" : "search";
  const heading = document.getElementById("kronika-question-heading");
  if (heading) heading.textContent = kronikaRuntime.questionKind === "research" ? "Research" : "Search";
  const submit = document.getElementById("kronika-question-submit");
  const generation = kronikaIdentityGeneration;
  kronikaSetText("kronika-capabilities-status", "Loading…");
  if (submit) submit.disabled = true;
  try {
    const response = await fetch("/api/research/capabilities", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (generation !== kronikaIdentityGeneration) return;
    if (!response.ok) throw new Error("unavailable");
    const payload = await response.json();
    if (generation !== kronikaIdentityGeneration) return;
    if (payload && payload.enabled === false) {
      kronikaSetText("kronika-capabilities-status", kronikaErrorCopy("E_DISABLED"));
      if (submit) submit.disabled = true;
    } else if (payload && payload.enabled === true && payload.provider_id) {
      kronikaSetText("kronika-capabilities-status", kronikaBudgetText(payload));
      if (submit) submit.disabled = !kronikaCanResearch();
    } else if (payload && payload.enabled === true) {
      kronikaSetText("kronika-capabilities-status", kronikaErrorCopy("E_NOT_CONFIGURED"));
      if (submit) submit.disabled = true;
    } else {
      kronikaSetText("kronika-capabilities-status", "Search and Research are unavailable.");
      if (submit) submit.disabled = true;
    }
  } catch {
    if (generation !== kronikaIdentityGeneration) return;
    kronikaSetText("kronika-capabilities-status", "Search and Research are unavailable.");
    if (submit) submit.disabled = true;
  }
  await kronikaOfferRecovery();
}

async function kronikaOfferRecovery() {
  let stored = null;
  try {
    const raw = sessionStorage.getItem(KRONIKA_ATTEMPT_KEY);
    stored = raw ? JSON.parse(raw) : null;
  } catch {
    kronikaSetText("kronika-question-status", "Reload loses recovery for this attempt.");
    return;
  }
  if (!stored || typeof stored !== "object" || typeof stored.id !== "string") return;
  if (stored.operationId) {
    kronikaSetText("kronika-question-status", "A previous request can be opened from History.");
    return;
  }
  kronikaAttempt.id = stored.id;
  kronikaAttempt.kind = kronikaRuntime.questionKind;
  kronikaSetText("kronika-question-status", "Re-enter the same question before retrying this submission, or check History.");
}

function kronikaNewRequestId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
  return `kronika-${Date.now()}`;
}

function kronikaReadStoredAttempt() {
  try {
    if (typeof sessionStorage === "undefined" || !sessionStorage) return null;
    const raw = sessionStorage.getItem(KRONIKA_ATTEMPT_KEY);
    if (!raw) return null;
    const stored = JSON.parse(raw);
    if (!stored || typeof stored !== "object") return null;
    return stored;
  } catch {
    return null;
  }
}

async function kronikaFreezeAttempt(kind, prompt) {
  const fingerprint = await kronikaFingerprint([
    kronikaLogin(),
    kind,
    prompt,
    KRONIKA_CONSENT_VERSION,
  ]);
  const stored = kronikaReadStoredAttempt();
  const storedFingerprint = stored && typeof stored.fingerprint === "string" ? stored.fingerprint : "";
  const storedId = stored && typeof stored.id === "string" ? stored.id : "";
  if (fingerprint && storedFingerprint && fingerprint === storedFingerprint && storedId) {
    kronikaAttempt.id = storedId;
    kronikaAttempt.kind = kind;
    kronikaAttempt.prompt = prompt;
    kronikaAttempt.consentVersion = KRONIKA_CONSENT_VERSION;
    return kronikaAttempt;
  }
  kronikaAttempt.id = kronikaNewRequestId();
  kronikaAttempt.kind = kind;
  kronikaAttempt.prompt = prompt;
  kronikaAttempt.consentVersion = KRONIKA_CONSENT_VERSION;
  kronikaAttempt.operationId = "";
  return kronikaAttempt;
}

async function kronikaFingerprint(parts) {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi || !cryptoApi.subtle || typeof cryptoApi.subtle.digest !== "function") return "";
  const digest = await cryptoApi.subtle.digest("SHA-256", new TextEncoder().encode(parts.join("\u001f")));
  return Array.from(new Uint8Array(digest)).map((part) => part.toString(16).padStart(2, "0")).join("");
}

async function kronikaPersistAttempt(attempt) {
  const fingerprint = await kronikaFingerprint([
    kronikaLogin(),
    attempt.kind,
    attempt.prompt,
    attempt.consentVersion,
  ]);
  try {
    sessionStorage.setItem(KRONIKA_ATTEMPT_KEY, JSON.stringify({
      id: attempt.id,
      fingerprint,
      operationId: attempt.operationId || "",
    }));
  } catch {
    kronikaSetText("kronika-question-status", "Reload loses recovery for this attempt.");
  }
}

function kronikaShowRetry(visible) {
  const retry = document.getElementById("kronika-question-retry");
  if (retry) retry.hidden = !visible;
}

async function kronikaPostAttempt(attempt) {
  if (!attempt || !attempt.id || kronikaAttempt.submitting) return;
  kronikaAttempt.submitting = true;
  const button = document.getElementById("kronika-question-submit");
  if (button) button.disabled = true;
  const generation = kronikaIdentityGeneration;
  try {
    const response = await fetch("/api/research-requests", {
      method: "POST",
      headers: framenestMutationHeaders(framenestJSONHeaders()),
      body: JSON.stringify({
        kind: attempt.kind,
        prompt: attempt.prompt,
        client_request_id: attempt.id,
        consent_version: attempt.consentVersion,
      }),
    });
    if (generation !== kronikaIdentityGeneration) return;
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const code = payload && payload.error ? payload.error.code : "";
      kronikaSetText("kronika-question-status", kronikaErrorCopy(code || "NETWORK"));
      if (code === "IDENTITY_REQUIRED") kronikaClearPrivate();
      kronikaShowRetry(code !== "E_IDEMPOTENCY_CONFLICT");
      return;
    }
    if (payload && payload.error_code) {
      kronikaSetText("kronika-question-status", kronikaErrorCopy(payload.error_code));
    }
    if (payload && payload.operation_id) {
      attempt.operationId = payload.operation_id;
      await kronikaPersistAttempt(attempt);
      kronikaShowRetry(false);
      kronikaAssignHash(`#/requests/${encodeURIComponent(payload.operation_id)}`);
    }
  } catch {
    if (generation !== kronikaIdentityGeneration) return;
    kronikaSetText("kronika-question-status", "The network request failed.");
    kronikaShowRetry(true);
    await kronikaPersistAttempt(attempt);
  } finally {
    kronikaAttempt.submitting = false;
    if (button) button.disabled = !kronikaCanResearch();
  }
}

async function kronikaSubmitQuestion(event) {
  if (event && typeof event.preventDefault === "function") event.preventDefault();
  if (kronikaAttempt.submitting) return;
  const input = document.getElementById("kronika-question-input");
  const consent = document.getElementById("kronika-consent");
  const prompt = input ? String(input.value) : "";
  const error = document.getElementById("kronika-question-error");
  if (!prompt.trim() || kronikaQuestionBytes(prompt) > 16384) {
    if (error) error.textContent = !prompt.trim() ? "Enter a question before submitting." : "The question is too long.";
    if (input && typeof input.focus === "function") input.focus();
    return;
  }
  if (!consent || !consent.checked) {
    if (error) error.textContent = "Consent is required before submitting.";
    if (consent && typeof consent.focus === "function") consent.focus();
    return;
  }
  if (error) error.textContent = "";
  const attempt = await kronikaFreezeAttempt(kronikaRuntime.questionKind || "search", prompt);
  await kronikaPostAttempt(attempt);
}

async function kronikaRetrySubmission() {
  if (!kronikaAttempt.id || !kronikaAttempt.prompt) return;
  await kronikaPostAttempt(kronikaAttempt);
}

function kronikaStateLabel(state) {
  switch (state) {
    case "admitted":
    case "submitting":
    case "running":
    case "validating":
      return "Working. Processing continues while this page is open and visible. Closing the page pauses these updates.";
    case "cancel_requested":
      return "Cancellation requested.";
    case "saved":
      return "Saved.";
    case "refused":
      return "Refused.";
    case "failed":
      return "Failed.";
    case "incomplete":
      return "Incomplete.";
    case "cancelled":
      return "Cancelled.";
    case "timeout":
      return "Timed out.";
    case "submission_unknown":
      return "Submission status is unknown.";
    default:
      return "Status is unavailable.";
  }
}

function kronikaApplyRequestPayload(payload) {
  const state = payload && typeof payload.state === "string" ? payload.state : "";
  const status = document.getElementById("kronika-request-status");
  const cancel = document.getElementById("kronika-request-cancel");
  const resume = document.getElementById("kronika-request-resume");
  const open = document.getElementById("kronika-request-open-answer");
  if (state !== kronikaRuntime.lastAnnouncedState && status) {
    status.textContent = payload && payload.error_code
      ? kronikaErrorCopy(payload.error_code)
      : kronikaStateLabel(state);
    kronikaRuntime.lastAnnouncedState = state;
  }
  const active = KRONIKA_ACTIVE_STATES.includes(state);
  if (cancel) cancel.hidden = !active || state === "cancel_requested";
  if (resume) resume.hidden = !kronikaRuntime.pollPaused;
  if (state === "saved" && payload.record_id) {
    kronikaLists.records = kronikaBlankList();
    if (open) {
      open.hidden = false;
      open.href = `#/records/${encodeURIComponent(payload.record_id)}`;
    }
  } else if (open) {
    open.hidden = true;
  }
  return state;
}

function kronikaArmPoll() {
  kronikaClearPollTimer();
  kronikaRuntime.pollTimer = setTimeout(() => {
    kronikaRuntime.pollTimer = null;
    void kronikaPollOnce();
  }, KRONIKA_POLL_DELAY_MS);
}

async function kronikaPollOnce() {
  if (kronikaRuntime.pollInFlight || kronikaRuntime.pollPaused || !kronikaRuntime.activeOperationId) return;
  const operationId = kronikaRuntime.activeOperationId;
  const generation = kronikaIdentityGeneration;
  kronikaRuntime.pollInFlight = true;
  let continuePolling = false;
  try {
    const response = await fetch(`/api/research-requests/${encodeURIComponent(operationId)}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (generation !== kronikaIdentityGeneration || operationId !== kronikaRuntime.activeOperationId) return;
    if (!response.ok) throw new Error("transport");
    const payload = await response.json();
    if (generation !== kronikaIdentityGeneration || operationId !== kronikaRuntime.activeOperationId) return;
    kronikaRuntime.pollFailures = 0;
    const state = kronikaApplyRequestPayload(payload);
    if (KRONIKA_ACTIVE_STATES.includes(state)) continuePolling = true;
    else if (!KRONIKA_TERMINAL_STATES.includes(state)) {
      kronikaSetText("kronika-request-status", "Status is unavailable.");
    }
  } catch {
    if (generation !== kronikaIdentityGeneration || operationId !== kronikaRuntime.activeOperationId) return;
    kronikaRuntime.pollFailures += 1;
    if (kronikaRuntime.pollFailures >= 3) {
      kronikaRuntime.pollPaused = true;
      kronikaSetText("kronika-request-status", "Updates paused. Resume updates to check again.");
      const resume = document.getElementById("kronika-request-resume");
      if (resume) resume.hidden = false;
    } else {
      continuePolling = true;
    }
  } finally {
    kronikaRuntime.pollInFlight = false;
    if (
      continuePolling
      && !kronikaRuntime.pollPaused
      && operationId === kronikaRuntime.activeOperationId
      && !(typeof document !== "undefined" && document.hidden)
    ) {
      kronikaArmPoll();
    }
  }
}

async function kronikaShowRequest(operationId) {
  if (!kronikaCanResearch()) {
    kronikaSetText("kronika-request-status", kronikaErrorCopy(kronikaVerified() ? "CAPABILITY_DENIED" : "IDENTITY_REQUIRED"));
    return;
  }
  if (kronikaRuntime.activeOperationId !== operationId) {
    kronikaClearPollTimer();
    kronikaRuntime.activeOperationId = operationId;
    kronikaRuntime.pollPaused = false;
    kronikaRuntime.pollFailures = 0;
    kronikaRuntime.cancelSent = false;
    kronikaRuntime.lastAnnouncedState = "";
  }
  await kronikaPollOnce();
}

async function kronikaCancelActiveRequest() {
  const operationId = kronikaRuntime.activeOperationId;
  if (!operationId || kronikaRuntime.cancelSent) return;
  kronikaRuntime.cancelSent = true;
  const generation = kronikaIdentityGeneration;
  try {
    const response = await fetch(`/api/research-requests/${encodeURIComponent(operationId)}/cancel`, {
      method: "POST",
      headers: framenestMutationHeaders(framenestJSONHeaders()),
    });
    if (generation !== kronikaIdentityGeneration) return;
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const code = payload && payload.error ? payload.error.code : "";
      kronikaSetText("kronika-request-status", kronikaErrorCopy(code || "NETWORK"));
    }
  } catch {
    kronikaSetText("kronika-request-status", "The network request failed.");
  }
  if (!kronikaRuntime.pollPaused) void kronikaPollOnce();
}

function kronikaResumeUpdates() {
  kronikaRuntime.pollPaused = false;
  kronikaRuntime.pollFailures = 0;
  const resume = document.getElementById("kronika-request-resume");
  if (resume) resume.hidden = true;
  void kronikaPollOnce();
}

function kronikaPauseForHide() {
  kronikaClearPollTimer();
  kronikaRuntime.hiddenHold = true;
}

function kronikaOnVisibility() {
  if (document.hidden) {
    kronikaPauseForHide();
    return;
  }
  if (!kronikaRuntime.hiddenHold) return;
  kronikaRuntime.hiddenHold = false;
  if (kronikaRuntime.pollPaused || !kronikaRuntime.activeOperationId) return;
  void kronikaPollOnce();
}

function kronikaRenderCitations(citations) {
  const list = document.getElementById("kronika-record-citations");
  if (!list) return;
  list.replaceChildren();
  (Array.isArray(citations) ? citations : []).forEach((item) => {
    const li = document.createElement("li");
    const safe = kronikaSafeCitationUrl(item && item.url);
    if (safe) {
      const link = document.createElement("a");
      link.href = safe;
      link.textContent = item && item.title ? String(item.title) : safe;
      link.rel = "noopener noreferrer";
      link.target = "_blank";
      if (typeof link.setAttribute === "function") link.setAttribute("referrerpolicy", "no-referrer");
      li.appendChild(link);
    } else {
      li.textContent = item && item.title ? String(item.title) : "Citation";
    }
    list.appendChild(li);
  });
}

function kronikaMountDocument(parentId) {
  const parent = document.getElementById(parentId);
  if (!parent) return;
  ["kronika-record-question", "kronika-record-status", "kronika-record-retry", "kronika-record-citations", "kronika-document-frame"].forEach((id) => {
    const node = document.getElementById(id);
    if (node && node.parentNode !== parent) parent.appendChild(node);
  });
}

async function kronikaLoadRecord(recordId, mountId) {
  kronikaMountDocument(mountId || "kronika-record");
  const frame = document.getElementById("kronika-document-frame");
  const retry = document.getElementById("kronika-record-retry");
  if (frame) {
    frame.srcdoc = "";
    if (typeof frame.removeAttribute === "function") frame.removeAttribute("src");
  }
  if (retry) retry.hidden = true;
  if (!kronikaVerified()) {
    kronikaSetText("kronika-record-status", kronikaErrorCopy("IDENTITY_REQUIRED"));
    return;
  }
  const generation = kronikaIdentityGeneration;
  const detail = await kronikaReadJson(`/api/records/${encodeURIComponent(recordId)}`, generation);
  if (!detail || detail.stale || generation !== kronikaIdentityGeneration) return;
  if (!detail.ok) {
    kronikaSetText("kronika-record-status", kronikaErrorCopy(detail.code || "NOT_FOUND"));
    kronikaSetText("kronika-record-question", "");
    return;
  }
  const documentPayload = detail.payload && detail.payload.document;
  const question = documentPayload && typeof documentPayload.question_text === "string"
    ? documentPayload.question_text
    : "";
  kronikaSetText("kronika-record-question", question);
  kronikaRenderCitations(documentPayload && documentPayload.citations);
  try {
    const response = await fetch(`/api/records/${encodeURIComponent(recordId)}/render`, {
      headers: { Accept: "text/html" },
      cache: "no-store",
    });
    if (generation !== kronikaIdentityGeneration) return;
    if (!response.ok) throw new Error("render");
    const html = await response.text();
    if (generation !== kronikaIdentityGeneration) return;
    if (frame) frame.srcdoc = kronikaSrcdoc(html);
    kronikaSetText("kronika-record-status", "");
  } catch {
    if (generation !== kronikaIdentityGeneration) return;
    if (frame) frame.srcdoc = "";
    kronikaSetText("kronika-record-status", "The answer could not be loaded.");
    if (retry) retry.hidden = false;
  }
}

async function kronikaLoadReview(route) {
  if (!kronikaCanApprove()) {
    kronikaSetText("kronika-review-status", kronikaErrorCopy(kronikaVerified() ? "CAPABILITY_DENIED" : "IDENTITY_REQUIRED"));
    return;
  }
  kronikaRuntime.reviewMode = route.name === "review-shared"
    ? "shared"
    : route.name === "review-requests"
      ? "requests"
      : "private";
  const privateLink = document.getElementById("kronika-review-private");
  const sharedLink = document.getElementById("kronika-review-shared");
  const requestLink = document.getElementById("kronika-review-requests");
  if (privateLink) {
    if (kronikaRuntime.reviewMode === "private") privateLink.setAttribute("aria-current", "page");
    else privateLink.removeAttribute("aria-current");
  }
  if (sharedLink) {
    if (kronikaRuntime.reviewMode === "shared") sharedLink.setAttribute("aria-current", "page");
    else sharedLink.removeAttribute("aria-current");
  }
  if (requestLink) {
    if (kronikaRuntime.reviewMode === "requests") requestLink.setAttribute("aria-current", "page");
    else requestLink.removeAttribute("aria-current");
  }
  const requestsMode = kronikaRuntime.reviewMode === "requests";
  const list = requestsMode ? kronikaLists.adminRequests : kronikaLists.adminRecords;
  const path = requestsMode ? "/api/admin/research-requests" : "/api/admin/records";
  const extra = requestsMode ? {} : {
    visibility: kronikaRuntime.reviewMode === "shared" ? "family" : "private",
  };
  const queryKey = JSON.stringify({ mode: kronikaRuntime.reviewMode, offset: route.offset, login: kronikaLogin() });
  const generation = kronikaIdentityGeneration;
  list.generation += 1;
  const requestGeneration = list.generation;
  const result = await kronikaReadJson(kronikaListUrl(path, route.offset, extra), generation);
  if (requestGeneration !== list.generation || generation !== kronikaIdentityGeneration) return;
  if (!kronikaAcceptList(list, queryKey, result)) return;
  const results = document.getElementById("kronika-review-results");
  if (list.error) {
    kronikaSetText("kronika-review-status", list.error);
    if (results) results.replaceChildren();
    return;
  }
  if (list.stale) kronikaSetText("kronika-review-status", "Showing the last loaded page. It was not refreshed.");
  else if (!list.error) kronikaSetText("kronika-review-status", "");
  if (!results) return;
  results.replaceChildren();
  list.items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "kronika-card";
    const title = document.createElement("strong");
    title.textContent = kronikaCardTitle(item);
    const meta = document.createElement("small");
    meta.textContent = requestsMode
      ? `${kronikaKindLabel(item)} · ${item.state || "unknown"}`
      : `${kronikaKindLabel(item)} · ${kronikaVisibilityLabel(item.visibility)}`;
    button.appendChild(title);
    button.appendChild(meta);
    button.addEventListener("click", () => {
      kronikaOpener = button;
      if (requestsMode) kronikaAssignHash(`#/requests/${encodeURIComponent(item.operation_id)}`);
      else void kronikaLoadReviewDetail(item.record_id);
    });
    results.appendChild(button);
  });
  kronikaPager("kronika-review-prev", "kronika-review-next", list);
}

function kronikaSyncReviewActions() {
  const approve = document.getElementById("kronika-review-approve");
  const withdraw = document.getElementById("kronika-review-withdraw");
  const reload = document.getElementById("kronika-review-reload");
  const readiness = document.getElementById("kronika-review-readiness");
  if (approve) {
    approve.textContent = kronikaReview.visibility === "family" ? "Approve current version" : "Approve for Timeline";
    approve.disabled = kronikaReview.needsReload || !kronikaReview.completed || kronikaReview.expectedVersion == null;
  }
  if (withdraw) {
    withdraw.hidden = kronikaReview.visibility !== "family";
    withdraw.disabled = kronikaReview.needsReload || kronikaReview.expectedVersion == null;
  }
  if (reload) reload.hidden = !kronikaReview.needsReload;
  if (readiness) readiness.hidden = kronikaReview.kind !== "media";
}

async function kronikaLoadReviewDetail(recordId) {
  const generation = kronikaIdentityGeneration;
  const detail = document.getElementById("kronika-review-detail");
  if (detail) detail.hidden = false;
  const result = await kronikaReadJson(`/api/records/${encodeURIComponent(recordId)}`, generation);
  if (!result || result.stale || generation !== kronikaIdentityGeneration) return;
  if (!result.ok || !result.payload || !result.payload.record) {
    kronikaSetText("kronika-review-status", kronikaErrorCopy(result.code || "NOT_FOUND"));
    return;
  }
  const record = result.payload.record;
  kronikaReview.recordId = record.record_id;
  kronikaReview.expectedVersion = result.payload.version;
  kronikaReview.visibility = record.visibility;
  kronikaReview.kind = record.kind;
  kronikaReview.completed = record.completed_at_ms != null;
  kronikaReview.needsReload = false;
  kronikaSetText("kronika-review-version", `Version ${result.payload.version}`);
  const media = document.getElementById("kronika-review-media");
  if (media) {
    media.replaceChildren();
    if (record.kind === "media" && record.media_id) {
      const link = document.createElement("a");
      link.href = `#/details/${encodeURIComponent(record.media_id)}`;
      link.textContent = "Open details";
      media.appendChild(link);
    }
  }
  if (record.kind !== "media") await kronikaLoadRecord(recordId, "kronika-review-detail");
  kronikaSyncReviewActions();
}

async function kronikaReloadReview() {
  if (!kronikaReview.recordId) return;
  kronikaReview.needsReload = false;
  await kronikaLoadReviewDetail(kronikaReview.recordId);
  if (kronikaRuntime.routeName.startsWith("review")) {
    await kronikaLoadReview(kronikaParseRoute(kronikaLocationHash()));
  }
}

async function kronikaSubmitApproval(action) {
  if (!kronikaReview.recordId || kronikaReview.needsReload || kronikaReview.expectedVersion == null || kronikaReview.pending) {
    return;
  }
  kronikaReview.pending = true;
  const version = kronikaReview.expectedVersion;
  const recordId = kronikaReview.recordId;
  const generation = kronikaIdentityGeneration;
  try {
    const response = await fetch(`/api/admin/records/${encodeURIComponent(recordId)}/approval`, {
      method: "POST",
      headers: framenestMutationHeaders(framenestJSONHeaders()),
      body: JSON.stringify({ action, expected_version: version }),
    });
    if (generation !== kronikaIdentityGeneration) return;
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (response.status === 409) {
      kronikaReview.needsReload = true;
      kronikaSetText("kronika-review-status", kronikaErrorCopy("RECORD_CONFLICT"));
      kronikaSyncReviewActions();
      return;
    }
    if (!response.ok || !payload || typeof payload.changed !== "boolean") {
      kronikaReview.needsReload = true;
      kronikaSetText("kronika-review-status", "The result is uncertain. Reload the record before trying again.");
      kronikaSyncReviewActions();
      return;
    }
    kronikaSetText(
      "kronika-review-status",
      payload.changed ? "Saved." : "The record is already in that state.",
    );
    kronikaLists.timeline = kronikaBlankList();
    kronikaLists.adminRecords = kronikaBlankList();
    kronikaLists.records = kronikaBlankList();
    await kronikaReloadReview();
  } catch {
    if (generation !== kronikaIdentityGeneration) return;
    kronikaReview.needsReload = true;
    kronikaSetText("kronika-review-status", "The result is uncertain. Reload the record before trying again.");
    kronikaSyncReviewActions();
  } finally {
    kronikaReview.pending = false;
  }
}

function kronikaRememberDetailsAddress(mediaId) {
  if (!mediaId || kronikaApplyingRoute) return;
  const next = `#/details/${encodeURIComponent(mediaId)}`;
  if (!kronikaLocationHash().startsWith("#/details/")) kronikaDetailsReturn = kronikaLocationHash() || "#/gallery";
  if (kronikaLocationHash() === next) return;
  kronikaSetHashSuppressed(next);
  kronikaMarkCurrent("details");
}

function kronikaReleaseDetailsAddress() {
  if (kronikaClosingFromRoute) return;
  if (!kronikaLocationHash().startsWith("#/details/")) return;
  kronikaSetHashSuppressed(kronikaDetailsReturn || "#/gallery");
  kronikaMarkCurrent("gallery");
}

function kronikaShiftOffset(route, delta) {
  const nextOffset = Math.max(0, (route.offset || 0) + delta);
  kronikaAssignHash(kronikaHashForRoute({ path: route.path, filter: route.filter, offset: nextOffset }));
}

function kronikaBindShell() {
  if (!kronikaDocumentReady() || kronikaRuntime.bound) return;
  kronikaRuntime.bound = true;
  document.querySelectorAll(".kronika-nav a").forEach((link) => {
    link.addEventListener("click", () => {
      kronikaOpener = link;
    });
  });
  document.querySelectorAll("#kronika-timeline-filters [data-kronika-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      kronikaOpener = button;
      const filter = button.getAttribute("data-kronika-filter") || "all";
      kronikaAssignHash(kronikaHashForRoute({ path: "/timeline", filter, offset: 0 }));
    });
  });
  const form = document.getElementById("kronika-question-form");
  if (form) form.addEventListener("submit", (event) => { void kronikaSubmitQuestion(event); });
  const input = document.getElementById("kronika-question-input");
  if (input) {
    input.addEventListener("input", () => {
      if (kronikaAttempt.prompt && input.value !== kronikaAttempt.prompt) {
        kronikaAttempt.id = "";
        kronikaAttempt.prompt = "";
        const consent = document.getElementById("kronika-consent");
        if (consent) consent.checked = false;
        kronikaShowRetry(false);
      }
    });
  }
  const retry = document.getElementById("kronika-question-retry");
  if (retry) retry.addEventListener("click", () => { void kronikaRetrySubmission(); });
  const cancel = document.getElementById("kronika-request-cancel");
  if (cancel) cancel.addEventListener("click", () => { void kronikaCancelActiveRequest(); });
  const resume = document.getElementById("kronika-request-resume");
  if (resume) resume.addEventListener("click", () => { kronikaResumeUpdates(); });
  const recordRetry = document.getElementById("kronika-record-retry");
  if (recordRetry) {
    recordRetry.addEventListener("click", () => {
      const route = kronikaParseRoute(kronikaLocationHash());
      if (route.name === "record") void kronikaLoadRecord(route.id);
    });
  }
  const approve = document.getElementById("kronika-review-approve");
  if (approve) approve.addEventListener("click", () => { void kronikaSubmitApproval("approve"); });
  const withdraw = document.getElementById("kronika-review-withdraw");
  if (withdraw) withdraw.addEventListener("click", () => { void kronikaSubmitApproval("withdraw"); });
  const reload = document.getElementById("kronika-review-reload");
  if (reload) reload.addEventListener("click", () => { void kronikaReloadReview(); });
  [
    ["kronika-timeline-prev", "/timeline", -24],
    ["kronika-timeline-next", "/timeline", 24],
    ["kronika-history-prev", "", -24],
    ["kronika-history-next", "", 24],
    ["kronika-review-prev", "", -24],
    ["kronika-review-next", "", 24],
  ].forEach(([id, path, delta]) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.addEventListener("click", () => {
      const route = kronikaParseRoute(kronikaLocationHash());
      const target = path || route.path;
      kronikaShiftOffset({ path: target, filter: route.filter, offset: route.offset }, delta);
    });
  });
  const timelineRetry = document.getElementById("kronika-timeline-retry");
  if (timelineRetry) {
    timelineRetry.addEventListener("click", () => {
      void kronikaLoadTimeline(kronikaParseRoute(kronikaLocationHash() || "#/timeline"));
    });
  }
  const historyRetry = document.getElementById("kronika-history-retry");
  if (historyRetry) {
    historyRetry.addEventListener("click", () => {
      void kronikaLoadHistory(kronikaParseRoute(kronikaLocationHash() || "#/history"));
    });
  }
}

function kronikaStartNavigation() {
  if (!kronikaDocumentReady()) return;
  if (typeof isPublicPublishedAudience === "function" && isPublicPublishedAudience()) {
    const nav = document.querySelector(".kronika-nav");
    if (nav) nav.hidden = true;
    kronikaHideProductSections();
    return;
  }
  const nav = document.querySelector(".kronika-nav");
  if (nav) nav.hidden = false;
  kronikaSyncReviewNav();
  kronikaBindShell();
  window.addEventListener("hashchange", kronikaOnHashChange);
  document.addEventListener("visibilitychange", kronikaOnVisibility);
  window.addEventListener("pagehide", kronikaPauseForHide);
  void kronikaFollowHash();
}

Object.assign(globalThis, {
  kronikaParseRoute,
  kronikaErrorCopy,
  kronikaSrcdoc,
  kronikaSafeCitationUrl,
  kronikaQuestionBytes,
  kronikaStartNavigation,
  kronikaFollowHash,
  kronikaLoadTimeline,
  kronikaSubmitQuestion,
  kronikaRetrySubmission,
  kronikaPollOnce,
  kronikaCancelActiveRequest,
  kronikaResumeUpdates,
  kronikaLoadRecord,
  kronikaSubmitApproval,
  kronikaReloadReview,
  kronikaNoteIdentity,
  kronikaClearPrivate,
  kronikaHideProductSections,
  kronikaMarkGalleryCurrent,
  kronikaRememberDetailsAddress,
  kronikaReleaseDetailsAddress,
});
/* KRONIKA_SHELL_END */
