// Package sourceupdate exposes the fixed fork update channel to the authenticated UI.
package sourceupdate

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
)

const Unit = "casaos-source-update.service"

var repositories = []string{"CasaOS", "CasaOS-AppManagement", "CasaOS-UI"}
var shaPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
var ErrBusy = errors.New("a source update is already running")
var ErrTerminalBusy = errors.New("a terminal source update is running; wait for it to finish")

type Repository struct {
	Name      string `json:"name"`
	Installed string `json:"installed"`
	Latest    string `json:"latest"`
	Available bool   `json:"available"`
}
type Status struct {
	Enabled      bool         `json:"enabled"`
	Branch       string       `json:"branch"`
	Owner        string       `json:"owner"`
	Repositories []Repository `json:"repositories"`
	Available    bool         `json:"available"`
	CheckedAt    string       `json:"checked_at"`
	CheckError   string       `json:"check_error"`
	Operation    string       `json:"operation"`
}
type Runner func(context.Context, string, ...string) (string, error)
type Manager struct {
	mu         sync.Mutex
	Root       string
	Run        Runner
	latest     map[string]string
	checkedAt  time.Time
	checkError string
	startedAt  time.Time
}

func Command(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	data, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(data)), err
}
func New(root string, run Runner) *Manager { return &Manager{Root: root, Run: run} }
func (m *Manager) path(path string) string { return filepath.Join(m.Root, path) }
func (m *Manager) installed() (map[string]string, error) {
	data, err := os.ReadFile(m.path("var/lib/casaos/fork-updates/installed.json"))
	if err != nil {
		return nil, err
	}
	var record struct {
		Commits map[string]string `json:"commits"`
	}
	if err = json.Unmarshal(data, &record); err != nil {
		return nil, err
	}
	if len(record.Commits) == 0 {
		return nil, errors.New("installed commits are missing")
	}
	return record.Commits, nil
}
func (m *Manager) Configured() bool {
	_, err := os.Stat(m.path("usr/local/sbin/casaos-source-update"))
	return err == nil
}
func (m *Manager) Enabled() bool {
	_, err := m.installed()
	info, scriptErr := os.Stat(m.path("usr/local/sbin/casaos-source-update"))
	return err == nil && scriptErr == nil && info.Mode().IsRegular() && info.Mode().Perm()&0111 != 0
}
func properties(text string) map[string]string {
	result := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		fields := strings.SplitN(line, "=", 2)
		if len(fields) == 2 {
			result[fields[0]] = fields[1]
		}
	}
	return result
}
func (m *Manager) operation(ctx context.Context) (string, error) {
	text, err := m.Run(ctx, "systemctl", "show", Unit, "--property=LoadState,ActiveState,SubState,Result")
	if err != nil {
		return "unknown", errors.New("cannot read source updater service status")
	}
	p := properties(text)
	if p["LoadState"] == "not-found" {
		return "unavailable", nil
	}
	switch p["ActiveState"] {
	case "activating", "deactivating", "reloading":
		return "running", nil
	case "active":
		if p["SubState"] == "exited" {
			return "succeeded", nil
		}
		return "running", nil
	case "failed":
		return "failed", nil
	case "inactive":
		if p["Result"] != "" && p["Result"] != "success" {
			return "failed", nil
		}
		return "idle", nil
	default:
		return "unknown", errors.New("unexpected source updater service status")
	}
}
func (m *Manager) status(ctx context.Context) (Status, error) {
	s := Status{Enabled: m.Enabled(), Branch: "main", Owner: "filippov-au", Repositories: []Repository{}, Operation: "idle", CheckError: m.checkError}
	installed, _ := m.installed()
	for _, name := range repositories {
		latest := m.latest[name]
		r := Repository{Name: name, Installed: installed[name], Latest: latest, Available: latest != "" && latest != installed[name]}
		s.Repositories = append(s.Repositories, r)
		s.Available = s.Available || r.Available
	}
	if !m.checkedAt.IsZero() {
		s.CheckedAt = m.checkedAt.UTC().Format(time.RFC3339)
	}
	if s.Enabled {
		operation, err := m.operation(ctx)
		if err != nil {
			return s, err
		}
		s.Operation = operation
		if operation == "unavailable" {
			s.Enabled = false
		}

	}
	return s, nil
}
func (m *Manager) Status(ctx context.Context) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.status(ctx)
}

// Check caches network requests, including failures, to avoid hammering GitHub.
// All three repositories must resolve before a new snapshot is published.
func (m *Manager) Check(ctx context.Context) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if time.Since(m.checkedAt) >= 30*time.Second {
		latest := map[string]string{}
		var failures []string
		for _, name := range repositories {
			checkCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
			output, err := m.Run(checkCtx, "git", "ls-remote", "https://github.com/filippov-au/"+name+".git", "refs/heads/main")
			cancel()
			fields := strings.Fields(output)
			if err != nil || len(fields) != 2 || !shaPattern.MatchString(fields[0]) || fields[1] != "refs/heads/main" {
				failures = append(failures, name)
			} else {
				latest[name] = fields[0]
			}
		}
		m.checkedAt = time.Now()
		m.checkError = ""
		if len(failures) > 0 {
			m.checkError = "Could not check GitHub: " + strings.Join(failures, ", ")
			m.latest = nil
		} else {
			m.latest = latest
		}
	}
	return m.status(ctx)
}
func (m *Manager) Start(ctx context.Context) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.Enabled() {
		return errors.New("install the source updater once from the terminal first")
	}
	operation, err := m.operation(ctx)
	if err != nil {
		return err
	}
	if operation == "running" || time.Since(m.startedAt) < 5*time.Second {
		return ErrBusy
	}
	if operation == "unavailable" {
		return errors.New("source updater service is not installed")
	}
	// The CLI updater uses the same lock. Do not start a second build or
	// truncate its log while a terminal-initiated update is running.
	lock, lockErr := os.OpenFile(m.path("var/lib/casaos/fork-updates/lock"), os.O_CREATE|os.O_RDWR, 0600)
	if lockErr != nil {
		return lockErr
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return ErrTerminalBusy
		}
		return err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	// Clear the log before accepting a new job so the existing update dialog
	// cannot mistake the previous job's success marker for this one's result.
	logPath := m.path("var/log/casaos/upgrade.log")
	if err := os.MkdirAll(filepath.Dir(logPath), 0755); err != nil {
		return err
	}
	if err := os.WriteFile(logPath, []byte("Preparing source update…\n"), 0644); err != nil {
		return err
	}
	// A fixed systemd unit survives the restart of CasaOS itself. No command, URL,
	// branch, or shell text is accepted from the browser.
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		return err
	}
	if _, err = m.Run(ctx, "systemctl", "restart", "--no-block", Unit); err != nil {
		return fmt.Errorf("could not start source updater: %w", err)
	}
	m.checkedAt = time.Time{}
	m.startedAt = time.Now()
	return nil
}
