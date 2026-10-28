# Post-Interview Cleanup

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
