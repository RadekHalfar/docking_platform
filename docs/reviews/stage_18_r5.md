# Review Stage R5: Phase 18 review (§4 + §5)

**Date:** 2026-10-29.
**Planning pass:** `C:\Users\radek\.claude\plans\snug-orbiting-gadget.md`.

## Scope

- **New code: `222b429..6e0ffc4`,** Stages 18.1–18.3 (63 files, +1,659 / −4,167). None of it had been reviewed. It covers:
  - 18.1, PyMOL removed (services, compose, CLI, parser, docs);
  - 18.2, provider `none` by default and Ollama behind the `local-llm` profile;
  - 18.3, the GUI's Ollama manager (`ui/ollama_models.py`, `ui_nice/settings_dialog.py`, `llm_adapters.pull_ollama_model`) and the remembered API key (`ui/settings.py`, `atomic_write_json(mode=)`).
- **The three points dev_plan.md named:** the compose profile against the build scripts and the installer; the GUI and the CLI agreeing on `none`; the settings file's permissions.
- **The test-coverage gap found 2026-10-27:** CI ran no GUI test, and two GUI-dependent files ran in no command at all.
- **§5 Cross-Service Compatibility:** compose changed in 18.1 and 18.2.

**Owner decisions (2026-10-29):**
- CI runs the GUI tier in a `ui` image it builds itself, not through a pip install.
- R5 may rebuild the images and recreate the live stack (project `af6ad48c…`, no `-v`).

## Method

1. **`/code-review` (high)** over the range: 10 candidate findings, triaged below.
2. **`/simplify`, report-only,** with four angle agents (reuse, simplification, efficiency, altitude), over the range.
3. **The three named focus points,** checked by hand.
4. **The exception pass.**
5. **The 18.x tests and the R4 trigger comments.**
6. **The coverage gap:** the GUI-tier command and a CI job.
7. **§5 on a freshly rebuilt stack.**
8. **A headless end-to-end run on port 3000.**

**Rules restated** (`development_process_rules.md` §4/§6):
- Exceptions are never a band-aid.
- Findings are not manufactured to look thorough; a clean pass is reported as clean.

## Correctness findings

### 1. Apply accepted a provider without a model (introduced in 18.2). Fixed, GUI and CLI.

**Symptom:** 18.2 made `LLM_MODEL` default to empty (it had been a Qwen tag). Picking OpenAI, or Ollama with nothing listed, and pressing Apply built a client with model `""`. The dialog said "Settings applied", the chat's setup hint went away, and every chat message then failed at the provider. The CLI was the same: `LLM_PROVIDER=openai` with no `LLM_MODEL` sent `model: ""`.

**Fix:**
- `ui/core/llm_settings.persist_settings` refuses it before building or saving anything (`MODEL_REQUIRED`: "Pick or type a model for OpenAI."), so nothing changes.
- `nlp_orchestrator.py` prints `NO_MODEL_MESSAGE` and exits 1 for a command or `--interactive`. `--list-models` and `--check-health` need no model and are unaffected.
- The check is in both front ends rather than in `create_llm_client`, because `--list-models` builds a client with no model on purpose.

**Tests:** `test_a_provider_without_a_model_is_refused_and_not_saved` (3 providers), `tests/test_nicegui_settings.py::test_apply_refuses_a_provider_without_a_model`, `tests/test_nlp_orchestrator.py::test_a_provider_without_a_model_says_so` (2 cases) and `test_list_models_needs_no_model`. On `6e0ffc4` the core and GUI tests fail. The CLI test hangs there, which is the bug: the request goes on to the LLM path and then waits for input.

**Real data:** `--llm-provider ollama "dock ATP into 6lu7"` with `LLM_MODEL` empty, in `script-runner`, printed the message.

### 2. The no-LLM chat reply lost its `<name>` (introduced in 18.2). Fixed.

**Symptom:** `NO_LLM_REPLY` wrote `**save results as <name>**`. The chat renders through markdown2, which passes raw HTML through, so the browser read `<name>` as an unknown tag and dropped it. The reply showed "save results as", which is the wrong command.

