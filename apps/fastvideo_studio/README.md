# FastVideo Studio

A lightweight web-based UI for interacting with FastVideo.

## Features

The UI currently supports:

- Inference
- Finetuning & Distillation
- Datasets
- Gallery View

## Project Structure

```
apps/fastvideo_studio/
├── server.py / job_runner.py / database.py   # FastAPI backend + job lifecycle
├── job_queue.py                              # Queue scheduling rules + last-frame references (GPU-free)
├── merge.py                                  # Join a scene's clips into one video (ffmpeg stream copy, GPU-free)
├── trim.py                                   # Cut a clip to an in/out range, non-destructively (GPU-free)
├── mock_server.py                            # In-memory API mock for e2e tests
├── models/                                   # Pydantic request models (shared with the mock)
├── training_config.py                        # Studio workloads → fastvideo/train YAML configs
├── tests/                                    # Backend unit tests (pytest)
├── e2e/                                      # Playwright specs (run against the mock)
└── src/
    ├── app/                                  # Next.js App Router pages (thin routes)
    ├── components/
    │   ├── shell/                            # App chrome: header, sidebars, layout
    │   ├── jobs/                             # Job queue, cards, create-job modal, log sidebar
    │   ├── datasets/                         # Dataset cards, upload, captions
    │   └── ui/                               # shadcn-style primitives (shared theme)
    ├── stores/                               # Framework-agnostic state + React bridge (hooks/)
    ├── lib/                                  # API client, types, option persistence
    └── test/                                 # Vitest setup + factories
```

The visual theme (slate light/dark palettes, IBM Plex type, `#356cff` accent)
is shared with `apps/dreamverse`; the toggle in the header persists the choice
per browser.

## Testing

```bash
npm run typecheck        # tsc
npm test                 # vitest unit tests
npm run e2e              # Playwright against the in-memory mock backend
python -m pytest tests/  # backend unit tests (from apps/fastvideo_studio)
```

## Quick Start

For local development, install dependencies and start the Next.js dev server:

```bash
cd apps/fastvideo_studio
npm install
npm run dev
```

You can then access the app at [http://localhost:3000](http://localhost:3000).
Start the Python API server (default port 8189) in a separate terminal — see below.

For a production build of the web app together with the API server:

```bash
cd apps/fastvideo_studio
npm install
npm run build
npm run start:all
```

### Running API and Web Separately

The UI is composed of two separate components:

- API Server
- Web Server

To run each component separately, you can use the commands `npm run start:web` (after `npm run build`) and `npm run start:api`.

You can also run the API server with this command from the `apps/` directory:

```bash
python -m fastvideo_studio.server --output-dir /path/to/videos --log-dir /path/to/logs
```

Running it this way allows you to pass command line parameters.

The API server defaults to port 8189. To configure this, you can edit `.env.local` file.
Refer to `.env.example` for reference.

### Job queue

`POST /api/jobs/queue` submits a batch; Start submits one job to the same
queue. A single persistent scheduler runs ready jobs in submission order and
waits for their reference jobs to complete. Inference uses isolated model
processes and reuses a compatible model between jobs. Switching settings or
models replaces it; failure or cancellation unloads it. Training and inference
share this single execution slot, so they cannot overwrite each other's GPU
runtime state. Running inference cancellation takes effect between pipeline
phases, not immediately inside a GPU kernel.

Queued and running jobs interrupted by an API restart are marked failed with a
restart explanation. They are never silently replayed. Review their settings and
start them again explicitly. Cancel marks a queued job stopped; Remove from
queue returns it to pending.

Reference-capable checkpoints can use another job's ending in two ways. FastH3
V2 is T2AV-only: it supports independent shots but cannot use image/video
references. Studio disables automatic linking for this checkpoint and rejects
reference-conditioned jobs before model allocation. Other checkpoints still
undergo the core runtime compatibility checks.

- **Last frame** -- a reference whose source is `job-last-frame:<job id>`. A
  conditioning image: the prompt is rewritten to request that opening.
  Visual continuity still depends on the checkpoint and is not guaranteed.
- **Last clip** -- a reference whose source is `job-last-clip:<job id>`, and
  `media_type: "video"`. The trailing ~1s of the source job's video instead of
  a single still, so the model has real motion to continue rather than a pose
  to guess it from. Costs one of H3's 3 video-reference slots instead of one
  of its 9 image slots.

Either is extracted when the job runs, so the reference can be added before
that job has finished, and the queue holds the job until it has. If that job
fails, is stopped, or is deleted, the waiting job fails with a message saying
so (and so does anything waiting on *it*); fix the earlier job and queue them
again. Start uses the same queue and waits for an unfinished source.

### Merging a scene

**Merge scene** on the Scenes page joins a scene's clips, in the order shown (the
take chosen for each), into one video under `<output-dir>/merged/`, then plays it
and offers a download. It is enabled once every clip has finished, and refuses
otherwise, so a scene missing a clip can't pass for the whole thing.

