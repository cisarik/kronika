# Kronika Desktop Architecture

## Status

This is a living permanent product architecture and UX reference. It records
accepted desktop direction; it does not claim that a desktop shell is currently
implemented.

Classification: living permanent product architecture/UX reference.

Consumers: Orchestrator, Worker, designers, implementers, maintainers, and
security reviewers.

Retention: remains while the desktop product subsystem exists.

Inbound links: [README.md](README.md), [PRODUCT.md](PRODUCT.md),
[SPEC.md](SPEC.md), [ROADMAP.md](ROADMAP.md), [ADR-0021](docs/adr/0021-tauri-desktop-shell.md),
and [ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task. Git history remains the archive.

## Desktop Product Role

Kronika will become a cross-platform desktop application for local-first video
and animated-media ownership. The normal end-user experience must not require
opening an external browser. Browser mode remains useful for development,
diagnostics, and controlled operator workflows, but the product should feel like
one native desktop application.

Initial practical implementation and product validation are MacBook-first.
Cross-platform compatibility remains required, but early implementation should
prioritize a polished and functional macOS MVP before simultaneous
platform-specific completion.

## Tauri Shell Responsibilities

Tauri v2 is the accepted future desktop shell. The shell is responsible for:

- displaying the existing Kronika HTML/CSS/JavaScript UI in a native system
  WebView;
- enforcing a single-instance lifecycle;
- launching and supervising the packaged Python/FastAPI sidecar;
- presenting tray or macOS menu-bar behavior;
- restoring or focusing the gallery window;
- opening future Settings;
- stopping the backend on explicit Quit;
- exposing narrowly scoped native capabilities.

Tauri must not duplicate domain, catalog, provider, media-analysis, persistence,
or transfer logic. Those responsibilities remain in the Python backend and the
Kronika domain/application layers.

## Python Sidecar Responsibilities

The Python/FastAPI sidecar remains the application backend. It owns:

- domain and application use cases;
- local database access through approved repository boundaries;
- media scanning and analysis adapters;
- provider adapter boundaries;
- API request and response contracts;
- structured sanitized errors and logs.

The sidecar should bind only to loopback by default. Future desktop bootstrap
should prefer an ephemeral port plus per-launch authorization, but exact IPC,
port discovery, bootstrap authentication, and bundling remain deferred.

## Startup Lifecycle

The future desktop startup sequence should conceptually:

1. enforce single instance before starting other long-lived work;
2. resolve application-owned runtime directories;
3. launch the packaged Python sidecar;
4. wait for a bounded local bootstrap or health handshake;
5. open the main WebView only when the backend is ready;
6. surface clear startup failure states without exposing private paths or raw
   diagnostics.

Startup progress must be truthful. If the backend readiness phase is
indeterminate, the UI may show restrained indeterminate motion but must not show
invented percentages.

## Shutdown Lifecycle

`Quit` is the explicit application termination action. It should stop the
supervised backend cleanly, release runtime resources, and then exit the shell.

Closing the main window should normally hide the window and keep the application
available through the tray or menu-bar presence. Unexpected backend termination
should be detected and surfaced as a recoverable application state when possible.

## Single Instance And Initial Tray Menu

Kronika desktop should run as one user-facing application instance. A second
launch should restore or focus the existing instance.

The initial tray or macOS menu-bar menu contains:

- `Gallery`: restore or focus the main gallery window;
- `Settings`: open the future Settings view;
- `Quit`: stop the backend and exit.

## Browser Development Fallback

The existing loopback FastAPI/browser mode remains available for development and
diagnostics. Browser mode may lack native-only features such as file-manager
reveal, native clipboard for media files, and tray behavior. It should degrade
clearly rather than pretending those capabilities exist.

The repository-root `./kronika` launcher is the current browser-development
bridge. It can supervise the local FastAPI server and open the external default
browser, but it does not replace or implement the accepted future Tauri shell.
The future `Kronika.app` will own its WebView and supervised sidecar
lifecycle.

## Native Capability Boundary

Future native capabilities may include file selection, directory selection,
save/export destinations, downloading to an approved destination, revealing a
file in the platform file manager, opening media through VLC, clipboard
operations, native notifications, single-instance handling, and application
lifecycle behavior.

The WebView must not receive unrestricted filesystem, shell, process, or
arbitrary-command access. Every native command must be explicit, allowlisted,
scoped to user-approved paths or Kronika-owned runtime directories, and tested
for sanitized failure behavior.

## VLC Boundary

External VLC remains the first intended full-playback backend. The desktop shell
may provide the native bridge needed to open local files or future authorized
remote URLs through VLC, but command construction and playback policy must stay
behind a Kronika-owned playback boundary. Inline GIF or short-video preview is
separate from full playback.

## Clipboard, Download, And Export Direction

Future desktop workflows may support `Download`, `Export`, and
`Download + Copy to Clipboard`. The clipboard workflow is especially important
for GIF and short MP4 media. It must use native platform capabilities, verify any
downloaded representation before placing it on the clipboard, and provide a
fallback when the platform or destination application does not accept the
payload.

Kronika must not claim universal direct-paste compatibility for GIF or MP4.

## Security And Least Privilege

Desktop native power must be constrained by least privilege. The shell must not
turn the WebView into a privileged local shell. Provider secrets must not be
distributed to ordinary clients or stored in browser local storage. Runtime
bootstrap credentials must be short-lived and hidden from ordinary UI state.

Loopback binding remains the default. Public exposure is not a desktop default.
Future remote access remains Tailscale-only unless superseded by a later
accepted decision.

## Premium Visual Principles

The desktop application should feel deliberate, high-quality, and cover-first.
It should avoid generic administrative-dashboard composition. Dense operational
surfaces may be restrained, but the flagship gallery should carry a premium
visual identity through strong covers, careful spacing, polished motion, clear
states, and fast interaction.

## Truthful Progress-State Taxonomy

Determinate operations should eventually show real values when available:

- transferred bytes;
- total bytes;
- percentage;
- speed;
- ETA;
- verification or finalization state.

Indeterminate operations, such as provider analysis or backend startup waiting,
may use restrained shimmer, masked text, pulse, or fade animation. They must not
show fabricated percentages or invented backend stages.

## Accessibility And Reduced Motion

Desktop UI must support keyboard navigation, visible focus, screen-reader
semantics, adequate contrast, reduced-motion behavior, and clear status text.
Animations must not be required to understand state. Reduced-motion users should
receive static or greatly simplified feedback for shimmer and preview motion.

## Later Cross-Platform Validation

MacBook-first implementation must not erase cross-platform requirements. Later
validation must address macOS, Windows, and Linux WebView behavior, menus,
clipboard formats, file selection, path handling, VLC invocation, notifications,
installation, update, and security model differences.

## Deferred Implementation Decisions

Deferred decisions include:

- exact Tauri project layout;
- frontend build tooling;
- Python sidecar bundling or freezer;
- sidecar IPC and bootstrap authorization;
- port discovery;
- native secret storage;
- signing, notarization, installer, and updater;
- tray icon assets;
- Settings structure;
- crash recovery;
- platform-specific clipboard payloads;
- temporary download cleanup rules;
- Windows and Linux packaging;
- remote streaming protocol;
- production deployment model.
