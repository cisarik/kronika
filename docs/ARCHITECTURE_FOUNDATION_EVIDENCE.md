# Kronika Architecture Foundation Evidence

## 1. Document Status

This document is an **evidence package** for the first Kronika architecture decisions.

It is **not** an Architecture Decision Record (ADR). It does **not** approve, select, or commit the project to any architecture option. The Orchestrator and Cooperator will decide **one question at a time**. No option in this document may be treated as accepted until an explicit ADR is later authorized and committed.

| Field | Value |
|---|---|
| Research date | 2026-06-23 |
| Repository starting HEAD | `584b4fde3a7d84f413150702e148921f8273cc45` |
| Scope | Supported Python version; Python environment and dependency manager; initial server API framework; initial repository layout boundary; local development configuration strategy |
| Decision status at research time | **Not accepted** for all five decisions |
| Methodology | Primary and official sources only; time-sensitive claims include retrieval date and URL |

Verified facts are cited to primary sources. Worker inference, provisional recommendations, and uncertainty are explicitly labeled.

### Decision status update (2026-06-23)

After this evidence package was written:

- **Decision 1 — Supported Python version** was accepted as **CPython 3.13** in [ADR-0001](adr/0001-supported-python-version.md).
- **Decision 2 — Python environment and dependency manager** was accepted as **Poetry** in [ADR-0002](adr/0002-python-environment-and-dependency-manager.md).
- **Decision 3 — Initial server API framework** was accepted as **FastAPI** in [ADR-0003](adr/0003-initial-server-api-framework.md).
- **Decision 4 — Repository layout** was accepted as the **hybrid staged monorepo** in [ADR-0004](adr/0004-repository-layout.md).
- **Decision 5 — Configuration strategy** was accepted as the **layered configuration model** in [ADR-0005](adr/0005-configuration-strategy.md).

The Cooperator selected Poetry, the strongest alternative identified in this evidence package, based on project familiarity and maintainability preference. The original provisional recommendation for `uv` below remains historical research evidence, not current project authority.

The provisional recommendation for FastAPI in Decision 3 matched the Cooperator's later acceptance. The provisional recommendation for Option C — Hybrid staged monorepo in Decision 4 matched the Cooperator's later acceptance. The provisional recommendation for Option C — Layered configuration model in Decision 5 matched the Cooperator's later acceptance. The framework and layout comparison sections below remain historical decision evidence and must not be rewritten to alter the original research record.

All five initial scaffold-gating decisions are now accepted. No application implementation exists yet.

| Decision | Current status | Authority |
|---|---|---|
| 1. Python version | **Accepted** — CPython 3.13 | [ADR-0001](adr/0001-supported-python-version.md) |
| 2. Python tooling | **Accepted** — Poetry | [ADR-0002](adr/0002-python-environment-and-dependency-manager.md) |
| 3. API framework | **Accepted** — FastAPI | [ADR-0003](adr/0003-initial-server-api-framework.md) |
| 4. Repository layout | **Accepted** — Hybrid staged monorepo | [ADR-0004](adr/0004-repository-layout.md) |
| 5. Configuration strategy | **Accepted** — Layered configuration | [ADR-0005](adr/0005-configuration-strategy.md) |

---

## 2. Kronika Constraints Used for Evaluation

The following approved constraints from committed project documentation frame this comparison:

- Apple Silicon macOS is the first development and test environment.
- Fedora KDE on an Intel NUC is the later server deployment target.
- Server-first implementation priority begins on macOS.
- Desktop local-first independence must remain: local gallery, metadata, search, and playback must remain usable without the server where required files are locally available.
- Python is the approved direction for domain, filesystem, downloader, metadata, server, and media-processing capabilities.
- A shared web-first UI with a future Tauri desktop shell is the conceptual direction.
- The server should bind to loopback when exposed through Tailscale Serve.
- SQLite is the approved direction for the local catalog/index.
- FFmpeg, ffprobe, yt-dlp, and VLC integrations are planned later, not selected in this task.
- The project is maintained by a small team; complexity must stay proportionate.
- Cross-platform requirements remain (macOS, Linux, Windows, shared filesystems).
- Typed, testable code is required by project direction.
- Conservative dependency and security posture is required: least privilege, secrets out of Git, no public backend exposure by default.

