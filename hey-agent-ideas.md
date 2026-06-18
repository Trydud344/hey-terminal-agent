# `hey`: A Systemwide Terminal AI Agent

## Core Idea

`hey` is a small, fast, general-purpose AI agent that lives in the terminal and works across the whole machine.

The interaction should feel like talking to a capable shell-native assistant:

```bash
hey update my system
hey why is my laptop overheating
hey clean up large unused files
hey set up a local postgres database
hey why is my internet slow
hey make a zip of my invoices from last month
```

The key difference from coding agents is scope. `hey` is not anchored to a project folder. It is anchored to the user’s computer. It can inspect system state, run commands, explain what it is doing, and help with everyday computer operations.

It should feel closer to a knowledgeable terminal operator than a developer workspace agent.

## Product Principles

1. **Conversational by default**
   The user should not have to remember flags or subcommands. The main interface is `hey` followed by natural language.

2. **Systemwide, not project-bound**
   `hey` should understand the current shell, OS, package managers, running processes, disk usage, network state, and user files when allowed.

3. **Fast first**
   Most tasks should start producing useful output within a second or two. The agent should avoid long planning screens and heavy setup.

4. **Transparent execution**
   The agent should show the commands it plans to run, the commands it actually runs, and short explanations of why they matter.

5. **Safe by default**
   Read-only inspection can happen freely. Destructive, expensive, privileged, or privacy-sensitive actions should ask for confirmation.

6. **No TUI at first**
   The first version should be plain terminal output: easy to pipe, easy to skim, easy to interrupt.

7. **Helpful even when it cannot act**
   If `hey` cannot fix something automatically, it should explain what it found, what is likely happening, and what the user can try next.

## What It Should Be

`hey` should be a terminal-native generalist for machine-level tasks:

- Diagnose system problems.
- Run maintenance tasks.
- Explain confusing command-line errors.
- Find files.
- Summarize disk, CPU, memory, battery, network, and process state.
- Install or update software through the system’s package manager.
- Automate small one-off chores.
- Help users learn what is happening on their machine.

It should work from anywhere:

```bash
cd ~/Downloads
hey find the biggest files here

cd ~/Desktop
hey organize these screenshots by month

cd /
hey why is my disk almost full
```

The current directory can be useful context, but it should not define the whole world of the agent.

## What It Should Not Be

`hey` should not start as:

- A full coding agent.
- A project manager.
- A complex terminal UI.
- A background daemon.
- A replacement shell.
- A cloud IDE.
- A tool that silently makes major system changes.

It can eventually grow into more advanced workflows, but the first version should stay sharp: ask a question, inspect the machine, run useful commands, report back.

## Example Experience

Command:

```bash
hey why is my laptop overheating
```

Possible output:

```text
hey: I’ll check CPU load, memory pressure, battery/power state, and recent thermal hints.

Running:
  ps -Ao pid,pcpu,pmem,comm -r | head -15

Why:
  This shows whether one process is using a lot of CPU.

Result:
  Chrome Helper is using 188% CPU.
  backupd is using 72% CPU.

Running:
  pmset -g therm

Why:
  This checks macOS thermal pressure.

Result:
  Thermal pressure is elevated.

Summary:
  Your laptop is probably hot because Chrome Helper and backupd are both active.

Suggested next steps:
  1. Quit the browser tab or extension causing Chrome Helper activity.
  2. Let the backup finish if you recently plugged in a drive.
  3. If you want, run: hey cool down my laptop
```

The agent should show a concise working trail, not a hidden internal monologue. A good format is:

- What I am checking.
- Command I am running.
- Why this command is useful.
- What I found.
- What I recommend.

## Command Syntax

Primary syntax:

```bash
hey <natural language request>
```

Examples:

```bash
hey update my system
hey install ffmpeg
hey remove node_modules folders older than 30 days
hey what is using port 3000
hey why did my last command fail
```

Optional flags can come later, but the core should not depend on them.

Useful future flags:

```bash
hey --dry-run clean my downloads folder
hey --yes update my system
hey --json what is using port 3000
hey --no-network diagnose my wifi
hey --cwd ~/Downloads summarize this folder
```

## Execution Model

Each run should behave like a short-lived agent session:

1. Read the user request.
2. Gather minimal context.
3. Decide whether the task is read-only, safe write, privileged, or destructive.
4. Show intended commands before running them.
5. Run commands.
6. Interpret results.
7. Continue if more inspection is needed.
8. Ask before risky actions.
9. End with a short summary.

The agent should prefer small command steps over giant shell scripts. This makes behavior easier to inspect and easier to stop.

## Safety Model

Suggested permission levels:

### Level 0: Always Allowed

Read-only commands that inspect local state:

```bash
pwd
ls
df -h
du -sh
ps
top -l 1
uname -a
which
cat small-safe-file
```

### Level 1: Ask If Sensitive

Commands that read personal or broad filesystem data:

```bash
find ~
mdfind
cat ~/.ssh/config
ls ~/Documents
```

### Level 2: Confirm Before Running

Commands that modify files or install software:

```bash
brew upgrade
npm install -g ...
mv
cp
chmod
launchctl
```

### Level 3: Strong Confirmation

Commands that are destructive, privileged, expensive, or broad:

```bash
sudo ...
rm -rf ...
diskutil ...
dd ...
kill -9 ...
find / -delete
```

For risky commands, `hey` should show:

- The exact command.
- The expected effect.
- The affected path or system area.
- A confirmation prompt.

Example:

