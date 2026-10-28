# 05: Current State and Next Steps

_Last updated: about 14:38 IST. The deadline is **15:04 IST**._

## Working agreements with the user

- **Work in approved stages.** Coding was approved at 14:10, and deployment was requested at 14:28.
- **Keep the docs current.** After each piece of work, update [../history.md](../history.md) and the notes in this folder.
- **Sources of truth:** [../spec.md](../spec.md) for requirements and [../design.md](../design.md) for architecture. Implementation details are in [06](06-implementation.md) and deployment in [07](07-deployment.md).
- **Secrets never go in chat.** App secrets live in `project/.env`, which is git-ignored.

## What exists

- **Repo:** https://github.com/akhrot/batch-inference-engine (public, branch `main`, remote `origin`). The `gh` account is `akhrot`.
- **CI:** green on Python 3.12 and 3.13.
- **Live app:** https://batch-inference-engine-ibptc.ondigitalocean.app, app ID `33d7abde-59c4-44d3-92ad-6e401d19a631`, in project `akhrot-interview-2`. It has two services: `api` (public) and `mock-llm` (internal).
- **Code and tests:** complete, 135 tests. See [06](06-implementation.md).
- **Docs:** README (quickstart, live deployment, API, resilience, scale, tests), design.md, history.md, and these notes.

## Remaining options, if time allows

1. Fix the commit author: set `git config user.name/email`, then `git rebase -r --root --exec "git commit --amend --reset-author --no-edit"` and `git push --force-with-lease`. **Ask the user before force-pushing.**
2. Bump the GitHub Actions versions to clear the Node 20 deprecation notice.
3. Switch to the real inference endpoint: add `INFERENCE_API_KEY` as a secret env var in the app spec, change `INFERENCE_BASE_URL`, and put auth in front of the API first.
4. Not implemented: Spaces checkpointing (design.md §10) and resuming interrupted jobs.

## End-of-session cleanup (spec rule)

- Delete the app if it shouldn't keep running: `doctl apps delete 33d7abde-59c4-44d3-92ad-6e401d19a631`. It costs about $10/month for two small instances.
- Run `doctl auth remove --context interview`, revoke the DigitalOcean token and keys, run `gh auth logout`, and sign out of Cursor and the browser.