---

## 3. Decision Sequence

The recommended future decision order is:

1. **Supported Python version**
2. **Python environment and dependency manager**
3. **Initial server API framework**
4. **Initial repository layout boundary**
5. **Local development configuration strategy**

### Dependencies between decisions

| Decision | Depends on | Why |
|---|---|---|
| 1. Python version | None | All later Python tooling, frameworks, and packaging constraints flow from this |
| 2. Python tooling | 1 | Tooling must support the chosen Python version and future monorepo direction |
| 3. API framework | 1, partially 2 | Framework Python support and project packaging must align |
| 4. Repository layout | 1, 2, 3 | Layout must accommodate chosen Python project boundaries and future web/Tauri components |
| 5. Configuration strategy | 1, 3, 4 | Configuration attaches to runtime boundaries created by server layout and deployment model |

Decisions 3 and 4 are partially independent once Python version is fixed, but repository layout should not be finalized before the server framework category is understood. Decision 5 should wait until the server process boundary and repository locations are known.

Decision 1 is accepted per [ADR-0001](adr/0001-supported-python-version.md). Decision 2 is accepted per [ADR-0002](adr/0002-python-environment-and-dependency-manager.md). Decision 3 is accepted per [ADR-0003](adr/0003-initial-server-api-framework.md). Decision 4 is accepted per [ADR-0004](adr/0004-repository-layout.md). Decision 5 is accepted per [ADR-0005](adr/0005-configuration-strategy.md).

---

## 4. Candidate Python Versions

### 4.1 Verified upstream status (2026-06-23)

| Version | First release | Current upstream phase | Expected end of regular bugfix binaries | Expected security support end |
|---|---|---|---|---|
| **3.12** | 2023-10-02 | security (source-only security fixes after final binary bugfix release) | 2025-04-08 (`3.12.10` final binary bugfix) | ~October 2028 |
| **3.13** | 2024-10-07 | bugfix | ~October 2026 (`3.13.16` expected final binary bugfix) | ~October 2029 |
| **3.14** | 2025-10-07 | bugfix | ~October 2027 (`3.14.14` expected final binary bugfix) | ~October 2030 |

**Verified facts:** Python Developer's Guide status table; PEP 693 (3.12), PEP 719 (3.13), PEP 745 (3.14).

### 4.2 Comparison table

| Criterion | Python 3.12 | Python 3.13 | Python 3.14 |
|---|---|---|---|
| Upstream support phase (Jun 2026) | security only | active bugfix | active bugfix |
| Remaining binary bugfix window | ended | ~4 months | ~16 months |
| Security support horizon | ~Oct 2028 | ~Oct 2029 | ~Oct 2030 |
| Apple Silicon macOS availability | official installers available | official installers available | official installers available; current docs describe 3.14.6 |
| Fedora deployment practicality | `python3.12` available in Fedora 42 | default `python3` in Fedora 42 is 3.13 | `python3.14` available as separate Fedora package; not the default `python3` in Fedora 42 |
| Django 6.0 support | supported | supported | supported |
| Django REST framework support | supported | supported | supported |
| FastAPI declared requirement | `>=3.10` | `>=3.10` | `>=3.10` |
| Litestar declared requirement | `>=3.8,<4.0` | `>=3.8,<4.0` | `>=3.8,<4.0` |
| yt-dlp declared requirement | `>=3.10` | `>=3.10` | `>=3.10` |
| Ecosystem maturity | highest among candidates | strong; broader than 3.14 | newest; some packages may lag wheels/build support |
| Newest-runtime risk | lowest | moderate | highest |
| Upgrade runway before security-only | already there | arrives ~late 2026 | arrives ~late 2027 |

### 4.3 Per-candidate assessment

#### Python 3.12

**Advantages**

- Broadest practical third-party compatibility among the three candidates.
- Still within upstream security support until approximately October 2028.
- Available on Fedora as `python3.12`.
- Satisfies declared requirements of the candidate server frameworks and yt-dlp.

**Disadvantages**

- Already past regular binary bugfix releases; only source-only security fixes remain.
- Shortest remaining support runway of the three candidates.
- Choosing it now would likely require another version migration sooner than 3.13 or 3.14.

