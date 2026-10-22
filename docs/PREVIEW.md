# Read-only preview build

The preview is the real frontend served **without a backend**. It replays API responses recorded during one
real run of the full pipeline (STEP upload → geometry analysis → FreeCAD CAM → LinuxCNC postprocessor →
validation and simulation → two-person release). It is meant for showing the product, not for running jobs.

What it does and does not do:

* GET requests are answered from `frontend/preview/preview-data.json`. A request with an unrecorded query
  string falls back to the same path, so search and filter fields do not filter.
* Every write (upload, setup changes, plan, CAM, review, release) is refused with a "read-only preview" message.
  Downloads are disabled. Nothing is computed in the browser.
* A banner on every page states that the data is a recording, when it was recorded, and that the checks are
  software-level only.
* Routing uses hash URLs (`#/jobs/…`) so the files can be served from any static location.

## Re-recording

With the full development stack running (`scripts/dev.sh`, FreeCAD runtime available):

```bash
cd frontend
CHROMIUM_PATH=/opt/pw-browsers/chromium RECORD_PREVIEW=$PWD/preview/preview-data.json npx playwright test
```

This runs `e2e/workflow.spec.ts` with an organisation named "Demo machine shop", then visits every page
reachable from the navigation (and every tab) so their responses are captured. Review the recorded file before
publishing it: it contains whatever the run produced (user e-mails, organisation names, programs).

## Building

```bash
cd frontend
npm run build:preview      # → dist-preview/ (index.html, assets/, preview-data.json)
```

`dist-preview/` is a static site: copy it to any web server or object store. It needs no API, database or
worker. To deploy the working platform instead, see [DEPLOYMENT.md](DEPLOYMENT.md).
