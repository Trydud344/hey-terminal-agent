#!/usr/bin/env bash

# Strict mode: exit immediately on errors, unset variables, or failed pipes
set -euo pipefail

# ────────────────────────────────────────────────────────────
#  hey-agent Installer
#  A robust, modern installer utilizing virtual environments.
# ────────────────────────────────────────────────────────────

# ---- Terminal Colors ----
RESET="\033[0m"
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
CYAN="\033[36m"

info()  { printf "${GREEN}==>${RESET}${BOLD} %s${RESET}\n" "$*"; }
warn()  { printf "${YELLOW}==>${RESET}${BOLD} %s${RESET}\n" "$*"; }
err()   { printf "${RED}==>${RESET}${BOLD} %s${RESET}\n" "$*" >&2; }
header(){ printf "\n${CYAN}━━━ %s ━━━${RESET}\n" "$*"; }

# ---- Configuration & Defaults ----
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local}"
CLONE_REPO=""
SKIP_PREREQS=0
TMP_CLONE_DIR=""

# Determine the directory of this script
SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE" 2>/dev/null || echo ".")" && pwd)"

# Trap exits to cleanly delete temporary clone directories
cleanup() {
    if [[ -n "${TMP_CLONE_DIR:-}" && -d "$TMP_CLONE_DIR" ]]; then
        info "Cleaning up temporary clone directory..."
        rm -rf "$TMP_CLONE_DIR"
    fi
}
trap cleanup EXIT INT TERM

# ---- Usage Instructions ----
usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --install-dir DIR   Base directory for installation (default: \$HOME/.local)
                      - Binaries will go to DIR/bin
                      - Isolation venv will go to DIR/share/hey-terminal-agent/venv
  --clone URL         Clone the repository from URL first, then install
  --skip-prereqs      Skip prerequisite validation checks
  -h, --help          Show this help information

Examples:
  ./install.sh
  ./install.sh --install-dir \$HOME/.custom-local
  ./install.sh --clone https://github.com/youruser/hey-agent.git
EOF
    exit 0
}

# Parse command line flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)
            if [[ -z "${2:-}" ]]; then err "Missing argument for --install-dir"; exit 1; fi
            INSTALL_DIR="$2"; shift 2 ;;
        --clone)
            if [[ -z "${2:-}" ]]; then err "Missing argument for --clone"; exit 1; fi
            CLONE_REPO="$2"; shift 2 ;;
        --skip-prereqs)
            SKIP_PREREQS=1; shift ;;
        -h|--help)
            usage ;;
        *)
            err "Unknown option: $1"; usage ;;
    esac
done