**Key unknowns**

- Whether any Kronika-specific dependency will require features added after 3.12.

#### Python 3.13

**Advantages**

- Active bugfix support with expected final binary bugfix around October 2026.
- Security support expected until approximately October 2029.
- Aligns with Fedora 42 default `python3` / `python3.13`.
- Supported by Django 6.0, Django REST framework, FastAPI, Litestar, and yt-dlp per official metadata.
- More conservative than 3.14 while newer than 3.12.

**Disadvantages**

- Bugfix phase ends sooner than 3.14.
- Some niche packages may still publish wheels or test matrices later for 3.13 than for 3.12.

**Key unknowns**

- Whether any required media-related native dependency will lack tested wheels for 3.13 on macOS arm64 at scaffold time.

#### Python 3.14

**Advantages**

- Longest remaining bugfix and security horizon among the candidates.
- Official macOS installers and Fedora `python3.14` package exist.
- Supported by Django 6.0 and Django REST framework per official compatibility tables.
- Satisfies declared requirements of FastAPI, Litestar, and yt-dlp.

**Disadvantages**

- Newest runtime; highest risk of ecosystem lag for binary wheels and downstream testing.
- Not the default `python3` on Fedora 42; deployment docs must be explicit about version selection.
- Free-threading and other new runtime features are optional and not required by current Kronika direction; they add review surface without current product benefit.

**Key unknowns**

- Real compatibility of future media, database, and packaging dependencies at project start time.

### 4.4 Provisional recommendation (analysis only — not accepted)

**Provisional recommendation:** Python **3.13**

**Strongest alternative:** Python **3.12** if maximum dependency conservatism outweighs support runway.

**Confidence:** Medium

**Conditions that could change the recommendation**

- Evidence that a required dependency lacks stable 3.13 support on macOS arm64 and Fedora.
- Cooperator preference for longest support runway and acceptance of 3.14 ecosystem risk.
- Evidence that the team wants maximum conservatism and accepts earlier migration pressure → 3.12.

**Status:** Not accepted

---

## 5. Candidate Python Tooling

### 5.1 Candidates

| Tooling approach | Summary |
|---|---|
| **uv** | Astral tool combining Python installation, project management, lockfiles, virtual environments, workspaces, and pip-compatible workflows |
| **Poetry** | Mature dependency manager and packager with lockfile, dependency groups, and PEP 621 support |
| **pyenv + venv + pip** | pyenv for interpreter selection; standard library `venv`; pip (optionally pip-tools) for dependencies |

### 5.2 Comparison table

| Criterion | uv | Poetry | pyenv + venv + pip |
|---|---|---|---|
| Python version management | built-in (`uv python install`, `uv python pin`) | external interpreter required | pyenv primary purpose |
| Virtual environments | built-in project environments | built-in | `python -m venv` |
| Lockfiles | `uv.lock` | `poetry.lock` | not standard unless pip-tools added |
| Workspace / monorepo support | first-class workspaces with shared lockfile | per-project `pyproject.toml`; `-C` subdirectory support documented | manual coordination |
| Reproducibility | lock + sync | lock + sync | depends on chosen pip workflow |
| macOS support | documented multi-platform project tooling | requires Python 3.10+; multi-platform | widely used on macOS |
| Fedora / CI suitability | suitable; explicit Python pin and lock sync | suitable; version pinning recommended in CI docs | suitable; more manual |
| Standards compatibility | PEP 621 project metadata; pip interface available | PEP 621, PEP 735 dependency groups, PEP 517 | standards-native minimal stack |
| Migration / lock-in risk | moderate; Astral-specific lock/workspace features | moderate; Poetry-specific metadata features remain | lowest tool lock-in |
| Tauri / web monorepo fit | workspaces documented for multi-package repos | multiple independent projects with `-C` | no unified orchestration |
| Additional version manager needed? | no, if uv manages Python | yes, unless system Python is sufficient | yes (pyenv) |

### 5.3 Notes from official sources

**uv (verified):** Provides Python installation, project commands (`uv init`, `uv add`, `uv lock`, `uv sync`, `uv run`), workspaces with a single shared lockfile, and a pip-compatible interface. Workspaces are inspired by Cargo and support multiple packages in one repository.

