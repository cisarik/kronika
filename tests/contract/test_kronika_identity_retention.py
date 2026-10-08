"""Retention ledger for the ADR-0085 Kronika sole identity.

Three live parts, none skippable, xfailable or disabled:

Part A pins SHA-256 values for the frozen blobs whose bytes no identity cut may
ever edit: the historical ADR bodies through 0084, the two superseded host
documents, and every applied Alembic revision through `0035`. A later cut may
move the Alembic directory path; it may not change those bytes.

Part B pins the exact set of tracked paths whose basename contains `framenest`
case-insensitively. Each later cut shrinks this set by an exact enumerated
difference, so a missed path rename fails loudly at the cut that owns it. The
expected set is a pinned literal in this file. It is deliberately not recomputed
from the working tree, which would make the assertion tautological.

Part C pins per-tree content-occurrence counts so that a missed content rename is
detectable even where the filename is already clean, as in
`deploy/ubuntu/fn-production-env-deploy`, and it pins the exact membership set of
tracked text paths whose content carries the token, because a scalar detects that
a total moved but not which occurrences moved.

The question-12 living-prose `\bframenest\b` scan over the living document list
is deliberately NOT implemented at this cut. It is armed in cut C7. That intent
is recorded here only; no disabled or skipped placeholder test exists.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_ALEMBIC_VERSIONS_GLOB = "src/*/infrastructure/persistence/alembic_environment/versions"

FROZEN_DOCUMENT_SHA256: dict[str, str] = {
    "docs/adr/0001-supported-python-version.md": "3f5698b977c44d3381752b98de2708817a43d3139805efdcdf1f6d4b3309b0dc",
    "docs/adr/0002-python-environment-and-dependency-manager.md": "6b014e4e6787a5ae090fed7fca1b7f27d2fdc59abb870fd165901a4e8761eddc",
    "docs/adr/0003-initial-server-api-framework.md": "141eb151a1bb20674d5c81a1d628806094835ad8ee3a7a5504dfa1ddfef55527",
    "docs/adr/0004-repository-layout.md": "4d8e198583b341155e98671b5848fdd1573e145613b60318008f64f31aab1b07",
    "docs/adr/0005-configuration-strategy.md": "e4ad7e14caa2c9494e40f47f19d912e527795b5caf426c390885abd1db3c4c35",
    "docs/adr/0006-macos-python-interpreter-provider.md": "e337088f8feaa132f173ad57cae6792ceb358a07f5f3b27a5756db08c3326d50",
    "docs/adr/0007-settings-library.md": "d24ce4e6a125a0b41ba9c8bcb5a79de4b64377c03ad851065d2699889d88178e",
    "docs/adr/0008-asgi-runtime.md": "8d8fe55e420585a03bb0fcf5c5530f2776885d76148c2c8d6ac5782fc65c47eb",
    "docs/adr/0009-structured-logging-approach.md": "b1e97bb44ba25e7e7974edef51fd0079c78c821b07a0b922f1c679ef3685e449",
    "docs/adr/0010-initial-persistence-foundation.md": "6a7462628d338d090664db0e85b81c705d2b70d77ad6aafebcda2f77c1cd8454",
    "docs/adr/0011-stable-domain-identities.md": "3bee51f271c6b666b380be2ea8a66eacfc32403775fe98b8b00f9b5026f7ec65",
    "docs/adr/0012-initial-device-registry.md": "31ebe857512cccf07a247482db85c310a31a208956c6484b01590dd2f261455b",
    "docs/adr/0013-initial-library-registry.md": "cd89a2fed1fc05945166323227ad7144be914639eb78bfd794dcf823bc94b9d1",
    "docs/adr/0014-safe-library-scan-preview.md": "a872d542cdc009c2132185f5a7f84d7c7e555ca92603f847ac8e61113ec9d311",
    "docs/adr/0015-deterministic-local-media-analysis-preparation.md": "bb74a7ccbccfafde314f853b8a57d2607b68376f011c7c5ec760c12ca7ab9fea",
    "docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md": "65c0e79675c903cf2e52fad4fda2cd97f9eb782c97d7ec5098bc18e2ba9fe5db",
    "docs/adr/0017-initial-local-web-application-delivery.md": "1e8172061476c223b56e1165cbf625bc2901e287e8f0383862d778d00331ce52",
    "docs/adr/0018-local-media-analysis-preview-api.md": "9356b06aaba319890c7d3e0fd83e8e230e059052b8dd0d3b70e9f85df2ac0d37",
    "docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md": "7cf3d261672e28ceeb028044e8ed9943ef0226b2d0e886e24aaed8fd4b53eb05",
    "docs/adr/0020-on-demand-ai-suggestion-review.md": "b7c11da86de2bec270c639578caca4426ff3a1450e6c1dc0c7fa716cc8e95f2e",
    "docs/adr/0021-tauri-desktop-shell.md": "e010ea1e35f50d413488616b91c3796c9826e17d7f80391fadf36a081b746fa1",
    "docs/adr/0022-selective-media-placement-and-server-aggregation.md": "85e074bc629ffe3b10f99f561a549cea0797f9dfe28257f42ffe468529f5652a",
    "docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md": "d5ddc82e437830ed5fff86c677de3a8fb69b514fccd2a5a49385dea5e3947fb1",
    "docs/adr/0024-cover-studio-and-ai-cover-candidates.md": "8476347d1dc5875a607d8c762d22156fbc79b7c316f16f3db22d6f334fe73388",
    "docs/adr/0025-minimum-persistent-media-catalog-foundation.md": "89348e9e15f1cff4f756d4a958d0b1f629053b1b31e7818cd0a760d6cfe9b53d",
    "docs/adr/0026-explicit-idempotent-scan-candidate-import.md": "b0c69889c064cb09bb453db05e54e6eb981deb7fc70159c6725f9c47e347a583",
    "docs/adr/0027-persistent-display-title-and-canonical-tags.md": "68488d3454c21c5cb714ab4caa4114c9d790674262d4a65db3d7accb174cf1e2",
    "docs/adr/0028-catalog-read-model-and-search-semantics.md": "c1b52c104e49ae5e6344d0564e024ca2f646217ffd0c8b6acf6a76332e10844c",
    "docs/adr/0029-persistent-plain-text-media-description.md": "7a6ab102a4cbeafc5255d95a3c4d6d24a5f5c5b21a09867ea5aa4d52377f05b0",
    "docs/adr/0030-automatic-processed-collection.md": "ea34b5f73c7f461bbaf6e98f72efd82ce5e3a6325666ab74be35c773de37abc5",
    "docs/adr/0031-fedora-systemd-service-foundation.md": "0673911c49929e11216051d856e1034d54c8b5c51f7d6a54765ffe244f054bc2",
    "docs/adr/0032-ubuntu-nuc-deployment-foundation.md": "7ebd7d912ce6a8245cc75644b138b419021aec63ca0a41a4693b4b003b811f9b",
    "docs/adr/0033-catalog-backup-and-recovery-foundation.md": "8ad14a7f6368024012af14a8f29ec1f6b74b8853d3ccbfcdf45fb15bd83f991b",
    "docs/adr/0034-canonical-analytic-programming-integration.md": "c4e0aa0318875d0eafb91b50f4b6cb32a355da749cbf5a001e917f07bc631060",
    "docs/adr/0035-authoritative-server-and-client-state-model.md": "f756d0b6a3b1c609dac5d88521c335daa97262682f5e15c945202b949961a7fd",
    "docs/adr/0036-production-ai-credentials-via-systemd.md": "10aa5f7da3f3ae3e6be31c8dcc1e92276d354b896269ec7d20be687faea1d01e",
    "docs/adr/0037-durable-upload-session-and-safe-ingest-foundation.md": "c61247b0fc6017bb881492bb0fde4bff28fd7281032bf81df8822521d6ea2e4c",
    "docs/adr/0038-bounded-upload-media-validation.md": "542719effd798f1957422aaa6eb3cda56756cfc21d0112cf4d2b61a70daa23e9",
    "docs/adr/0039-lifecycle-owned-upload-validation-orchestration.md": "ae81c662b369199a6ba67be7c31fbd72a51fbdadb768ea135424a1d0f8bbb10a",
    "docs/adr/0040-canonical-upload-byte-identity-foundation.md": "e732aff7a8e9cc4094593f67e95b3b84f12c9964b9785208a41a1e25fabdd7bc",
    "docs/adr/0041-exact-byte-upload-duplicate-disposition.md": "7a7a13819aca863581998c22191aba5bfd4461e2e1e1f850204b05c76624c4f2",
    "docs/adr/0042-atomic-upload-publication.md": "9db4a739ce428347c63c19af8984787e50860c6fdf71db63cdaf0d8430ab591c",
    "docs/adr/0043-upload-to-catalog-transaction.md": "5dfcaa971679bd181c636a3d997c19cd898b42b7c8d7b758a7583752a84824f1",
    "docs/adr/0044-durable-automatic-post-catalog-analysis.md": "c5085561a5af05a101c0c67fe8b61e082b33632d83680e39bd87784ed0b059c7",
    "docs/adr/0045-content-classification-and-movie-identification.md": "1c3fcd2e4296345f13b4729b5fb5acf22472ae45a0c39fc7c3eab8f5504a9470",
    "docs/adr/0046-youtube-manual-ingestion-and-provenance.md": "2812e17a005142a287448390d96113dae67c33cbd61a017142ddfd7032566c0f",
    "docs/adr/0047-operator-cli-configuration-and-working-directory-hygiene.md": "a07b054809e88b61102b40cbd0c497823ff685bc6544ad201f92b31aef0baf05",
    "docs/adr/0048-tailscale-remote-access-and-identity-foundation.md": "4c9ff4434a10f6e974c37afb50c4edfe5a0a4f1e010a8929a288b2a07ffff0d4",
    "docs/adr/0049-durable-content-publication-boundary.md": "fc6f9632230aa9d609428e20c7d301519f27d5399d0ef6fe1303206d0c52de07",
    "docs/adr/0050-durable-manual-cover-foundation.md": "8e296272fb96a7efe7e2efd1cd152112d951ec706a45ba0d9de71d533da1e951",
    "docs/adr/0051-administrator-catalog-removal.md": "8a8b0a26c27e4464dcc863eef9a6bedca3212fd39b28d48bf8fe414c33d022e5",
    "docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md": "a368387902d3d6cfe5496003700c86566d3022aca6620152c5d2486dff8246ba",
    "docs/adr/0053-ordinary-user-upload-submission-and-administrator-review-boundary.md": "1a2fb5067b6ea1108b859dec8bb4125363e4c77740d5a1167dbf58bf4b8d6192",
    "docs/adr/0054-requester-private-youtube-acquisition-and-promotion-boundary.md": "574a12bdb6d25c0e0c775926c4461746849591b665d12d2145006fbac7bc3eb0",
    "docs/adr/0055-youtube-creator-taxonomy-and-immutable-provenance.md": "91c998e802670fbc976ae6e6b39f150d571b043d5cc757145b86e4e17aca389c",
    "docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md": "2b1c618ff3777723b3ccf1ea93b193c735e5617830663239094a8ff12c4163be",
    "docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md": "e9aed13900b76e7d251097b7f9bfb452663149205367670797a6ffc6c046a14d",
    "docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md": "d97aa35ccc10fa93a168b4680835264a22faa4c8e5b264e6c0c1f6220bf1ea98",
    "docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md": "4cecdb8c0a423993da81049db8580b953bfd1cea35407a99926057c128f3d8da",
    "docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md": "55d76342c5262dc4871428f7c839673106cac0d36e05533087d13a44d4aabb60",
    "docs/adr/0061-x-meme-browser-companion.md": "2cd9b01780af9f3286621e60979d1c9141f99f6dc656f2a575cde7fe733cc2c5",
    "docs/adr/0062-per-user-media-alias-overlay.md": "eb6dd1badf56cf588b9fe1f7db3fada959361ca30acb9af5f606b3ef218d72cb",
    "docs/adr/0063-companion-side-panel-web-host.md": "936794900ace1ee45ce70fef6edd299cbd382524727e86a47e6c82e313546595",
    "docs/adr/0064-x-save-category-and-public-photo-acquisition.md": "16964ee944f7f15e5742e880966170a5e4300244dedb845ecd0fa671cc460490",
    "docs/adr/0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md": "c50b6a63dc66981af8f7936e2f5a558e0cc4dbbd5290941630c33459b595b811",
    "docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md": "722a2561efdbf2f21ccbe8a293bc5c2897547370c52b3d430d6c38c04f825694",
    "docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md": "0cfd9c30db045d0085f48063d621791c15ad509d289195f6fb7108d368e4780b",
    "docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md": "c51a3f422b414f14cfe915c3dc91ceaa2a4b1f11227168f086d64754cdb50bbf",
    "docs/adr/0069-five-tag-generic-media-suggestion-contract.md": "e2ae9a27c7d3a49919951798c7a15c66ae91a69fb8ccab6a614e65839e87e728",
    "docs/adr/0070-companion-exclusion-of-movie-workflows.md": "cd456428cc5b438e1dc9ceda7f5ba498a5bc92124de29dc5c42307b2ee8a0f12",
    "docs/adr/0071-native-side-panel-review-inbox-chrome.md": "9b7d99a14ba16ccd17338381f4f0ef67a86053a282197c78116b6019f3eaceb2",
    "docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md": "65482d8a0eeeb99ad0baf4c1d038ef82ec7e1e170be89053f2d19fed64b4d75c",
    "docs/adr/0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md": "9ceef42017ada04f5835ce1ece4fedd59be440c29cb0f1950f07d78f73823a5a",
    "docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md": "5cd52ec9b713dabaab48e53e2e0aaa203677f63e05d30320e54ed3501a1fd10b",
    "docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md": "5fce22c3e3c04655081858548bf76a3f2528d744aa3b901c492ad7e47490a7a7",
    "docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md": "501699f5d70d0ffe06df7417742e40dba9a0f82624b41209f0eaa897bb55a5a5",
    "docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md": "8d8d535a2e82253a98fd96fe2685f2694dd297c86d1629a2e7dd50133d148aec",
    "docs/adr/0078-gallery-card-ai-per-field-review.md": "f366bdd747e9423822f5532670c74b5da3a952d8464fa6034bbfe52f6edd6297",
    "docs/adr/0079-administrator-automatic-analysis-runtime-setting.md": "c68f600be6a9bc2ed5b70763b177299335f37d22ed30b73bcd93333925da2aaa",
    "docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md": "1cb16f1184e5470f36b11fa59a77b11fb50116d1fb6db5b3941ad457309d449a",
    "docs/adr/0081-declarative-openai-compatible-provider-registry-and-administrator-vision-probe.md": "fc0391e43b878a6588a3ada610738249b3add0e03ae111af8b2a93a10a89909f",
    "docs/adr/0082-kronika-one-product-and-private-records.md": "150e3236b9b66f7ea9ec657aa183bdacc1660f27e79424bd0e65cadf02d73c33",
    "docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md": "a9bd8f5abb06a40b06d27addff8594f8d001909548aab1cc5b6b9b0d1fad956a",
    "docs/adr/0084-administrator-managed-research-settings-and-versioned-pricing.md": "8c59e6d4f239e5dc1ad7b1919817837f01c60db4f7666090519f76f14896db08",
    "docs/FEDORA_SERVICE.md": "c95b2c091cf6d7e82dc9d4b2ea1b7ec5161588094fb9758814c49756a1fad756",
    "docs/NUC_HOST_BASELINE.md": "f7a47391dded54c51bf863a711b18a8e6875420d2aa8ddc8fd2e943980cef98f",
}

FROZEN_ALEMBIC_SHA256: dict[str, str] = {
    "0001_initial_foundation.py": "7fe8de6d406bd7c7768c6ca66eff324185262d8e72586278a710a3cc609f295c",
    "0002_device_registry.py": "e43419d76e3161e99c78e5a694e032c58013e1e4794fd74df13542994a04cbdd",
    "0003_library_registry.py": "56aba5b474e76c04206df88785e590b33c5b5bfdc864bacacbee1da42fdf705b",
    "0004_media_catalog_foundation.py": "87c93511b47ad5a15b6361ffa69e227d6b44d12323eee3113888c80932c6a419",
    "0005_media_metadata_and_canonical_tags.py": "b325ad4316c3bee5466074697e6ed6a1cc8a50cd5a13ca59541ab7886ee2a6f7",
    "0006_persistent_media_description.py": "4c82d3af6e117bed3731c875be8e424a00fa2b3615d68e0c5b8e6b053de5864c",
    "0007_automatic_processed_collection.py": "28c0b5ca29cb853e0c0184eed05f554e5a3445af720cd9ec025a8d01d10cb818",
    "0008_upload_sessions.py": "95752e4f6373e5c151f38344c1ec210cd6ec982aa472000303b19345b96cf91d",
    "0009_upload_session_completeness.py": "7d92eebb8b471bf461d7b4386a67928ab5261dff4ec575da2c5dbcf6a1f451c4",
    "0010_upload_validation_evidence.py": "83713c2b3dc9a994bf89b8a3ee018048cdaf9d4dfc67b806e0001f7ebba80894",
    "0011_upload_byte_identities.py": "38c45926a7364ba46b1bcb1409a685b18e073dceb54c802f3f6972aa5d7f332b",
    "0012_upload_duplicate_disposition.py": "9624a19c4d300056eac682870c0fc61d6983f98376b3a432e9e19014626f59c7",
    "0013_atomic_upload_publication.py": "f6420a5ffc69ccd3c424763cb85322c4e4147293555d42165a1da0e9624b20b2",
    "0014_upload_catalog_linkage.py": "4b82a50134da2820ad0f8ea52077414540ea436c552f8c409839e9e16befb6f4",
    "0015_media_analysis_runs.py": "c3fbe3e7246599a211413aa976293c98f2cece3b1950090c8ad266effef35b00",
    "0016_still_image_media_kinds.py": "6a2bdd8e7ead7a5cd40f22c86c9362560bcb77d1ad9a138453b27808997ca4f8",
    "0017_content_classification_and_movie_identification.py": "2a02319fed16ded32ef1d398eab6424b5d303969b918268fbb5ee9795fc28484",
    "0018_analysis_run_history_and_active_uniqueness.py": "c2582e74f7b136ecb01a0254917b9647cbc5a922f058b8120eb107c1aac3435a",
    "0019_youtube_manual_acquisition.py": "d37e6210cfd47ad9fbeb012e269f2c2ebff2fdd6bff4f030c1839821dc0a1ee9",
    "0020_security_audit_events.py": "e18e1b55a903504894a9cb9a87de05b0085141a47b42faf3d9bdb81aa2a1f722",
    "0021_content_publication.py": "6920cd8a383c33f56c6baa5cd7b2230fd1d31fd63a195155c43800fa788f1c9b",
    "0022_media_covers.py": "3e5288979e523f8897ae31276e44a9950ed46c00181f95e23e474b21c1bdad3f",
    "0023_still_image_covers.py": "58efb070b5409ea69969203062de5e964de7770b86bd3faf230c8dae0cf059f7",
    "0024_catalog_removal_receipts.py": "f097eaba7ab5c088b346c710e51468cd06b7cfd75fe06c0635b058bed9d5ed03",
    "0025_upload_session_ownership_and_duplicate_mode.py": "c9415c84c9bfc94e36b142ab35a31c3dd5dd311b9b7a382777ed5e200b23900a",
    "0026_youtube_requester_ownership.py": "be7de26ad0fa780211e8a4219a00d66757f8def88e1e06ec589b1f153dc3fdc2",
    "0027_youtube_creator_taxonomy.py": "94358d837d80fc9b8944a23023b9e7b1cf061257025da569a82f65ce14f26934",
    "0028_x_requester_acquisition.py": "73b23326c3fadd78d823f7c0ddd0f5eb45395a6fdf91313d8bacd0ecdd55fa03",
    "0029_media_user_alias_overlay.py": "fde17806a87a07ae2292ed460c35c358f002b7a8dc16f78a1121076048496cd7",
    "0030_x_claim_requested_content_category.py": "b0d02e88f8373765138b455810662c8df6a0c1a53ccdb1f647cc5b0c91329ed2",
    "0031_companion_review_inbox.py": "3b6a4ff6d0a6a4f61ab098e9e2e1a658e566a4d2963086a0f7476e35908c5697",
    "0032_companion_review_tag_sources.py": "d3945b58903b2345caa81243dc867475e601afe743d6a5693815d91c61683dcd",
    "0033_media_analysis_proposals.py": "27a206cef7a114869fdad52110836e42fd0ef28116dd79ccf693c2a7845172d8",
    "0034_kronika_records.py": "f59b6bc28910378c5699057768c5740005ed12b56f79afbf1c18515b78823110",
    "0035_research_requests_and_accounting.py": "a05ab13a9d4bfa7f0d7fbe532d8c8d3cb43be1d826fc5a97d86a62ebc89c48c2",
    "__init__.py": "481b131444236a2cc2544f8a83585f28c52da320c73bbb94181edd5b1bc8447c",
}

EXPECTED_FRAMENEST_BASENAME_PATHS: frozenset[str] = frozenset(
    {
        "deploy/systemd/framenest-ai-credential-nvidia-nim.conf",
        "deploy/systemd/framenest-ai-credential-opencode-go.conf",
        "deploy/systemd/framenest-ai-credential-vercel-ai-gateway.conf",
        "deploy/systemd/framenest-catalog-backup.service",
        "deploy/systemd/framenest-catalog-backup.timer",
        "deploy/systemd/framenest-catalog-offdevice.service",
        "deploy/systemd/framenest-catalog-offdevice.timer",
        "deploy/systemd/framenest.env.example",
        "deploy/systemd/framenest-research-credential.conf",
        "deploy/systemd/framenest.service",
        "deploy/ubuntu/framenest-catalog-export-v1",
        "deploy/ubuntu/framenest-release",
        "deploy/ubuntu/framenest_release.py",
        "framenest",
        "scripts/operator/infosec/framenest_log_triage.sh",
        "scripts/operator/infosec/framenest_public_surface_check.sh",
        "scripts/operator/infosec/framenest_socket_permissions_check.sh",
        "scripts/operator/network/framenest_mullvad_egress.fish",
        "scripts/operator/network/framenest_mullvad_egress.sh",
        "scripts/operator/network/framenest_nuc_worker_gate.fish",
    }
)

PER_TREE_FRAMENEST_FILE_COUNT = {
    # KSI-IMPL-C2B moved `src` by -1 and `extension` by -2, and nothing else.
    # No path was renamed, so `src` losing a file is content-only:
    # `adapters/api/web/index.html` carried five user-visible `FrameNest` prose
    # strings and now carries none, so it leaves this content set entirely.
    # In `extension`, `manifest.json` (three display fields) and `ui/sidebar.html`
    # (five human-readable strings) each lost their last occurrence and left.
    #
    # KSI-IMPL-C2C moved `extension` by -2 and nothing else. `ui/save.html`
    # (document title and heading) and `ui/picker.html` (document title) each
    # lost their last occurrence and left this content set. No path was renamed,
    # and `ui/save.js`, `ui/picker.js`, `ui/sidebar.js`, `shared/messages.js` and
    # `content/x_adapter.js` all stay, each still naming the retired spelling
    # through a CSS or DOM hook, a port name, a storage key or a global.
    #
    # KSI-IMPL-C3B moved `src` by -68 and `tests` by -138, and nothing else.
    # Both movements are whole-file consequences of the package move, not
    # content-only renames: a file whose every retired-spelling occurrence was a
    # `framenest.<module>` import, a `src/framenest/...` path, a wheel or
    # dist-info name or a logging identifier now carries none and leaves this
    # content set. One path was added to `src` and it carries the token, because
    # it is the Alembic compatibility shim, whose entire purpose is to name the
    # retired spelling. `deploy`, `scripts`, `docs` and `extension` are unmoved:
    # this cut touches none of them.
    #
    # KSI-IMPL-C5 moved `src` by -2, and nothing else. `infrastructure/ai/
    # constants.py` and `infrastructure/ai/vision_probe.py` each held the
    # retired spelling only in the one outbound identity this cut switched
    # (`framenest/0.1` and `framenest-vision-probe-v1`), and both files now
    # carry no retired spelling at all, so they leave this set. Every other
    # switched writer file retains the former spelling as a named historical
    # constant and stays.
    "src": 184,
    # KSI-IMPL-C4A moved `deploy` by +1 and `tests` by +1, and nothing else.
    #
    # `deploy` +1: `deploy/ubuntu/kronika-release` and
    # `deploy/ubuntu/kronika_release.py` each join this set because the engine
    # and its canonical entry point still carry the retired spelling in their
    # writer constants, the accepted marker tables, the host layout they own and
    # the retained wrapper they forward to. `deploy/ubuntu/framenest-release`
    # leaves it because the retained Fish wrapper no longer names the retired
    # engine file. Net +1.
    #
    # `scripts` is unmoved: `framenest_nuc_worker_gate.fish` becomes a wrapper
    # that carries no retired spelling and leaves this set, while
    # `kronika_nuc_worker_gate.fish` joins it by declaring both accepted
    # identity prefixes. One in, one out.
    #
    # `tests` +1: the new
    # `contract/test_kronika_durable_analysis_identity_readers.py`, which pins
    # both spellings of every durable analysis identity and therefore names the
    # retired ones beside the canonical ones.
    #
    # KSI-IMPL-C3A moved `tests` by +1 and nothing else. The one addition is the
    # new `contract/test_kronika_product_string_agreement.py`, which imports the
    # product modules whose brand strings it pins and therefore carries the
    # lowercase token. `support/kronika_identity.py` was added by the same cut and
    # deliberately carries no token at all, because it reads the extension
    # manifest rather than importing the product package, so it correctly stays
    # out of this count and out of the content set below.
    #
    # KSI-CORR-C3B-2 moved `tests` by -1 and nothing else. Exactly one file left
    # this content set: `contract/test_workspace_media.py`, whose single retired
    # occurrence in the whole file was a `src/framenest` path that now names the
    # moved package directory. It is a whole-file consequence of that one path
    # literal, not a content-only rename, and no path was added to any tree.
    #
    # KSI-IMPL-C4B added two files to `deploy` and one file to `tests`, and
    # moved nothing else. The `deploy` additions are the canonical
    # counterparts of the two retired artifacts whose content carries a named
    # frozen residue: `kronika-catalog-offdevice.service` and
    # `kronika.env.example` both keep `/mnt/framenest-catalog-offdevice`
    # exactly as written. The other nine canonical artifacts added by this cut
    # carry no retired spelling in any case, so they correctly stay out of
    # this content set. The `tests` addition is the new
    # `contract/test_kronika_identity_migration.py`, which pins the former
    # layout, the former environment keys and the frozen mount in order to
    # prove the typed transformation. No existing file left this set.
    #
    # KSI-IMPL-C5 moved `src` by -2 and `tests` by -2, and nothing else.
    # The `src` paragraph above this block names the two departing files.
    #
    # `tests` -2: three files left and one entered. `integration/
    # test_media_sidecar_roundtrip.py` no longer names the retired writer suffix
    # because it locates the writer's canonical output;
    # `unit/infrastructure/ai/test_vision_probe.py` no longer pins the retired
    # probe version; and `unit/application/
    # test_movie_identification_lifecycle.py` no longer pins the retired movie
    # prompt version. The new `contract/
    # test_kronika_durable_writer_identities.py` enters with the retired
    # spellings it pins as historical. Net -2.
    "tests": 183,
    "deploy": 22,
    "scripts": 7,
    "docs": 88,
    "extension": 8,
}

# Occurrence counts, not file counts. A content-only rename inside an already
# matching file leaves the file counts unchanged, so these are the measures that
# actually detect a missed content rename. See the `fn-production-env-deploy`
# case, whose filename is clean while its content names `framenest`.
PER_TREE_FRAMENEST_OCCURRENCE_COUNT = {
    # KSI-IMPL-C2 moved `extension` by -10 and `tests` by +59, and nothing else.
    #
    # `extension` -10, per file: `background/service_worker.js` -3 (the inline
    # `frameNestOrigin` key gave way to `companion.STORAGE.origin`, and the three
    # inline mutation-header literals collapsed into one shared constant);
    # `shared/messages.js` -1 (the two internal protocol literals became
    # `kronika.*`, while `framenest-companion.v1` and `framenest.review-inbox`
    # each survive once as the retained retired spelling);
    # `ui/picker.js` -4 and `ui/sidebar.js` -2 (the `frameNestOrigin` literals
    # gave way to the shared resolver, which retains the retired name once).
    #
    # `tests` +59 is entirely the new Class 1 to Class 4 assertions, which name
    # the retired spellings they pin. Per file: companion_review +27,
    # tailscale_identity_frontend +13, x_companion_extension +10,
    # test_local_web_application +7, gallery_filter_controls +3,
    # youtube_acquisition_cockpit -1 (the vm preamble now reuses the shared
    # constant instead of repeating the literal).
    #
    # KSI-CORR-05 dual-accepts the companion web protocol, so it moved three
    # scalars and nothing else. `src` +1 and `extension` +1 are the single
    # retired `framenest.companion.web.v1` spelling each new ordering-constraint
    # comment names beside its protocol constant;
    # `tests` +12 is companion_web_bridge +9 (the three acceptance cases, the
    # emit-spelling test and the two new protocol constants it pins) and
    # companion_review_extension +3 (the synthetic end-to-end delivery of the
    # retired spelling in that test).
    # KSI-IMPL-C2B moved `src` by -7, `extension` by -14, and `tests` by +1.
    #
    # `src` -7: `media_content_api.py` -1 and `application/media_content.py` -1
    # for the download-filename fallback, and `adapters/api/web/index.html` -5
    # for the five user-visible prose strings.
    #
    # `extension` -14: `manifest.json` -3 (display name, description, action
    # title), `ui/sidebar.html` -5 (title, wordmark, origin label, origin note,
    # frame title), `shared/messages.js` -1 (the context-recovery copy only),
    # and -5 for the mirrored download filename across
    # `background/service_worker.js` -2, `content/x_adapter.js` -1,
    # `ui/picker.js` -1 and `ui/sidebar.js` -1. No CSS, DOM hook, port name,
    # storage key, alarm name, protocol string or API version moved.
    #
    # `tests` +1: `x_companion_extension.test.js` +2 and
    # `companion_review_extension.test.js` -1, and
    # `contract/test_media_content_api.py` is unmoved because its pin lost one
    # retired spelling and the new fallback test adds one back.
    #
    # KSI-IMPL-C2C moved `src` by -10, `extension` by -31 and `tests` by -26.
    #
    # `src` -10: `adapters/api/web/app.js` -9 for eight user-visible prose
    # strings (the two provider-pong words, the removal confirmation and the
    # credential hint counted once each, the acquisition confirmation once, the
    # health detail once, the provider-credential reason once and the two AI
    # disclosures once each), and `application/media_content.py` -1 for the
    # deterministic download-filename fallback stem.
    #
    # `extension` -31, per file: `shared/messages.js` -13 (thirteen outcome names
    # across twelve literals, one of which names the brand twice),
    # `ui/sidebar.js` -9 (eight status and aria strings across eight literals,
    # one of which names the brand twice), `content/x_adapter.js` -4,
    # `ui/save.html` -2, and `ui/save.js`, `ui/picker.html` and `ui/picker.js`
    # -1 each. No CSS, DOM hook, port name, storage key, alarm name, protocol
    # string, API version or mutation-header spelling moved.
    #
    # `tests` -26, per file: `x_companion_extension.test.js` -30 (thirty-two
    # repointed display assertions and one test title, less the three brand
    # carriers its new derived-brand header adds),
    # `companion_review_extension.test.js` +6 (the extended agreement guard names
    # the retired spelling in its machine-read exclusion and its negative
    # assertion, and names it in its own provenance comment; the repointed
    # side-panel status pin lost one and the new derived source reads add none),
    # and `companion_settings_automatic_analysis.test.js`, `test_local_web_
    # media_playback.py`, `test_media_content_application.py` and
    # `youtube_acquisition_cockpit.test.js` net -1, 0, 0 and -1. The two
    # download-stem pins each lost the retired stem and added a retired-spelling
    # prohibition in its place, so each is provably unchanged.
    #
    # KSI-IMPL-C2D moved `src` by -49 and `tests` by -14, and nothing else.
    #
    # `src` -49 is exactly the forty-nine user-visible and operator-facing
    # `FrameNest` strings this cut retired, one per changed line, across
    # twenty-three files: `domain/media.py` -3, `domain/media_cover.py` -2,
    # `domain/libraries.py` -2, `adapters/api/x_request_api.py` -2,
    # `application/x_acquisition.py` -3, `adapters/cli/youtube.py` -3,
    # `adapters/cli/development.py` -3, `infrastructure/runtime/development.py`
    # -12, `infrastructure/runtime/production.py` -3,
    # `infrastructure/persistence/cli.py` -3, and -1 each in
    # `domain/media_metadata.py`, `domain/media_user_alias.py`,
    # `domain/identities.py`, `domain/devices.py`, `domain/uploads.py`,
    # `adapters/api/media_alias_api.py`, `application/library_workflow.py`,
    # `application/movie_identification.py`, `infrastructure/ai/prompts.py`,
    # `infrastructure/persistence/alembic_environment/env.py`,
    # `adapters/cli/ai.py`, `configuration.py` and `server.py`. No docstring, no
    # comment, no class or module name, no path expression, no Alembic revision
    # and no cross-boundary protocol string moved. `extension`, `deploy`,
    # `scripts` and `docs` are unmoved because this cut touches no JavaScript
    # and no document.
    #
    # `tests` -14 is exactly the fourteen repointed literals, one per changed
    # line, across nine files: `unit/domain/test_media.py` -3,
    # `unit/domain/test_libraries.py` -2,
    # `unit/application/test_library_workflow.py` -2,
    # `contract/test_operator_cli_hygiene.py` -2, and -1 each in
    # `unit/domain/test_identities.py`, `unit/domain/test_devices.py`,
    # `unit/infrastructure/runtime/test_development_runtime.py`,
    # `contract/test_x_request_api.py` and
    # `integration/test_development_launcher.py`. No test was added or removed,
    # no negative assertion was repointed, and no test fixture literal was
    # touched.
    # KSI-IMPL-C3A moved `tests` by +102 and nothing else. No product string was
    # renamed by this cut; every movement is test-side, and all of it is additive
    # except one removal. Per file:
    # `contract/test_kronika_product_string_agreement.py` +84 (the new file: its
    # pinned inventory names all fifty brand-bearing runtime string nodes by their
    # production paths, its product imports, and the one deliberately dual-spelled
    # mutation-header sentence it must pin verbatim),
    # `unit/infrastructure/runtime/test_development_runtime.py` +4 and
    # `unit/test_server_runtime.py` +4 (product imports, and the existing
    # `python -m framenest.server` launch-module marker its new branch fakes
    # reuse), `contract/test_x_request_api.py` +3 (the alias-error import and two
    # class references), `contract/test_youtube_cli.py` +2 (the settings type on
    # two fake client constructors), `unit/domain/test_media_cover.py` +2,
    # `unit/domain/test_media_metadata.py` +1,
    # `unit/domain/test_media_user_alias.py` +1,
    # `unit/domain/test_upload_sessions.py` +1, `unit/test_configuration.py` +1,
    # and `contract/test_development_cli.py` -1.
    #
    # That single removal is the repaired `RuntimeStatus` fake, which carried the
    # only retired spelling in its file. It was fixture data rather than an
    # assertion, so removing it lost no coverage: the fake now builds the same
    # derived message the real runtime result carries, and
    # `test_cli_status_open_and_logs` asserts it. No negative assertion was
    # repointed or removed by this cut.
    #
    # KSI-IMPL-C3B moved `src` by -1224 and `tests` by -2729, and nothing else.
    # Both movements are the same single cause seen from two trees: every
    # `framenest.<module>` import, every `src/framenest/...` path, every
    # `framenest-<package>.dist-info` and `framenest-*.whl` artefact name and
    # every logging identifier became its canonical spelling, so the occurrences
    # are gone rather than moved. Per category in `src`: the module-path imports,
    # the four logging handler, filter and formatter names, the four Tailscale
    # log-context keys, the `framenest_umask` connection key, and the
    # `version("framenest")` metadata lookup. Per category in `tests`: the same
    # module-path and `src/framenest/...` rewrites, the repointed capture
    # packaging and wheel-glob assertions, the repointed root-launcher paths, and
    # the `logging.getLogger("framenest")` namespace assertions.
    #
    # Nothing in `src` or `tests` gained a retired spelling. The four default
    # data locations, the `FRAMENEST_` environment prefix, the command error
    # codes, the sidecar suffix, the `framenest/0.1` user agent, the capture
    # state-directory name and the Unix account all keep it, and this cut does
    # not own them.
    #
    # KSI-IMPL-C5 moved `src` by -5, and nothing else. Per file:
    # `infrastructure/ai/constants.py` -1 and `infrastructure/ai/vision_probe.py`
    # -1 (each file's only retired spelling became its canonical one),
    # `infrastructure/filesystem/media_sidecar.py` -1 (the sidecar temporary
    # prefix; no recognizer reads it), `infrastructure/persistence/
    # catalog_backup_ops.py` -1 (a hardcoded retired temporary prefix became a
    # shared accepted-prefix constant), and `infrastructure/persistence/
    # catalog_backup_workstation.py` -1 (a duplicated retired snapshot-purpose
    # assignment was folded into the single canonical one). Every other switched
    # module moved its retired literal from the writer constant to the retained
    # historical constant, one for one, and the canonical values it now emits
    # carry no retired spelling, so those files are unmoved.
    "src": 1690,
    # KSI-CORR-C3B-2 moved `tests` by -16 and nothing else. Sixteen lowercase
    # occurrences left the `tests` tree, all of them test-side references to the
    # names C3-B moved: four `src/framenest` path literals across four files
    # (including one inside a module docstring), five `dictConfig` keys
    # (`framenest_json` three times, `framenest_redaction`, `framenest_stderr`),
    # two `logging.getLogger("framenest")` root-namespace arguments, three
    # `framenest.public_published_api`/`_application` logger names, and two
    # `framenest/**` source globs. No occurrence was added to any tree and no
    # capitalized occurrence moved, so the capitalized counts below are unchanged.
    # KSI-IMPL-C4A moved `deploy` by -11, `scripts` by -18 and `tests` by
    # +36, and nothing else. Every movement is one of two causes: the engine
    # moved to its canonical filename, or a call site stopped naming a retired
    # spelling as a literal.
    #
    # `deploy` -11, per path: `framenest_release.py` -49 and `kronika_release.py`
    # +41 are the same file at two names, so the move itself is -8, and the
    # retained Fish wrapper is -4 because it no longer names the retired engine
    # file. The canonical Fish entry point is +1.
    #
    # `scripts` -18: the retained gate wrapper is -20 because it no longer reads
    # any `FRAMENEST_` variable by name, and the canonical gate is +2 because it
    # declares both accepted prefixes as data.
    #
    # `tests` +36, per path: `test_nuc_release_remote_contract.py` +27 (the
    # marker matrix, the installed-unit guard demonstrations, the deploy-lock
    # reclaim cases and their command assertions),
    # `test_kronika_durable_analysis_identity_readers.py` +11 (the new file, a
    # symmetric acceptance table per durable identity),
    # `test_operator_network_scripts.py` +5 (both gate paths, both accepted
    # prefixes, and the conflict case), `test_nuc_release_docs.py` +4 (the
    # canonical and retained entry points and the two new exit codes),
    # `test_nuc_release_source_contract.py` -3 (its literal marker paths became
    # resolved markers), and `test_kronika_capture_services.py` -8 (the capture
    # activation runner resolves markers through the shared resolver).
    #
    # KSI-IMPL-C4B moved `deploy` by +45 and `tests` by +127, and nothing else.
    #
    # `deploy` +45, per path: `kronika_release.py` +42 (the accepted layout
    # table, the canonical target layout, the migration constants and unit
    # artifact table, the typed path classes, and the migration command
    # builders), `kronika-catalog-offdevice.service` +2 and
    # `kronika.env.example` +1, both the named frozen mount only. The other
    # nine canonical artifacts carry no retired spelling and contribute zero.
    #
    # `tests` +135, per path: the new
    # `test_kronika_identity_migration.py` +120 (its former layout readings,
    # the sample environment, the frozen mount, the production-host phase
    # command assertions and the recovery assertions),
    # `test_nuc_release_remote_contract.py` +8 (the two effective layout probe
    # answers and the canonical capture-pointer branch), and
    # `test_kronika_capture_services.py` +7 (the same layout probe answers for
    # the capture activation harness).
    #
    # KSI-IMPL-C5 moved `tests` by +2 and `deploy` by -3, and nothing else.
    #
    # `tests` +2: the new `contract/test_kronika_durable_writer_identities.py`
    # +26 (the identity table, the literal writer and historical pins, the
    # collapse demonstrations, the recognizer cases and the two round trips),
    # less 24 occurrences retired by writer-pin updates. The retirement is
    # `unit/domain/test_media_sidecar.py` -3, `contract/
    # test_kronika_durable_artifact_readers.py` -2, `unit/application/
    # test_media_suggestion.py` -2, `unit/infrastructure/ai/test_nvidia_nim.py`
    # -2, `contract/test_nuc_release_source_contract.py` -2, `contract/
    # test_nuc_release_remote_contract.py` -2, and -1 each in
    # `contract/test_kronika_cli_and_release_readers.py`, `contract/
    # test_media_analysis_lifecycle_api.py`, `contract/test_sidecar_cli.py`,
    # `integration/test_media_sidecar_roundtrip.py`, `unit/application/
    # test_media_sidecar.py`, `unit/application/
    # test_movie_identification_lifecycle.py`, `unit/infrastructure/ai/
    # test_vision_probe.py`, `unit/infrastructure/backup/
    # test_catalog_backup_offdevice.py`, `unit/infrastructure/backup/
    # test_catalog_backup_workstation.py`, `unit/infrastructure/filesystem/
    # test_media_sidecar_store.py` and `unit/infrastructure/media_analysis/
    # test_movie_contact_sheet_selection.py`. Sum -24; net +2.
    #
    # KSI-IMPL-C6P2 moved `tests` by +75 and `deploy` by +6, and nothing else.
    #
    # `deploy` +6, one file: `kronika_release.py` adds nine retired spellings
    # and removes three. The nine are the structural transformation guards that
    # name the token they classify: `User`/`Group`, `LoadCredential`,
    # `ExecStart`, the structured `path=` form and the sudoers run-as and
    # command checks each test for the retired spelling; the three removals are
    # the old routine-scratch helper paths and the old token-replacement regex
    # that the structural parser replaces. The engine adds no host literal.
    #
    # `tests` +75: `test_kronika_identity_migration.py` +74 and
    # `test_recovery_cli.py` +1. The migration additions are the new fixtures
    # and acceptance tests for the exact-state binding, the vendor drop-in and
    # unit observations, the structural systemd and sudoers forms, the
    # installed-mode evidence, the pointer guard and switch, readiness, the
    # scheduling preservation and the capture-identity comparison; the one
    # recovery addition pins the retained-tuple compatibility of the temporary
    # workstation layout selection. No test file was added or removed.
    #
    # KSI-IMPL-C6P1 moved `tests` by +41 and `deploy` by +1, and nothing else.
    #
    # `deploy` +1, one file: `kronika_release.py` adds three retired spellings
    # and removes two, for +1 net. Added: the exact owned helper path
    # `{REMOTE_DEPLOY_DIR}/framenest_release.py` that the shared-lock release
    # removes, and the two conditional reverse-rename forms
    # (`usermod -l framenest -d ...` / `usermod -l framenest kronika`) that
    # replace the single unconditional reverse command. Removed: the
    # `"framenest.service"` literal skip in `stop_writers`, now the layout's own
    # service name, and the single old reverse command.
    #
    # `tests` +41, one file: `test_kronika_identity_migration.py` adds 42 and
    # removes one. The additions are the C6-P1 stateful production-adapter
    # boundary and its matrix: the former account/group/state-root fixtures, the
    # three writer unit names with their enablement and activity, the reverse
    # rename assertions, the shared-exclusion concurrency assertions, the
    # durable-boundary recovery-selection assertions and the observed-schedule
    # restoration assertions. The one removal is the legacy reverse-rename
    # assertion, which now pins the `-d /var/lib/framenest` form.
    #
    # KSI-IMPL-C6P2 moved `tests` by +93 (2049 -> 2142): +92 in
    # `test_kronika_identity_migration.py` for the exact-state binding,
    # vendor-installation, non-unit-artifact, structural-transformation,
    # installed-mode, pointer, readiness, scheduling, capture-identity,
    # literal-membership and pure-guard tests and their fixtures, and +1 in
    # `test_recovery_cli.py` for the retained-tuple compatibility pin.
    #
    # KSI-C-RUNBOOK moved `tests` by +8 (2142 -> 2150). The three new
    # production composite tests in `test_kronika_identity_migration.py` add
    # ten occurrences (the writer unit names in the quiesce order test, the
    # checkpoint command, the observed-unit assertion and the ordering
    # probes), the three new engine-derived lock tests in
    # `test_nuc_release_docs.py` add six and remove four, and the updated
    # schema-continuation pin in `test_nuc_operator_runbook.py` removes the
    # four residual literal-path assertions, for +2, +10 and -4.
    "tests": 2150,
    # `deploy` -3: in `kronika_release.py` the two writer marker constants and
    # the release-manifest identity key now emit the canonical spelling. The
    # accepted marker tables keep both former spellings as frozen data, so a
    # historical release tree still resolves.
    #
    # KSI-IMPL-C6P2 moved `deploy` by +6 (244 -> 250): `kronika_release.py`
    # adds the nine structural-transformation token guards and removes the
    # routine-scratch helper paths and the old token-replacement regex.
    "deploy": 250,
    "scripts": 86,
    # KSI-C-RUNBOOK moved `docs` by +13 (1216 -> 1229) and nothing else. The
    # corrected runbook adds fifteen occurrences and removes two: the new
    # shared-release-lock section names the lock directory, the `.owner`
    # record, every deploy/rollback artifact path, both reclaim reasons, both
    # quarantine paths and the non-recursive lock creation command, and the
    # two replaced schema/lock bullets are removed. No host-path literal, no
    # capitalized spelling, no environment token and no unit-account line
    # moved; the corrected annex introduces no permanent schema pair.
    "docs": 1229,
    "extension": 145,
}

# The exact tracked text paths, this ledger excluded, whose decoded content
# contains `framenest` case-insensitively. Part B is membership over basenames
# and cannot see a content-only rename inside an already matching file; the
# Part C scalars below detect that a total moved but not which occurrences
# moved, so a partial rename re-pinned to match the partial work still passes
# them. This set is membership per path, so no compensating swap is possible: it
# fails when a file that should have been renamed still carries the token and
# when a file that should not have been renamed no longer does. It is
# deliberately not recomputed from the working tree, which would make the
# assertion tautological.
#
# KSI-IMPL-C2B removed exactly three paths from this set, and no path was added.
# Each removal is a whole-file consequence of retiring user-visible text, not a
# path rename: `extension/manifest.json` lost its three display fields,
# `extension/ui/sidebar.html` lost its five human-readable strings, and
# `src/framenest/adapters/api/web/index.html` lost its five prose strings. Each
# of those files now carries no `framenest` spelling in any case, so it is
# correctly absent rather than merely renamed. No file that still carries the
# token was dropped, and no file that no longer carries it was retained.
#
# KSI-IMPL-C2C removed exactly two paths and added none. `extension/ui/save.html`
# and `extension/ui/picker.html` each lost their last occurrence, so they are now
# correctly absent rather than merely renamed. Both remain tracked and both are
# still served and still rendered; a later cut that reintroduces any `framenest`
# spelling into either file will now fail loudly here.
#
# KSI-IMPL-C3A added exactly one path and removed none.
# `tests/contract/test_kronika_product_string_agreement.py` joins this set because
# it imports the product modules whose brand-bearing string nodes it pins, so its
# inventory necessarily names them by their `src/framenest/...` paths. It is not a
# renamed path and it is not a content-only rename of an existing member. The
# companion `tests/support/kronika_identity.py` added by the same cut is
# deliberately absent: it reads `extension/manifest.json` and imports nothing from
# the product package, so it carries no token and correctly stays out.
#
# KSI-IMPL-C3B added exactly two paths and removed exactly 209. Both additions
# are new files whose content names the retired spelling on purpose: the root
# `./kronika` launcher, which still reads and exports `FRAMENEST_ENV_FILE`, and
# `src/kronika/infrastructure/persistence/alembic_compat.py`, the ADR-0085
# Alembic compatibility shim, whose four exposed module names are the retired
# spelling by definition.
#
# The 208 removals are 207 whole-file consequences of the package move - a file
# whose every occurrence was a `framenest.<module>` import or a
# `src/framenest/...` path now carries none - plus two singletons. `ap.project.conf`
# left because its only retired spellings were `provenanceModule` and the
# runtime-info import, both of which this cut canonicalises, and the root
# `framenest` launcher left because it became a thin pass-through wrapper and
# lost its last occurrence with its last message. Both `deploy/**` and
# `scripts/**` members are untouched and stay. No file that still carries the
# token was dropped, and no file that no longer carries it was retained.
#
# KSI-CORR-C3B-2 removed exactly one path and added none.
# `tests/contract/test_workspace_media.py` leaves this set because its only
# retired occurrence in the entire file was a `src/framenest` path literal naming
# the package directory that C3-B moved; with that literal repointed the file
# carries no retired spelling in any case and is correctly absent rather than
# merely renamed. Every other edited file keeps at least one occurrence for a
# reason this cut does not own - an environment variable prefix, a systemd unit
# name, a socket suffix, a companion protocol string, a negative assertion, a
# deliberately frozen `FrameNest*` class name, or a historical provenance
# comment - so no other member moves. No path was added to any tree.
#
# KSI-IMPL-C5 added exactly one path and removed exactly five, for a net -4
# (511 -> 507). The addition is `tests/contract/
# test_kronika_durable_writer_identities.py`, which pins every retired spelling
# it retains as historical. The removals are whole-file consequences of the
# writer switch, not renames: `src/kronika/infrastructure/ai/constants.py` and
# `src/kronika/infrastructure/ai/vision_probe.py` each held the retired spelling
# only in the outbound identity this cut switched; `tests/unit/infrastructure/
# ai/test_vision_probe.py` and `tests/unit/application/
# test_movie_identification_lifecycle.py` each lost their only retired spelling
# when the writer pin became canonical; and `tests/integration/
# test_media_sidecar_roundtrip.py` lost its only retired spelling when its
# writer-output path became canonical. Every other switched module keeps the
# former spelling as a named historical constant and stays.
EXPECTED_FRAMENEST_CONTENT_PATHS: frozenset[str] = frozenset(
    {
        ".gitignore",
        "AGENTS.md",
        "AI_WORKSPACE.md",
        "COVER_PIPELINE.md",
        "DESKTOP.md",
        "DEVELOPMENT.md",
        "GALLERY.md",
        "PRODUCT.md",
        "README.md",
        "ROADMAP.md",
        "SECURITY.md",
        "SERVER.md",
        "SPEC.md",
        "deploy/systemd/framenest-ai-credential-nvidia-nim.conf",
        "deploy/systemd/framenest-ai-credential-opencode-go.conf",
        "deploy/systemd/framenest-ai-credential-vercel-ai-gateway.conf",
        "deploy/systemd/framenest-catalog-backup.service",
        "deploy/systemd/framenest-catalog-backup.timer",
        "deploy/systemd/framenest-catalog-offdevice.service",
        "deploy/systemd/framenest-catalog-offdevice.timer",
        "deploy/systemd/framenest-research-credential.conf",
        "deploy/systemd/framenest.env.example",
        "deploy/systemd/framenest.service",
        "deploy/systemd/kronika-capture-bridge.service",
        "deploy/systemd/kronika-capture-runner.service",
        "deploy/systemd/kronika-capture.env.example",
        "deploy/systemd/kronika-catalog-offdevice.service",
        "deploy/systemd/kronika.env.example",
        "deploy/ubuntu/README.md",
        "deploy/ubuntu/fn-production-env-deploy",
        "deploy/ubuntu/framenest-catalog-export-v1",
        "deploy/ubuntu/framenest_release.py",
        "deploy/ubuntu/kronika-release",
        "deploy/ubuntu/kronika_release.py",
        "deploy/ubuntu/production_ai_deploy.py",
        "docs/ACCEPTANCE_DUAL_AUDIENCE.md",
        "docs/ARCHITECTURE_FOUNDATION_EVIDENCE.md",
        "docs/BACKUP_AND_RECOVERY.md",
        "docs/FEDORA_SERVICE.md",
        "docs/INFOSEC.md",
        "docs/NUC_HOST_BASELINE.md",
        "docs/OPERATOR_NETWORK.md",
        "docs/UBUNTU_NUC_DEPLOYMENT.md",
        "docs/WORKER_EXECUTION_CONTRACT.md",
        "docs/X_COMPANION.md",
        "docs/adr/0001-supported-python-version.md",
        "docs/adr/0002-python-environment-and-dependency-manager.md",
        "docs/adr/0003-initial-server-api-framework.md",
        "docs/adr/0004-repository-layout.md",
        "docs/adr/0005-configuration-strategy.md",
        "docs/adr/0006-macos-python-interpreter-provider.md",
        "docs/adr/0007-settings-library.md",
        "docs/adr/0008-asgi-runtime.md",
        "docs/adr/0009-structured-logging-approach.md",
        "docs/adr/0010-initial-persistence-foundation.md",
        "docs/adr/0011-stable-domain-identities.md",
        "docs/adr/0012-initial-device-registry.md",
        "docs/adr/0013-initial-library-registry.md",
        "docs/adr/0014-safe-library-scan-preview.md",
        "docs/adr/0015-deterministic-local-media-analysis-preparation.md",
        "docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md",
        "docs/adr/0017-initial-local-web-application-delivery.md",
        "docs/adr/0018-local-media-analysis-preview-api.md",
        "docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md",
        "docs/adr/0020-on-demand-ai-suggestion-review.md",
        "docs/adr/0021-tauri-desktop-shell.md",
        "docs/adr/0022-selective-media-placement-and-server-aggregation.md",
        "docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md",
        "docs/adr/0024-cover-studio-and-ai-cover-candidates.md",
        "docs/adr/0025-minimum-persistent-media-catalog-foundation.md",
        "docs/adr/0026-explicit-idempotent-scan-candidate-import.md",
        "docs/adr/0027-persistent-display-title-and-canonical-tags.md",
        "docs/adr/0028-catalog-read-model-and-search-semantics.md",
        "docs/adr/0029-persistent-plain-text-media-description.md",
        "docs/adr/0030-automatic-processed-collection.md",
        "docs/adr/0031-fedora-systemd-service-foundation.md",
        "docs/adr/0032-ubuntu-nuc-deployment-foundation.md",
        "docs/adr/0033-catalog-backup-and-recovery-foundation.md",
        "docs/adr/0034-canonical-analytic-programming-integration.md",
        "docs/adr/0035-authoritative-server-and-client-state-model.md",
        "docs/adr/0036-production-ai-credentials-via-systemd.md",
        "docs/adr/0037-durable-upload-session-and-safe-ingest-foundation.md",
        "docs/adr/0038-bounded-upload-media-validation.md",
        "docs/adr/0039-lifecycle-owned-upload-validation-orchestration.md",
        "docs/adr/0040-canonical-upload-byte-identity-foundation.md",
        "docs/adr/0041-exact-byte-upload-duplicate-disposition.md",
        "docs/adr/0042-atomic-upload-publication.md",
        "docs/adr/0043-upload-to-catalog-transaction.md",
        "docs/adr/0044-durable-automatic-post-catalog-analysis.md",
        "docs/adr/0045-content-classification-and-movie-identification.md",
        "docs/adr/0046-youtube-manual-ingestion-and-provenance.md",
        "docs/adr/0047-operator-cli-configuration-and-working-directory-hygiene.md",
        "docs/adr/0048-tailscale-remote-access-and-identity-foundation.md",
        "docs/adr/0049-durable-content-publication-boundary.md",
        "docs/adr/0050-durable-manual-cover-foundation.md",
        "docs/adr/0051-administrator-catalog-removal.md",
        "docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md",
        "docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md",
        "docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md",
        "docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md",
        "docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md",
        "docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md",
        "docs/adr/0061-x-meme-browser-companion.md",
        "docs/adr/0062-per-user-media-alias-overlay.md",
        "docs/adr/0063-companion-side-panel-web-host.md",
        "docs/adr/0064-x-save-category-and-public-photo-acquisition.md",
        "docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md",
        "docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md",
        "docs/adr/0069-five-tag-generic-media-suggestion-contract.md",
        "docs/adr/0071-native-side-panel-review-inbox-chrome.md",
        "docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md",
        "docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md",
        "docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md",
        "docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md",
        "docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md",
        "docs/adr/0079-administrator-automatic-analysis-runtime-setting.md",
        "docs/adr/0081-declarative-openai-compatible-provider-registry-and-administrator-vision-probe.md",
        "docs/adr/0082-kronika-one-product-and-private-records.md",
        "docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md",
        "docs/adr/0084-administrator-managed-research-settings-and-versioned-pricing.md",
        "docs/adr/0085-kronika-sole-identity.md",
        "docs/adr/README.md",
        "docs/provenance/kronika-capture.json",
        "extension/background/service_worker.js",
        "extension/content/x_adapter.js",
        "extension/content/x_adapter_contract_v1.js",
        "extension/shared/messages.js",
        "extension/ui/picker.js",
        "extension/ui/review.js",
        "extension/ui/save.js",
        "extension/ui/sidebar.js",
        "kronika",
        "pyproject.toml",
        "scripts/operator/infosec/framenest_log_triage.sh",
        "scripts/operator/infosec/framenest_public_surface_check.sh",
        "scripts/operator/infosec/framenest_socket_permissions_check.sh",
        "scripts/operator/network/README.md",
        "scripts/operator/network/framenest_mullvad_egress.fish",
        "scripts/operator/network/framenest_mullvad_egress.sh",
        "scripts/operator/network/kronika_nuc_worker_gate.fish",
        "src/kronika/__init__.py",
        "src/kronika/adapters/api/analysis_proposal_api.py",
        "src/kronika/adapters/api/application.py",
        "src/kronika/adapters/api/catalog_removal_api.py",
        "src/kronika/adapters/api/companion_review_api.py",
        "src/kronika/adapters/api/content_audience_api.py",
        "src/kronika/adapters/api/content_publication_api.py",
        "src/kronika/adapters/api/cover_api.py",
        "src/kronika/adapters/api/gallery_preview_api.py",
        "src/kronika/adapters/api/library_api.py",
        "src/kronika/adapters/api/media_alias_api.py",
        "src/kronika/adapters/api/media_analysis_api.py",
        "src/kronika/adapters/api/media_analysis_lifecycle_api.py",
        "src/kronika/adapters/api/media_catalog_api.py",
        "src/kronika/adapters/api/media_content_api.py",
        "src/kronika/adapters/api/media_import_api.py",
        "src/kronika/adapters/api/media_metadata_api.py",
        "src/kronika/adapters/api/media_suggestion_api.py",
        "src/kronika/adapters/api/public_published_api.py",
        "src/kronika/adapters/api/public_published_application.py",
        "src/kronika/adapters/api/tailscale_ingress.py",
        "src/kronika/adapters/api/team_alias_api.py",
        "src/kronika/adapters/api/upload_api.py",
        "src/kronika/adapters/api/web/__init__.py",
        "src/kronika/adapters/api/web/app.js",
        "src/kronika/adapters/api/web/companion_host.js",
        "src/kronika/adapters/api/workspace_media_api.py",
        "src/kronika/adapters/api/x_companion_api.py",
        "src/kronika/adapters/api/x_request_api.py",
        "src/kronika/adapters/api/youtube_browser_api.py",
        "src/kronika/adapters/api/youtube_operator_api.py",
        "src/kronika/adapters/api/youtube_request_api.py",
        "src/kronika/adapters/cli/ai.py",
        "src/kronika/adapters/cli/backup.py",
        "src/kronika/adapters/cli/catalog.py",
        "src/kronika/adapters/cli/covers.py",
        "src/kronika/adapters/cli/development.py",
        "src/kronika/adapters/cli/library.py",
        "src/kronika/adapters/cli/library_root.py",
        "src/kronika/adapters/cli/previews.py",
        "src/kronika/adapters/cli/recovery.py",
        "src/kronika/adapters/cli/sidecar.py",
        "src/kronika/adapters/cli/youtube.py",
        "src/kronika/application/__init__.py",
        "src/kronika/application/analysis_proposal.py",
        "src/kronika/application/companion_picker.py",
        "src/kronika/application/companion_review.py",
        "src/kronika/application/companion_x_tag.py",
        "src/kronika/application/content_publication.py",
        "src/kronika/application/library_scan.py",
        "src/kronika/application/library_workflow.py",
        "src/kronika/application/media_analysis.py",
        "src/kronika/application/media_analysis_coordinator.py",
        "src/kronika/application/media_analysis_lifecycle.py",
        "src/kronika/application/media_catalog.py",
        "src/kronika/application/media_import.py",
        "src/kronika/application/media_sidecar.py",
        "src/kronika/application/media_suggestion.py",
        "src/kronika/application/media_user_alias.py",
        "src/kronika/application/movie_identification.py",
        "src/kronika/application/movie_identification_lifecycle.py",
        "src/kronika/application/ports/analysis_proposal.py",
        "src/kronika/application/ports/companion_review_repository.py",
        "src/kronika/application/ports/content_publication_repository.py",
        "src/kronika/application/ports/device_repository.py",
        "src/kronika/application/ports/library_repository.py",
        "src/kronika/application/ports/media_analysis_runs.py",
        "src/kronika/application/ports/media_attribution.py",
        "src/kronika/application/ports/media_catalog_repository.py",
        "src/kronika/application/ports/media_cover_repository.py",
        "src/kronika/application/ports/media_metadata_repository.py",
        "src/kronika/application/ports/media_repository.py",
        "src/kronika/application/ports/media_sidecar_store.py",
        "src/kronika/application/ports/media_user_alias_repository.py",
        "src/kronika/application/ports/upload_publications.py",
        "src/kronika/application/ports/upload_sessions.py",
        "src/kronika/application/ports/x_acquisition.py",
        "src/kronika/application/ports/youtube_acquisition_claims.py",
        "src/kronika/application/upload_catalog.py",
        "src/kronika/application/upload_catalog_coordinator.py",
        "src/kronika/application/upload_publication.py",
        "src/kronika/application/upload_publication_coordinator.py",
        "src/kronika/application/upload_transport.py",
        "src/kronika/application/upload_validation.py",
        "src/kronika/application/upload_validation_coordinator.py",
        "src/kronika/application/workspace_media.py",
        "src/kronika/application/x_acquisition.py",
        "src/kronika/application/youtube_acquisition.py",
        "src/kronika/configuration.py",
        "src/kronika/domain/__init__.py",
        "src/kronika/domain/devices.py",
        "src/kronika/domain/identities.py",
        "src/kronika/domain/identity_access.py",
        "src/kronika/domain/libraries.py",
        "src/kronika/domain/media.py",
        "src/kronika/domain/media_analysis_runs.py",
        "src/kronika/domain/media_byte_identities.py",
        "src/kronika/domain/media_classification.py",
        "src/kronika/domain/media_cover.py",
        "src/kronika/domain/media_metadata.py",
        "src/kronika/domain/media_sidecar.py",
        "src/kronika/domain/media_user_alias.py",
        "src/kronika/domain/records.py",
        "src/kronika/domain/security_audit.py",
        "src/kronika/domain/upload_publications.py",
        "src/kronika/domain/uploads.py",
        "src/kronika/domain/x_acquisition.py",
        "src/kronika/domain/youtube_acquisition.py",
        "src/kronika/identity_env.py",
        "src/kronika/infrastructure/__init__.py",
        "src/kronika/infrastructure/ai/chatgpt_page/budget.py",
        "src/kronika/infrastructure/ai/configuration.py",
        "src/kronika/infrastructure/ai/image_derivative.py",
        "src/kronika/infrastructure/ai/nvidia_nim.py",
        "src/kronika/infrastructure/ai/openai_chat_completions.py",
        "src/kronika/infrastructure/ai/still_frame_smoke.py",
        "src/kronika/infrastructure/filesystem/media_sidecar.py",
        "src/kronika/infrastructure/media_analysis/adapter.py",
        "src/kronika/infrastructure/media_analysis/contact_sheet.py",
        "src/kronika/infrastructure/media_analysis/cover_frame.py",
        "src/kronika/infrastructure/media_analysis/ffmpeg.py",
        "src/kronika/infrastructure/media_analysis/ffprobe.py",
        "src/kronika/infrastructure/media_analysis/filesystem.py",
        "src/kronika/infrastructure/media_analysis/movie_identification.py",
        "src/kronika/infrastructure/media_analysis/process.py",
        "src/kronika/infrastructure/media_analysis/still_image.py",
        "src/kronika/infrastructure/persistence/__init__.py",
        "src/kronika/infrastructure/persistence/alembic_compat.py",
        "src/kronika/infrastructure/persistence/alembic_environment/__init__.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0001_initial_foundation.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0002_device_registry.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0003_library_registry.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0016_still_image_media_kinds.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0017_content_classification_and_movie_identification.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0018_analysis_run_history_and_active_uniqueness.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0023_still_image_covers.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0024_catalog_removal_receipts.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0025_upload_session_ownership_and_duplicate_mode.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0026_youtube_requester_ownership.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0027_youtube_creator_taxonomy.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0028_x_requester_acquisition.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0030_x_claim_requested_content_category.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/0031_companion_review_inbox.py",
        "src/kronika/infrastructure/persistence/alembic_environment/versions/__init__.py",
        "src/kronika/infrastructure/persistence/analysis_proposal_repository.py",
        "src/kronika/infrastructure/persistence/catalog_backup.py",
        "src/kronika/infrastructure/persistence/catalog_backup_offdevice.py",
        "src/kronika/infrastructure/persistence/catalog_backup_ops.py",
        "src/kronika/infrastructure/persistence/catalog_backup_workstation.py",
        "src/kronika/infrastructure/persistence/catalog_removal_repository.py",
        "src/kronika/infrastructure/persistence/catalog_schema.py",
        "src/kronika/infrastructure/persistence/cli.py",
        "src/kronika/infrastructure/persistence/companion_review_repository.py",
        "src/kronika/infrastructure/persistence/content_publication_repository.py",
        "src/kronika/infrastructure/persistence/device_repository.py",
        "src/kronika/infrastructure/persistence/engine.py",
        "src/kronika/infrastructure/persistence/errors.py",
        "src/kronika/infrastructure/persistence/library_repository.py",
        "src/kronika/infrastructure/persistence/media_analysis_run_repository.py",
        "src/kronika/infrastructure/persistence/media_attribution_repository.py",
        "src/kronika/infrastructure/persistence/media_catalog_repository.py",
        "src/kronika/infrastructure/persistence/media_cover_repository.py",
        "src/kronika/infrastructure/persistence/media_metadata_repository.py",
        "src/kronika/infrastructure/persistence/media_repository.py",
        "src/kronika/infrastructure/persistence/media_user_alias_repository.py",
        "src/kronika/infrastructure/persistence/migrations.py",
        "src/kronika/infrastructure/persistence/private_state.py",
        "src/kronika/infrastructure/persistence/security_audit_repository.py",
        "src/kronika/infrastructure/persistence/upload_publication_repository.py",
        "src/kronika/infrastructure/persistence/upload_session_repository.py",
        "src/kronika/infrastructure/persistence/x_acquisition_claim_repository.py",
        "src/kronika/infrastructure/persistence/youtube_acquisition_claim_repository.py",
        "src/kronika/infrastructure/runtime/__init__.py",
        "src/kronika/infrastructure/runtime/development.py",
        "src/kronika/infrastructure/runtime/production.py",
        "src/kronika/infrastructure/youtube/downloader.py",
        "src/kronika/server.py",
        "src/kronika/structured_logging.py",
        "src/kronika_capture/__init__.py",
        "src/kronika_capture/_assets/extension/src/headless/bridge_client.mjs",
        "src/kronika_capture/bridge/server.py",
        "src/kronika_capture/cli.py",
        "src/kronika_capture/config.py",
        "src/kronika_capture/paths.py",
        "tests/admin_batch_actions_frontend.test.js",
        "tests/admin_content_publication_frontend.test.js",
        "tests/ai_providers_admin_frontend.test.js",
        "tests/browser/x_browser_server.py",
        "tests/browser_catalog_removal_evidence.test.js",
        "tests/browser_companion_evidence.test.js",
        "tests/browser_cover_evidence.test.js",
        "tests/browser_movie_identification_evidence.test.js",
        "tests/catalog_card_ai_quick_action.test.js",
        "tests/companion_review_extension.test.js",
        "tests/companion_settings_automatic_analysis.test.js",
        "tests/companion_web_bridge.test.js",
        "tests/contract/test_ai_provider_admin_api.py",
        "tests/contract/test_ai_server_composition.py",
        "tests/contract/test_analysis_proposal.py",
        "tests/contract/test_ap_integration.py",
        "tests/contract/test_ap_project_contract.py",
        "tests/contract/test_automatic_analysis_privacy_contract.py",
        "tests/contract/test_automatic_analysis_settings_api.py",
        "tests/contract/test_backup_cli.py",
        "tests/contract/test_catalog_backup_timer.py",
        "tests/contract/test_catalog_cli.py",
        "tests/contract/test_catalog_offdevice_timer.py",
        "tests/contract/test_chatgpt_page_packaging.py",
        "tests/contract/test_companion_review_api.py",
        "tests/contract/test_content_publication_unpublish.py",
        "tests/contract/test_cover_ingress.py",
        "tests/contract/test_fedora_systemd_service.py",
        "tests/contract/test_gallery_preview_api.py",
        "tests/contract/test_health_api.py",
        "tests/contract/test_kronika_approved_projection.py",
        "tests/contract/test_kronika_capture_services.py",
        "tests/contract/test_kronika_cli_and_release_readers.py",
        "tests/contract/test_kronika_durable_analysis_identity_readers.py",
        "tests/contract/test_kronika_direct_reader_routing.py",
        "tests/contract/test_kronika_durable_artifact_readers.py",
        "tests/contract/test_kronika_durable_writer_identities.py",
        "tests/contract/test_kronika_identity_dual_read.py",
        "tests/contract/test_kronika_identity_migration.py",
        "tests/contract/test_kronika_mutation_header.py",
        "tests/contract/test_kronika_product_string_agreement.py",
        "tests/contract/test_kronika_settings_parity.py",
        "tests/contract/test_library_api.py",
        "tests/contract/test_library_cli.py",
        "tests/contract/test_local_web_application.py",
        "tests/contract/test_media_alias_api.py",
        "tests/contract/test_media_analysis_api.py",
        "tests/contract/test_media_analysis_lifecycle_api.py",
        "tests/contract/test_media_catalog_api.py",
        "tests/contract/test_media_content_api.py",
        "tests/contract/test_media_import_api.py",
        "tests/contract/test_media_metadata_api.py",
        "tests/contract/test_media_metadata_repository.py",
        "tests/contract/test_media_repository.py",
        "tests/contract/test_media_suggestion_api.py",
        "tests/contract/test_metadata_field_contract_parity.py",
        "tests/contract/test_nuc_operator_runbook.py",
        "tests/contract/test_nuc_release_docs.py",
        "tests/contract/test_nuc_release_remote_contract.py",
        "tests/contract/test_nuc_release_source_contract.py",
        "tests/contract/test_operator_cli_hygiene.py",
        "tests/contract/test_operator_network_scripts.py",
        "tests/contract/test_ordinary_upload_ownership_boundary.py",
        "tests/contract/test_persistence_cli.py",
        "tests/contract/test_persistence_package_resources.py",
        "tests/contract/test_previews_console_script.py",
        "tests/contract/test_production_ai_deployment.py",
        "tests/contract/test_public_published_uds.py",
        "tests/contract/test_recovery_cli.py",
        "tests/contract/test_requester_private_youtube_details.py",
        "tests/contract/test_research_provider_contract.py",
        "tests/contract/test_server_process_output.py",
        "tests/contract/test_sidecar_cli.py",
        "tests/contract/test_tailscale_ingress_security.py",
        "tests/contract/test_team_alias_api.py",
        "tests/contract/test_uvicorn_logging.py",
        "tests/contract/test_uvicorn_runtime.py",
        "tests/contract/test_web_package_resources.py",
        "tests/contract/test_worker_execution_contract.py",
        "tests/contract/test_x_companion_api.py",
        "tests/contract/test_x_request_api.py",
        "tests/contract/test_youtube_browser_api.py",
        "tests/contract/test_youtube_operator_api.py",
        "tests/contract/test_youtube_request_api.py",
        "tests/gallery_filter_controls.test.js",
        "tests/integration/persistence/test_analysis_run_history_migration.py",
        "tests/integration/persistence/test_companion_review_migration.py",
        "tests/integration/persistence/test_device_repository.py",
        "tests/integration/persistence/test_kronika_record_repository.py",
        "tests/integration/persistence/test_library_repository.py",
        "tests/integration/persistence/test_populated_0015_upgrade_to_0017.py",
        "tests/integration/persistence/test_upload_session_migration.py",
        "tests/integration/test_cover_workflow_real_tools.py",
        "tests/integration/test_development_launcher.py",
        "tests/integration/test_local_web_media_metadata.py",
        "tests/integration/test_local_web_media_playback.py",
        "tests/integration/test_media_analysis_real_tools.py",
        "tests/integration/test_nvidia_nim_live.py",
        "tests/integration/test_persistence_migrations.py",
        "tests/integration/test_still_image_vertical_slice.py",
        "tests/kronika_ui.test.js",
        "tests/research_settings_admin_frontend.test.js",
        "tests/support/metadata_field_contract_cases.json",
        "tests/support/x_fake_demo.py",
        "tests/support/x_fixtures/composer.html",
        "tests/support/youtube_fake_demo.py",
        "tests/tailscale_identity_frontend.test.js",
        "tests/unit/application/test_companion_review.py",
        "tests/unit/application/test_companion_x_tag.py",
        "tests/unit/application/test_in_process_lifecycle.py",
        "tests/unit/application/test_library_scan.py",
        "tests/unit/application/test_media_analysis.py",
        "tests/unit/application/test_media_analysis_lifecycle.py",
        "tests/unit/application/test_media_content_application.py",
        "tests/unit/application/test_media_sidecar.py",
        "tests/unit/application/test_media_suggestion.py",
        "tests/unit/application/test_media_user_alias.py",
        "tests/unit/application/test_still_frame_smoke.py",
        "tests/unit/application/test_upload_transport.py",
        "tests/unit/application/test_upload_validation.py",
        "tests/unit/application/test_upload_validation_coordinator.py",
        "tests/unit/application/test_x_acquisition_lifecycle.py",
        "tests/unit/application/test_x_automatic_analysis_policy.py",
        "tests/unit/chatgpt_page/test_bridge_security.py",
        "tests/unit/chatgpt_page/test_projects.py",
        "tests/unit/domain/__init__.py",
        "tests/unit/domain/test_creator_attribution.py",
        "tests/unit/domain/test_devices.py",
        "tests/unit/domain/test_identities.py",
        "tests/unit/domain/test_libraries.py",
        "tests/unit/domain/test_media.py",
        "tests/unit/domain/test_media_byte_identities.py",
        "tests/unit/domain/test_media_classification.py",
        "tests/unit/domain/test_media_cover.py",
        "tests/unit/domain/test_media_metadata.py",
        "tests/unit/domain/test_media_sidecar.py",
        "tests/unit/domain/test_media_user_alias.py",
        "tests/unit/domain/test_upload_publications.py",
        "tests/unit/domain/test_upload_sessions.py",
        "tests/unit/domain/test_x_acquisition_domain.py",
        "tests/unit/domain/test_youtube_acquisition.py",
        "tests/unit/infrastructure/ai/chatgpt_page/test_budget.py",
        "tests/unit/infrastructure/ai/test_ai_configuration_storage.py",
        "tests/unit/infrastructure/ai/test_credentials.py",
        "tests/unit/infrastructure/ai/test_image_derivative.py",
        "tests/unit/infrastructure/ai/test_nvidia_nim.py",
        "tests/unit/infrastructure/backup/test_catalog_backup.py",
        "tests/unit/infrastructure/backup/test_catalog_backup_offdevice.py",
        "tests/unit/infrastructure/backup/test_catalog_backup_ops.py",
        "tests/unit/infrastructure/backup/test_catalog_backup_workstation.py",
        "tests/unit/infrastructure/filesystem/test_local_media_content.py",
        "tests/unit/infrastructure/filesystem/test_media_sidecar_store.py",
        "tests/unit/infrastructure/filesystem/test_quarantine_storage.py",
        "tests/unit/infrastructure/media_analysis/test_cover_frame.py",
        "tests/unit/infrastructure/media_analysis/test_ffprobe_ffmpeg.py",
        "tests/unit/infrastructure/media_analysis/test_filesystem.py",
        "tests/unit/infrastructure/media_analysis/test_movie_contact_sheet_selection.py",
        "tests/unit/infrastructure/media_analysis/test_process.py",
        "tests/unit/infrastructure/persistence/test_companion_review_repository.py",
        "tests/unit/infrastructure/persistence/test_media_analysis_run_repository.py",
        "tests/unit/infrastructure/persistence/test_upload_publication_repository.py",
        "tests/unit/infrastructure/persistence/test_upload_session_repository.py",
        "tests/unit/infrastructure/runtime/test_development_runtime.py",
        "tests/unit/infrastructure/runtime/test_production_health.py",
        "tests/unit/infrastructure/runtime/test_production_runtime.py",
        "tests/unit/test_configuration.py",
        "tests/unit/test_configuration_env_file.py",
        "tests/unit/test_configuration_ingress.py",
        "tests/unit/test_gallery_preview.py",
        "tests/unit/test_identity_access.py",
        "tests/unit/test_identity_env.py",
        "tests/unit/test_import_boundaries.py",
        "tests/unit/test_persistence_boundaries.py",
        "tests/unit/test_persistence_engine.py",
        "tests/unit/test_previews_cli.py",
        "tests/unit/test_runtime_settings_store.py",
        "tests/unit/test_server_runtime.py",
        "tests/unit/test_structured_logging.py",
        "tests/upload_cockpit_async_ownership.test.js",
        "tests/workspace_media_frontend.test.js",
        "tests/x_companion_extension.test.js",
        "tests/youtube_acquisition_cockpit.test.js",
        "tests/youtube_request_cockpit.test.js",
    }
)

# KSI-IMPL-C3B moved the token count by -2 and the distinct-name count by -1,
# and moved neither the bare count nor anything else. Both movements are the one
# rename of `FORBIDDEN_FRAMENEST_DOMAIN_IMPORT_PREFIXES` to
# `FORBIDDEN_KRONIKA_DOMAIN_IMPORT_PREFIXES` in `unit/test_import_boundaries.py`:
# the pattern matches that identifier as a substring, so its two occurrences and
# its one distinct name leave. No `FRAMENEST_` environment name was added,
# removed or respelled anywhere: the settings prefix is a later cut, and this
# count is the evidence that it did not move here.
#
# KSI-IMPL-C4A moved the token count by -13, the distinct-name count by +1 and
# the bare count by +5, and moved nothing else.
#
# The -13 is almost entirely the gate: `framenest_nuc_worker_gate.fish` loses
# 18 tokens because the retained wrapper no longer reads any `FRAMENEST_`
# variable by name, and the moved engine keeps the same two
# `FRAMENEST_ENV_FILE` occurrences it always had. Against that, this cut names
# the variable family in prose and in tests: `AGENTS.md` +1,
# `docs/WORKER_EXECUTION_CONTRACT.md` +1 and
# `test_operator_network_scripts.py` +3, each one the family spelling
# `FRAMENEST_NUC_SSH_*` that the compatibility rule names.
#
# The +1 distinct name is that family spelling, which the token pattern matches
# up to the trailing `*`. The +5 bare spellings are the two engine files
# exchanging one declaration of the compatible prefix (-1 and +1) plus the
# canonical gate declaring it (+1), `test_nuc_release_docs.py` naming it (+1)
# and `test_operator_network_scripts.py` composing both prefixes as data (+3).
#
# KSI-IMPL-C4B moved the token count by +27 and all of it is one file: the new
# `contract/test_kronika_identity_migration.py` names every typed path key in
# its synthetic environment fixture. Two of those names are distinct and new
# (`FRAMENEST_FUTURE_UNKNOWN_KEY` and `FRAMENEST_UPLOAD_QUARANTINE_ROOT`), so
# the distinct count is +2. Its three bare spellings are the `FRAMENEST_`
# prefix checks, so the bare count is +3. The masked capture installation and
# the new remote-contract probe answers add no environment tokens, and the
# engine's two declarations are unchanged.
ENV_PREFIX_TOKEN_COUNT = 655
ENV_PREFIX_DISTINCT_NAME_COUNT = 103
ENV_PREFIX_BARE_SPELLING_COUNT = 29

MUTATION_HEADER = "X-FrameNest-Request"
# KSI-IMPL-C2 kept every occurrence that existed before it and added 14:
# -2 where the service worker's three inline literals became one shared constant,
# +9 in tests/tailscale_identity_frontend.test.js, +5 in
# tests/companion_review_extension.test.js, and +2 in
# tests/contract/test_local_web_application.py for the new dual-send and
# order-independence assertions. The file count moved 29 -> 30 only because that
# last file now names the header for the first time.
#
# KSI-IMPL-C3B deliberately moved neither count. The mutation header is a later
# cut's surface, and this pair is the evidence that no header literal and no
# assertion naming one was touched while the package moved.
MUTATION_HEADER_OCCURRENCE_COUNT = 73
MUTATION_HEADER_FILE_COUNT = 30

# KSI-IMPL-C4A moved `/opt/framenest` by +6 and moved every other literal
# nothing. The move itself is neutral: `framenest_release.py` loses 12 and
# `kronika_release.py` gains the same 12, because the engine keeps the whole
# old host layout this cut is forbidden to change. The +6 is test-side: the
# remote-contract suite gained 10 occurrences of the release root through the
# installed-unit executable guard and the deploy-lock quarantine paths, and the
# capture and reader suites each lost 2 because their marker literals became
# resolved marker names.
#
# KSI-IMPL-C4B is a content-additive cut and every literal moved upward:
# `/opt/framenest` +13, `/etc/framenest` +7, `/var/lib/framenest` +15,
# `/var/cache/framenest` +8 and the frozen mount +10. The causes are the new
# migration section of the engine (the accepted layout table, the plan and
# observation constants, and the command builders), the two layout-probe
# answers added to the existing test fakes, and the new migration test file,
# which necessarily fixtures the former layout, the former state and cache
# roots, the former environment file and one credential source path. The
# frozen mount +10 is the two new canonical artifacts that name it (2 + 1) and
# seven occurrences in the new test file. No existing literal was rewritten.
#
# KSI-IMPL-C6P1 moved `/var/lib/framenest` by +11 and
# `/var/cache/framenest` by +2, and moved no other host literal. All of it is
# the new C6-P1 stateful boundary in `test_kronika_identity_migration.py`:
# eleven former state-root fixtures and assertions (the copied catalog tree,
# the account home, the link target, the observed-home reverse rename and the
# conflict plan) and two former cache-root fixtures. The engine adds no host
# literal; the moved journal path is the new sibling
# `/var/lib/kronika-identity-migration`.
#
# KSI-IMPL-C6P2 moved `/opt/framenest` by +3 (223 -> 226),
# `/etc/framenest` by +3 (83 -> 86), `/var/lib/framenest` by +1 (120 -> 121)
# and the frozen mount by +2 (23 -> 25), and moved `/var/cache/framenest`
# nothing. Every movement is in `test_kronika_identity_migration.py`'s new
# fixtures and assertions: the former environment file, drop-in directory,
# credential source and sudo-rule paths, the writer unit fragments, the
# observed-home conflict plans and the mount comment/assertion fixtures. The
# engine adds no host literal; the structural transformer names only the
# generic token.
HOST_PATH_OCCURRENCE_COUNT = {
    "/opt/framenest": 226,
    "/etc/framenest": 86,
    "/var/lib/framenest": 121,
    "/var/cache/framenest": 31,
    "/mnt/framenest-catalog-offdevice": 25,
}

# KSI-IMPL-C4B moved each of these by +3: one `User=framenest` and
# `Group=framenest` pair in each of the two effective-layout probe answers
# added to the existing test fakes, plus one negative assertion in the new
# migration test file that the canonical units do not carry either line.
#
# KSI-IMPL-C6P2 moved each by +1 (8 -> 9): the new structural systemd
# transformation fixture in `test_kronika_identity_migration.py` names both
# the retired `User=framenest` and `Group=framenest` directives it rewrites.
UNIT_ACCOUNT_OCCURRENCE_COUNT = {
    "User=framenest": 9,
    "Group=framenest": 9,
}

# KSI-IMPL-C2 moved this by +15: -2 where the service worker stopped spelling the
# retired origin key and the header inline, and +17 across the four test files
# whose new test names and messages name the brand they pin
# (tailscale_identity_frontend +9, companion_review_extension +5,
# test_local_web_application +2, x_companion_extension +1). The file count is
# unmoved.
#
# KSI-CORR-05 moved this by +1: the one capitalized product name its new
# `loadSidebarBridgeContext` helper returns alongside the bridge. The file count
# is unmoved, because that file already carried the name.
#
# KSI-IMPL-C2B moved this by -13 occurrences and -3 files. The occurrences are
# the fourteen user-visible `FrameNest` strings it retired
# (`manifest.json` 3, `ui/sidebar.html` 5, `shared/messages.js` 1,
# `adapters/api/web/index.html` 5) less the one retired spelling its repointed
# tests reintroduce as a negative assertion. The three files are
# `extension/manifest.json`, `extension/ui/sidebar.html` and
# `src/framenest/adapters/api/web/index.html`, each of which now carries no
# capitalized name at all and so leaves this count as well as the content set.
#
# KSI-IMPL-C2C moved this by -69 occurrences and -2 files. The occurrences are
# the forty-one user-visible `FrameNest` strings it retired across nine files,
# less the twenty-eight retired spellings its repointed assertions and its
# extended agreement guard reintroduce. The two files are
# `extension/ui/save.html` and `extension/ui/picker.html`, each of which now
# carries no capitalized name at all and so leaves this count as well as the
# content set.
#
# KSI-IMPL-C2D moved this by -63 occurrences and -3 files. The occurrences are
# the forty-nine product strings it retired plus the fourteen repointed test
# literals, and nothing was reintroduced: this cut added no guard, no negative
# assertion and no provenance comment inside a counted path, so the movement is
# the exact sum of its two per-tree movements above with no offset.
#
# The three files are `src/framenest/infrastructure/ai/prompts.py` (its only
# capitalized name was the externally sent media-suggestion system prompt),
# `tests/contract/test_operator_cli_hygiene.py` (its two were the two
# configuration-error message literals) and
# `tests/unit/application/test_library_workflow.py` (its two were the two
# device display-name literals). Each still carries a lowercase `framenest`
# spelling in an import path, so all three correctly REMAIN in
# `EXPECTED_FRAMENEST_CONTENT_PATHS` below and this is a capitalized-count
# movement only, not a content-set movement.
# KSI-IMPL-C3A moved the occurrences by +12 and the files by 0. The file count is
# a genuine swap: `contract/test_development_cli.py` left this count because the
# repaired `RuntimeStatus` fake removed its only capitalized spelling, and
# `contract/test_kronika_product_string_agreement.py` entered it because the new
# inventory pins the deliberately dual-spelled mutation-header sentence verbatim,
# which is the one place a retired spelling is still correct product text while
# both header spellings are accepted.
#
# Of the twelve added occurrences, eleven are Python identifiers and import paths
# rather than prose: `FrameNestMediaCoverError` twice, `FrameNestSettings` twice,
# `FrameNestMediaUserAliasError` twice as an exception reference and once as an
# import, `FrameNestConfigurationError` once as an exception reference and once as
# an import, `FrameNestUploadSessionError` once and `FrameNestMediaMetadataError`
# once. C2D deliberately did not rename a class or module name and C3-B owns that
# move, so every one of these is a correct reference to a name that still exists.
# The twelfth is the dual-spelled header sentence above. Nothing here is product
# prose that should have been retired.
#
# KSI-IMPL-C3B moved the occurrences by -522 and the files by -80, and the
# -522 decomposes exactly, with no residual:
# -504 for the `FrameNestSettings` -> `KronikaSettings` rename, 504 occurrences
# across `src` and `tests` and zero in the frozen ADRs this ledger does not
# count;
# -1 for the `migrations.py` module docstring;
# -1 for the generated downgrade message in `script.py.mako`;
# -16 for the root launcher, whose sixteen capitalized messages moved with it
# into `./kronika` as the same sixteen `Kronika` messages.
#
# The -80 is a pure loss and no path entered this count: exactly eighty files
# held `FrameNest` only inside a `FrameNestSettings` reference or inside the root
# launcher and now hold none, and neither `./kronika` nor the Alembic shim holds
# a capitalized retired spelling. The long `FrameNest*Error` domain hierarchy
# and the three logging classes still hold theirs, which is deliberate: this cut
# renames the settings class and the logging identifiers it is coupled to, and
# the error hierarchy is not one of them.
# KSI-IMPL-C4A moved the occurrences by +6 and the files by 0.
#
# `deploy/ubuntu/framenest-release` -3: the retained Fish wrapper no longer
# carries the three `FrameNest` messages it used to print. The new
# `contract/test_kronika_durable_analysis_identity_readers.py` +9: its prose
# deliberately uses the retired exception-class names
# (`FrameNestMediaSuggestionError`, `FrameNestMovieIdentificationError` and
# `FrameNestIdentityError`) where they are the objects under test. No product
# message, class name or module name changed.
CAPITALIZED_OCCURRENCE_COUNT = 2748
CAPITALIZED_FILE_COUNT = 394

# KSI-IMPL-C3B added thirteen canonical `kronika-*` entries and thirteen
# retained `framenest-*` aliases to the script table, so the table holds
# twenty-eight entries while this count stays 14: the pattern names retired
# spellings only, and the thirteen aliases plus `framenest-chatgpt-page` are
# exactly those. C7-B removes the aliases and this count becomes 1.
CONSOLE_SCRIPT_ENTRY_COUNT = 14

ENV_PREFIX_TOKEN_PATTERN = re.compile(r"FRAMENEST_[A-Z0-9_]+")
ENV_PREFIX_BARE_PATTERN = re.compile(r"FRAMENEST_(?![A-Z0-9_])")
CONSOLE_SCRIPT_PATTERN = re.compile(r"framenest-[a-z0-9-]+")

_SELF_RELATIVE_PATH = "tests/contract/test_kronika_identity_retention.py"


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ("git", "ls-files", "-z"),
        check=True,
        capture_output=True,
        cwd=REPOSITORY_ROOT,
    )
    raw = result.stdout.decode("utf-8")
    return sorted(
        entry
        for entry in raw.split("\0")
        # Submodule gitlinks are reported as directories and hold no repository
        # blob of this repository, so they are excluded exactly as `git grep`
        # does not descend into them.
        if entry and (REPOSITORY_ROOT / entry).is_file()
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _alembic_versions_directory() -> Path:
    candidates = sorted(REPOSITORY_ROOT.glob(_ALEMBIC_VERSIONS_GLOB))
    assert len(candidates) == 1, (
        "expected exactly one alembic versions directory, found "
        f"{[str(candidate) for candidate in candidates]}"
    )
    return candidates[0]


def _decoded_texts(relative_paths: list[str]) -> list[str]:
    """Decode tracked text files, skipping binaries exactly as `git grep -I` does."""
    return [text for _, text in _decoded_pairs(relative_paths)]


def _decoded_pairs(relative_paths: list[str]) -> list[tuple[str, str]]:
    """Pair each tracked text path with its decoded text, skipping binaries."""
    pairs: list[tuple[str, str]] = []
    for relative in relative_paths:
        raw = (REPOSITORY_ROOT / relative).read_bytes()
        if b"\0" in raw:
            continue
        pairs.append((relative, raw.decode("utf-8", errors="replace")))
    return pairs


# ---------------------------------------------------------------------------
# Part A - frozen-blob hashes
# ---------------------------------------------------------------------------


def test_frozen_adr_and_host_document_bytes_are_unchanged() -> None:
    mismatched = {
        relative: (expected, _sha256(REPOSITORY_ROOT / relative))
        for relative, expected in FROZEN_DOCUMENT_SHA256.items()
        if _sha256(REPOSITORY_ROOT / relative) != expected
    }

    assert not mismatched, f"frozen document bytes changed: {mismatched}"


def test_frozen_adr_and_host_document_set_is_complete() -> None:
    """Every ADR through 0084 plus the two host documents must be pinned."""
    present = {
        relative
        for relative in _tracked_paths()
        if re.fullmatch(r"docs/adr/\d{4}-.+\.md", relative)
        and int(relative.split("/")[-1][:4]) <= 84
    }
    present.add("docs/FEDORA_SERVICE.md")
    present.add("docs/NUC_HOST_BASELINE.md")

    assert present == set(FROZEN_DOCUMENT_SHA256), (
        "frozen document pin set drifted: "
        f"missing={sorted(present - set(FROZEN_DOCUMENT_SHA256))} "
        f"extra={sorted(set(FROZEN_DOCUMENT_SHA256) - present)}"
    )


def test_frozen_alembic_revision_bytes_are_unchanged() -> None:
    versions_directory = _alembic_versions_directory()
    mismatched = {
        name: (expected, _sha256(versions_directory / name))
        for name, expected in FROZEN_ALEMBIC_SHA256.items()
        if _sha256(versions_directory / name) != expected
    }

    assert not mismatched, f"applied alembic bytes changed: {mismatched}"


def test_frozen_alembic_revision_set_is_complete() -> None:
    versions_directory = _alembic_versions_directory()
    present = {path.name for path in versions_directory.iterdir() if path.is_file()}

    assert present == set(FROZEN_ALEMBIC_SHA256), (
        "frozen alembic pin set drifted: "
        f"missing={sorted(present - set(FROZEN_ALEMBIC_SHA256))} "
        f"extra={sorted(set(FROZEN_ALEMBIC_SHA256) - present)}"
    )


# ---------------------------------------------------------------------------
# Part B - path-name ledger
# ---------------------------------------------------------------------------


def test_framenest_basename_path_ledger_matches_exactly() -> None:
    measured = {
        relative
        for relative in _tracked_paths()
        if "framenest" in relative.rsplit("/", 1)[-1].lower()
    }

    assert measured == set(EXPECTED_FRAMENEST_BASENAME_PATHS), (
        "path-name ledger drifted: "
        f"unexpected={sorted(measured - set(EXPECTED_FRAMENEST_BASENAME_PATHS))} "
        f"missing={sorted(set(EXPECTED_FRAMENEST_BASENAME_PATHS) - measured)}"
    )


# ---------------------------------------------------------------------------
# Part C - content occurrence ledger
# ---------------------------------------------------------------------------


def _counted_paths() -> list[str]:
    """Tracked text paths excluding this ledger file.

    This file necessarily contains the very tokens it pins, so counting itself
    would be self-referential. Every later cut keeps it excluded.
    """
    return [relative for relative in _tracked_paths() if relative != _SELF_RELATIVE_PATH]


def test_per_tree_framenest_file_counts_match() -> None:
    """Count tracked files whose content contains `framenest` case-insensitively."""
    counted = _counted_paths()
    measured: dict[str, int] = {tree: 0 for tree in PER_TREE_FRAMENEST_FILE_COUNT}
    for relative, text in _decoded_pairs(counted):
        if "framenest" not in text.lower():
            continue
        for tree in measured:
            if relative.startswith(f"{tree}/"):
                measured[tree] += 1
                break

    assert measured == PER_TREE_FRAMENEST_FILE_COUNT


def test_per_tree_framenest_occurrence_counts_match() -> None:
    """Count `framenest` occurrences per tree, so a content-only rename fails."""
    measured: dict[str, int] = {tree: 0 for tree in PER_TREE_FRAMENEST_OCCURRENCE_COUNT}
    for relative, text in _decoded_pairs(_counted_paths()):
        occurrences = text.lower().count("framenest")
        if not occurrences:
            continue
        for tree in measured:
            if relative.startswith(f"{tree}/"):
                measured[tree] += occurrences
                break

    assert measured == PER_TREE_FRAMENEST_OCCURRENCE_COUNT


def test_framenest_content_path_ledger_matches_exactly() -> None:
    """Pin the membership of content-carrying paths, so a partial rename fails."""
    measured = {
        relative
        for relative, text in _decoded_pairs(_counted_paths())
        if "framenest" in text.lower()
    }

    assert measured == set(EXPECTED_FRAMENEST_CONTENT_PATHS), (
        "content-path ledger drifted: "
        f"unexpected={sorted(measured - set(EXPECTED_FRAMENEST_CONTENT_PATHS))} "
        f"missing={sorted(set(EXPECTED_FRAMENEST_CONTENT_PATHS) - measured)}"
    )


def test_environment_prefix_token_counts_match() -> None:
    texts = _decoded_texts(_counted_paths())
    tokens = [
        match
        for text in texts
        for match in ENV_PREFIX_TOKEN_PATTERN.findall(text)
    ]

    assert len(tokens) == ENV_PREFIX_TOKEN_COUNT
    assert len(set(tokens)) == ENV_PREFIX_DISTINCT_NAME_COUNT


def test_bare_environment_prefix_spellings_match() -> None:
    texts = _decoded_texts(_counted_paths())
    bare = [
        match
        for text in texts
        for match in ENV_PREFIX_BARE_PATTERN.findall(text)
    ]

    assert len(bare) == ENV_PREFIX_BARE_SPELLING_COUNT


def test_mutation_header_occurrence_counts_match() -> None:
    counted = _counted_paths()
    occurrences = 0
    files = 0
    for relative in counted:
        text = (REPOSITORY_ROOT / relative).read_bytes()
        if b"\0" in text:
            continue
        count = text.decode("utf-8", errors="replace").count(MUTATION_HEADER)
        if count:
            occurrences += count
            files += 1

    assert occurrences == MUTATION_HEADER_OCCURRENCE_COUNT
    assert files == MUTATION_HEADER_FILE_COUNT


def test_host_path_occurrence_counts_match() -> None:
    texts = _decoded_texts(_counted_paths())

    measured = {
        path: sum(text.count(path) for text in texts)
        for path in HOST_PATH_OCCURRENCE_COUNT
    }

    assert measured == HOST_PATH_OCCURRENCE_COUNT


def test_unit_account_occurrence_counts_match() -> None:
    texts = _decoded_texts(_counted_paths())

    measured = {
        marker: sum(text.count(marker) for text in texts)
        for marker in UNIT_ACCOUNT_OCCURRENCE_COUNT
    }

    assert measured == UNIT_ACCOUNT_OCCURRENCE_COUNT


def test_capitalized_framenest_occurrence_counts_match() -> None:
    counted = _counted_paths()
    occurrences = 0
    files = 0
    for relative in counted:
        raw = (REPOSITORY_ROOT / relative).read_bytes()
        if b"\0" in raw:
            continue
        count = raw.decode("utf-8", errors="replace").count("FrameNest")
        if count:
            occurrences += count
            files += 1

    assert occurrences == CAPITALIZED_OCCURRENCE_COUNT
    assert files == CAPITALIZED_FILE_COUNT


def test_framenest_console_script_entry_count_matches() -> None:
    text = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert len(CONSOLE_SCRIPT_PATTERN.findall(text)) == CONSOLE_SCRIPT_ENTRY_COUNT
