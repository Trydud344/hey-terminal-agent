# hey — A Systemwide Terminal AI Agent

`hey` is a small, fast, general-purpose AI agent that lives in your terminal and works across your whole machine.

```bash
hey why is my laptop overheating
hey update my system
hey find the biggest files here
hey what is using port 3000
hey clean up large unused files
```

No project folder anchoring. No complex TUI. Just you, your terminal, and an AI assistant that can inspect, explain, and carefully act.

## How it works

1. You type `hey <your request>` — natural language, no flags needed.
2. `hey` sends your request to an LLM along with system context (OS, architecture, shell, package managers).
3. The model may ask to run shell commands to inspect or change the system.
4. `hey` shows each command and its purpose before running it.
5. Read-only inspection commands run automatically; destructive or system-modifying commands ask for confirmation.
6. The model interprets results and gives you a concise final answer.

## Quick Install

### One-liner (curl | bash)

```bash
curl -fsSL https://raw.githubusercontent.com/Trydud344/hey-terminal-agent/main/install.sh | bash
```

This detects your OS, checks for Python ≥ 3.12, installs any missing prerequisites, builds the package, and adds the `hey` command to your PATH.

### With options

```bash
# Install to a custom directory
curl -fsSL https://raw.githubusercontent.com/Trydud344/hey-terminal-agent/main/install.sh | bash -s -- --install-dir ~/tools

# Skip automatic package manager installs (if you already have prerequisites)
curl -fsSL https://raw.githubusercontent.com/Trydud344/hey-terminal-agent/main/install.sh | bash -s -- --skip-prereqs
```

### Manual install

```bash
git clone https://github.com/Trydud344/hey-terminal-agent.git
cd hey-terminal-agent
chmod +x install.sh
./install.sh
```

The install script supports these flags:

| Flag | Description |
|------|-------------|
| `--install-dir DIR` | Install to `DIR/bin` (default: `~/.local`) |
| `--clone URL` | Clone from a URL first, then install |
| `--skip-prereqs` | Skip OS package manager prerequisite installation |
| `-h`, `--help` | Show help |

## Requirements

- **OS**: Linux (any modern distro) or macOS
- **Architecture**: x86_64 or ARM64
- **Python**: ≥ 3.12 with pip

The install script will attempt to install missing prerequisites automatically using your system package manager (apt, dnf, pacman, zypper, apk, or brew).

## First-time setup

```bash
# Set your API endpoint
hey --set-url https://api.openai.com/v1

# Set your API key
hey --set-key sk-...your-api-key...

# Refresh system information so the agent knows your environment
hey --refresh-system-info

# Try it
hey what is my current IP address
```

You can also set the `HEY_CONFIG_PATH` environment variable to use a custom config location.

## Usage examples

```bash
# Diagnose system issues
hey why is my laptop overheating
hey why is my disk almost full
hey why is my internet slow

# Inspect and manage
hey what is using port 3000
hey find the biggest files here
hey clean up large unused files

# Install and update
hey update my system
hey install ffmpeg

# Explain errors
hey why did my last command fail

# System-wide queries (works from any directory)
cd ~/Downloads
hey organize these screenshots by month

cd /
hey why is my disk almost full
```

## Configuration

`hey` stores configuration in `~/.hey/config` (TOML format):

```toml
# hey config
base_url = "https://api.openai.com/v1"
api_key = "sk-..."
model = "gpt-4o-mini"
```

You can change settings with:

```bash
hey --set-url https://api.openai.com/v1
hey --set-key sk-...
hey --set-model gpt-4o-mini
```

Or override for a single request:

```bash
hey --model gpt-4o --debug-stream "why is my system slow"
```

## Development

```bash
git clone https://github.com/Trydud344/hey-terminal-agent.git
cd hey-terminal-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run tests:

```bash
pytest
```

## Safety

`hey` classifies commands into three risk levels:

| Level | Behavior | Examples |
|-------|----------|---------|
| **Read-only** | Runs automatically | `ps`, `df`, `ls`, `cat`, `uname` |
| **System-modifying** | Asks for confirmation | `rm`, `mv`, `apt install`, `brew install` |
| **Dangerous** | Requires strong confirmation | `sudo ...`, `rm -rf /`, disk operations |

## Project status

Early development. The core agent loop, shell command execution, and safety rules are functional. Future plans include session memory, shell integration for "why did my last command fail?", and more OS-specific diagnostics.

## License

MIT