**Poetry (verified):** Requires Python 3.10+. Provides lockfiles, dependency groups (PEP 735 / `tool.poetry.group`), synchronization via `poetry sync`, and monorepo usage through per-directory `pyproject.toml` with `poetry -C ./subdirectory`. Official CI guidance recommends explicit version pinning.

**pyenv + venv + pip (verified):** pyenv is a dedicated Python version manager. Standard library `venv` creates isolated environments. pip is the baseline installer; reproducibility requires an additional policy such as pinned requirements or pip-tools, which is not automatic.

**Worker inference:** uv's performance advantage is widely reported, but this evidence package does not rely on third-party benchmarks. Official uv documentation emphasizes workflow unification rather than benchmark claims.

### 5.4 Provisional recommendation (analysis only — not accepted)

**Provisional recommendation:** **uv**

**Strongest alternative:** **Poetry** if the team prefers a longer-established packaging workflow and independent per-package lockfiles.

**Confidence:** Medium

**Rationale (analysis):** A single tool can cover Python version pinning, lockfiles, environments, and future workspace needs for a hybrid monorepo without immediately adopting full multi-app scaffolding. Poetry remains credible if the Cooperator values its mature dependency-group model and broader historical adoption.

**Status:** Not accepted

---

## 6. Candidate Server Frameworks

### 6.1 Candidate set

| Framework | Role |
|---|---|
| **FastAPI** | High-level ASGI API framework built on Starlette and Pydantic |
| **Litestar** | Opinionated ASGI API framework with broader built-in feature set |
| **Django + Django REST framework** | Full-stack web framework plus API toolkit |

### 6.2 Starlette positioning

**Verified fact:** FastAPI documentation states it uses Starlette for web parts and Pydantic for data parts.

**Assessment (analysis):** Starlette should be treated as an **underlying ASGI component**, not the preferred application framework for Kronika's first server skeleton. Choosing Starlette directly would require more manual composition for routing, validation, OpenAPI, and dependency patterns that FastAPI already integrates.

### 6.3 Comparison table

| Criterion | FastAPI | Litestar | Django + DRF |
|---|---|---|---|
| Primary model | typed API-first ASGI app | typed API-first ASGI app | full-stack web framework + API layer |
| Async support | yes | yes | ASGI supported; much ecosystem remains sync-oriented |
| Typed request/response models | Pydantic-native | Pydantic, dataclasses, msgspec, others | DRF serializers; Django models often central |
| OpenAPI generation | built-in | built-in | available through DRF |
| Streaming / large files | via Starlette/ASGI | ASGI-native | possible; less API-first by default |
| WebSockets / SSE | via Starlette | supported | not the primary DRF focus |
| Dependency injection | `Depends` pattern | documented DI system | Django patterns / DRF view layers |
| Middleware / auth integration | Starlette middleware ecosystem | built-in middleware/guards | mature Django auth stack; heavier |
| Testing support | httpx `TestClient` documented | httpx-based testing documented | Django test client; DRF API tests |
| SQLite without assuming ORM | straightforward | straightforward | Django ORM is central to typical DRF apps |
| Background work boundary | external task runner likely needed | external task runner likely needed | Django ecosystem options exist; heavier |
| Project maturity | widely used; PyPI classifiers show Beta | Production/Stable classifier on current PyPI release | very mature |
| Documentation quality | extensive official docs | extensive official docs | extensive official docs |
| Security maintenance | active FastAPI / Starlette / Pydantic ecosystems | active Litestar ecosystem | strong Django security process |
| Complexity for small localhost service | low to moderate | moderate to higher (more built-ins) | high for API-only localhost service |
| systemd + loopback localhost fit | good fit for small ASGI service | good fit | workable but broader than needed |

### 6.4 Per-framework notes

#### FastAPI

**Advantages:** Minimal API surface for a localhost server skeleton; strong typing with Pydantic; OpenAPI and validation integrated; async-friendly; official docs position it as standards-based OpenAPI/JSON Schema.

**Disadvantages:** Not a complete application platform; background jobs, admin UI, and many cross-cutting concerns remain external choices.

**Risks:** Ecosystem encourages many optional extras; discipline needed to avoid scope creep.

#### Litestar

