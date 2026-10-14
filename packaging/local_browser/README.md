# Harmo — local browser release 1

Harmo runs on your PC and opens an English interface in your browser. Processing
and data storage stay on that PC. No public server or account is needed. This
distribution replaces the Mac-specific application as the primary preview.

## First use

1. Extract this entire ZIP into a permanent, writable folder. Do not run it from
   inside an archive, a temporary downloads preview, or a read-only folder.
2. Run **Setup** once. It downloads a checksum-pinned uv environment manager,
   a managed Python 3.12 runtime, and the locked Python dependencies. It does not
   replace your system Python, edit shell profiles, or require administrator access.
3. Run **Start**. Your default browser opens Harmo.
4. Choose **Try a demonstration**, or **Open project…** and select a `.harmo` file.
   File selection and transfer stay between your browser and the service on the
   same PC. A prepared project must be shared separately; real study data is not
   included with the software.
5. Review evidence, evaluate conditions, run supported diagnostic processing and
   save harmonized data. Review choices are not scientific approval by themselves.
6. Return to **Projects → Quit**. Closing the browser tab alone does not stop the service.

| OS | Setup | Start | Environment check |
|---|---|---|---|
| macOS (Apple Silicon / Intel) | `Setup.command` | `Start.command` | `Check.command` |
| Windows x64 | `Setup.cmd` | `Start.cmd` | `Check.cmd` |
| Linux x64, glibc | `sh bootstrap.sh setup` | `sh bootstrap.sh start` | `sh bootstrap.sh check` |

The launchers open a small terminal window; leave it open until quitting Harmo.
On macOS, if an archive extractor did not retain executable permissions, run
`sh bootstrap.sh setup` / `sh bootstrap.sh start` from that folder. Windows must
permit locally reviewed PowerShell scripts under your institution's policy. These
launchers do **not** disable execution policy, Gatekeeper, TLS verification or other
OS protections. Ask your administrator if the download/script is blocked.

The scripts cover these OS/CPU combinations; that is not equivalent to verified
compatibility on every machine. macOS is tested locally. Windows and Linux need
native acceptance testing. Windows ARM64, Linux ARM64 and Alpine/musl are outside
this release. The pinned C3D dependency currently lacks a usable Linux ARM64
wheel; setup does not silently attempt a compiler-based source build.

## Network and dependencies

Setup requires HTTPS access to GitHub releases and PyPI. uv is fixed at **0.12.15**,
and its downloaded binary archive is verified before extraction. Python package
versions and artifacts are recorded in `uv.lock`. The wheel and support files are
checked against `RELEASE.json` at setup/start. Check the ZIP checksum supplied by
a trusted distributor; a manifest inside an archive does not authenticate its sender.

Normal Start/Check does not install or update dependencies. Setup's processing
test uses generated signals and does not download a study. uv manages Python using
[python-build-standalone](https://docs.astral.sh/uv/guides/install-python/);
see [uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/).
Exact installed versions and the Python version are saved in `.runtime/installed.json`.

The Python package includes the existing acquisition CLI (`.venv/bin/harmo`, or
`.venv\Scripts\harmo.exe`). However, the current GUI starts from **prepared projects**:
it does not yet expose the URL acquisition workflow. R/PhysioIO and optional local
LLM runtimes/models are not automatically installed. **Check** reports whether Rscript
is on PATH, but that does not certify the PhysioIO packages. Do not interpret a
successful review setup as validation of all acquisition commands.

## Storage, updates and recovery

Study data is separate from this application/environment folder:

- macOS: `~/Library/Application Support/Harmo` (same location as the previous Mac app).
- Windows: `%LOCALAPPDATA%\Harmo`.
- Linux: `$XDG_DATA_HOME/harmo`, or `~/.local/share/harmo`.

To update, **Quit**, extract a new version into a new folder, run its Setup and
then Start. Keep the previous release for rollback. Do not overwrite its `.venv`:
environments contain absolute paths and cannot be moved. To move the software,
extract a fresh release and run Setup there. No automatic remote updater is used.

Back up the study-data folder after quitting. Use project export/import to change
PCs: a raw folder copy preserves absolute references and is not a portable project.
An export transfers sources and prepared records, **not** earlier user decisions
or saved output history; imported decisions start unreviewed. Download review
decisions before leaving/reloading a page. There is no automatic draft recovery.
New sessions do not silently resume unfinished computation; saved collection
revisions remain accessible.

The shared storage schema is unchanged in this release. Starting the old app with
the same data folder while a new release is running reuses the live session on Mac.
Do not remove the data folder when uninstalling the software unless you explicitly
intend to delete your study data. Check data licensing/privacy before exporting.

## Scope

The GUI still uses the existing bounded plan: up to two records, with timing and
amplitude evaluated separately and unresolved conditions held. This distribution
work does not expand scientific qualification or the manuscript's validation claims.
The synthetic example is for installation tests, not physiological evidence.
