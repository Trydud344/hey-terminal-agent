#!/usr/bin/env bash
set -eo pipefail
# Handle unbound BASH_SOURCE when piping via curl | bash
if [[ -z "${BASH_SOURCE:-}" || "${#BASH_SOURCE[@]}" -eq 0 ]]; then
    BASH_SOURCE=("$0")
fi
shopt -s expand_aliases 2>/dev/null || true
# Re-enable -u after BASH_SOURCE guard
set -u

# ────────────────────────────────────────────────────────────
#  hey-agent install script
#  Detects platform, installs prerequisites, builds, and
#  installs the 'hey' command into the user's PATH.
# ────────────────────────────────────────────────────────────

# ---- Colors ----
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

# ---- Detect if running from a local file or piped via curl ----
# When piped via curl | bash, SCRIPT_DIR will not point to the repo.
SCRIPT_SOURCE="$0"
if [[ "$SCRIPT_SOURCE" == "bash" || "$SCRIPT_SOURCE" == "/dev/stdin" || "$SCRIPT_SOURCE" == "sh" || "$SCRIPT_SOURCE" == "zsh" || ! -f "$SCRIPT_SOURCE" ]]; then
    PIPED_INSTALL=true
else
    PIPED_INSTALL=false
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE" 2>/dev/null || echo ".")" 2>/dev/null && pwd 2>/dev/null || echo "")"
REPO_DIR="$SCRIPT_DIR"

# ---- Parse flags ----
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local}"
SKIP_PREREQS=0
CLONE_REPO=""
CLONE_DEST=""

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --install-dir DIR   Install binaries to DIR/bin  (default: \$HOME/.local)
  --clone URL         Clone the repository from URL first, then install
  --skip-prereqs      Skip OS package manager prerequisite installation
  -h, --help          Show this help

Examples:
  ./install.sh
  ./install.sh --install-dir \$HOME/.local
  ./install.sh --clone https://github.com/youruser/hey-agent.git
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)
            INSTALL_DIR="$2"; shift 2 ;;
        --clone)
            CLONE_REPO="$2"; shift 2 ;;
        --skip-prereqs)
            SKIP_PREREQS=1; shift ;;
        -h|--help)
            usage ;;
        *)
            err "Unknown option: $1"; usage ;;
    esac
done