**Advantages:** Richer built-in feature set (middleware, plugins, DTOs, OpenAPI, CLI). Official docs state it is not a microframework and provide broader batteries than Starlette/FastAPI-style minimalism.

**Disadvantages:** Higher conceptual surface for a small project; official comparison positions it as more opinionated and broader than minimal API frameworks.

**Risks:** More framework features upfront can encourage premature architectural commitment.

#### Django + Django REST framework

**Advantages:** Mature security and release process; excellent documentation; strong if the server later needs admin interfaces, ORM-centric workflows, and a large integrated ecosystem.

**Disadvantages:** Heavier than needed for an initial localhost API skeleton; DRF commonly assumes Django ORM patterns; conflicts with "do not select ORM yet" and increases risk of server-centric design.

**Risks:** Framework overreach and hidden coupling to Django's application model.

### 6.5 Provisional recommendation (analysis only — not accepted)

**Provisional recommendation:** **FastAPI**

**Strongest alternative:** **Litestar** if the Cooperator prefers more built-in ASGI features and accepts higher initial framework surface.

**Decision questions for later ADR**

- How much built-in middleware/auth structure is needed in the first skeleton?
- Will the first server remain API-only without Django admin/ORM assumptions?
- What background-work boundary is acceptable before a job runner ADR exists?

**Status:** Not accepted

---

## 7. Candidate Repository Layout Boundaries

No directories may be created by this task. This section compares conceptual layouts only.

### 7.1 Option A — Python-first root package

```
pyproject.toml
src/kronika/
tests/
docs/
# future: web/, desktop/ added later
```

**Advantages:** Simplest start for server-first work; clear Python packaging; low CI complexity; matches current documentation-only state.

**Disadvantages:** Later web/Tauri addition may require layout migration or ad hoc top-level directories.

### 7.2 Option B — Application-oriented monorepo from the beginning

```
apps/server/
apps/web/
apps/desktop/
packages/
pyproject.toml or per-app manifests
```

**Advantages:** Clear ownership boundaries early; matches long-term multi-surface product.

**Disadvantages:** Premature empty `apps/web` and `apps/desktop` risk; higher CI and tooling complexity before code exists; violates current stage boundary against scaffolding.

### 7.3 Option C — Hybrid staged monorepo

```
pyproject.toml
src/kronika/
tests/
docs/
# reserved but not scaffolded: apps/web/, apps/desktop/ or frontend/
```

**Advantages:** Supports server-first delivery without empty multi-app scaffolding; allows future workspace expansion; keeps domain package name stable.

**Disadvantages:** Requires documented transition rules; some migration work still likely when web/desktop become active.

### 7.4 Comparison summary

| Criterion | Option A | Option B | Option C |
|---|---|---|---|
| Fit for current docs-only state | excellent | poor | excellent |
| Server-first priority | excellent | good | excellent |
| Future Tauri/web UI | deferred | immediate structure | reserved path |
| Python packaging clarity | excellent | moderate | excellent |
| CI complexity now | low | high | low to moderate |
| Premature empty scaffolding risk | low | high | low |
| Future multi-language tooling | moderate | excellent | good |

### 7.5 Provisional recommendation (analysis only — not accepted)

**Provisional recommendation:** **Option C — Hybrid staged monorepo**

**Strongest alternative:** **Option A** if the Cooperator wants the absolute minimum structure and accepts a later move.

**Migration implications:** If web/desktop arrive later, add dedicated directories and optionally convert to a workspace managed by the selected Python tooling. Domain code should remain importable from `src/kronika/` to avoid server/UI coupling.

**Status:** Not accepted

---

## 8. Candidate Configuration Strategy

This section compares strategies only. It does **not** choose a configuration library.

### 8.1 Option A — Environment variables plus ignored `.env` for local development

**Model:** Committed `.env.example` templates; real `.env` ignored; environment variables are the primary override mechanism.

**Advantages:** Aligns with twelve-factor guidance that config varies per deploy and should be stored in environment variables; simple for developers.

**Disadvantages:** Precedence among multiple env sources can become unclear; non-secret defaults may be duplicated across environments without a committed baseline layer.

### 8.2 Option B — Version-controlled non-secret configuration plus env/secrets for secrets

