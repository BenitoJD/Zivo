---
name: install-setup
description: "Installation and setup automation skill. Use when the user asks to install, configure, or set up tools, libraries, CLIs, or services. Guides web research, documentation lookup, installation execution, and configuration."
---

# Install & Setup Skill

Use this skill when the user asks you to install, configure, or set up any tool, library, CLI, service, or dependency.

## Trigger

Activate when the user says things like:
- "Install X"
- "Set up Y"
- "Configure Z"
- "Get X working"
- "Do whatever is needed to use X"
- "Make X available"

## Workflow

```
┌─────────────────────────────────────────────┐
│  1. IDENTIFY                                │
│     What needs to be installed/configured?   │
│     Is it a CLI tool, library, service,      │
│     extension, or environment?               │
├─────────────────────────────────────────────┤
│  2. RESEARCH                                │
│     Search for:                              │
│     • Official website / GitHub repo         │
│     • Installation documentation             │
│     • Package manager availability           │
│     • System requirements / prerequisites    │
│     • Configuration steps                    │
├─────────────────────────────────────────────┤
│  3. VERIFY ENVIRONMENT                      │
│     Check current system state:              │
│     • Is it already installed?               │
│     • What OS / environment are we in?       │
│     • What package managers are available?   │
│     • Are dependencies already present?      │
├─────────────────────────────────────────────┤
│  4. INSTALL                                 │
│     Execute installation commands:           │
│     • Use appropriate package manager        │
│     • Handle dependencies first              │
│     • Install in isolated env if needed      │
│     • Never install outside working dir      │
│       without user confirmation              │
├─────────────────────────────────────────────┤
│  5. CONFIGURE                               │
│     Set up the tool for use:                 │
│     • Create config files                    │
│     • Set environment variables              │
│     • Initialize / authenticate if needed    │
│     • Register with project / repo           │
├─────────────────────────────────────────────┤
│  6. VERIFY                                  │
│     Confirm installation works:              │
│     • Run version command                    │
│     • Run basic functionality test           │
│     • Check integration with project         │
│     • Document any manual steps needed       │
└─────────────────────────────────────────────┘
```

## Research Steps

### Step 1: Web Search

Use `webfetch` to search for:
- Official website (e.g., `https://<tool>.com` or `https://<tool>.io`)
- GitHub repository (e.g., `https://github.com/<org>/<tool>`)
- Official documentation installation page

If direct URLs fail, use general web search with queries like:
- `"<tool>" install guide`
- `"<tool>" GitHub`
- `"<tool>" documentation setup`

### Step 2: Documentation Review

Once you find the official source, read the installation instructions and note:
- Supported platforms (macOS, Linux, Windows)
- Installation methods (homebrew, npm, pip, cargo, go install, etc.)
- Prerequisites and dependencies
- Post-installation configuration steps
- Any required environment variables or PATH modifications

### Step 3: Check Project Context

Before installing, check:
- Is there already a version specified in `package.json`, `requirements.txt`, `pyproject.toml`, etc.?
- Are there existing config files for this tool in the repo?
- Does the project have a preferred installation method?

## Installation Rules

### Safety First

- **Never run `sudo` or admin commands** without explicit user confirmation
- **Never install outside the working directory** unless the tool must be system-wide
- **Never modify system PATH** or shell configs without asking
- **Prefer isolated environments** (virtualenv, conda, nvm, local node_modules)
- **Check if already installed** before installing again

### Package Manager Priority

For this repo, prefer package managers in this order:

1. **Project-local** (if tool is project dependency):
   - Frontend: `pnpm` (never `npm`)
   - Backend: `pip` or `uv` (respect `requirements.txt`/`pyproject.toml`)
   - Global in project: `pnpm add -D <pkg>` or `pip install -e .`

2. **Language-specific** (if tool is a CLI written in that language):
   - Node.js tools → `pnpm add -g` or `npx`
   - Python tools → `pipx` or `uv tool install`
   - Rust tools → `cargo install`
   - Go tools → `go install`

3. **System package manager** (if no other option):
   - macOS → `brew install`
   - Ubuntu/Debian → `apt-get install`
   - Alpine → `apk add`

## Configuration Steps

After installation, check if configuration is needed:

1. **Config files**: Create or modify `.<tool>rc`, `<tool>.config.*`, etc.
2. **Environment variables**: Set in `.env` or shell config
3. **Authentication**: API keys, tokens, login — prompt user if needed
4. **Project integration**: Add to `package.json` scripts, CI config, etc.
5. **Git hooks**: If setting up lint/format tools, configure pre-commit

## Verification Steps

Always verify the installation:

```bash
# Check version
<tool> --version

# Check help / basic functionality
<tool> --help

# Run a minimal smoke test specific to the tool
# (document what you ran and the output)
```

## Examples

### Example 1: Install a CLI tool

```
User: "Install ripgrep"

Research:
- webfetch https://github.com/BurntSushi/ripgrep
- Found: `brew install ripgrep` on macOS, `apt-get install ripgrep` on Ubuntu

Verify:
- Run `which rg` → already installed? Skip
- Run `brew install ripgrep` if not

Verify:
- `rg --version` → should show version
- `rg --help` → should show usage
```

### Example 2: Install a Python package for the project

```
User: "Install black for formatting"

Research:
- Check `pyproject.toml` for existing formatter
- Check if `black` is in `requirements-dev.txt`

Install:
- `pip install black` or add to `pyproject.toml` dependencies

Configure:
- Check if `.pre-commit-config.yaml` needs updating
- Check if `pyproject.toml` has `[tool.black]` section

Verify:
- `black --version`
- `black --check .` on a test file
```

### Example 3: Install and configure a new tool from GitHub

```
User: "Install gitnexus and set it up"

Research:
- webfetch https://github.com/gitnexus (failed, 404)
- Search web for "gitnexus" → no results
- Ask user: "What is gitnexus? Do you have a repo URL or docs?"

(If user provides URL, proceed with installation)
```

## Error Handling

If installation fails:

1. **Capture the exact error message**
2. **Check prerequisites**: Missing dependencies? Wrong OS?
3. **Try alternative methods**: Different package manager? Build from source?
4. **Document the issue**: What failed, what you tried, what worked
5. **Escalate to user**: If stuck, report clearly with next steps

## Anti-Patterns

- **Blind installation**: Installing without checking if it's already present
- **Ignoring docs**: Not reading official docs and guessing install steps
- **System pollution**: Installing globally when project-local works
- **No verification**: Installing but not confirming it actually works
- **Secret leakage**: Embedding API keys or tokens in config files without warning user

## Output Format

After completing installation, report:

```markdown
## Installation Summary

**Tool**: <name>
**Version**: <version installed>
**Method**: <how it was installed>
**Location**: <where it lives>

### What Was Done
- Step 1...
- Step 2...

### Configuration
- Config file created at: <path>
- Environment variable set: <name>=<value>

### Verification
- `<command>` → output: <result>

### Manual Steps Needed (if any)
- <describe anything the user must do manually>
```