# Normalise install dir
# Portable realpath: resolve ~ and make absolute without requiring GNU realpath
case "$INSTALL_DIR" in
    "~"/*|"~")
        INSTALL_DIR="$HOME/${INSTALL_DIR#"/"~}" ;;
esac
INSTALL_DIR="$(cd -- "$INSTALL_DIR" 2>/dev/null && pwd -P 2>/dev/null || echo "$INSTALL_DIR")"
BIN_DIR="$INSTALL_DIR/bin"
TARGET_BIN_DIR="$BIN_DIR"

# ────────────────────────────────────────────────────────────
#  Step 0 — Banner
# ────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}hey-agent — terminal AI assistant installer${RESET}"
echo ""

# ────────────────────────────────────────────────────────────
#  Step 1 — Platform detection
# ────────────────────────────────────────────────────────────
header "Platform detection"

OS="$(uname -s)"
ARCH="$(uname -m)"
KERNEL="$(uname -r)"

case "$OS" in
    Linux)
        OS_NAME="Linux"
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            OS_PRETTY="${PRETTY_NAME:-$NAME $VERSION_ID}"
        else
            OS_PRETTY="Linux"
        fi
        # Determine package manager
        if   command -v apt-get >/dev/null 2>&1; then PKG_MANAGER="apt-get"
        elif command -v dnf     >/dev/null 2>&1; then PKG_MANAGER="dnf"
        elif command -v yum     >/dev/null 2>&1; then PKG_MANAGER="yum"
        elif command -v pacman  >/dev/null 2>&1; then PKG_MANAGER="pacman"
        elif command -v zypper  >/dev/null 2>&1; then PKG_MANAGER="zypper"
        elif command -v apk     >/dev/null 2>&1; then PKG_MANAGER="apk"
        else PKG_MANAGER="unknown"
        fi
        ;;
    Darwin)
        OS_NAME="macOS"
        OS_PRETTY="macOS $(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
        PKG_MANAGER="brew"
        # Ensure Xcode CLI tools are present on macOS
        if ! xcode-select -p >/dev/null 2>&1; then
            info "Installing Xcode Command Line Tools (this may take a while)..."
            xcode-select --install 2>/dev/null || true
            until xcode-select -p >/dev/null 2>&1; do
                echo "  Waiting for Xcode CLI tools installation to complete..."
                echo "  If a dialog appeared, please click 'Install' and wait."
                sleep 5
            done
            info "Xcode Command Line Tools installed."
        fi
        ;;
    *)
        err "Unsupported operating system: $OS"
        err "hey-agent currently supports Linux and macOS."
        exit 1
        ;;
esac

case "$ARCH" in
    x86_64|amd64) ARCH_NICE="x86_64" ;;
    aarch64|arm64) ARCH_NICE="ARM64"  ;;
    *)
        err "Unsupported architecture: $ARCH"
        err "hey-agent supports x86_64 and ARM64."
        exit 1
        ;;
esac

echo "  OS:           $OS_PRETTY"
echo "  Kernel:       $KERNEL"
echo "  Architecture: $ARCH_NICE"
echo "  Machine arch: $ARCH"
echo "  Shell:        ${SHELL:-unknown}"
echo "  Package mgr:  $PKG_MANAGER"
echo "  Bin dir:      $BIN_DIR"

# ────────────────────────────────────────────────────────────
#  Step 2 — Find Python >= 3.12 (with pip)
# ────────────────────────────────────────────────────────────
header "Python version check"

# Returns the major.minor version string for a given python binary.
py_short_version() {
    "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null
}

# Returns true if the given python binary has a working pip module.
py_has_pip() {
    "$1" -m pip --version >/dev/null 2>&1
}

PYTHON=""

# Try candidates in order of preference. We need both Python >= 3.12 AND pip.
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver="$(py_short_version "$candidate")" || true
        major="${ver%.*}"; minor="${ver#*.}"
        if [[ -n "$major" && "$major" -ge 3 && "$minor" -ge 12 ]] 2>/dev/null; then
            PYTHON="$candidate"
            if py_has_pip "$PYTHON"; then
                break  # best case: has both version and pip
            fi
            # Keep looking — maybe a later candidate has pip
        fi
    fi
done

# If we found a python with the right version but no pip, try to bootstrap pip.
if [[ -n "$PYTHON" ]] && ! py_has_pip "$PYTHON"; then
    warn "$PYTHON has no pip module. Attempting to install pip..."
    "$PYTHON" -c "import ensurepip; ensurepip.bootstrap()" 2>/dev/null && {
        info "pip installed via ensurepip."
    } || {
        warn "ensurepip failed. Trying get-pip.py..."
        if command -v curl >/dev/null 2>&1; then
            curl -sS https://bootstrap.pypa.io/get-pip.py | "$PYTHON" 2>&1 || true
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- https://bootstrap.pypa.io/get-pip.py | "$PYTHON" 2>&1 || true
        fi
    }
    if ! py_has_pip "$PYTHON"; then
        # On Debian/Ubuntu, try installing pip via apt-get directly
        if [[ "$PKG_MANAGER" == "apt-get" ]] && command -v apt-get >/dev/null 2>&1; then
            warn "get-pip.py failed. Trying to install python3-pip via apt-get..."
            sudo apt-get install -y -qq python3-pip python3-venv 2>/dev/null || true
            if py_has_pip "$PYTHON"; then
                info "python3-pip installed via apt-get."
            fi
        fi
    fi
    if ! py_has_pip "$PYTHON"; then
        # Fall back to system pip if available
        if command -v pip3 >/dev/null 2>&1; then
            warn "Using pip3 directly instead."
        else
            err "Could not get pip working for $PYTHON."
            err "Install pip manually or use a system Python with pip."
            exit 1
        fi
    fi
fi

# If we still don't have a valid python, try to install one.
if [[ -z "$PYTHON" ]] || ! py_has_pip "$PYTHON"; then
    if [[ -z "$PYTHON" ]]; then
        warn "Python >= 3.12 is required but not found."
    else
        warn "$PYTHON found but pip is not available and could not be installed."
    fi

    if [[ $SKIP_PREREQS -eq 1 ]]; then
        err "Python >= 3.12 with pip missing. Install it manually or re-run without --skip-prereqs."
        exit 1
    fi

    info "Attempting to install Python 3.12+ via $PKG_MANAGER..."

    case "$PKG_MANAGER" in
        apt-get)
            sudo apt-get update -qq
            sudo apt-get install -y -qq python3 python3-pip python3-venv 2>/dev/null ||
            sudo apt-get install -y -qq python3.12 python3.12-pip python3.12-venv 2>/dev/null ||
            sudo apt-get install -y -qq python3.13 python3.13-pip python3.13-venv 2>/dev/null ||
            { err "Could not install Python 3.12+. Do it manually."; exit 1; }
            PYTHON="python3"
            ;;
        dnf|yum)
            sudo "$PKG_MANAGER" install -y python3 python3-pip 2>/dev/null ||
            { err "Could not install Python. Do it manually."; exit 1; }
            PYTHON="python3"
            ;;
        pacman)
            sudo pacman -S --noconfirm python python-pip 2>/dev/null ||
            { err "Could not install Python. Do it manually."; exit 1; }
            PYTHON="python3"
            ;;
        zypper)
            sudo zypper install -y python3 python3-pip 2>/dev/null ||
            { err "Could not install Python. Do it manually."; exit 1; }
            PYTHON="python3"
            ;;
        apk)
            sudo apk add python3 py3-pip 2>/dev/null ||
            { err "Could not install Python. Do it manually."; exit 1; }
            PYTHON="python3"
            ;;
        brew)
            brew install python@3.12 2>/dev/null ||
            brew install python@3.13 2>/dev/null ||
            { err "Could not install Python via Homebrew. Try: brew install python"; exit 1; }
            PYTHON="python3"
            ;;
        *)
            err "No supported package manager found. Install Python >= 3.12 manually."
            exit 1
            ;;
    esac

    # Re-check version and pip after install
    ver="$(py_short_version "$PYTHON")" || true
    major="${ver%.*}"; minor="${ver#*.}"
    if [[ "$major" -lt 3 || "$minor" -lt 12 ]]; then
        err "Python version still insufficient: $("$PYTHON" --version 2>&1)"
        err "Please install Python >= 3.12 manually."
        exit 1
    fi
    if ! py_has_pip "$PYTHON"; then
        "$PYTHON" -c "import ensurepip; ensurepip.bootstrap()" 2>/dev/null || true
    fi
fi

PYTHON_VERSION="$("$PYTHON" --version 2>&1)"
echo "  Found: $PYTHON_VERSION at $(command -v "$PYTHON")"
py_has_pip "$PYTHON" && echo "  pip:    $("$PYTHON" -m pip --version 2>&1)"

# ────────────────────────────────────────────────────────────
#  Step 3 — Check / install prerequisite tools
# ────────────────────────────────────────────────────────────
header "Build prerequisites"

MISSING=()

if ! py_has_pip "$PYTHON"; then
    MISSING+=("pip (for $PYTHON)")
fi

if ! "$PYTHON" -c "import venv" 2>/dev/null; then
    MISSING+=("python3-venv")
fi

if [[ -n "$CLONE_REPO" ]] && ! command -v git >/dev/null 2>&1; then
    MISSING+=("git")
fi

if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    MISSING+=("curl or wget")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "  Missing: ${MISSING[*]}"
    if [[ $SKIP_PREREQS -eq 1 ]]; then
        err "Missing required tools: ${MISSING[*]}. Install them manually or re-run without --skip-prereqs."
        exit 1
    fi

    info "Installing missing build prerequisites via $PKG_MANAGER..."
    case "$PKG_MANAGER" in
        apt-get)
            sudo apt-get update -qq
            sudo apt-get install -y -qq python3-pip python3-venv git 2>/dev/null || true
            ;;
        dnf|yum)
            sudo "$PKG_MANAGER" install -y python3-pip python3-virtualenv git 2>/dev/null || true
            ;;
        pacman)
            sudo pacman -S --noconfirm python-pip python-virtualenv git 2>/dev/null || true
            ;;
        zypper)
            sudo zypper install -y python3-pip python3-virtualenv git 2>/dev/null || true
            ;;
        apk)
            sudo apk add py3-pip git 2>/dev/null || true
            ;;
        brew)
            warn "pip and git should already be available on macOS with Xcode CLI tools."
            ;;
        *)
            warn "Cannot auto-install prerequisites. Continuing anyway..."
            ;;
    esac
else
    echo "  All build prerequisites available."
fi

# ────────────────────────────────────────────────────────────
#  Step 4 — Get the source code
# ────────────────────────────────────────────────────────────
header "Source code"

if [[ -n "$CLONE_REPO" ]]; then
    CLONE_DEST="$(mktemp -d "/tmp/hey-agent-XXXXXX")"
    info "Cloning $CLONE_REPO ..."
    git clone --depth=1 "$CLONE_REPO" "$CLONE_DEST"
    cd "$CLONE_DEST"
    echo "  Source: $CLONE_DEST"
elif $PIPED_INSTALL; then
    err "This script was piped via curl | bash and no local repo was found."
    err "When piping the installer, you must provide --clone:"
    err ""
    err "    curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash -s -- --clone https://github.com/youruser/hey-agent.git"
    err ""
    err "Alternatively, clone the repo manually first then run install.sh from it:"
    err ""
    err "    git clone https://github.com/youruser/hey-agent.git"
    err "    cd hey-agent"
    err "    ./install.sh"
    err ""
    exit 1
else
    cd "$REPO_DIR"
    echo "  Source: $REPO_DIR (local)"
fi

# Verify there's a pyproject.toml
if [[ ! -f pyproject.toml ]]; then
    err "No pyproject.toml found in $(pwd). Cannot build."
    exit 1
fi

# Extract package name cleanly
PKG_NAME="$(grep -E '^name\s*=' pyproject.toml | head -1 | sed 's/.*=\s*"\(.*\)".*/\1/' 2>/dev/null || echo "hey-agent")"
echo "  Package: $PKG_NAME"

