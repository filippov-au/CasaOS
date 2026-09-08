# App Store updates and rollback

The App Store's **Updates** tab provides one update flow. CasaOS refreshes the configured app catalogs, starts with each app's current store definition, and then checks its image registries for newer versions. Available apps show a clear installed-to-offered version change and an **Update** button. Image references and the **Restore** action are under **Details**; there is no source selector.

The latest catalog supplies image repositories, tag channels, metadata, and newly introduced configuration defaults. Explicit installed settings take precedence, preserving environment values, commands, ports, and data mounts. Incompatible service layouts still require a manual update. Custom Compose apps without a store identity can receive registry image updates using their existing configuration; ambiguous or missing configured store origins remain errors.

For stable `x.y.z` or `vx.y.z` tags, the checker offers the highest newer stable tag with the same prefix that supports the installed OS and CPU architecture. This can include a new major version. Floating tags such as `latest`, shortened tags such as `16`, and custom suffixes such as `1.2.3-alpine` follow new builds of that channel. A newer installed semantic version is not downgraded to an older catalog tag. Explicit digest pins are preserved. The comparison uses the platform's image configuration digest, so changes confined to another architecture do not produce false alerts.

A successful check prepares an installation plan in the private recovery record. **Update** confirms the displayed version and sends that plan's token. A different plan, changed app settings, or a plan older than 24 hours requires another check. After pulling, CasaOS verifies the image IDs against the checked plan before replacing any containers. A tag that moved in the meantime is reported as an error rather than silently installing a different build. Checking itself never downloads image layers or replaces containers.

Registry checks use HTTPS with certificate verification, support anonymous and configured registry authentication, and have a 45-second deadline per image. A failed service check prevents an installable offer for that app and leaves the error visible. Previous update/rollback recovery remains available independently.

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
- `POST /v2/app_management/updates/check`: refresh catalogs and prepare combined catalog/registry updates. Responses include `target_version`, `update_ready`, `update_token`, and per-service `registry_images`.
- `PATCH /v2/app_management/compose/{id}?update_token=…`: install the reviewed plan, saving recovery before replacing containers. A stale token returns HTTP 409.
- Explicit legacy checks with `?source=store` or `?source=registry` remain accepted for existing clients. Store-only checks clear combined plans; registry-only checks remain informational. Clients without a combined check can still use the original PATCH update behavior.
- `POST /v2/app_management/compose/{id}/rollback`: starts recovery; poll the list endpoint for its result.

Conflicting operations on one app return HTTP 409. The list response contains no saved environment values or Compose configuration. `combined_updates_supported` identifies backends that can install combined updates; the UI explains when an older backend needs upgrading. Old API clients retain their existing update endpoint and message-bus notifications.

Recovery records live in `app-updates` alongside the configured apps directory (normally `/var/lib/casaos/app-updates`). Records use private permissions and atomic writes. Previous images have dedicated `casaos-rollback/` references. Removing these images outside CasaOS disables recovery and is reported in the UI.

A replacement recovery point is committed only after the new app starts and configured Compose health checks pass. Startup failures retain a pending recovery point and offer manual rollback. A service restart marks unfinished operations as interrupted; saved installations remain discoverable even when no containers survived the failed operation. Data volumes are never removed by updates or rollback, and anonymous volumes are retained by their actual Docker names.

Apps with incompatible service layouts, replicated services, custom version control, or ambiguous store origins require resolving that condition before updating. The feature does not provide automatic updates, bulk updates, data backups, or recovery for updates made before it was installed.

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