**Model:** Committed safe defaults (for example `config/default.toml` or structured settings files); secrets only through environment variables or secret store.

**Advantages:** Clear separation between safe defaults and secrets; easier review of non-secret settings in Git.

**Disadvantages:** Requires discipline to keep secrets out of committed files; multiple file formats possible without a single precedence model.

### 8.3 Option C — Layered configuration with explicit precedence

**Model:** Precedence among defaults, version-controlled local-safe settings, environment variables, and secret-store values (for example later systemd `EnvironmentFile`, macOS development `.env`, and secret injection on Fedora).

**Advantages:** Supports local development, test isolation, and production deployment with one conceptual model; compatible with typed settings validation later (for example Pydantic Settings loading from env and files, per official docs).

**Disadvantages:** More design work up front; precedence must be documented and tested.

### 8.4 Assessment against Kronika constraints

| Criterion | A | B | C |
|---|---|---|---|
| Secret safety | good if `.env` stays ignored | good | best if precedence is explicit |
| Local developer ergonomics | strong | strong | strong |
| Test isolation | moderate | good | best |
| macOS development | strong | strong | strong |
| Fedora systemd deployment | workable | workable | best fit for env files + overrides |
| Server/client separation | moderate | good | good |
| Typed validation later | possible | possible | best |
| Future settings UI | weaker | moderate | strongest |
| Secret replacement without display | moderate | moderate | strongest |

### 8.5 Provisional recommendation (analysis only — not accepted)

**Provisional recommendation:** **Option C — Layered configuration model**

**Strongest alternative:** **Option A** for the smallest initial implementation.

**Open implementation questions**

- Which non-secret settings belong in committed files versus environment variables?
- Where will provider secrets live on the Fedora NUC versus macOS development machines?
- What test harness will override settings without reading developer `.env` files?

**Status:** Not accepted

---

## 9. Compatibility Matrix

Provisional recommendations only. Nothing here is accepted.

|  | **Provisional: 3.13** | **Provisional: uv** | **Provisional: FastAPI** | **Provisional: Hybrid layout** | **Provisional: Layered config** |
|---|---|---|---|---|---|
| **Provisional: 3.13** | — | hard dep | soft pref | soft pref | independent |
| **Provisional: uv** | hard dep | — | soft pref | hard dep | soft pref |
| **Provisional: FastAPI** | soft pref | soft pref | — | soft pref | soft pref |
| **Provisional: Hybrid layout** | soft pref | hard dep | soft pref | — | soft pref |
| **Provisional: Layered config** | independent | soft pref | soft pref | soft pref | — |

### Interaction notes (analysis)

- **Hard dependencies:** Python version before tooling; Python tooling before repository workspace decisions become concrete.
- **Soft preferences:** FastAPI pairs naturally with Pydantic-based typed settings later, but does not mandate a specific configuration library choice.
- **Independent decisions:** Configuration strategy can be designed after server boundary exists, though early principles can be agreed now.
- **Postpone:** ORM, manifest format, IPC, frontend framework, authentication above Tailscale, and packaging/signing should remain outside this decision group.

---

## 10. Risk Register

| Risk | Likelihood | Impact | Evidence / rationale | Mitigation | Resolve by |
|---|---|---|---|---|---|
| Newest-runtime ecosystem lag | Medium (higher for 3.14) | High | Python devguide support phases; PyPI metadata shows broad support but not future media wheels | Prefer 3.13 unless dependency audit proves 3.14 safe | Decision 1 ADR |
| Dependency-manager lock-in | Medium | Medium | uv and Poetry both use tool-specific lock/metadata features | Document export/escape paths; pin tool versions in CI ADR later | Decision 2 ADR |
| Framework overreach | Medium | High | Django+DRF breadth vs localhost API skeleton need | Prefer API-first framework; defer ORM/admin choices | Decision 3 ADR |
| Premature monorepo complexity | Medium | Medium | Current repo is documentation-only; roadmap warns against oversized first implementation | Hybrid staged layout; no empty apps | Decision 4 ADR |
| Hidden server dependency violating local-first | Low now; Medium later | Critical | Product invariants in `SPEC.md` | Keep desktop catalog path independent in layout and config boundaries | Domain ADR + scaffold task |
| Configuration-secret leakage | Medium | Critical | Twelve-factor config guidance; Kronika security rules | Layered config, ignored local env files, no secrets in Git | Decision 5 ADR |
| macOS/Fedora divergence | Medium | Medium | Fedora 42 default `python3` is 3.13; macOS has system and python.org interpreters | Pin one project version; document both platforms explicitly | Decision 1 + deployment ADR |
| Media-tool subprocess and packaging risk | Medium | High | yt-dlp requires Python >=3.10 and external ffmpeg binary; not a framework concern alone | Keep media integration behind adapters per `SPEC.md` | Acquisition-phase ADR |

