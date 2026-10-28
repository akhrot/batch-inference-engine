# Post-Interview Cleanup

## Cleanup performed (28 Oct 2026, 16:08 IST)

At the user's request, **the GitHub repo, the local checkout and the DigitalOcean app (with its serverless-inference configuration) were kept**. All local access to GitHub and DigitalOcean, and all keys on this machine, were removed.

| # | Step | Result |
|---|---|---|
| 1 | Credential scan (counts only, no values printed): `.env` files, token environment variables, the `doctl` and `gh` configs, `~/.git-credentials`, `~/.netrc`, AWS/S3 configs, `~/.ssh`, shell history, and the repo (excluding `.git` and `.venv`) for DigitalOcean, GitHub and inference token patterns | Found: `project/.env` (inference key), an empty `/workspaces/.env`, the `doctl` context `interview`, the `gh` login, gh credential helpers in the global git config, and the repo-local git identity. No stray tokens elsewhere. |
| 2 | Checked app health before removing access | `{"status":"ok"}` |
| 3 | Deleted `project/.env` and `/workspaces/.env` | Both gone |
| 4 | `doctl auth remove --context interview` | Only `default` (no token) remains. 0 DigitalOcean tokens in the `doctl` config. `doctl account get` returns "access token is required". |
| 5 | Pushed this record to GitHub | Last action needing GitHub access |
| 6 | `gh auth logout --hostname github.com` | Not logged in |
| 7 | Removed the gh credential helpers (`credential.https://github.com.helper`, `credential.https://gist.github.com.helper`) from the global git config | Removed |
| 8 | Unset the repo-local `user.name` and `user.email` | The workstation default `candidate` identity remains |

**Intentionally kept:**
- The app `33d7abde-…` at https://batch-inference-engine-ibptc.ondigitalocean.app.
- Its encrypted `INFERENCE_API_KEY` secret. Removing it would break the app.
- The repo https://github.com/akhrot/batch-inference-engine.
- `/workspaces` and its git remote, which contains no credentials.

**Still to do by the user (needs the web consoles):**
- **DigitalOcean, API then Tokens:** revoke the personal access token used for `doctl`. Deleting it locally doesn't invalidate it. The app keeps running without it.
- **DigitalOcean, Spaces Access Keys:** revoke the keys created for the session. They were never used.
- **DigitalOcean, Model Access Keys:** **keep** the key the app uses. Revoking it stops inference.
- **GitHub, Settings, Applications, Authorized OAuth Apps:** revoke "GitHub CLI". `gh auth logout` removes the local token but doesn't revoke it on GitHub.
- Sign out of Cursor and the browser.

---

The spec requires signing out of all personal accounts (GitHub, Cursor, browser) before handing back the workstation. This checklist also removes the cloud resources and credentials created during the session.

**Do the steps in order.** Step 1 needs the DigitalOcean token that step 2 removes.

## 1. Cloud resources (DigitalOcean)

Do this once the reviewers no longer need the live URL. The app is billed per second to team **`Blitz_team2_Hyd`**, about $0.33/day.

| Resource | ID |
|---|---|
| App Platform app `batch-inference-engine` | `33d7abde-59c4-44d3-92ad-6e401d19a631` |
| Project `akhrot-interview-2` | `a7442609-637f-48a0-9523-3dbd9dc991dd` |

```bash
doctl apps delete 33d7abde-59c4-44d3-92ad-6e401d19a631 --force
doctl apps list --format ID,Spec.Name                                  # app should be gone
doctl projects resources list a7442609-637f-48a0-9523-3dbd9dc991dd     # should be empty
doctl projects delete a7442609-637f-48a0-9523-3dbd9dc991dd --force     # optional: the project only works once it's empty
```

No droplets, Spaces buckets, registries or databases were created. The three existing droplets belong to other projects and were not touched.

## 2. DigitalOcean credentials

```bash
doctl auth remove --context interview
doctl auth list                                                         # "interview" should no longer appear
```

Then, in the DigitalOcean console:

- **API, then Tokens:** delete the personal access token created for this session.
- **Gradient AI / Serverless Inference, then Model Access Keys:** delete the key created for this session.
- **Spaces Object Storage, then Access Keys:** delete the key created for this session.

## 3. GitHub

The repo **https://github.com/akhrot/batch-inference-engine** is the submission. **Keep it.**

```bash
gh auth logout --hostname github.com
gh auth status                                                          # "You are not logged into any GitHub hosts"

# remove the credential helper that `gh auth setup-git` added to the global git config
git config --global --unset-all credential.https://github.com.helper
git config --global --unset-all credential.https://gist.github.com.helper

# remove the personal identity set on this repo (the global "candidate" identity is the workstation default; leave it)
git -C /workspaces config --unset user.name
git -C /workspaces config --unset user.email
```

If you signed in to GitHub in the browser, sign out there too.

## 4. Local secrets

- `project/.env` was never created, so no inference key is on disk. If you added one later: `rm /workspaces/project/.env`.
- Tokens were entered at interactive prompts, not on the command line. If you ever pasted one into a command, clear the shell history: `history -c && rm -f ~/.bash_history`.

## 5. Cursor and browser

- **Cursor:** sign out (Settings, then Account, then Sign out).
- **Browser:** sign out of GitHub, DigitalOcean, and any other personal accounts, and clear any saved passwords or sessions.

## 6. Final check

```bash
gh auth status                       # not logged in
doctl auth list                      # only "default" (no token)
doctl apps list 2>&1 | head -1       # fails with "access token is required" once credentials are removed
ls /workspaces/project/.env          # "No such file"
```
