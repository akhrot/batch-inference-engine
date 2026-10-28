# 01: Spec Transcription

## Source

The user photographed a printed two-page Google Doc titled **"IC4 - Q5: Batch Inference Engine REST API Service"** and shared the photos in chat. The photos are saved in the agent's assets folder, not in the repo.

- **Round 1, 12:13:** two photos, one per page, at 1024×682. The headings and the logistics table were readable. The body text was small, so the photos were cropped and upscaled with ImageMagick (`magick ... -crop -resize 300-400% -colorspace Gray`) to read it.
- **Round 2, 12:27–12:28:** six closer photos covering each section. Everything was clearly readable.

## Words that were uncertain after round 1, and the round 2 result

| Item | Round 1 guess | Confirmed |
|---|---|---|
| Endpoint paths | `/job/{id}` or `/jobs/{id}` | **`/job/{id}/status`** and **`/job/{id}/download`** (singular) |
| Model example | `meta-llama-3-8b-instruct` | **`meta-llama-3-8b-instruct`** |
| Architecture diagram bullet | "ingestion task" or "mask" | **"ingestion track"** (the user edited this in the file) |

## Other corrections from round 2

- Memory bullet: "avoid **full-table** OOM memory crashes" (was "full-scale").
- Scatter-gather bullet: "(e.g., …" with a comma.
- Table row label: "Pre-**Installed** Utilities".
- Removed the "Transcription Notes" section from `spec.md` once nothing was uncertain.

## Result

`docs/spec.md` is a **verbatim** transcription, 47 lines. It omits only the page header (date, time, and document title in the browser print header) and the footer (Google Docs URL and page number).

## Key spec facts to keep in mind

- **Deliverables:** code, an architecture diagram, unit and integration tests, a GitHub Actions CI pipeline, a README (quickstart against the 1,000-prompt input plus a scale-threshold discussion), and a push to a **personal GitHub repo** before the timer expires.
- **Required endpoints:** `GET /job/{id}/status` and `GET /job/{id}/download`. The submission endpoint is not specified; the design uses `POST /job`.
- **Scale question:** explain how the code avoids running out of memory at **500,000 items**.
- **Extensions:** stream state to **DigitalOcean Spaces**, and a completion **webhook**.
- **Environment rules:** work only in `/workspaces`, never save to the host's Desktop or Downloads, and sign out of GitHub, Cursor and the browser at the end.