---

## 11. Recommended Decision Order

| Step | Decision | Evidence sufficient now? | Must wait for | Future ADR topic | Implementation legal after acceptance |
|---|---|---|---|---|---|
| 1 | Supported Python version | Yes | None | `ADR-Python-Version` | Pin version in project metadata |
| 2 | Python tooling | Mostly | Step 1 | `ADR-Python-Tooling` | Create authorized `pyproject.toml`, lockfile policy |
| 3 | Server API framework | Mostly | Steps 1–2 | `ADR-Server-API-Framework` | Server skeleton dependencies only |
| 4 | Repository layout | Mostly | Steps 1–3 | `ADR-Repository-Layout` | Authorized directories and package paths |
| 5 | Configuration strategy | Partially | Steps 3–4 | `ADR-Configuration-Strategy` | Non-secret config files, env precedence rules, example templates |

---

## 12. Provisional Recommendation Summary

| Decision | Provisional recommendation | Strongest alternative | Confidence | Unresolved blocker | Status |
|---|---|---|---|---|---|
| 1. Python version | 3.13 | 3.12 | Medium | Future media/native wheel audit | **Accepted** — see [ADR-0001](adr/0001-supported-python-version.md) |
| 2. Python tooling | uv | Poetry | Medium | Cooperator preference on tool maturity | **Accepted** — Poetry; see [ADR-0002](adr/0002-python-environment-and-dependency-manager.md) |
| 3. API framework | FastAPI | Litestar | Medium | Background-work and auth boundary ADRs | **Accepted** — see [ADR-0003](adr/0003-initial-server-api-framework.md) |
| 4. Repository layout | Hybrid staged monorepo (Option C) | Python-first root (Option A) | Medium | Future web/Tauri packaging choice | **Accepted** — see [ADR-0004](adr/0004-repository-layout.md) |
| 5. Configuration strategy | Layered model (Option C) | Env + `.env` (Option A) | Medium | Secret storage ADR on Fedora NUC | **Accepted** — see [ADR-0005](adr/0005-configuration-strategy.md) |

---

## 13. Primary Sources

### Python version and platform support

| Source title | Owner | Retrieved | URL | Supported claims |
|---|---|---|---|---|
| Status of Python versions | Python Software Foundation | 2026-06-23 | https://devguide.python.org/versions/ | Support phases for 3.12, 3.13, 3.14 |
| PEP 693 – Python 3.12 Release Schedule | Python Software Foundation | 2026-06-23 | https://peps.python.org/pep-0693/ | 3.12 release date, bugfix end, security lifespan |
| PEP 719 – Python 3.13 Release Schedule | Python Software Foundation | 2026-06-23 | https://peps.python.org/pep-0719/ | 3.13 release date, expected bugfix end, security lifespan |
| PEP 745 – Python 3.14 Release Schedule | Python Software Foundation | 2026-06-23 | https://peps.python.org/pep-0745/ | 3.14 release date, expected bugfix end, security lifespan |
| Using Python on macOS | Python Software Foundation | 2026-06-23 | https://docs.python.org/3/using/mac.html | Official macOS installers; universal2 builds; Apple Silicon support |
| python3.13 Fedora 42 package | Fedora Project | 2026-06-23 | https://packages.fedoraproject.org/pkgs/python3.13/python3/fedora-42-updates.html | Fedora 42 provides Python 3.13 |
| python3.14 package overview | Fedora Project | 2026-06-23 | https://packages.fedoraproject.org/pkgs/python3.14/python3.14/ | Python 3.14 available as separate Fedora package |
| Changes/Python means Python3 | Fedora Project | 2026-06-23 | https://fedoraproject.org/wiki/Changes/Python_means_Python3 | `python3` command maps to Python 3 |

