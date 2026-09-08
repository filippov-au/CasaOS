package sourceupdate

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

const shaA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const shaB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

func setup(t *testing.T, runner Runner) *Manager {
	t.Helper()
	m := New(t.TempDir(), runner)
	for name, data := range map[string]string{
		"usr/local/sbin/casaos-source-update":        "#!/usr/bin/python3",
		"var/lib/casaos/fork-updates/installed.json": `{"commits":{"CasaOS":"` + shaA + `","CasaOS-UI":"` + shaA + `","CasaOS-AppManagement":"` + shaA + `"}}`,
	} {
		path := m.path(name)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(data), 0755); err != nil {
			t.Fatal(err)
		}
	}
	return m
}
func idle(context.Context, string, ...string) (string, error) {
	return "LoadState=loaded\nActiveState=inactive\nResult=success", nil
}
func TestCheckDetectsCommitWithoutVersionChange(t *testing.T) {
	calls := 0
	m := setup(t, func(ctx context.Context, name string, args ...string) (string, error) {
		if name == "git" {
			calls++
			if !strings.HasPrefix(args[1], "https://github.com/filippov-au/") || args[2] != "refs/heads/main" {
				t.Fatal(args)
			}
			sha := shaA
			if strings.Contains(args[1], "CasaOS-UI") {
				sha = shaB
			}
			return sha + "\trefs/heads/main", nil
		}
		return idle(ctx, name, args...)
	})
	s, err := m.Check(context.Background())
	if err != nil || !s.Enabled || !s.Available || s.CheckError != "" {
		t.Fatalf("%+v %v", s, err)
	}
	if s.Repositories[0].Available || !s.Repositories[2].Available {
		t.Fatalf("%+v", s.Repositories)
	}
	_, _ = m.Check(context.Background())
	if calls != 3 {
		t.Fatalf("cache missed: %d", calls)
	}
}
func TestCheckFailureDoesNotClaimLatestOrKeepStaleCommit(t *testing.T) {
	m := setup(t, func(ctx context.Context, name string, args ...string) (string, error) {
		if name == "git" {
			return "", errors.New("offline")
		}
		return idle(ctx, name, args...)
	})
	m.latest = map[string]string{"CasaOS": shaB}
	s, err := m.Check(context.Background())
	if err != nil || s.CheckError == "" || s.Available || s.Repositories[0].Latest != "" {
		t.Fatalf("%+v %v", s, err)
	}
}
func TestCheckRejectsMalformedRemote(t *testing.T) {
	m := setup(t, func(ctx context.Context, name string, args ...string) (string, error) {
		if name == "git" {
			return shaB + "\trefs/heads/other", nil
		}
		return idle(ctx, name, args...)
	})
	s, _ := m.Check(context.Background())
	if s.CheckError == "" {
		t.Fatal("accepted a different branch")
	}
}
func TestStartUsesFixedUnitAndClearsOldCompletionLog(t *testing.T) {
	var start []string
	m := setup(t, func(ctx context.Context, name string, args ...string) (string, error) {
		if name == "systemctl" && args[0] == "restart" {
			start = args
			return "", nil
		}
		return idle(ctx, name, args...)
	})
	log := m.path("var/log/casaos/upgrade.log")
	_ = os.MkdirAll(filepath.Dir(log), 0755)
	_ = os.WriteFile(log, []byte("CasaOS upgrade successfully"), 0644)
	if err := m.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if strings.Join(start, " ") != "restart --no-block "+Unit {
		t.Fatal(start)
	}
	data, _ := os.ReadFile(log)
	if strings.Contains(string(data), "successfully") {
		t.Fatal("stale success marker")
	}
}
func TestStartDoesNotInterruptRunningUpdate(t *testing.T) {
	m := setup(t, func(ctx context.Context, name string, args ...string) (string, error) {
		if args[0] != "show" {
			t.Fatal("started duplicate update")
		}
		return "LoadState=loaded\nActiveState=activating\nSubState=start", nil
	})
	if err := m.Start(context.Background()); !errors.Is(err, ErrBusy) {
		t.Fatal(err)
	}
}
func TestStartFailsClosedWithoutInstalledMarker(t *testing.T) {
	m := setup(t, func(context.Context, string, ...string) (string, error) {
		t.Fatal("command on incomplete install")
		return "", nil
	})
	_ = os.Remove(m.path("var/lib/casaos/fork-updates/installed.json"))
	if err := m.Start(context.Background()); err == nil {
		t.Fatal("started incomplete installation")
	}
	if !m.Configured() {
		t.Fatal("must not fall back to official updates")
	}
}
func TestCheckComparesInstalledCommitsAgainAfterRollback(t *testing.T) {
	m := setup(t, idle)
	m.latest = map[string]string{"CasaOS": shaB}
	m.checkedAt = time.Now()
	marker := m.path("var/lib/casaos/fork-updates/installed.json")
	record, _ := json.Marshal(map[string]interface{}{"commits": map[string]string{"CasaOS": shaB}})
	_ = os.WriteFile(marker, record, 0644)
	s, _ := m.Check(context.Background())
	if s.Available {
		t.Fatal("installed snapshot ignored")
	}
	record, _ = json.Marshal(map[string]interface{}{"commits": map[string]string{"CasaOS": shaA}})
	_ = os.WriteFile(marker, record, 0644)
	s, _ = m.Check(context.Background())
	if !s.Available {
		t.Fatal("rollback snapshot ignored")
	}
}

func TestStartRejectsTerminalUpdateWithoutClearingLog(t *testing.T) {
	m := setup(t, idle)
	lock, err := os.OpenFile(m.path("var/lib/casaos/fork-updates/lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	if err := m.Start(context.Background()); !errors.Is(err, ErrTerminalBusy) {
		t.Fatal(err)
	}
	if _, err := os.Stat(m.path("var/log/casaos/upgrade.log")); !os.IsNotExist(err) {
		t.Fatal("log changed")
	}
}
func TestStartRejectsRapidDoubleClickBeforeSystemdReportsJob(t *testing.T) {
	m := setup(t, idle)
	if err := m.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := m.Start(context.Background()); !errors.Is(err, ErrBusy) {
		t.Fatal(err)
	}
}
