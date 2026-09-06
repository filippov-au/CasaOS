# App Store updates and rollback

The App Store has an **Updates** tab for checking installed applications and updating them individually to their configured store's version. Each app shows its current version, the offered version, check errors, operation progress, and an available previous version.

Rollback restores the previous images and Compose settings. It keeps current app data, including changes made after the update. It does not reverse database migrations. There is one recovery point per app; a successful rollback consumes it. Failed rollback attempts keep it available for retry.

## Source projects

This feature spans the separate `CasaOS-UI` and `CasaOS-AppManagement` repositories. The CasaOS core service does not own either the App Store interface or container operations. For local development, keep these three repositories as siblings:

```
Work/
  CasaOS/
  CasaOS-UI/
  CasaOS-AppManagement/
```

In the UI project, `src/components/Apps/AppUpdates.vue` implements the tab's content and `src/service/updates.js` calls the backend. In app-management, `service/updates.go` owns persistent state and `service/updates_docker.go` handles image retention and container replacement. The API is defined in `api/app_management/openapi.yaml`.

## API and recovery storage

All endpoints use the existing app-management authentication middleware:

- `GET /v2/app_management/updates`: installed apps with check and operation status.
- `POST /v2/app_management/updates/check`: refresh catalogs and check all installed apps; individual failures remain visible in the response.
- `PATCH /v2/app_management/compose/{id}`: existing update endpoint, now saves a recovery point before replacing containers.
- `POST /v2/app_management/compose/{id}/rollback`: starts recovery; poll the list endpoint for its result.

Conflicting operations on one app return HTTP 409. The list response contains no saved environment values or Compose configuration. Old API clients retain their existing update endpoint and message-bus notifications.

Recovery records live in `app-updates` alongside the configured apps directory (normally `/var/lib/casaos/app-updates`). Records use private permissions and atomic writes. Previous images have dedicated `casaos-rollback/` references. Removing these images outside CasaOS disables recovery and is reported in the UI.

A replacement recovery point is committed only after the new app starts and configured Compose health checks pass. Startup failures retain a pending recovery point and offer manual rollback. A service restart marks unfinished operations as interrupted; saved installations remain discoverable even when no containers survived the failed operation. Data volumes are never removed by updates or rollback, and anonymous volumes are retained by their actual Docker names.

Apps with incompatible service layouts, replicated services, custom version control, or ambiguous store origins require resolving that condition before updating. Unmanaged apps remain visible without a store update action. The feature does not provide automatic updates, bulk updates, data backups, or recovery for updates made before it was installed.

## Build and validation

Backend, using the repository's Go 1.21 toolchain:

```sh
cd ../CasaOS-AppManagement
go generate ./...
go test ./...
go build -o dist/casaos-app-management .
```

The opt-in Docker integration test creates and removes a disposable two-service project. It verifies both image versions, custom environment values, bind-mounted data, and an anonymous volume across an update and rollback:

```sh
CASAOS_UPDATE_DOCKER_TEST=1 go test ./service -run '^TestDockerUpdateAndRollback$' -count=1 -timeout 7m
```

The test uses API 1.44 for compatibility between the existing Compose library and Docker 29. Production Docker compatibility otherwise follows app-management's existing Docker client.

Frontend:

```sh
cd ../CasaOS-UI
pnpm install --frozen-lockfile
pnpm exec vitest run
pnpm run build
```

Deploy the app-management build before the UI build, following the existing CasaOS packaging process. The UI displays an unavailable-feature message if connected to a backend without the new endpoints. Building and testing this feature does not install it into a running CasaOS instance.

## Following your fork's main branches

For a mini-PC that should run your latest changes without publishing releases, use [source updates](source-updates.md). The updater fetches your branches, builds and tests in Docker, and backs up the installed components before replacement.
