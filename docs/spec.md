# IC4 - Q5: Batch Inference Engine REST API Service

## Coding Interview: Batch Inference Engine REST API Service

### Mandatory Environment Rules

- **Stay in the Workspace:** All development must occur inside the `/workspaces` directory within your IDE.
- **No Host Saving:** Do not save code or assets to the host machine's Desktop or Downloads folder. These areas are not bridged to the container; data saved there will be permanently lost when the session ends.
- **Final Submission:** You must push your code and architecture diagram to a personal GitHub repository before the timer expires.
- **Session Cleanup:** Sign out of all personal accounts (GitHub, Cursor, Browser) before handing back the workstation.

### Logistics and Tooling

| | |
|---|---|
| **Time Limit** | 3 Hours |
| **Supported Languages** | Go, Python, Java, or TypeScript / Node.js (Standard extensions for YAML, Python, Go, and Java are highly recommended) |
| **IDE Environment** | VS Code or Cursor, bridged directly to a Ubuntu 24 Dockerized container. |
| **AI Assistance** | GitHub Copilot, Claude Code, and Cursor are permitted. You are encouraged to use Chrome, documentation, and any AI tools to assist your development. You remain fully responsible for the review, architecture, and correctness of all generated outputs. |
| **Permissions** | You have passwordless sudo access to install additional dependencies via apt or brew. |
| **Pre-Installed Utilities** | GitHub CLI (gh), doctl, s3cmd, jq, yq, neovim, and core image processing libraries |

---

## Project Objective

**Summary:** Build a production-ready asynchronous batch evaluation engine that reads a file array of prompts from local workspace memory, chunks and fans out execution across a bounded worker pool invoking live inference endpoints, manages high upstream rate-limiting backpressure, and aggregates scattered results into structured reports.

## Functional Expectations

- **Batch File Ingestion:** Read and parse a local workspace input file (`sample_batch.json` containing an array of 1,000 prompt items). Instantly return an operational Job ID; execution must run fully unblocked in the background.
- **Scatter-Gather Distribution:** Partition the 1,000 prompt records into concurrent execution chunks and feed them across a bounded internal worker pool invoking live inference endpoints. Workers must prioritize cost-efficient configurations (e.g., `meta-llama-3-8b-instruct`) for high-throughput testing.
- **API Backpressure Control:** Real-world endpoints frequently throw HTTP 429 Rate Limits under high parallel load. Workers must enforce resilient exponential back-off and jitter to maximize completion velocity without dropping elements.
- **Partial Failure & Reporting:** Intercept individual row failures (such as persistent 500 blocks or corrupt inputs) cleanly as isolated errors. Compile all successful prompt responses together, allowing tracking via `GET /job/{id}/status` and final compiled array assembly retrieval through `GET /job/{id}/download`.

## Engineering Expectations

- **Architecture Flow Diagram:** Provide a diagram defining the input file ingestion track, the scatter pool segmentation pipeline, the backpressure throttling layout, and the final gather collection layer.
- **Memory Footprint & Resource Throttling:** Detail and reason through scaling ceilings. If the input dataset scales up to 500,000 items, explain how your code paths avoid full-table OOM memory crashes.
- **Testing:** Cover all scenarios with unit and integration tests.
- **CI/CD:** A functional GitHub Actions pipeline that runs your full test suite on push.
- **Documentation:** README covering quickstart commands against the 1,000-prompt input template and explicit discussion around architectural scale thresholds.

## Extensions & Next Steps

- **State Streaming Output:** Persist intermediate block iterations progressively out to a DigitalOcean Spaces bucket to preserve large batch tracking chunks from unexpected hardware crashes.
- **Real-Time Webhook Hook:** Support registration of a completion callback payload URL to notify external consumer listeners immediately when a heavy aggregation processing job wraps.