# ────────────────────────────────────────────────────────────
#  Step 5 — Build and install the package
# ────────────────────────────────────────────────────────────
header "Build and install"

mkdir -p "$BIN_DIR"

# Check if we're inside a virtual environment
IN_VENV=false
if [[ -n "${VIRTUAL_ENV:-}" || -n "${PIP_REQUIRE_VIRTUALENV:-}" ]]; then
    IN_VENV=true
fi

# Check if system Python has PEP 668 externally-managed-environment
PEP668_BREAK=""
SITE_PACKAGES_HINT="$("$PYTHON" -m pip install --dry-run --user hey 2>&1 || true)"
if echo "$SITE_PACKAGES_HINT" | grep -qi "externally-managed" 2>/dev/null; then
    PEP668_BREAK="--break-system-packages"
fi

# Upgrade pip/setuptools/wheel in a safe way
info "Ensuring pip, setuptools, and wheel are up to date..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel --quiet $PEP668_BREAK 2>/dev/null || true

# Decide install method
if $IN_VENV; then
    info "Installing inside active virtualenv (${VIRTUAL_ENV:-detected})..."
    "$PYTHON" -m pip install -e "$(pwd)" --no-build-isolation --quiet
    TARGET_BIN_DIR="$(dirname "$(command -v "$PYTHON" 2>/dev/null || echo "$HOME/.local/bin")" 2>/dev/null)" 2>/dev/null || TARGET_BIN_DIR="$HOME/.local/bin"
