package v1

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/IceWhaleTech/CasaOS/internal/sourceupdate"
	"github.com/labstack/echo/v4"
)

func sourceFixture(t *testing.T, offline bool) {
	t.Helper()
	root := t.TempDir()
	for path, data := range map[string]string{
		"usr/local/sbin/casaos-source-update":        "script",
		"var/lib/casaos/fork-updates/installed.json": `{"commits":{"CasaOS":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`,
	} {
		path = filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(data), 0755); err != nil {
			t.Fatal(err)
		}
	}
	previous := sourceUpdates
	sourceUpdates = sourceupdate.New(root, func(ctx context.Context, name string, args ...string) (string, error) {
		if name == "git" {
			if offline {
				return "", errors.New("offline")
			}
			return "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/heads/main", nil
		}
		return "LoadState=loaded\nActiveState=inactive\nResult=success", nil
	})
	t.Cleanup(func() { sourceUpdates = previous })
}
func TestSourceUsesExistingVersionContract(t *testing.T) {
	sourceFixture(t, false)
	rec := httptest.NewRecorder()
	ctx := echo.New().NewContext(httptest.NewRequest("GET", "/v1/sys/version", nil), rec)
	if err := GetSystemCheckVersion(ctx); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Success int `json:"success"`
		Data    struct {
			NeedUpdate     bool   `json:"need_update"`
			CurrentVersion string `json:"current_version"`
			Version        struct {
				ChangeLog string `json:"change_log"`
			} `json:"version"`
		} `json:"data"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if rec.Code != 200 || result.Success != 200 || !result.Data.NeedUpdate || result.Data.CurrentVersion == "" || !strings.Contains(result.Data.Version.ChangeLog, "bbbbbbbbbbbb") {
		t.Fatal(rec.Body.String())
	}
}
func TestSourceOfflineCheckDoesNotBreakLogin(t *testing.T) {
	sourceFixture(t, true)
	rec := httptest.NewRecorder()
	ctx := echo.New().NewContext(httptest.NewRequest("GET", "/v1/sys/version", nil), rec)
	if err := GetSystemCheckVersion(ctx); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Success int `json:"success"`
		Data    struct {
			NeedUpdate bool   `json:"need_update"`
			Error      string `json:"check_error"`
			Current    string `json:"current_version"`
		} `json:"data"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &result)
	if rec.Code != 200 || result.Success != 200 || result.Data.Error == "" || result.Data.NeedUpdate || result.Data.Current == "" {
		t.Fatal(rec.Body.String())
	}
}
func TestSourceUsesExistingUpdateContract(t *testing.T) {
	sourceFixture(t, false)
	rec := httptest.NewRecorder()
	ctx := echo.New().NewContext(httptest.NewRequest("POST", "/v1/sys/update", nil), rec)
	if err := SystemUpdate(ctx); err != nil {
		t.Fatal(err)
	}
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"success":200`) {
		t.Fatal(rec.Body.String())
	}
}
