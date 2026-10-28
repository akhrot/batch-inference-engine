# 07: Deployment to DigitalOcean (from 14:28)

**Request:** deploy to the DigitalOcean project **`akhrot-interview-2`** (ID `a7442609-637f-48a0-9523-3dbd9dc991dd`), keeping the inference endpoint **mocked**.

## What was found

- `doctl` is authenticated (context `interview`, current). The account email is visible through `doctl account get`. The account status shows `warning`.
- **Droplet limit: 3, and 3 are in use** (`agents-s-2vcpu-4gb-nyc1`, `ingest-api`, `ubuntu-s-2vcpu-4gb-blr1`), all outside this project. A new droplet would be rejected. Deleting or reusing them is the user's call; they were not touched.
- The project had no resources yet. The account already has 4 App Platform apps in other projects.
- There is no Docker here, so building an image for the container registry (DOCR) isn't practical. App Platform must build from source, which means a **public GitHub repo** using the `git.repo_clone_url` source. A private repo would need DigitalOcean's GitHub app to be authorized in the console.

## Chosen approach: App Platform (`.do/app.yaml`)

```mermaid
flowchart LR
    user(["Client"]) -->|HTTPS| ingress["App Platform ingress<br/>*.ondigitalocean.app"]
    ingress --> api["service: api<br/>uvicorn :8080, 1 instance<br/>SQLite on local disk"]
    api -->|"http://mock-llm:9000/v1"| mock["service: mock-llm<br/>internal port 9000 only"]
```

- **Region:** `blr`. **Size:** `apps-s-1vcpu-0.5gb` for both services ($5/month each).
- **`api`:** built by the Python buildpack from `project/`, using `project/requirements.txt` and `project/.python-version` (3.13). It runs `uvicorn --app-dir src --factory batch_engine.api:create_app`. The health check is `/healthz`, and `INFERENCE_BASE_URL=http://mock-llm:9000/v1`.
- **`mock-llm`:** the same build, running `scripts/mock_inference.py`. It is reachable only inside the app (`internal_ports: [9000]`, no route).
- **Must stay at exactly 1 `api` instance:** the queue is in process and SQLite is on local disk. Job history is lost on every redeploy, because App Platform disks are ephemeral. That's acceptable for a demo with a mocked backend; the production fix is the Postgres plus Redis layout in design.md §9.
- **No authentication on the API.** It can only read files inside `DATA_DIR`, and the backend is a mock, so nothing costs money. Before using a real key, put it behind auth.
- **Validation:** `doctl apps spec validate .do/app.yaml` passes, both schema-only and full.

## Steps once the repo is public on GitHub

```bash
sed -i "s#github.com/OWNER/#github.com/<gh-user>/#" .do/app.yaml && git commit -am "Point app spec at repo" && git push
doctl apps create --spec .do/app.yaml --project-id a7442609-637f-48a0-9523-3dbd9dc991dd --wait
doctl apps list --format ID,Spec.Name,DefaultIngress
curl -s https://<default-ingress>/healthz
```

**Cleanup after the interview:** `doctl apps delete <app-id>`.