**Fix:** the placeholder is in a code span, like the other replies' placeholders. A grep of the reply strings found no other `<…>` outside one.

**Test:** `tests/test_nicegui_chat.py::test_the_setup_hint_shows_its_save_placeholder` renders the reply with the GUI's own extras. It fails on `6e0ffc4`.

### 3. A remembered key was saved again for a provider without a key field (introduced in 18.3). Fixed.

**Symptom:** for Ollama and None the key and "Remember" fields are only hidden, not cleared. So after remembering an OpenAI key, applying Ollama or None wrote the key to the file again, and the hidden checkbox couldn't be unticked.

**Fix:** `settings_from_form` drops `api_key` and `remember_api_key` for providers outside `API_KEY_PROVIDERS`, as it already dropped a hidden Base URL. Switching back to OpenAI means typing the key again, which is what the visible fields suggest.

**Tests:** `test_a_provider_without_a_key_field_neither_keeps_nor_remembers_one` and `tests/test_nicegui_settings.py::test_a_remembered_key_is_dropped_for_a_provider_without_one`. Both fail on `6e0ffc4`.

### 4. A download finishing after a provider switch overwrote the other provider's model (introduced in 18.3). Fixed.

**Symptom:** the download section stays visible while a pull runs, whatever the provider. Start a pull, switch to OpenAI and type `gpt-4o`. When the pull finished, the shared model field became `gemma3:4b`, and Apply (enabled again by then) saved OpenAI with an Ollama model.

**Fix:** the finished pull becomes the pick only while Ollama is still the provider.

**Test:** `test_a_download_ending_after_a_provider_switch_keeps_the_new_model`, which fails on `6e0ffc4`.

### 5. A malformed endpoint left the dialog silent (introduced in 18.3). Fixed.

**Symptom:** `urlsplit` raises `ValueError` for a typo such as `http://[::1:11434`, and so did a model entry that isn't an object. The search task died, `searched` stayed False, and the dialog showed neither "found" nor the Re-check panel.

**Fix:** a malformed endpoint counts as unreachable, and the listing (now shared, see Quality) skips entries that aren't objects and returns None for a `models` that isn't a list.

**Tests:** `tests/test_ollama_models.py::test_a_malformed_endpoint_returns_none` and `test_a_reply_that_is_not_ollamas_returns_none_or_skips_entries`. Both fail on `6e0ffc4`.

### 6. `.env.example` pointed the Ollama block at `localhost` (introduced in 18.2). Fixed.

**Symptom:** the block 18.2 rewrote suggested `LLM_ENDPOINT=http://localhost:11434`. Inside a container that is the container itself, and it overrides compose's `http://ollama:11434`. Someone who started the profile and uncommented the block as written got a GUI startup client and a CLI that couldn't reach Ollama. `docs/LLM_ORCHESTRATION.md`'s Ollama section had the same line.

**Fix:** both now show `http://ollama:11434`, the compose default, and say when `localhost` is right (a run outside Docker).

### 7. Closing the page left its download running and the lock held (introduced in 18.3; from the efficiency review). Fixed.

**Symptom:** "closing the dialog doesn't stop it" was intended, but closing the tab didn't stop it either. A 4.7 GB pull ran on with no UI, and every other page got "Another model download is already running" until it ended, with no way to cancel.

**Fix:** the dialog sets its Cancel flag from `client.on_delete`, which NiceGUI calls once a closed page is gone for good (not on a brief reconnect).

**Test:** `test_closing_the_page_cancels_its_download`, which fails on `6e0ffc4`.

### 8. A capitalized `LLM_PROVIDER` showed as "None" in the dialog (introduced in 18.2; found by the focus check). Fixed.

**Symptom:** the factory and `is_llm_configured` case-fold the provider, so `LLM_PROVIDER=OpenAI` built an OpenAI client in the GUI and in the CLI. The dialog compared the raw value to its list, didn't find it, and showed "None (forms only)". An Apply then switched the LLM off.

**Fix:** `ui/settings.load_settings` reads the provider as the factory does (stripped, lower-case).

**Test:** `tests/test_settings.py::test_the_provider_env_var_is_read_as_the_factory_reads_it`.

