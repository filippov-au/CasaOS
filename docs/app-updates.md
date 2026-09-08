# App Store updates and rollback

The App Store has an **Updates** tab that opens with **Docker registry** selected. It checks every installed Compose app, including custom apps, directly against its image registries. Each service shows its configured image, the offered registry image, installed and remote image IDs, and any check error. Checking does not download image layers or change containers.

For stable `x.y.z` or `vx.y.z` tags, the checker offers the highest newer stable tag with the same prefix that supports the installed OS and CPU architecture. This can include a new major version. Floating tags such as `latest`, shortened tags such as `16`, and custom suffixes such as `1.2.3-alpine` are checked for new builds of that same tag. Prerelease tags are not offered as stable releases. Digest references stay pinned. Local builds and restored rollback references show an explanation instead of claiming to be current.

The comparison uses the platform's image configuration digest, so changes to another architecture in a multi-platform index do not produce false alerts. Checks use HTTPS with certificate verification, support anonymous and Docker registry authentication through the existing credential configuration, and have a 45-second deadline per image. Errors remain visible separately from “up to date.”

Registry versions are informational: select a different tag in app settings after reviewing its compatibility. Choose **App Store** in the source menu to check catalogs and use the existing per-app update action. Store updates show the current version, offered store version, operation progress, and an available previous version.

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
- `POST /v2/app_management/updates/check?source=registry`: check installed registry images without consulting store catalogs. Results are returned in additive `registry_images` and `registry_checked_at` fields, independently of the existing store status.
- `POST /v2/app_management/updates/check` (or `?source=store`): refresh catalogs and check all installed apps; individual failures remain visible in the response. Omitting the source preserves the behavior of existing clients.
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
