#!/usr/bin/env bash
#
# Provision an Ubuntu/Debian host to run a logged-in Chrome around the clock,
# reachable over the Chrome DevTools Protocol on loopback only.
#
# Idempotent: safe to re-run after a change or a failed attempt.
#
#   bash deploy/ubuntu/install.sh              # fonts, Xvfb, VNC, Chrome, units
#   bash deploy/ubuntu/install.sh --no-vnc     # skip VNC (no hand-login needed)
#   bash deploy/ubuntu/install.sh --vnc-insecure
#
set -euo pipefail

WITH_VNC=1
VNC_INSECURE=0
START_URL="about:blank"
SCREEN="1920x1080x24"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-vnc)        WITH_VNC=0 ;;
    --vnc-insecure)  VNC_INSECURE=1 ;;
    --start-url)     START_URL="$2"; shift ;;
    --screen)        SCREEN="$2"; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!   %s\033[0m\n' "$*"; }
die()  { printf '\033[31mx   %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && warn "Running as root. Chrome's sandbox will not start as root, which
    removes a real security boundary around a browser holding live logins.
    Consider re-running this as an unprivileged user."

command -v apt-get >/dev/null || die "This script targets Debian/Ubuntu (apt-get not found)."

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

# ---------------------------------------------------------------------------
say "Installing packages"
# x11-utils supplies xdpyinfo, which the window-manager unit uses to wait for
# the display instead of sleeping a fixed number of seconds.
# The fonts matter more than they look: without them Chrome still renders, so
# nothing errors -- the text just comes out as empty boxes in every screenshot.
PACKAGES=(
  xvfb
  openbox
  x11-utils
  fontconfig
  fonts-liberation
  fonts-noto-core
  fonts-noto-cjk
  curl
  wget
  ca-certificates
  python3
  python3-venv
)
[ "$WITH_VNC" -eq 1 ] && PACKAGES+=(x11vnc)

$SUDO apt-get update -qq
DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends "${PACKAGES[@]}"

# ---------------------------------------------------------------------------
say "Ensuring Chrome is present"
if command -v google-chrome-stable >/dev/null || command -v google-chrome >/dev/null; then
  echo "    $(command -v google-chrome-stable || command -v google-chrome)"
elif command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
  echo "    $(command -v chromium || command -v chromium-browser)"
  warn "Using Chromium. It works, but snap-packaged Chromium is confined and often
    cannot write to a --user-data-dir outside \$HOME. Prefer the Google build."
else
  echo "    Installing google-chrome-stable"
  TMP_DEB="$(mktemp -d)/chrome.deb"
  wget -qO "$TMP_DEB" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "$TMP_DEB"
  rm -f "$TMP_DEB"
fi

# ---------------------------------------------------------------------------
say "Installing tabpilot"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if command -v uv >/dev/null; then
  uv tool install --force "$REPO_ROOT"
  TABPILOT="$HOME/.local/bin/tabpilot"
elif command -v pipx >/dev/null; then
  pipx install --force "$REPO_ROOT"
  TABPILOT="$HOME/.local/bin/tabpilot"
else
  echo "    Neither uv nor pipx found; using a dedicated venv."
  VENV="$HOME/.local/share/tabpilot-venv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q "$REPO_ROOT"
  TABPILOT="$VENV/bin/tabpilot"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$TABPILOT" "$HOME/.local/bin/tabpilot"
  TABPILOT="$HOME/.local/bin/tabpilot"
fi
echo "    $TABPILOT"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "\$HOME/.local/bin is not on your PATH. Add it:
    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && . ~/.bashrc" ;;
esac

# ---------------------------------------------------------------------------
if [ "$WITH_VNC" -eq 1 ] && [ "$VNC_INSECURE" -eq 0 ] && [ ! -f "$HOME/.vnc/passwd" ]; then
  say "Setting a VNC password"
  echo "    VNC gives full control of a browser holding your live logins, so it is"
  echo "    bound to loopback and password-protected. Choose a password now."
  mkdir -p "$HOME/.vnc"
  x11vnc -storepasswd "$HOME/.vnc/passwd"
fi

# ---------------------------------------------------------------------------
say "Installing systemd units"
INSTALL_ARGS=(--screen "$SCREEN" --start-url "$START_URL")
[ "$VNC_INSECURE" -eq 1 ] && INSTALL_ARGS+=(--vnc-insecure)
"$TABPILOT" install-stack "${INSTALL_ARGS[@]}"

say "Starting the stack"
"$TABPILOT" up

say "Checking the result"
"$TABPILOT" doctor || true

cat <<EOF

$(printf '\033[1m')Done.$(printf '\033[0m')

Sign in to your sites once, inside this Chrome profile:

  On your laptop, tunnel the VNC port (it is not exposed to the network):
    ssh -N -L 5901:127.0.0.1:5901 $(whoami)@$(hostname)
  then point a VNC client at localhost:5901 and log in to your sites.
  Close the viewer when done -- Chrome keeps running.

Point an MCP client at this browser, without opening any port:

  "tabpilot": {
    "command": "ssh",
    "args": ["$(whoami)@$(hostname)", "TABPILOT_REMOTE=1 ~/.local/bin/tabpilot serve"]
  }

Day to day:
  tabpilot status          what is running
  tabpilot logs chrome -f  follow Chrome's log
  tabpilot up --restart    restart after changing ~/.config/tabpilot/stack.env
  tabpilot doctor          diagnose anything that broke
EOF
