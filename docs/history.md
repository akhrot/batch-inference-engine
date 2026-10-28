# Session History

Chronological log of the interview session. Times are IST (UTC+5:30). Detailed notes for each topic are in [notes/](notes/README.md).

**Clock:** started 12:04; the 3-hour limit ends at **15:04**.

| Time | What happened | Details |
|---|---|---|
| 12:04 | Session clock started. | |
| 12:13–12:20 | Two photos of the printed spec were shared. Legibility was checked by cropping and upscaling the photos, and the spec was transcribed to `docs/spec.md` with three uncertain words flagged. | [01](notes/01-spec-transcription.md) |
| 12:20 | Environment checked: languages, utilities, and the empty `/workspaces/docs` and `/workspaces/project` folders. `sample_batch.json` does not exist anywhere and must be generated. | [02](notes/02-environment-and-tooling.md) |
| 12:27–12:29 | Six clearer photos were shared, and every uncertain word was confirmed. Fixed "full-table", "e.g.,", "Pre-Installed" and "ingestion track" (the last fixed by the user); removed the transcription notes. The spec is now verbatim. | [01](notes/01-spec-transcription.md) |
| 12:39 | Language discussion. The agent recommended Go on technical fit and named Python as the strongest alternative. | [03](notes/03-language-choice.md) |
| 12:42 | **Decision: Python**, because the user is much more fluent in it. | [03](notes/03-language-choice.md) |
| 12:42–12:50 | Verified that all Python dependencies have prebuilt wheels for Python 3.15. Found no inference credentials, `doctl` without a token, and `gh` not logged in. Proposed the initial design and build order. | [02](notes/02-environment-and-tooling.md), [04](notes/04-design-decisions.md) |
| 13:02 | The user asked four design questions (chunking, database vs. files, separate 429/5xx retries, where the queue lives) and for `doctl` login steps. All were answered. | [04](notes/04-design-decisions.md) |
| 13:02–13:03 | The agent began scaffolding: `git init`, a venv with dependencies, `pyproject.toml`, `.gitignore` and `scripts/make_sample.py`. | [05](notes/05-current-state.md) |
| 13:03 | **User: "Don't start coding yet."** Scaffolding stopped and the files were kept. The sample generator has not been run. | [05](notes/05-current-state.md) |
| 13:09 | The user reported completing the `doctl` login and creating the console keys (the agent has not verified this yet). The user asked for a design document. | [02](notes/02-environment-and-tooling.md) |
| 13:09–13:12 | Wrote `docs/design.md`: architecture, lifecycle, scatter, retries, storage, API, memory/scale, extensions, config, testing and layout, with 7 Mermaid diagrams. All 7 pass Mermaid's parser after renaming a reserved node ID (`call`). | [04](notes/04-design-decisions.md) |
| 13:12 | The user asked for this history file plus detailed notes in `docs/notes/`, to be maintained going forward. | [05](notes/05-current-state.md) |
| 13:17 | The user asked how input is submitted. The answer: by file path (`POST /job {"input_path"}`), read from `DATA_DIR`. Upload is a possible later addition; prompts inline in the body were rejected because of memory use. | [04](notes/04-design-decisions.md) |
| 13:58 | The user proposed three separate services (FastAPI, a durable queue, worker pools). Feasibility analysis: there is no Redis or Docker on the machine, and it would add 40–50 minutes. **Decision:** keep three tiers in code (`api.py`, `job_queue.py` with a `JobQueue` interface, `worker.py`) running in one process; the separate-services version is documented as the scale-out path. | [04](notes/04-design-decisions.md) |
| 14:10 | **Coding approved.** The user asked for in-depth unit tests and separate integration tests per component, with the other components faked. | [06](notes/06-implementation.md) |
| 14:10–14:18 | Implemented all modules, the fake-provider test kit, 107 unit tests and 28 integration tests (135 total, all passing). | [06](notes/06-implementation.md) |
| 14:18–14:22 | Added the mock inference server and GitHub Actions CI. Demo against the mock: 1,000 rows, 995 ok, 5 invalid rows isolated, 142 retries, zero drops. | [06](notes/06-implementation.md) |
| 14:22–14:26 | Wrote README.md, made the first local commit, and updated these docs. Waiting on the user's GitHub login before pushing. | [05](notes/05-current-state.md) |
| 14:28 | The user asked to deploy to the DigitalOcean project `akhrot-interview-2`, keeping inference mocked. `doctl` works (context `interview`). The **droplet limit is 3/3**, all used by other projects, so a VM can't be created. Prepared an App Platform spec (`.do/app.yaml`: public `api` plus internal `mock-llm`), and it passes validation. It needs the repo pushed as **public** first. | [07](notes/07-deployment.md) |

| 14:32–14:37 | The user finished `gh auth login`. Pushed to the public repo [akhrot/batch-inference-engine](https://github.com/akhrot/batch-inference-engine), and **CI passed** on 3.12 and 3.13. Deployed the App Platform app in `akhrot-interview-2` (ACTIVE in about 100 s). Live smoke test with 1,000 rows: 995 ok, 5 invalid isolated, 132 retries, zero drops. | [07](notes/07-deployment.md) |
| 14:39–14:41 | The user approved a force-push. All commits were re-authored as `Jinish Raiyarela <23030561+akhrot@users.noreply.github.com>` and pushed with `--force-with-lease`. Billing question: the app is billed to DigitalOcean team `Blitz_team2_Hyd` (the token's team), not to the individual; the user has no billing permission there. | [05](notes/05-current-state.md) |

_Earlier versions of this file estimated 14:35–14:50 for the coding rows. The real times are above._

## Status at last update (14:38)

- **Done:** spec, design, the full implementation (API, bounded pipeline, separate 429/5xx retries, SQLite, webhook extension), 135 tests, a green CI run, README, docs, pushed to GitHub, and **deployed live** at https://batch-inference-engine-ibptc.ondigitalocean.app with the mocked inference backend.
- **Commit authorship:** fixed (Jinish Raiyarela, GitHub no-reply email).
- **Open:** delete the app after the review (it's billed to team `Blitz_team2_Hyd`), plus the end-of-session sign-out steps.
- **Not done:** Spaces checkpointing extension, resuming interrupted jobs, a live smoke test against the real endpoint.
