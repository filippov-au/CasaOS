package v1

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/IceWhaleTech/CasaOS/common"
	"github.com/IceWhaleTech/CasaOS/internal/sourceupdate"
	"github.com/IceWhaleTech/CasaOS/model"
	"github.com/labstack/echo/v4"
)

var sourceUpdates = sourceupdate.New("/", sourceupdate.Command)

// Preserve the existing version API and update modal contract for fork installs.
func getSourceVersion(ctx echo.Context) error {
	request, cancel := context.WithTimeout(ctx.Request().Context(), 40*time.Second)
	defer cancel()
	status, err := sourceUpdates.Check(request)
	// Version lookup is also used during login. Report check failures as data,
	// preserving the current version and allowing offline login to complete.
	if err != nil {
		status.CheckError = err.Error()
	}
	if !status.Enabled {
		status.CheckError = "Source updater setup is incomplete; run casaos-source-update update once from the terminal"
	}
	var log strings.Builder
	log.WriteString("# CasaOS · filippov-au / main\n\n")
	log.WriteString("Build and install the latest commits from your fork. Existing components are backed up before installation. App data is preserved.\n\n")
	for _, repo := range status.Repositories {
		current := repo.Installed
		if len(current) > 12 {
			current = current[:12]
		}
		latest := repo.Latest
		if len(latest) > 12 {
			latest = latest[:12]
		}
		log.WriteString("- **" + repo.Name + "**: `" + current + "` → `" + latest + "`\n")
	}
	running := status.Operation == "running"
	if running {
		log.WriteString("\nAn update is already running. Click Upgrade Now to view its log.\n")
	}
	return ctx.JSON(http.StatusOK, model.Result{Success: 200, Message: "success", Data: map[string]interface{}{
		"need_update":     status.Available || running,
		"current_version": common.VERSION,
		"version":         model.Version{Version: "main", ChangeLog: log.String()},
		"check_error":     status.CheckError,
		"source":          status,
	}})
}
func startSourceUpdate(ctx echo.Context) error {
	request, cancel := context.WithTimeout(ctx.Request().Context(), 10*time.Second)
	defer cancel()
	if err := sourceUpdates.Start(request); err != nil && !errors.Is(err, sourceupdate.ErrBusy) {
		return ctx.JSON(http.StatusServiceUnavailable, map[string]string{"message": err.Error()})
	}
	return ctx.JSON(http.StatusOK, model.Result{Success: 200, Message: "Source update started"})
}