```text
This will delete 4.2 GB from ~/Downloads/archive.
Run this command?

  rm -rf ~/Downloads/archive

Type "yes" to continue:
```

## Trust And Transparency

The agent should make trust visible.

Good output:

```text
Running:
  lsof -i :3000

Why:
  This identifies the process listening on port 3000.
```

Avoid:

```text
Thinking...
Done.
```

Also avoid dumping huge logs unless the user asks. Summarize first, then offer the exact command or log path.

## Context Sources

`hey` can use:

- Current directory.
- Shell type and environment.
- OS and version.
- Package managers present on the system.
- Recent command failure, if shell integration is installed.
- Process list.
- Disk usage.
- Network state.
- Battery and thermal state.
- User-approved files and folders.
- A small config file.

Possible config path:

```bash
~/.config/hey/config.toml
```

Possible history path:

```bash
~/.local/share/hey/history.jsonl
```

History should be local, inspectable, and easy to disable.

## Suggested MVP

The first version should do a few things very well:

1. Accept `hey <query>`.
2. Classify the task.
3. Generate a small command plan.
4. Show each command before execution.
5. Run read-only commands automatically.
6. Ask before write, destructive, or privileged commands.
7. Summarize results.
8. Work on macOS and Linux, even if some commands are OS-specific.

Good MVP task categories:

- “What is using this port?”
- “Why is my disk full?”
- “Why is my laptop slow?”
- “Update my system.”
- “Install this tool.”
- “Find large files.”
- “Explain this error.”
- “Clean this folder.”

## Implementation Shape

Possible architecture:

```text
CLI entrypoint
  -> request parser
  -> context collector
  -> planner
  -> safety checker
  -> command runner
  -> result summarizer
```

The command runner should capture:

- Command.
- Exit code.
- stdout.
- stderr.
- Duration.
- Whether the command was approved by the user.

The planner should produce structured actions, not just prose:

```json
{
  "summary": "Check what is using port 3000",
  "commands": [
    {
      "cmd": "lsof -i :3000",
      "risk": "read_only",
      "why": "Shows the process listening on port 3000"
    }
  ]
}
```

This makes it easier to enforce safety rules before anything reaches the shell.

## Python-First Stack

For the first version, Python is probably the best choice. The product shape will change a lot early on, and Python makes it easier to iterate on prompts, command safety rules, OS-specific checks, and output formatting.

The downside is distribution. A Rust or Go binary is cleaner later, but that can wait until the behavior feels right.

Recommended starting stack:

- **Language:** Python 3.12+
- **CLI:** Typer
- **Terminal output:** Rich
- **Structured command plans:** Pydantic
- **HTTP client:** httpx
- **Config:** TOML via `tomllib` for reading and `tomli-w` later if writing is needed
- **Command execution:** `asyncio.create_subprocess_exec`
- **Local history:** JSONL files
- **Secrets:** environment variables first, OS keychain later
- **Packaging:** `pyproject.toml` with a console script named `hey`
- **Dependency/project tool:** `uv`

Python lets the first implementation stay very direct:

```text
hey CLI
  -> collect request words
  -> collect lightweight system context
  -> ask model for a structured command plan
  -> validate the plan with Pydantic
  -> safety-check every command
  -> show command + reason with Rich
  -> run commands with asyncio subprocesses
  -> summarize results
```

The most important part is that the model should not directly control the shell. The model should produce a structured plan, and `hey` should decide what is allowed to run.

Example plan shape:

```json
{
  "summary": "Find out why the laptop is hot",
  "steps": [
    {
      "command": ["ps", "-Ao", "pid,pcpu,pmem,comm", "-r"],
      "risk": "read_only",
      "why": "Shows which processes are using the most CPU"
    }
  ]
}
```

Useful Python modules:

```text
hey/
  __main__.py
  cli.py
  config.py
  context.py
  llm.py
  models.py
  safety.py
  runner.py
  render.py
  history.py
```

Suggested responsibility split:

- `cli.py` parses `hey <query>` and flags.
- `context.py` gathers OS, shell, current directory, package manager, and safe system hints.
- `llm.py` talks to the model provider.
- `models.py` defines Pydantic models for plans, steps, risks, and command results.
- `safety.py` classifies commands and asks for confirmation.
- `runner.py` executes commands and captures stdout, stderr, exit code, and duration.
- `render.py` prints readable terminal output.
- `history.py` writes local JSONL logs.

This keeps the first version flexible without turning it into a loose script.

## Personality

`hey` should feel calm, direct, and useful.

It should not sound like an enterprise chatbot. It should not over-apologize. It should not bury the answer under policy language.

Good tone:

```text
I found the process using port 3000. It is a Node dev server with PID 41822.
```

Less good:

```text
As an AI language model, I have analyzed your request and determined the following...
```

## Future Ideas

After the plain CLI version works, useful additions could include:

- Shell integration for “why did my last command fail?”
- Local memory for user preferences.
- Named recipes, like `hey doctor`, `hey clean`, or `hey update`.
- A plugin system for package managers and OS-specific diagnostics.
- A `--json` mode for scripting.
- A `--dry-run` mode for cautious users.
- A local-only mode.
- Scheduled checks, like “tell me when disk usage is above 90%.”
- Voice input later, if the terminal UX remains the primary interface.

## Strongest Starting Point

The best first version is not a universal autonomous agent. It is a transparent terminal helper that can inspect, explain, and carefully act.

The magic is the command shape:

```bash
hey why is my laptop overheating
```

That one line should feel like asking a technically skilled friend sitting next to your terminal. They look around, explain what they are checking, run the right commands, and tell you what to do next.