elif [[ "$INSTALL_DIR" == "$HOME/.local" ]]; then
    info "Installing with pip install --user ..."
    "$PYTHON" -m pip install --user -e "$(pwd)" --no-build-isolation --quiet $PEP668_BREAK || {
        warn "pip --user failed. Trying --prefix $HOME/.local ..."
        mkdir -p "$INSTALL_DIR/lib/python$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/site-packages" 2>/dev/null || true
        "$PYTHON" -m pip install \
            --prefix "$INSTALL_DIR" \
            -e "$(pwd)" \
            --no-build-isolation \
            --quiet $PEP668_BREAK
    }
    TARGET_BIN_DIR="$HOME/.local/bin"
else
    info "Installing to $INSTALL_DIR with --prefix ..."
    mkdir -p "$INSTALL_DIR/lib"

    "$PYTHON" -m pip install \
        --prefix "$INSTALL_DIR" \
        -e "$(pwd)" \
        --no-build-isolation \
        --quiet $PEP668_BREAK

    TARGET_BIN_DIR="$INSTALL_DIR/bin"

    # Create a simple wrapper script as a fallback
    if [[ ! -x "$TARGET_BIN_DIR/hey" ]]; then
        cat > "$TARGET_BIN_DIR/hey" << 'WRAPPER'
#!/usr/bin/env bash
exec python3 -m hey "$@"
WRAPPER
        chmod +x "$TARGET_BIN_DIR/hey"
        echo "  Created wrapper script at $TARGET_BIN_DIR/hey"
    fi
fi

echo ""
info "Package installed."

# Verify the binary
HEY_BIN=""
if command -v hey >/dev/null 2>&1; then
    HEY_BIN="$(command -v hey)"
elif [[ -x "$TARGET_BIN_DIR/hey" ]]; then
    HEY_BIN="$TARGET_BIN_DIR/hey"
