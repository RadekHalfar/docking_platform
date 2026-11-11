# Getting Started: From Download to Your First Docking

This guide is for scientists who want to **use** the platform, not program
it. It walks you from an empty computer to a finished docking run in the
web interface, one step at a time. You will type a few commands into a
terminal window, and each one is given here exactly. You don't need any
programming knowledge.

The steps are written for **Windows 10/11**. Notes for macOS and Linux are
at the end of each step where something differs.

**Time needed:** about 30–60 minutes the first time, mostly waiting for
downloads. Afterwards, starting the platform takes under a minute.

---

## What you need

- A computer with **at least 8 GB RAM** (16 GB recommended) and **10 GB
  free disk space**.
- An internet connection (to download the software, and the protein and
  ligand structures from the RCSB Protein Data Bank).
- Administrator rights on the computer, to install Docker Desktop.
- **No API key and no account** are needed for docking. An API key is
  only needed if you want the optional chat assistant that understands
  plain-English requests (see [Step 8](#step-8-optional-the-chat-assistant-llm)).

### What is Docker, and why do I need it?

The platform is built from several programs (structure download, protein and
ligand preparation with RDKit/Meeko/OpenBabel, the Moldina docking engine, the
web interface). Instead of installing each one, everything comes packaged in
**Docker containers**: small self-contained environments that Docker Desktop
runs for you. You install Docker Desktop once; it takes care of the rest.

---

## Step 1: Install Docker Desktop

1. Go to <https://www.docker.com/products/docker-desktop/> and download
   **Docker Desktop for Windows**.
2. Run the installer. When asked, keep **"Use WSL 2 instead of Hyper-V"**
   ticked (the recommended option).
3. Restart the computer when the installer asks you to.
4. Start **Docker Desktop** from the Start menu. Accept the service
   agreement. You can skip signing in and skip the survey; an account is not
   needed.
5. Wait until Docker Desktop says **"Engine running"** (bottom-left corner of
   its window). The first start can take a few minutes.

> **Docker Desktop must be running whenever you use the platform.** If you
> close it or restart the computer, start it again first.

If Docker Desktop reports that **virtualization is disabled** or **WSL 2 is
missing**, see [Troubleshooting](#troubleshooting).

**macOS:** download Docker Desktop for Mac (choose Apple chip or Intel chip),
drag it to Applications, start it. See also the note on
[Apple-chip Macs](#a-note-on-macs-with-an-apple-chip-m1m4).
**Linux:** install Docker Engine and the Compose plugin following
<https://docs.docker.com/engine/install/>.

---

## Step 2: Download the platform

1. Open the project's GitHub page in your browser.
2. Click the green **Code** button, then **Download ZIP**.
3. Unzip the downloaded file to a simple location, for example
   `C:\docking_platform`. Avoid folders synced by OneDrive or similar, and
   avoid very long paths.

After unzipping, the folder should contain files such as `README.md`,
`docker-compose.yml` and `docker_build.ps1`. If you instead see a single
folder (e.g. `docking_platform-main`), open it — that inner folder is the
project folder used in the rest of this guide.

*(If you know git, `git clone <repository URL>` does the same thing and makes
updating easier later.)*

---

## Step 3: Open a terminal in the project folder

1. Open the project folder in **File Explorer**.
2. Right-click an empty area inside the folder and choose **Open in
   Terminal**. (On Windows 10: hold **Shift**, right-click, and choose
   **Open PowerShell window here**.)

A window with a text prompt opens, ending in the folder's path, e.g.
`PS C:\docking_platform>`. This is where you type the commands below: type
(or paste) a command and press **Enter**.

To check Docker is reachable, type:

```powershell
docker --version
```

It should print something like `Docker version 27...`. If it says the
command is not recognised, Docker Desktop isn't installed or running (see
Step 1).

**macOS:** open the **Terminal** app, type `cd ` (with a space), drag the
project folder into the window and press Enter.
**Linux:** open a terminal and `cd` into the project folder.

---

## Step 4: Build the platform (once)

In the terminal, type:

```powershell
powershell -ExecutionPolicy Bypass -File .\docker_build.ps1
```

This builds the platform's containers. It downloads about 2 GB and takes
**about 5–15 minutes** the first time. Lots of text scrolls past; that is
normal. It is finished when you see:

```
🎉 All images built successfully!
```

If it ends with `❌ Build failed`, scroll up to the first red error and see
[Troubleshooting](#troubleshooting).

You only need to repeat this step after [updating](#updating-to-a-new-version)
the platform.

**macOS / Linux:** type `./docker_build.sh` instead.

---

## Step 5: Start the platform

In the same terminal, type:

```powershell
docker compose up -d
```

After about 30 seconds, check that everything is up:

```powershell
docker compose ps
```

Each line should show **`Up`** and, after a short while, **`(healthy)`**. If
some still show `(health: starting)`, wait a few seconds and run the command
again.

You can now close the terminal — the platform keeps running in the
background (inside Docker Desktop).

---

## Step 6: Open the web interface

Open your web browser and go to:

**<http://localhost:3000>**

You'll see the **Molecular Docking Platform** page:

- three tabs: **Setup** (enter what to dock), **Results** (see and download
  the results) and **Sessions** (your past runs);
- a **gear icon** (top right) for **Settings**;
- a **chat icon** (top right) that shows or hides the **Chat** panel.

The platform runs only on your own computer: `localhost` means "this
machine", and nothing is reachable from other computers.

---

## Step 7: Your first docking run

This example docks **ATP** into the SARS-CoV-2 main protease (PDB entry
**6LU7**), with the docking box placed at the active site where the
co-crystallised inhibitor binds. Everything is downloaded automatically
from the RCSB Protein Data Bank.

### 7.1 Enter the protein and ligand

On the **Setup** tab:

1. Under **Input & Parameters**, select **RCSB IDs**.
2. In **Protein ID (PDB)**, type `6lu7`.
3. In **Ligand IDs (comma-separated)**, type `ATP`.

Ligands are given by their **RCSB chemical component ID** — the three-letter
code shown on the ligand's page at <https://www.rcsb.org/> (e.g. `ATP`,
`HEM`, `NAD`). Several ligands are separated by commas: `ATP, ADP, HEM`.

### 7.2 Set the docking box

The **docking box** is the region of the protein where the program searches
for binding poses. It is given by a **center** (x, y, z, in ångström, in the
protein file's coordinate system) and a **size** along each axis.

For this example, fill in the **Docking Box** fields:

| Center X | Center Y | Center Z | Box X | Box Y | Box Z |
|---------:|---------:|---------:|------:|------:|------:|
| -10.8    | 12.5     | 68.9     | 22    | 22    | 22    |

For your own proteins there are three other ways to set the box, all of
which fill these same fields:

- **Click it visually:** on the right of the Setup tab, click **Load
  receptor for box picking**. When the 3D view of the protein appears, click
  an atom in the binding site to put the box center there, and use the
  **X − / X +**, **Y − / Y +**, **Z − / Z +** buttons to make the box smaller
  or larger (1 Å per click). Residues inside the box are highlighted.
- **Whole protein ("blind docking"):** type `set up box center as protein
  center`, then `look across all protein`, into the chat. This covers the
  entire protein. It works without the chat assistant, but it is slower and
  less precise than a box around a known site.
- **From literature or another program:** type the coordinates in.

More on choosing a box: [DOCKING_PARAMETERS.md](DOCKING_PARAMETERS.md#determining-docking-box-parameters).

### 7.3 Run the docking

1. Optionally give the run a **Session name** (e.g. `6lu7 ATP test`), so
   you can find it later.
2. Click **Run Docking**.

A progress card appears under the buttons ("N / M ligands docked"), with a
**Stop** button. The platform now downloads the structures, prepares the
protein and the ligand (adds hydrogens, assigns charges, converts to
PDBQT), docks, and converts the poses. This example takes **about a
minute**.

When it finishes, the **Results** tab opens by itself.

### 7.4 Read the results

The **Results** tab shows:

- a **bar chart of binding affinities** — the predicted binding energy of
  each ligand's best pose in **kcal/mol**. **More negative means stronger
  predicted binding.**
- a **3D viewer** with the protein (cartoon) and the docked ligand (sticks;
  green = best affinity, red = worst). Drag to rotate, scroll to zoom.
- a **Ligands** table next to the viewer: click a ligand to list its poses,
  then click a pose to show it.
- **Docked Poses** downloads: choose a **Download format** — `pdbqt`, `pdb`,
  `sdf` or `mol2` — and download each ligand's poses for use in PyMOL,
  Chimera, Maestro or other tools.
- **Session Log**: a readable record of everything the run did, useful if
  something went wrong.

The same files are also saved on your disk, in the project folder under
`orchestrator_output\job_<date>_<time>_<code>\` (docked poses in
`docking_results\`).

### 7.5 Try changes

- **Change a setting and click Run Docking again.** The platform reruns
  only what's needed (e.g. a new box redoes docking but not the
  preparation) and asks you to **Confirm redock** first.
- **Choose a different search:** open **Advanced** and pick **Docking
  algorithm** → **Moldina (PSO)** (default; seconds per ligand) or **Vina
  (Monte Carlo)** (minutes per ligand, more thorough search). Advanced also
  has pH, exhaustiveness and parallel docking settings — see
  [DOCKING_PARAMETERS.md](DOCKING_PARAMETERS.md).
- **Keep a result before changing things:** on the Results tab, use **Save
  results as…** and give it a name.
- **Go back:** the **Sessions** tab lists every past run (**Load** to reopen
  one, **Rename**, **Delete**), shows the revision history of the current
  session (**Restore revision**), and can **compare** several runs: tick two
  or more and click **Compare selected sessions**.
- **Start over:** **New Session** keeps your entered values for a new,
  separate run; **Reset** clears the form.

### Docking your own files

Instead of **RCSB IDs**, choose:

- **Local Files** — upload your own protein (`.pdb`, `.cif`, `.mmcif`) and
  one or more ligand files (`.sdf`, `.mol`, `.mol2`, or already-prepared
  `.pdbqt`). Drag the files onto the upload areas. A multi-molecule `.sdf`
  is split into one ligand per molecule.
- **Batch Processing** — a protein from the PDB by its ID, plus many ligand
  files dropped at once (a screening set).

Then set the docking box and click **Run Docking** as above. Tips on
preparing input files: [PROTEIN_PREPARATION.md](PROTEIN_PREPARATION.md) and
[LIGAND_PREPARATION.md](LIGAND_PREPARATION.md).

Full description of every control: [GUI_GUIDE.md](GUI_GUIDE.md).

---

## Step 8 (optional): The chat assistant (LLM)

Everything above works **without** an AI assistant. The **Chat** panel
already understands fixed commands without one, such as `run`, `use vina`,
`show history`, `save results as trial_a`, `set up box center as protein
center`.

With a language model (LLM) connected, you can also type free-text requests
such as *"Download 6lu7 and dock ATP and HEM with center -10.8 12.5 68.9 box
22 22 22"*, and the assistant fills in the form for you. You can then check
it and say `run` (or click **Run Docking**). The assistant only fills in the
form; it never invents coordinates.

You have two options:

### Option A: a free local model (no account, no key)

The model runs on your own computer, and nothing leaves it. It needs about
4–8 GB of extra disk space and works best with 16 GB RAM.

1. Install the **Ollama** app from <https://ollama.com/download> and start
   it. Ollama uses your graphics card if you have one.
2. In the platform, click the **gear icon** (Settings) and choose **LLM
   Provider** → **Ollama (local)**. The dialog should say **"Ollama found
   at …"**.
3. Under **Download a model**, pick **`gemma3:4b`** (3.3 GB) and click
   **Download**. Wait for the progress bar to finish.
4. Select that model and click **Apply settings**.

*(Alternative without installing the Ollama app: in the project's terminal,
run `docker compose --profile local-llm up -d ollama`, which downloads a
~4.8 GB container that runs on the CPU only, then continue with step 2.)*

### Option B: a cloud model (needs an API key, paid per use)

An **API key** is a password-like code that lets the platform use a
company's model on your account. Requests are billed to that account (a
short request typically costs a fraction of a cent), and the text of your
requests is sent to that company.

Get a key from one of these providers:

| Provider | Where to get a key | Model name to enter |
|---|---|---|
| **OpenAI** | <https://platform.openai.com/api-keys> (add a payment method under Billing first) | `gpt-4o-mini` |
| **Anthropic** | <https://console.anthropic.com/> → **API Keys** (add credit under Billing first) | `claude-haiku-4-5-20251001` |
| **MetaCentrum (e-INFRA CZ)** | for Czech academic users; see the e-INFRA CZ / MetaCentrum documentation (<https://docs.metacentrum.cz/>) for how to obtain an API token | one of the models the service lists |

Copy the key when it is shown — most providers show it only once. Then:

1. In the platform, click the **gear icon** (Settings).
2. Choose the **LLM Provider** (e.g. **OpenAI**).
3. Type the **model name** from the table into **Model**.
4. Paste your key into **API Key**.
5. If you want the key remembered after the computer restarts, tick
   **Remember API key on this computer**. Otherwise you'll paste it again
   after each restart.
6. Click **Apply settings**.

**About keeping the key safe:** a remembered key is stored **in plain text**
in the project folder, in `cache\.docking_platform_settings.json`. Only tick
"Remember" on a computer that only you use. Never share that file or post
your key anywhere. If a key leaks, delete it on the provider's website and
create a new one.

*(Advanced alternative: copy the file `.env.example` to `.env` in the
project folder, edit the `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY` lines,
and run `docker compose up -d` again. Settings made in the web interface
take precedence over `.env`.)*

More detail: [LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md).

---

## Everyday use

### Starting the platform again (after a restart)

1. Start **Docker Desktop** and wait for **"Engine running"**.
2. Open a terminal in the project folder (Step 3) and type:

   ```powershell
   docker compose up -d
   ```

3. Open <http://localhost:3000>.

There's no need to build again (Step 4).

### Stopping the platform

In a terminal in the project folder:

```powershell
docker compose down
```

This stops the platform and frees the memory it uses. **Your results and
settings are kept** — they live in the project folder, not inside the
containers. Quitting Docker Desktop also stops everything.

### Where your data is

Everything stays in the project folder:

| Folder | Contents |
|---|---|
| `orchestrator_output\job_…\` | one folder per docking run: inputs, prepared structures, docked poses (`docking_results\`), the log, saved result bundles (`saved_results\`) |
| `orchestrator_output\uploads\` | files you uploaded in the web interface |
| `cache\` | downloaded PDB/ligand files and your Settings |

To free space, delete old runs from the **Sessions** tab, or delete their
`job_…` folders.

### Updating to a new version

1. Stop the platform (`docker compose down`).
2. Download the new ZIP and unzip it to a **new** folder.
3. Move the `orchestrator_output` and `cache` folders from the old folder
   into the new one (to keep your runs and settings).
4. In a terminal in the new folder, repeat **Step 4** (build) and **Step 5**
   (start).

*(With git: `git pull` in the project folder, then Steps 4 and 5.)*

Check `CHANGELOG.md` for what changed.

---

## Troubleshooting

**Docker Desktop says virtualization is not enabled.**
Virtualization must be switched on in the computer's BIOS/UEFI settings
(often called *Intel VT-x*, *AMD-V* or *SVM*). On a managed work computer,
ask your IT department.

**Docker Desktop asks to install or update WSL.**
Follow its instructions, or open a terminal and run `wsl --install`, then
restart the computer.

**`docker` is not recognised as a command.**
Docker Desktop is not installed, or the terminal was opened before
installing it. Close the terminal, make sure Docker Desktop is running, and
open a new terminal.

**`docker_build.ps1 cannot be loaded because running scripts is disabled`.**
Use the exact command from Step 4 (`powershell -ExecutionPolicy Bypass -File
.\docker_build.ps1`), which allows just this one script.

**The build fails with a network or "timeout" error.**
Check your internet connection (and, on an institute network, whether a
proxy is required — Docker Desktop's Settings → Resources → Proxies) and
run the build command again; it continues where it stopped.

**`http://localhost:3000` doesn't open.**
Run `docker compose ps` in the project folder. If nothing is listed, run
`docker compose up -d`. If the `ui` line isn't `(healthy)` after a minute,
run `docker compose logs ui` and look at the last lines. If another program
already uses port 3000, the start command reports *port is already
allocated* — close that program.

**A run fails.**
Open the **Session Log** on the Results tab (failed runs are listed under
**Failed Sessions** on the Sessions tab; **Load** opens them). Common causes:
a mistyped PDB or ligand ID (check it on <https://www.rcsb.org/>), a ligand
name instead of its three-letter component ID, or a docking box that is
missing or lies outside the protein.

**A ligand is missing from the results.**
A ligand that couldn't be downloaded is listed as **Not docked (could not be
downloaded)**, with the reason, in the chat when the run finishes and at the
top of the Results tab. "Not found on RCSB" usually means a typo: check the
three-letter code on <https://www.rcsb.org/> and run again.

**The chat answers "Chat needs an LLM…".**
No language model is set up. That's fine for docking, or see
[Step 8](#step-8-optional-the-chat-assistant-llm).

**Settings says "Ollama isn't running" or "No models downloaded yet".**
Start the Ollama app (or the Ollama container), click **Re-check**, then
download a model in the same dialog.

**The computer becomes slow during docking.**
Docking uses up to 10 CPU cores by default. Use fewer ligands at a time, or
leave **Parallel ligand docking** (under Advanced) switched off.

More technical help: [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md#troubleshooting).

### A note on Macs with an Apple chip (M1–M4)

The bundled docking engine (Moldina) is a program for Intel/AMD
processors. On Apple-chip Macs the platform has **not been tested**;
docking may fail or need Docker Desktop's Rosetta emulation (Settings →
General → "Use Rosetta for x86_64/amd64 emulation on Apple Silicon"). Intel
Macs, Windows and Linux PCs are the supported setups.

---

## Glossary

- **PDB ID** — four-character code of an experimental structure in the
  Protein Data Bank, e.g. `6LU7`.
- **Ligand component ID** — the RCSB code of a small molecule, e.g. `ATP`.
- **PDBQT** — the PDB-like file format the docking engine reads and writes;
  it adds partial charges and atom types.
- **Docking box** — the search region (center + size, in Å).
- **Pose** — one predicted position and conformation of the ligand in the
  protein. Each ligand gets several poses, ranked by affinity.
- **Affinity** — the docking program's estimated binding free energy, in
  kcal/mol; more negative is better. It is a ranking score, not a measured
  value.
- **PSO / Monte Carlo** — the two search methods available: Moldina's
  Particle Swarm Optimization (fast) and AutoDock Vina's Monte Carlo search
  (slower, more thorough). See [MOLDINA_DOCKING.md](MOLDINA_DOCKING.md).
- **LLM** — large language model, the optional chat assistant.