### 9. The CLI couldn't reach an Ollama app on a Linux host (introduced in 18.2; from the altitude review). Fixed.

**Symptom:** 18.2 gave `ui` `extra_hosts: host.docker.internal:host-gateway`, and the docs name that address for an Ollama app on the host. `script-runner` didn't get it, so on Linux the CLI couldn't resolve the name. Docker Desktop provides it anyway.

**Fix:** `script-runner` has the same `extra_hosts`. Verified live: `getent hosts host.docker.internal` answers inside it.

### Deferred

1. **Cancel is checked once per streamed line** (`llm_adapters.pull_ollama_model`).
   - **Symptom:** while Ollama sends nothing, for example while verifying a multi-GB layer or on a stalled connection, a Cancel waits for the next line, up to the 600 s read timeout, and the GUI's pull lock stays held.
   - **Fix:** let the cancelling thread close the response.
   - **Recommendation:** leave it. Ollama sends status lines throughout a normal pull, and the fix needs the response shared across threads. Do it if a Cancel is seen to hang.
   - **Trigger:** a `DEFERRED` comment in `pull_ollama_model`'s loop, and a Future Work bullet in dev_plan.md.

### Accepted risk

- **A typed endpoint whose name takes more than 1.5 s to resolve reads as "not running"** (`PROBE_TIMEOUT_S` bounds the lookup). This is the bound 18.3 added on purpose; without it every dialog open waited 7 s for Docker's DNS to say `ollama` doesn't exist. A slow first lookup through VPN DNS is usually cached by the time of Re-check. **Recommendation:** leave it.

### Declined

- **The probes run one after another** (up to ~3 s with nothing running). 18.3 measured about 3 s from opening the dialog to the "isn't running" panel, clicks included. Probing in parallel would save about 1.5 s once per open. Not worth the threads.
- **An env key gets copied into the file when "Remember" is ticked.** The field shows the env key, and ticking "Remember" asks for exactly that. `.env` holds the same key in plain text next to it.

## Quality (`/simplify`)

**Fixed:**
- **One Ollama listing** (reuse and altitude). `llm_adapters.list_ollama_models` now serves both the GUI and `OllamaAdapter.list_models`. The two copies already disagreed: the adapter raised on an entry without a name, and returned `[]` for "unreachable". The GUI keeps only its DNS bound around it. The adapter keeps its `[]`.
- **Dead code left by 18.1/18.3:**
  - `OllamaAdapter.pull_url`;
  - `find_ollama`'s `default_endpoint` (its only value was already a candidate). `OLLAMA_CANDIDATES` now uses `ui.settings.DEFAULT_ENDPOINT`, so the two can't drift;
  - the CLI's `output_base` parameter on both clarification helpers, unused since the PyMOL box path left.
- **Derivable state in `SettingsView`:** `_ollama_found` was always `_models is not None`; the `downloaded` flag repeated `finished`.
- **The literal `"none"`** in the dialog is `NO_LLM_PROVIDER`.