# Normalize installation directories
# Resolve ~ and relative directories safely without GNU realpath requirement
case "$INSTALL_DIR" in
    "~"/*|"~")
        INSTALL_DIR="$HOME/${INSTALL_DIR#"~/"}" ;;
esac
mkdir -p "$INSTALL_DIR"
INSTALL_DIR="$(cd -- "$INSTALL_DIR" && pwd -P)"
BIN_DIR="$INSTALL_DIR/bin"
SHARE_DIR="$INSTALL_DIR/share/hey-terminal-agent"
VENV_DIR="$SHARE_DIR/venv"

echo ""
echo "${BOLD}hey-agent — terminal AI assistant installer${RESET}"
echo ""

# ────────────────────────────────────────────────────────────
#  Step 1 — Platform Detection
# ────────────────────────────────────────────────────────────
header "Platform detection"

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux)
        OS_PRETTY="Linux"
        if [[ -f /etc/os-release ]]; then
            # Source os-release in a subshell to avoid polluting env
            OS_PRETTY="$( (source /etc/os-release && echo "${PRETTY_NAME:-Linux}") )"
        fi
        ;;
    Darwin)
        OS_PRETTY="macOS $(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
        # Fast non-blocking check for Xcode Command Line Tools
        if ! xcode-select -p >/dev/null 2>&1; then
            warn "Xcode Command Line Tools are missing."
            info "Running 'xcode-select --install'. Please approve the UI prompt if it appears."
            xcode-select --install 2>/dev/null || true
            err "Please wait for Xcode CLI tools to install, then re-run this script."
            exit 1
        fi
        ;;
    *)
        err "Unsupported operating system: $OS"
        exit 1
        ;;
esac

echo "  OS:           $OS_PRETTY"
echo "  Architecture: $ARCH"
echo "  Shell:        ${SHELL:-unknown}"
echo "  Install path: $INSTALL_DIR"

# ────────────────────────────────────────────────────────────
#  Step 2 — Verify Dependencies (No Silent Sudo)
# ────────────────────────────────────────────────────────────
header "Prerequisite verification"

# Helper: search for valid Python binary (needs to be >= 3.12 and support venv)
find_python_candidate() {
    for cmd in python3 python python3.13 python3.12; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >/dev/null 2>&1; then
                # Ensure the standard venv library is functional (e.g. not stripped out like on Debian)
                if "$cmd" -c "import venv" >/dev/null 2>&1; then
                    echo "$(command -v "$cmd")"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

PYTHON=""
if [[ $SKIP_PREREQS -eq 0 ]]; then
    PYTHON="$(find_python_candidate || true)"

    if [[ -z "$PYTHON" ]]; then
        err "Python >= 3.12 (with the 'venv' module) is required but was not found."
        echo ""
        echo "Please install it using your system package manager:"
        echo "  - Debian/Ubuntu:  sudo apt update && sudo apt install python3 python3-venv git"
        echo "  - Fedora:         sudo dnf install python3 git"
        echo "  - Arch Linux:     sudo pacman -S python git"
        echo "  - Alpine Linux:   sudo apk add python3 py3-virtualenv git"
        echo "  - macOS:          brew install python@3.12 git"
        echo ""
        exit 1
    fi

    if [[ -n "$CLONE_REPO" ]] && ! command -v git >/dev/null 2>&1; then
        err "Git is required to clone the repository but was not found."
        exit 1
    fi
else
    # If skipping checks, fallback to standard executable search
    PYTHON="$(command -v python3 || command -v python || true)"
    if [[ -z "$PYTHON" ]]; then
        err "Could not find a Python executable. Please install Python."
        exit 1
    fi
fi

echo "  Python:       $("$PYTHON" -version 2>&1 || "$PYTHON" -V 2>&1) ($PYTHON)"
echo "  Requirements: Python >= 3.12 (OK)"

# ────────────────────────────────────────────────────────────
#  Step 3 — Retrieve Source Code
# ────────────────────────────────────────────────────────────
header "Source code preparation"

# Verify if piped installation was attempted without clone parameter
PIPED_INSTALL=false
if [[ ! -t 0 && -z "$CLONE_REPO" ]]; then
    # Standard terminal check. If stdin isn't a terminal and clone is empty,
    # then they likely piped "curl ... | bash" without providing repo details.
    PIPED_INSTALL=true
fi

if [[ -n "$CLONE_REPO" ]]; then
    TMP_CLONE_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t 'hey-agent')"
    info "Cloning $CLONE_REPO to temporary directory..."
    git clone --depth=1 "$CLONE_REPO" "$TMP_CLONE_DIR"
    cd "$TMP_CLONE_DIR"
elif $PIPED_INSTALL; then
    err "This script was piped via stdin (curl | bash) and cannot locate the local repo."
    err "Please run the installer with the --clone flag:"
    err ""
    err "  curl -fsSL <install-url> | bash -s -- --clone https://github.com/youruser/hey-agent.git"
    err ""
    exit 1
else
    # Use local directory where script resides
    cd "$SCRIPT_DIR"
    if [[ ! -f pyproject.toml ]]; then
        # Try parent directory just in case it's run from inside a scripts subfolder
        if [[ -f ../pyproject.toml ]]; then
            cd ..
        else
            err "Could not locate 'pyproject.toml' in $(pwd) or parent directory."
            err "Please run this installer script from inside the repository cloned directory."
            exit 1
        fi
    fi
    info "Using local source tree: $(pwd)"
fi

# Ensure pyproject.toml is present before attempting installation
if [[ ! -f pyproject.toml ]]; then
    err "Missing 'pyproject.toml'. Cannot build/install package."
    exit 1
fi

# Clean extraction of package name
PKG_NAME="$(grep -E '^name\s*=' pyproject.toml | head -1 | sed 's/^name\s*=\s*"\(.*\)".*/\1/' 2>/dev/null || echo "hey-agent")"
echo "  Project Name: $PKG_NAME"

# ────────────────────────────────────────────────────────────
#  Step 4 — Build and Install (Isolated Venv)
# ────────────────────────────────────────────────────────────
header "Building in isolated environment"

info "Creating virtual environment in $VENV_DIR ..."
mkdir -p "$SHARE_DIR"
"$PYTHON" -m venv "$VENV_DIR"

info "Updating packaging tools inside virtual environment..."
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel --quiet

