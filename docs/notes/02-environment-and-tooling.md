# 02: Environment and Tooling

## Machine

- Ubuntu 24 Docker container (per the spec), **arm64** (`go version` reports `linux/arm64`).
- Workspace: `/workspaces`, which is the **git repo root** (branch `main`, initialised at 13:02, **no commits and no remote yet**).
- The pre-created folders `/workspaces/docs` and `/workspaces/project` were empty at the start.
- `/workspaces/scratch.txt` is the user's scratch file, currently empty. **Decide whether to git-ignore it before the first commit.**

## Languages installed

| Language | Version | Path |
|---|---|---|
| Python (default) | 3.15.0 | `/usr/local/python/current/bin/python3` |
| Python (system) | 3.14 | `/usr/bin/python3.14` |
| Go | 1.27.2 | `/usr/local/go/bin/go` |
| Java | OpenJDK 21 | sdkman |
| Node | v24.22.0 | nvm |

## Utilities

- Present: `gh`, `doctl` 1.119.0, `jq`, `yq`, `nvim`, and ImageMagick (`magick`/`convert`).
- **`s3cmd` is not installed**, even though the spec lists it. It's only relevant to the Spaces extension. Install it with `sudo apt install s3cmd`, or use `boto3` from Python.
- **`doctl` 1.119 has no `genai`, `gradient` or `spaces keys` commands.** The inference Model Access Key and the Spaces access keys must be created in the DigitalOcean web console.

## Python environment

- A virtual environment is at **`project/.venv`** (Python 3.15), created at 13:02.
- Installed: fastapi 0.162.0, uvicorn 0.54.0, httpx 0.28.1, ijson 3.6.0, pydantic 2.14.1, pydantic-core 2.50.1, pytest, pytest-asyncio.
- A dry-run install (`pip install --dry-run --only-binary=:all:`) confirmed that **prebuilt wheels exist for cp315 on aarch64** for pydantic-core and ijson, so nothing needs compiling.
- Activate with `source project/.venv/bin/activate`, or call `project/.venv/bin/python` directly.

## Credentials and auth status

| Item | State at 12:50 (agent checked) | Update |
|---|---|---|
| `doctl` | `default` context, **no token** | 13:09: the user says they ran `doctl auth init --context interview`. Not yet verified by the agent. |
| Inference key | none in the environment | 13:09: the user says they created it in the console. Not yet placed where the app can read it. |
| Spaces keys | none | 13:09: the user says they created them in the console. |
| `gh` | **not logged in** | `GH_TOKEN` and `GITHUB_TOKEN` variable names exist in the environment, but `gh auth status` reports not logged in. Values were not inspected. |
| Git identity | global: `candidate <candidate@interview.local>` (preset by the workstation) | Consider setting a repo-local personal identity before committing to the personal repo. |

**Secrets policy agreed in chat:** the user enters tokens in their own terminal and never pastes them into chat. App secrets go in **`project/.env`**, which is git-ignored, for example `INFERENCE_API_KEY=...`.

**End-of-session cleanup (spec rule):** run `doctl auth remove --context interview`, revoke the DigitalOcean token and keys in the console, run `gh auth logout`, and sign out of Cursor and the browser.

## Agent sandbox notes

- The agent's shell has allowlisted network access only. PyPI, npm and GitHub work. Calls to `api.digitalocean.com` or the inference endpoint may need the agent to request `full_network` permission.
- Agent writes are limited to `/workspaces`, plus `/tmp`, which is ephemeral.

## Mermaid validation (no browser available)

There is no Chromium, so `mmdc` can't render. Diagrams are validated with Mermaid's own parser under Node and jsdom. This checks syntax only and does not produce a visual render.

```bash
mkdir -p /tmp/mmd && cd /tmp/mmd && npm init -y && npm install mermaid jsdom   # mermaid 12.2.0 at time of writing
node check.mjs /workspaces/docs/design.md
```

`check.mjs` sets up the jsdom globals (`window`, `document`, `DOMParser`, `Element`, `HTMLElement`), imports `mermaid`, extracts every ```` ```mermaid ```` block, and calls `mermaid.parse()` on each.

**Gotcha:** `call` (and `end`) are reserved words in flowcharts and can't be used as node IDs.