**Declined, with reasons:**
- **The factory could own `none`** (return None instead of each caller asking `is_llm_configured`). 18.2 chose explicit callers on purpose, there are two of them, and changing `create_llm_client`'s return type would reach every caller's types for no behaviour change.
- **A `default_provider()` helper** for the two `LLM_PROVIDER` reads. Two copies don't justify it, and finding 8 made the GUI's copy different on purpose.
- **Draw download progress from the page's 1 s tick** rather than the dialog's own 0.5 s timer. The timer runs only during a download and is smoother; both work.
- **`with response:` in `pull_ollama_model`.** The tests' `MagicMock` responses would need `__enter__` wiring in three places for a one-level nesting saving.
- **Keep the last Ollama search across dialog opens.** Searching afresh on open is how a just-started Ollama gets noticed (18.3's design), and Re-check only exists inside the dialog.
- **Delete prompt rule 7** ("deterministic UI choices … are handled elsewhere") in the follow-up extraction prompt. It is stale wording but still a true instruction, and an LLM prompt change wants a live re-validation that R5's scope doesn't include.
- **`_resolves` leaves a daemon thread per failed lookup** (about 7 s each, at most one per Re-check click). Negligible.
- **CLI-side Ollama discovery** (`find_ollama` for the CLI). The CLI takes an explicit endpoint, and finding 9 gives it the host route.

## The three named points

- **Compose profile vs build scripts and installer:** clean apart from findings 6 and 9.
  - `docker_build.sh`/`.ps1` build base, the four services and `ui` ("Successful: 6"), and none of them pulls or waits for Ollama.
  - The installer's `start_services` and its three launchers run `up -d --remove-orphans` without the profile, and its docstring names the opt-in command.
  - `ollama` is in neither `depends_on` list.
  - `config --services` lists 5 services without the profile and adds `ollama` with it.
- **GUI and CLI agreeing on `none`:** the same `NO_LLM_PROVIDER` and `is_llm_configured`. Both treat an empty provider as `none`, and both build no client for it. They disagreed on a capitalized provider (finding 8) and on a missing model (finding 1); both fixed.
- **Settings-file permissions:**
  - **Inside Docker,** the file is 0600 `appuser`. On Docker Desktop's Windows bind mount the mode holds inside Docker: a container running as uid 1001 got "Permission denied" reading a 0600 probe file. `docs/DOCKER_DEPLOYMENT.md` said the mode was "emulated" there; it now says what was measured, and that on the host the file is as private as the repo folder, like `.env`.
  - **Where the key could leak:** `cache/` is git- and docker-ignored. The key is never logged, and never written to `job.log` or to checkpoints (grep of the writers).

## Exception pass

Clean. The catches 18.x added are boundaries:
- the dialog's download and Apply, each shown to the user;
- `_resolves`' `gaierror`/`UnicodeError`, which are lookup results;
- `pull_ollama_model`'s `RequestException`, re-raised as `RuntimeError` with Ollama's message.

R5 adds two input parses: `ValueError` from `urlsplit` (a typo is "unreachable") and `ValueError` from the tags reply's JSON. Both are inputs, not band-aids.

## 18.x tests and R4 triggers

- Every test the 18.1–18.3 entries cite still exists and passes. Four changed their expectations because of R5's fixes, by design:
  - `test_settings_from_form_…` (a hidden key is dropped);
  - `test_settings_the_client_rejects_change_nothing` (now gives a model, so it still tests the client's rejection);
  - `test_the_settings_default_endpoint_also_falls_back` (no `default_endpoint`);
  - the CLI clarification tests (no `output_base`).
- R4's two trigger comments (`ui_nice/sessions_tab.py::SessionsView._draw_rows`, `ui/core/form.py::params_summary`) and the earlier R1/R2 ones are in place and accurate.
- **PyMOL leftovers:** `git grep -i pymol` outside history finds only advice to check output in PyMOL as desktop software, and the vendored 3Dmol.js.

## Test coverage gap

- **Locally:** `tests/test_ui_comparison.py` and `tests/test_ui_results_rendering.py` run under the GUI-tier config (18 passed). They are now part of its command in README, `docs/DOCKER_DEPLOYMENT.md` "GUI Tests", `ui_nice/pytest.ini` and `workplan_creation_guide.md`.
- **CI:** a new `gui-tier` job in `.github/workflows/tests.yml` builds `mcp-docking-base` and `mcp-docking-ui` and runs those files in the image, read-only, as `appuser`. Its exact `docker run` command, run locally against the freshly built image, passed: 212 tests. The job's first run on GitHub is for the owner to check after pushing.
- **A flake seen once:** in the first full GUI-tier run, `test_nicegui_results.py::test_an_unreadable_log_keeps_the_last_one_and_the_tick_going` failed once. It then passed 3 times alone, in a 97-test subset, and in two later full runs. The failure output was lost to a `tail`, so the cause is unknown. If CI shows it again, it needs looking at; R5 doesn't change that code.

## §5 Cross-Service Compatibility

- **Rebuild:** `bash docker_build.sh` built all 6 images ("Successful: 6").
- **Fresh stack:** `docker compose -p af6ad48c… --profile local-llm down`, then `up -d --remove-orphans`, without `-v`. All six containers were healthy within about 15 s. The Ollama volume was kept (`gemma3:4b`, `gemma3:1b` listed).
- **`ui`:**
  - runs as `appuser`;
  - `/health` returns `{"status":"healthy"}`;
  - its env carries the four `MCP_*` URLs and `LLM_ENDPOINT=http://ollama:11434`;
  - `host.docker.internal` resolves.
- **Ports:** all six published ports bind to `127.0.0.1`.
- **MCP tool signatures:** 18.1 removed two services and changed one caller, `example_complete_workflow.py` step 5, to `ligand-preparer.convert_docked_poses`, whose signature is unchanged. No other tool or caller changed in Phase 18.
- **`--check-health`** (in `script-runner`): with `--llm-provider none`, "➖ not configured" and all four services ✅, exit 0. With `ollama` / `gemma3:4b`, "✅ ollama (http://ollama:11434)".
- **Stop integration test** (`tests/test_docking_stop_integration.py`, `-m integration`): 1 passed.
- **CLI workflow:** 6LU7/ATP with local Ollama `gemma3:4b`, "using 4 cpus" → `CPUs: 4`. `job_20261029_070450_7a1a17` completed.

## End-to-end run (headless, port 3000)

**Setup:** Playwright 1.55, headless Chromium 1600 × 1000, on `container:mcp-ui`, against the recreated stack. The script is a scratch driver on `benchmark_gui_browser.NiceGuiDriver`. The owner's settings file was copied aside first.

1. **None (forms only):** Apply → "Settings applied."; **Set up an LLM to chat** shown. "dock ATP into 6lu7" → the setup reply, ending "Commands such as run, use vina, history and save results as \<name\> work without one." (Correctness #2, live).
2. **Forms-only run:** Local Files, the benchmark receptor and 1 ligand (CHEMBL355330), the reference box. Run Docking → `job_20261029_071805_846398` → "Workflow completed successfully!".
3. **OpenAI, model left empty:** Apply → "Could not apply settings: Pick or type a model for OpenAI." (Correctness #1, live).
4. **Remembered key:** model `gpt-r5`, a dummy key, **Remember** ticked, Apply. In the container the file was `600 appuser` with `api_key` and `remember_api_key: true`. Then None → Apply: still `600 appuser`, with no `api_key` and `remember_api_key: false` (Correctness #3, live).
5. **Ollama:** "Ollama found at http://ollama:11434."; `gemma3:4b` picked; Apply hid the setup hint. On a fresh page (the settings loaded from the file), "Download 6lu7 and ATP, prepare and dock" → "Got it! … Protein: 6lu7 - Ligands: ATP - Algorithm: Moldina (PSO) … Still needed: center, box_size".

**Page errors:** none.

**Script bugs, not app bugs:** the first attempt lacked the `playwright` package in the image. The first step-5 wait matched a hidden run-log line ("Protein prepared successfully"), so step 5 was rerun on its own with a wait on the reply.

**Cleanup:**
- the GUI run, the CLI run, the CLI's first attempt (`job_20261029_070425_e300d1`, stopped at its `[Y/n]` with no stdin) and the run's two upload dirs were deleted, so `orchestrator_output/` lists the same entries as before R5;
- the owner's settings file was restored byte for byte, mode 600.

## Verification

- **Hermetic suite** (`docker compose --profile dev run --rm --no-deps script-runner python -m pytest tests/ -m "not integration"`): 1344 passed, 20 skipped (1332 at 18.3).
- **GUI tier, extended** (`docker compose run --rm --no-deps ui python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py tests/test_ui_comparison.py tests/test_ui_results_rendering.py`): 212 passed. That is 189 at 18.3, plus the 18 tests that had run nowhere, plus 5 new.
- **CI's command:** its `docker run`, against the freshly built image, gave 212 passed.
- **Fail-before** (worktree on `6e0ffc4`, removed afterwards):
  - 5 new GUI tests failed;
  - the hermetic correctness tests failed: the model check, the hidden key, the malformed endpoint, the non-Ollama reply and the shared listing;
  - the CLI no-model test hung waiting for input, which is the bug.
- `docker compose config --quiet` passes.
- **Live `mcp-ui`** runs R5's code: it was recreated with the stack after the last code change.