info "Installing $PKG_NAME inside virtual environment..."
"$VENV_DIR/bin/pip" install . --quiet

# Verify installation succeeded and wrapper executable exists
VENV_BIN="$VENV_DIR/bin/hey"
if [[ ! -f "$VENV_BIN" ]]; then
    # Fallback search if the executable name differs from 'hey'
    VENV_BIN="$(find "$VENV_DIR/bin" -type f -maxdepth 1 ! -name "pip*" ! -name "python*" ! -name "activate*" -print -quit || true)"
fi

if [[ -z "$VENV_BIN" || ! -x "$VENV_BIN" ]]; then
    err "Installation completed but the executable binary was not created in the venv."
    exit 1
fi

info "Linking binary wrapper..."
mkdir -p "$BIN_DIR"
# Creating an absolute path symlink allows the Python entrypoint shebang context to auto-evaluate
ln -sf "$VENV_BIN" "$BIN_DIR/hey"

if [[ -x "$BIN_DIR/hey" ]]; then
    info "Successfully installed 'hey' to $BIN_DIR/hey"
else
    err "Failed to create executable link at $BIN_DIR/hey"
    exit 1
fi

# ────────────────────────────────────────────────────────────
#  Step 5 — Shell PATH Setup
# ────────────────────────────────────────────────────────────
header "Shell environment path check"

SHELL_RC=""
SHELL_NAME="$(basename "${SHELL:-bash}")"
PATH_LINE="export PATH=\"\$PATH:$BIN_DIR\""

case "$SHELL_NAME" in
    bash)
        # Check profiles in order of evaluation preference
        if [[ -f "$HOME/.bashrc" ]]; then SHELL_RC="$HOME/.bashrc"
        elif [[ -f "$HOME/.bash_profile" ]]; then SHELL_RC="$HOME/.bash_profile"
        else SHELL_RC="$HOME/.profile"
        fi
        ;;
    zsh)
        SHELL_RC="$HOME/.zshrc"
        ;;
    fish)
        SHELL_RC="$HOME/.config/fish/config.fish"
        PATH_LINE="set -gx PATH \$PATH $BIN_DIR"
        ;;
    *)
        # Keep empty for unknown shells
        ;;
esac

# Check if path is already available
if echo ":$PATH:" | grep -qF ":$BIN_DIR:"; then
    echo "  $BIN_DIR is already configured in your active PATH."
else
    echo "  $BIN_DIR is not currently found in your active PATH."
    echo ""
    
    SHOULD_WRITE="n"
    # Prompt user dynamically if running in an interactive terminal (TTY)
    if [[ -t 0 && -n "$SHELL_RC" ]]; then
        printf "  Would you like to append $BIN_DIR to your PATH in $SHELL_RC? [Y/n]: "
        read -r response
        if [[ "$response" =~ ^([yY][eE][sS]|[yY]|"")$ ]]; then
            SHOULD_WRITE="y"
        fi
    fi

    if [[ "$SHOULD_WRITE" == "y" && -n "$SHELL_RC" ]]; then
        mkdir -p "$(dirname "$SHELL_RC")"
        {
            echo ""
            echo "# >>> hey-agent PATH setup >>>"
            echo "$PATH_LINE"
            echo "# <<< hey-agent PATH setup <<<"
        } >> "$SHELL_RC"
        info "Configuration appended to $SHELL_RC. Run 'source $SHELL_RC' or restart shell to apply."
    else
        warn "Manual path configuration required."
        echo "  Add the following line to your shell profile configurations manually:"
        echo ""
        echo "    $PATH_LINE"
        echo ""
    fi
fi

# ────────────────────────────────────────────────────────────
#  Step 6 — Installation Summary
# ────────────────────────────────────────────────────────────
header "Installation complete!"

echo "  ${GREEN}✓${RESET} hey-agent has been cleanly installed."
echo ""
echo "  Binary location: $BIN_DIR/hey"
echo "  Venv location:   $VENV_DIR"
echo "  Config folder:   ~/.hey/config"
echo ""
echo "  Quick start options:"
echo "    1. Configure your endpoint API details:"
echo "       hey --set-url https://api.openai.com/v1"
echo "       hey --set-key sk-...your-api-key..."
echo ""
echo "    2. Run system checks:"
echo "       hey --refresh-system-info"
echo ""
echo "    3. Interact directly:"
echo "       hey explain how to inspect disk space usage on this server"
echo ""
echo "  ${BOLD}Enjoy using hey!${RESET}"
echo ""