fi

if [[ -n "$HEY_BIN" ]]; then
    echo "  Installed: $HEY_BIN"
else
    warn "Could not find 'hey' binary in PATH or $TARGET_BIN_DIR."
    warn "It may need to be added to PATH manually."
fi

# ────────────────────────────────────────────────────────────
#  Step 6 — Add to PATH via shell rc files
# ────────────────────────────────────────────────────────────
header "Shell setup"

add_to_path_block() {
    local rc_file="$1"
    local path_line="$2"
    local marker_start="# >>> hey-agent >>"
    local marker_end="# <<< hey-agent <<"

    if [[ ! -f "$rc_file" ]]; then
        echo "  $rc_file does not exist — skipping."
        return 1
    fi

    if grep -qF "$marker_start" "$rc_file" 2>/dev/null; then
        echo "  Already configured in $rc_file"
        return 0
    fi

    {
        echo ""
        echo "$marker_start"
        echo "# Add hey-agent bin directory to PATH"
        echo "$path_line"
        echo "$marker_end"
    } >> "$rc_file"

    echo "  Added PATH entry to $rc_file"
    return 0
}

if echo ":$PATH:" | grep -qF ":$TARGET_BIN_DIR:"; then
    echo "  $TARGET_BIN_DIR is already in PATH."
else
    echo "  NOTE: $TARGET_BIN_DIR is not in your PATH."
    echo ""

    if [[ -n "${SHELL:-}" ]]; then
        SHELL_NAME="$(basename "$SHELL")"
        case "$SHELL_NAME" in
            bash)
                for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
                    add_to_path_block "$rc" "export PATH=\"\$PATH:$TARGET_BIN_DIR\"" || true
                done
                ;;
            zsh)
                add_to_path_block "$HOME/.zshrc" "export PATH=\"\$PATH:$TARGET_BIN_DIR\"" || true
                ;;
            fish)
                FISH_CONFIG="$HOME/.config/fish/config.fish"
                mkdir -p "$(dirname "$FISH_CONFIG")" 2>/dev/null || true
                if [[ -d "$(dirname "$FISH_CONFIG")" ]]; then
                    if ! grep -qF "$TARGET_BIN_DIR" "$FISH_CONFIG" 2>/dev/null; then
                        {
                            echo ""
                            echo "# >>> hey-agent >>"
                            echo "set -gx PATH \$PATH $TARGET_BIN_DIR"
                            echo "# <<< hey-agent <<"
                        } >> "$FISH_CONFIG"
                        echo "  Added PATH entry to $FISH_CONFIG"
                    else
                        echo "  Already configured in $FISH_CONFIG"
                    fi
                fi
                ;;
            *)
                warn "Unrecognized shell: $SHELL_NAME"
                echo "  Add this line to your shell config:"
                echo "    export PATH=\"\$PATH:$TARGET_BIN_DIR\""
                ;;
        esac
    else
        warn "Could not detect shell."
        echo "  Add this line to your shell config:"
        echo "    export PATH=\"\$PATH:$TARGET_BIN_DIR\""
    fi
    echo ""
fi

# ────────────────────────────────────────────────────────────
#  Step 7 — Post-install instructions
# ────────────────────────────────────────────────────────────
header "Installation complete"

if [[ -n "$HEY_BIN" ]]; then
    echo ""
    echo "  ${GREEN}✓${RESET} hey-agent is installed!"
    echo ""
    echo "  Quick start:"
    echo ""
    echo "    1. Set up your API endpoint:"
    echo "       hey --set-url https://api.openai.com/v1"
    echo "       hey --set-key sk-...your-api-key..."
    echo ""
    echo "    2. Or use environment variables:"
    echo "       export HEY_CONFIG_PATH=\$HOME/.hey/config"
    echo ""
    echo "    3. Try it:"
    echo "       hey what is my current IP address"
    echo ""
    echo "    4. To refresh system information:"
    echo "       hey --refresh-system-info"
    echo ""
fi

echo "  Config file:  ~/.hey/config"
echo "  Bin location: $TARGET_BIN_DIR/hey"
echo ""

# ────────────────────────────────────────────────────────────
#  Clean up temporary clone if used
# ────────────────────────────────────────────────────────────
if [[ -n "$CLONE_DEST" && -d "$CLONE_DEST" ]]; then
    rm -rf "$CLONE_DEST" 2>/dev/null || true
fi

echo ""
echo "${BOLD}Enjoy using hey!${RESET}"
echo ""