### Python tooling

| Source title | Owner | Retrieved | URL | Supported claims |
|---|---|---|---|---|
| uv Features | Astral | 2026-06-23 | https://docs.astral.sh/uv/getting-started/features/ | Python install, project, lock, sync, tool commands |
| Using workspaces | Astral | 2026-06-23 | https://docs.astral.sh/uv/concepts/workspaces/ | Workspace layout, shared lockfile, monorepo support |
| Poetry Introduction | Poetry | 2026-06-23 | https://python-poetry.org/docs/ | Poetry purpose, Python 3.10+ requirement, lockfiles |
| Managing dependencies | Poetry | 2026-06-23 | https://python-poetry.org/docs/managing-dependencies/ | Dependency groups, sync, install options |
| pre-commit hooks (monorepo `-C`) | Poetry | 2026-06-23 | https://python-poetry.org/docs/pre-commit-hooks/ | Per-subdirectory Poetry project usage |
| pyenv repository | pyenv | 2026-06-23 | https://github.com/pyenv/pyenv | Python version management purpose |

### Server frameworks

| Source title | Owner | Retrieved | URL | Supported claims |
|---|---|---|---|---|
| FastAPI documentation | FastAPI / Sebastián Ramírez | 2026-06-23 | https://fastapi.tiangolo.com/ | Framework scope; Starlette and Pydantic dependency |
| fastapi PyPI metadata | PyPI / FastAPI | 2026-06-23 | https://pypi.org/project/fastapi/ | Requires Python >=3.10 |
| Litestar documentation | Litestar | 2026-06-23 | https://docs.litestar.dev/latest/ | Framework scope; OpenAPI; non-microframework positioning |
| litestar PyPI metadata | PyPI / Litestar | 2026-06-23 | https://pypi.org/project/litestar/ | Requires Python >=3.8,<4.0; Production/Stable classifier |
| Starlette documentation | Encode | 2026-06-23 | https://www.starlette.io/ | ASGI toolkit/framework positioning |
| Django download and support table | Django Software Foundation | 2026-06-23 | https://www.djangoproject.com/download/ | Django 6.0.6 current release |
| FAQ: Installation (Python versions) | Django Software Foundation | 2026-06-23 | https://docs.djangoproject.com/en/6.0/faq/install/ | Django 6.0 supports Python 3.12, 3.13, 3.14 |
| Django REST framework homepage | Encode | 2026-06-23 | https://www.django-rest-framework.org/ | DRF requirements for Django and Python versions |

### Media dependency boundary

| Source title | Owner | Retrieved | URL | Supported claims |
|---|---|---|---|---|
| yt-dlp README | yt-dlp | 2026-06-23 | https://github.com/yt-dlp/yt-dlp | Python 3.10+ support statement |
| yt-dlp PyPI metadata | PyPI / yt-dlp | 2026-06-23 | https://pypi.org/project/yt-dlp/ | Requires Python >=3.10 |

### Configuration strategy

| Source title | Owner | Retrieved | URL | Supported claims |
|---|---|---|---|---|
| The Twelve-Factor App — Config | twelve-factor.net | 2026-06-23 | https://12factor.net/config | Store config in environment; separate config from code |
| Settings Management | Pydantic | 2026-06-23 | https://docs.pydantic.dev/latest/concepts/pydantic_settings/ | Typed settings from environment and files (library not selected here) |

---

## 14. Questions for the Orchestrator and Cooperator

1. **Supported Python version:** Should Kronika standardize on Python 3.12, 3.13, or 3.14 for the first implementation phase, given the support-phase and Fedora/macOS evidence above?

Future decision questions (not to be decided in this document):

2. Python environment and dependency manager
3. Initial server API framework
4. Initial repository layout boundary
5. Local development configuration strategy

---

## 15. Explicit Non-Decisions

This task did **not** select or create:

- project Python version
- dependency manager
- API framework
- repository scaffold
- configuration library
- ORM
- database schema
- IPC
- frontend framework
- authentication design
- packaging strategy

No ADR was created. No application source code, tests, package files, lockfiles, environment templates, or application directories were added.
