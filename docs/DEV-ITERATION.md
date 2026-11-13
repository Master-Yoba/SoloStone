# Fast iteration on Windows: redeploy without reinstalling

Building the installer and running setup after every plugin change takes minutes. `tools/dev/redeploy.py` skips that:
it builds the plugin and engine from your checkout and swaps the two DLLs into an already installed **test-hooks** copy.
It is for development on your own machine. It is not part of any release.

## What it changes

The launcher refuses to start a copy whose plugin or engine DLL differs from the hashes it recorded at install time.
Between your builds only two files change, `BepInEx\plugins\SoloStone.Plugin.dll` and `SabberStoneCore.dll`, so the
script:

1. refuses to run while `Hearthstone.exe` or `SoloStone.exe` is running, or if the installed copy is not a hook build;
2. builds the hook plugin from your checkout;
3. backs up the current DLLs, `app\payload.json` and `install.json`;
4. copies the two DLLs into `client\BepInEx\plugins` and `app\payload\BepInEx\plugins`;
5. rewrites their hashes in `app\payload.json` and `install.json`.

Because it rewrites the launcher's integrity records, **use it only on a copy you installed from a hook build**.
Uninstalling and running any setup again always gives you a launcher-verified install.

## One-time setup

You need the .NET SDK from `global.json`, Python 3.10 or newer, Git, Inno Setup (see [BUILDING.md](BUILDING.md)) and the
reference libraries in `references\` ([BUILDING.md](BUILDING.md), section 1). Then install a hook build once.
Use `-Staged` on Windows: the plain `build.ps1` path runs launcher tests that use a Unix-only call and fail here.

```powershell
python installer\stage.py --hooks --out $env:TEMP\solostone-stage --dotnet (Get-Command dotnet).Source --references references
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1 -HookBuild -Version 0.1.0 `
    -Staged $env:TEMP\solostone-stage -ClientReference "<your Hearthstone folder>"
```

Run the resulting `SoloStone-TEST-HOOKS-Setup-0.1.0.exe` from `installer\out`. Stage downloads need Python to find
certificates; if it fails with an SSL error, set `SSL_CERT_FILE` to the `certifi` bundle (`python -m certifi`).

If your Windows account is in the Administrators group (even as a UAC-filtered admin), the plugin's startup guard may
refuse to run the installed copy. That guard is a release-safety check. Do not weaken it in a commit or a pull request.
If you need to get past it on your own machine, do it as a local change in a checkout you never push, and build from
that checkout with `--tree`.

## Every change after that

Close the game and the launcher, then:

```powershell
python tools\dev\redeploy.py            # build this checkout and deploy
```

Start the game and wait for the plugin to load:

```powershell
& "$env:LOCALAPPDATA\SoloStone\app\SoloStone.exe" launch --quiet
```

A fresh `%LOCALAPPDATA%\SoloStone\...\reports\plugin-loaded.txt` means the new build is running.

| Command | Does |
|---|---|
| `python tools\dev\redeploy.py` | Build this checkout and deploy |
| `python tools\dev\redeploy.py --tree <folder>` | Build another checkout |
| `python tools\dev\redeploy.py --no-build` | Deploy the last build again |
| `python tools\dev\redeploy.py --status` | Compare the hashes in `payload.json`, the client copy and the last build |
| `python tools\dev\redeploy.py --rollback` | Restore the newest backup |

Options `--root` (default `%LOCALAPPDATA%\SoloStone`) and `--references` (default `references\` in the checkout) move the
install and reference folders. Build output and backups go to `%LOCALAPPDATA%\SoloStone-dev`, outside the checkout.
`--rollback` puts an older build back, so check `--status` before you relaunch.

## When you still have to reinstall

- You changed the launcher or installer (`installer\`). Setup refuses a copy whose `app\SoloStone.exe` hash differs, so
  uninstall, rebuild as above and run setup again.
- You want the Mono compatibility check, which `redeploy.py` skips. A full stage and setup still runs it.

## What not to commit

Nothing built by this script, no client file and no path from your machine ([AGENTS.md](../AGENTS.md)). Run
`python3 tools/release_scan.py --source . --skip-git --allow-no-private-config --strict --trust-public-rules --public-rules tools/release_scan.public.json`
before you push.