The clips are copied, not re-encoded (about a second for 15 clips, no quality
loss). That needs them to share size, frame rate and audio, which clips from one
scene do; if one differs, the merge says which. Each clip is cut at the end of its
last video frame, because H3's audio runs a few milliseconds longer and would
otherwise leave a small gap at every join. It uses `ffmpeg` from `PATH`,
`$FASTVIDEO_FFMPEG_BIN`, or the copy bundled with `imageio-ffmpeg`.

### Editing a clip's video

**Edit video** on a finished clip in Scenes cuts it to a chosen in/out range and
adjusts its brightness, contrast and saturation. Each edit writes a new file
and atomically publishes its path; the original and previous versions remain
unchanged. Ending caches include the source version, so restore cannot reuse a
stale frame. Editing is blocked while descendants are queued or running, and
completed descendants become stale with an explicit regeneration message.
Publishing the edited path and invalidating descendants share one transaction.
Retrying or duplicating an edited job preserves its original generation length. Frame-accurate
(re-encoded, not stream-copied), so the cut isn't limited to the source's
keyframes. Audio is cut to the same range and carried over too (re-encoded to
AAC regardless of the source's codec), unless the source has none.

Color grading is per **section**, not the whole clip at once, like a razor/blade
tool: **Split at current time** cuts the kept range wherever the preview is
paused, and each resulting section gets its own brightness, contrast and
saturation sliders, next to a before/after preview of a still frame
(**Preview** on a section seeks to its start and captures that frame; **Use
current frame** captures wherever the preview is paused instead). That preview
is computed entirely in the browser (`src/lib/grading.ts` mirrors `trim.py`'s
`_apply_grade` pixel math) as the sliders move, so it never waits on the
server -- only **Apply** does that, to actually re-render the file. **Previous
frame**/**Next frame** step the preview by exactly one frame (at the clip's
own fps), for lining up a cut precisely -- the native scrub bar alone isn't
frame-accurate. **Merge with next** undoes a cut, keeping the earlier of the
two sections' grade.
Sections are always contiguous and cover the whole kept range -- there's no
reordering or gaps, only where to cut and what to grade each side; a plain
range trim with no cuts is just the one-section case. Audio is one continuous
track regardless of how many sections there are.

The untouched original is always kept alongside it the first time a clip is
edited, and **every edit always re-renders from that original** with the full
set of values shown in the dialog -- range and sections together, in one pass.
That's what lets adjusting either one leave the other in place instead of each
one silently discarding the other. The job itself records the range/sections
of its last edit (`edit_start_seconds`/`edit_end_seconds`/`edit_segments`,
alongside `output_path`), so reopening the dialog -- even after closing it or
reloading the page -- shows exactly what produced the current video, not a
guess: a never-edited clip shows one neutral section covering its full length;
an edited one shows every section that made it, cuts and start offset
included. **Restore original** undoes every edit in one step and resets those
recorded values back to one neutral section too.

### API Endpoints

| Method   | Path                          | Description                                |
| -------- | ----------------------------- | ------------------------------------------ |
| `GET`    | `/api/models`                 | List available models                      |
| `GET`    | `/api/jobs`                   | List all jobs (newest first)               |
| `GET`    | `/api/jobs/{id}`              | Get a single job's details                 |
| `POST`   | `/api/jobs`                   | Create a new job                           |
| `POST`   | `/api/jobs/{id}/start`        | Start a pending/stopped/failed job         |
| `POST`   | `/api/jobs/{id}/stop`         | Request a running job to stop              |
| `POST`   | `/api/jobs/queue`             | Queue jobs (`{"job_ids": [...]}`), in order |
| `POST`   | `/api/jobs/{id}/queue`        | Queue one job                              |
| `POST`   | `/api/jobs/{id}/dequeue`      | Take a queued job back out of the queue    |
| `POST`   | `/api/jobs/{id}/last-frame`   | Save the job's last frame, for another job to start from |
| `POST`   | `/api/jobs/{id}/last-clip`    | Save the job's trailing ~1s, for another job to continue from |
| `POST`   | `/api/scenes/merge`           | Join finished jobs' videos (`{"job_ids": [...], "name": ""}`) in order |
| `POST`   | `/api/jobs/{id}/trim`         | Cut a job's video to a range and/or grade it section by section (`{"start_seconds": 0, "end_seconds": null, "segments": [{"end_seconds": null, "brightness": 0, "contrast": 1, "saturation": 1}]}`), in place |
| `POST`   | `/api/jobs/{id}/restore-video`| Undo every edit, back to the original video |
| `GET`    | `/api/merged/{filename}`      | Stream a merged scene video                |
| `DELETE` | `/api/jobs/{id}`              | Delete a job                               |
| `GET`    | `/api/jobs/{id}/video`        | Stream the generated video/image           |
| `GET`    | `/api/jobs/{id}/logs`         | Get job logs (polling, supports `?after=`) |
| `GET`    | `/api/jobs/{id}/download_log` | Download the job's log file                |

## Per-job attention settings

Inference jobs expose **Attention Backend** and **VSA Tile Size**. An explicit
job backend takes priority, followed by a server/environment override. With
neither set, Auto uses the backend from a matching self-contained T2V serving
recipe, then falls back to FastVideo's core selection. An unset tile uses the
matching recipe's tile when the resolved backend agrees with that recipe.
Unknown models, non-T2V workloads and pipeline overrides keep the core defaults.
The form previews the Auto selection; logs record requested and resolved values.
Auto does not change sampling steps, guidance, frame count or offload settings. A backend appearing in the list does not guarantee that the
current model or hardware supports it. Core checkpoint checks remain enabled.

When a matching text-to-video serving YAML with an explicit backend and no
component or quantization overrides exists under `examples/serving`,
**Apply recipe defaults** copies its attention and sampling settings into the
form. This is an explicit replacement of those fields; opening the form or
changing models does not silently overwrite them. GPU count and CPU offload
remain under the user's control. FastH3 8-Step V2 uses 9 sigma-grid points
(8 transformer forwards), guidance 1, VSA sparsity 0.8 and tile size 64.

Backend and tile selections are persisted with the job, survive edit/duplicate
and server restart, and participate in generator cache identity. Old databases
migrate to Auto for both backend and tile size. For a matching T2V recipe,
previously unset attention fields now resolve from that recipe at execution.

![Per-job attention settings](../../docs/assets/studio-attention-settings.png)

## Task execution and isolation

Starting a job places it in a FIFO queue (`queued`). One persistent scheduler
thread executes jobs serially, including training, so this Studio instance does
not concurrently allocate the same visible GPUs. Queued jobs can be cancelled;
running inference stops cooperatively after loading or generation completes.
This does not reserve GPUs against other applications.

Inference always uses FastVideo's multiprocessing executor, even with one GPU.
Model parallel groups and initialization cleanup therefore live in worker
processes rather than the API process. Studio retains one generator for reuse
by the same effective configuration. Switching models/settings releases the
previous workers before loading; an inference error evicts the cached generator.
Restarting the API marks interrupted queued/running jobs failed rather than
silently replaying them. Queued jobs cannot be edited or started twice.

### Saved scenes and takes

The Scenes page stores scene names, ordered clips, and selected takes in SQLite.
Use **New scene** to add existing inference jobs as clips or alternative takes;
**Edit scene** renames or reorders clips. Selecting a take persists immediately.
**Import job groups** explicitly converts legacy name-based groups; simply
opening the page never writes a scene. Repeated imports do not duplicate takes.
Deleting a referenced job leaves a visible missing take rather than silently
substituting another video. Repair the scene before merging.

Each inference attempt writes into its own `attempt-<uuid>` directory. Exports
also have unique filenames; exporting again cannot overwrite an earlier film.
This first workbench release supports one executing job at a time. It does not
claim automatic character consistency, agent planning, or multi-GPU scheduling.

## Story agent workspace

Open `/agent` for a persistent story, reference library and storyboard. The
workspace supports manual editing, importing ViMax planning artifacts, optional
LLM planning/revision, optional character-reference generation, bounded video
submission, review, and export through the existing Studio executor.

1. Create a story and write its brief and visual style.
2. Plan with the configured model, import `{characters, shot_descriptions}` from
   ViMax, or add/edit shots directly. Stable IDs preserve revision history.
3. Upload reference images or generate a character portrait. Assign them to
   characters and shots. A continuity link uses the preceding shot's last frame
   as a **reference**, not as an exact first-frame constraint.
4. Save and approve the storyboard, then set a new-job budget before generation.
   Changing generation inputs invalidates approval and affected selected takes;
   old takes remain available in the project record. Previously completed,
   matching takes are reused. A rejected/failed take requires an explicit seed
   change through **Request new take**.
5. Review completed current takes, accept them, and export. A changed source
   reference or mutated job configuration cannot reuse the original approval.

The initial reference-bearing path uses full `MiniMaxAI/MiniMax-H3` Ref2VA.
FastH3 V2 is T2AV-only and is rejected when a plan requires references. Model
support does not prove that the default one-GPU recipe fits your hardware:
configure the generation settings for your allocation and retain core checks.
Character/scene continuity and visual quality still require real-model review.

### Planner and image configuration

Copy `agent_config.example.json` to a private path **outside the checkout** and
replace its example URLs/models. Both endpoints use OpenAI-compatible HTTP
contracts. The planner must support vision and JSON responses; uploaded images
(up to nine) and story context are sent to that configured endpoint. The optional
image endpoint must support `/images/generations` with `response_format=b64_json`.
Remove the `image` block to use uploads only. Keys stay in the private file and
are not returned by the app or saved in project records.

```bash
# From apps/, in the existing FastVideo environment:
python -m fastvideo_studio.server --host 127.0.0.1 --port 8210 \
  --data-dir /absolute/path/to/agent-data \
  --output-dir /absolute/path/to/agent-videos \
  --log-dir /absolute/path/to/agent-logs \
  --agent-config /absolute/private/path/agent.json
```

Omit `--agent-config` for manual/import mode. No model requests are made just by
opening the UI. Planner/image errors remain errors; there is no synthetic
fallback in the real server. Image requests are not automatically retried after
uncertain transport failures.

For a remote machine, use the same-origin proxy and forward only the web port:

```bash
# From apps/fastvideo_studio; use the same variables for build and start.
NEXT_PUBLIC_API_BASE_URL=/api STUDIO_API_PROXY_TARGET=http://127.0.0.1:8210 npm run dev -- --port 3010
```

For a production build, set those variables for `npm run build` and
`npm run start -- --port 3010` too. Clear any older API URL override in Settings.
Project/asset state is stored beside the Studio database and upload directory;
keep both when moving a project. Use one Studio process, not multiple independent
API workers sharing the same in-memory JobRunner.

### Validation and limitations

The mock server displays **Simulated generation** and uses synthetic clips. It
can start without torch, Triton, or model weights; its attention catalogue is a
fixed fixture, not production capability discovery. Browser tests must use the
mock sentinel and must never run against a real GPU backend by accident.

```bash
python -m pytest apps/fastvideo_studio/tests/test_agent_project.py \
  apps/fastvideo_studio/tests/test_agent_service.py \
  apps/fastvideo_studio/tests/test_agent_routes.py \
  apps/fastvideo_studio/tests/test_agent_planner.py -q
# From apps/fastvideo_studio:
npm test -- --run src/app/agent/page.test.tsx
npm run e2e -- agent.spec.ts
```

The planner returns editable proposals; users approve generation and review
media. There is no automatic cinematic-quality acceptance or claimed parity
with Grok's proprietary models. Live LLM/image-provider and reference-conditioned
GPU acceptance are separate from unit tests and the synthetic browser flow.
