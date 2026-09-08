# CasaOS Development Instructions

## Workspace

Development spans three separate Git repositories located next to each other:

```text
workspace/
├── CasaOS/                # Core service, system APIs, and system updates
├── CasaOS-UI/             # Web interface
└── CasaOS-AppManagement/  # App Store, Docker, and Compose
```

Relative paths below start at the `CasaOS` repository root. In another workspace, locate sibling repositories relative to the current checkout.

Each repository has its own changes, branches, dependencies, and commits. Before working, check `git status --short --branch` and `git remote -v` in each affected repository. Preserve existing user changes.

The project forks are `filippov-au/CasaOS`, `filippov-au/CasaOS-UI`, and `filippov-au/CasaOS-AppManagement`. In the current checkouts, the core fork remote is `origin`; the UI and AppManagement fork remote is `fork`, while their `origin` points to `IceWhaleTech`. Verify remote URLs before pushing. Commit and push separately in each repository when authorized by the user's request.

Before editing a sibling repository, read its own `AGENTS.md` files if present. This file lives in `CasaOS`; when starting an agent separately in UI or AppManagement, provide this file as shared project context.

## Where to Make Changes

| Task | Repository and main locations |
| --- | --- |
| Screens, components, and user interactions | `CasaOS-UI/src/components/`, `src/service/` |
| App installation, updates, rollback, and container operations | `CasaOS-AppManagement/service/`, `route/v2/` |
| App management API contract | `CasaOS-AppManagement/api/app_management/openapi.yaml` |
| Core system APIs | `CasaOS/route/`, `service/`, `api/casaos/openapi.yaml` |
| Updating CasaOS itself from fork sources | `CasaOS/internal/sourceupdate/`, `route/v1/source_update.go`, `scripts/casaos-source-update.py` |

A feature can span multiple repositories. Trace the complete flow: UI component → API client → backend route → service. Place new App Store and container logic in AppManagement. Gateway, UserService, LocalStorage, and MessageBus are separate services outside these three checkouts.

The UI uses Vue 2.7, Vue CLI, Vuex, and Buefy/Bulma. Follow existing components and conventions; do not migrate to Vue 3 without a specific request. The root `CasaOS/package.json` serves TypeScript SDK generation; the web application lives in the sibling `CasaOS-UI` repository.

The `.gitmodules`, `Makefile`, and older sections of `DEVELOPING.md` still refer to nested `UI`/`CasaOS-UI` directories and Yarn. For this workspace, use the sibling `../CasaOS-UI` directory and the pnpm version specified in its `package.json`.

## Build and Validation

Both Go modules declare Go 1.21. The source updater pins Go 1.21.13, Node 20, and pnpm 9.0.6 for builds. For compatibility issues, consult `scripts/casaos-source-update.py` and the repository manifests.

Run commands from the root of the relevant repository. Run generation before building when generated files are needed, and after changing OpenAPI. Edit the source schema to change the contract, then review the generated output.

Core (`CasaOS`):

```sh
go generate ./...
go test ./... -run '^$'
go test ./internal/sourceupdate ./route/v1 -run 'Source|Check|Start'
go build -o /tmp/casaos-dev .
python3 -m unittest discover -s tests -p 'test_source_update.py' -v
```

AppManagement (`../CasaOS-AppManagement`):

```sh
go generate ./...
go test ./... -run '^$'
go test ./service ./route/...
go test ./pkg/docker -run '^TestPullStreamDetectsDaemonErrors$'
go build -o /tmp/casaos-app-management-dev .
```

`go test ./... -run '^$'` checks that tests compile without executing them. The commands above reflect the source updater's main checks; also run tests for changed packages. Account for test environment requirements when running the full `go test ./...` suite.

UI (`../CasaOS-UI`):

```sh
pnpm install --frozen-lockfile
pnpm exec vitest run
pnpm run build
```

The UI build output is `build/sysroot/var/lib/casaos/www/`. For development, use `pnpm run dev`; configure the backend address through `VUE_APP_DEV_IP` and `VUE_APP_DEV_PORT` (see `.env.dev` and `vue.config.js`).

AppManagement includes an integration test for updates and rollback in a test Docker environment:

```sh
CASAOS_UPDATE_DOCKER_TEST=1 go test ./service -run '^TestDockerUpdateAndRollback$' -count=1 -timeout 7m
```

It requires Docker and creates and removes a disposable Compose project. Run ordinary checks without this flag unless the task requires integration testing.

## Two Update Mechanisms

1. **App Store applications.** UI: `src/components/Apps/AppUpdates.vue` and `src/service/updates.js`. Backend: `service/updates.go` and `service/updates_docker.go` in AppManagement. See [docs/app-updates.md](docs/app-updates.md).
2. **CasaOS system components.** The source updater fetches the three forks' `main` branches, builds and tests them, backs up the previous installation, and replaces components. See [docs/source-updates.md](docs/source-updates.md).

Preserve API compatibility and existing clients when making changes. For system updates, preserve the `/v1/sys/version`, `/v1/sys/update`, and `/var/log/casaos/upgrade.log` contracts, as well as the behavior of official installations without the source updater.

Application updates and rollback must preserve data and volumes. Rollback restores previous images and Compose settings but does not reverse database migrations. Preserve protection against concurrent operations, recovery after failures, and the confidentiality of saved settings.

Local builds do not install changes on a server. Deploy to the target server using `docs/source-updates.md` when deployment is part of the user's request. The updater builds fetched fork commits, not uncommitted working directory changes. When deploying new APIs together with UI changes, install AppManagement before the UI.

## Completing a Task

Keep changes scoped to the task, format changed Go code with `gofmt`, and avoid unrelated dependency updates or build artifacts. Add or update appropriate tests for behavior changes; for documentation changes, checking paths, commands, and the diff is sufficient.

Before finishing, review the diff and run `git diff --check` in every changed repository. Briefly report what changed, which repositories were affected, which checks ran, and what remains unverified. Clearly distinguish locally prepared changes, pushed commits, and the version deployed on the server.
