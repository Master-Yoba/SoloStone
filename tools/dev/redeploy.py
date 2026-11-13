#!/usr/bin/env python3
"""Redeploy a locally built SoloStone plugin + engine into the installed TEST-HOOKS copy, without uninstall/reinstall.

DEV MACHINE ONLY (Windows). The launcher refuses to start a copy whose plugin/engine DLLs differ from the hashes in
app\\payload.json and install.json (installer/SoloStone.Launcher/WindowsHost.cs RunGame; plugin/InstallerRuntime.cs).
Between our builds only BepInEx/plugins/SoloStone.Plugin.dll and SabberStoneCore.dll change, so this script:

  1. refuses while Hearthstone.exe or SoloStone.exe runs, or when the installed copy is not a hook build;
  2. builds the installer-flavoured hook plugin from --tree (default: the checkout this script is in);
  3. backs up the current DLLs, app\\payload.json and install.json to redeploy-backups\\<utc>;
  4. copies the two DLLs into client\\BepInEx\\plugins and app\\payload\\BepInEx\\plugins;
  5. rewrites pluginSha256 / engineSha256 / files[...] in app\\payload.json and install.json["payload"].

  python redeploy.py                 build this checkout and deploy
  python redeploy.py --tree C:\\x    build another checkout
  python redeploy.py --no-build      deploy the last build again
  python redeploy.py --rollback      restore the newest backup
  python redeploy.py --status        show installed vs built hashes

A real reinstall (uninstall, then any setup .exe) always restores a launcher-verified install.
Not run: the Mono guard (build.ps1/stage.py check that the plugin needs nothing Unity's Mono lacks); a full
stage + setup still runs it. Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL = Path(os.environ.get('LOCALAPPDATA', ''))
DEFAULT_TREE = REPO
DEFAULT_ROOT = LOCAL / 'SoloStone'
DEFAULT_REFERENCES = REPO / 'references'
BUILD = LOCAL / 'SoloStone-dev'  # build output and backups, outside the checkout so nothing here can be committed
WORK = BUILD / 'redeploy-work'
BACKUPS = BUILD / 'redeploy-backups'
DOTNET = os.environ.get('DOTNET') or shutil.which('dotnet') or str(LOCAL / 'Microsoft' / 'dotnet' / 'dotnet.exe')
PLUGIN = 'BepInEx/plugins/SoloStone.Plugin.dll'
ENGINE = 'BepInEx/plugins/SabberStoneCore.dll'
HOOK_MARK = 'enable-test-control'.encode('utf-16le')  # NativeTestControl's flag file; only hook builds carry it


def fail(msg):
    print('REDEPLOY REFUSED: ' + msg, file=sys.stderr)
    sys.exit(1)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def running(image):
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + image, '/NH'], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout
    return image.lower() in out.lower()


def read_json(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def write_json(path, data, indent):
    tmp = Path(str(path) + '.redeploy-tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=indent, separators=(',', ':') if indent is None else (',', ': '))
    os.replace(tmp, path)


def paths(root):
    return {
        'client_plugin': root / 'client' / PLUGIN, 'client_engine': root / 'client' / ENGINE,
        'app_plugin': root / 'app' / 'payload' / PLUGIN, 'app_engine': root / 'app' / 'payload' / ENGINE,
        'payload_json': root / 'app' / 'payload.json', 'install_json': root / 'install.json',
    }


def preflight(root):
    p = paths(root)
    for key, path in p.items():
        if not path.is_file():
            fail('%s is missing (%s); is SoloStone installed at %s?' % (key, path, root))
    for image in ('Hearthstone.exe', 'SoloStone.exe'):
        if running(image):
            fail('%s is running; close the game and the launcher first.' % image)
    if HOOK_MARK not in p['client_plugin'].read_bytes():
        fail('the installed plugin is not a TEST-HOOKS build; this script only redeploys over a hook install.')
    return p


def build(tree, references):
    src = tree / 'plugin' / 'OfflineProbe.cs'
    if not src.is_file():
        fail('%s is not a SoloStone tree' % tree)
    cmd = [DOTNET, 'build', str(tree / 'plugin' / 'OfflineProbe.csproj'), '-c', 'Debug', '-p:EngineSource=true',
           '-p:Installer=true', '-p:TestHooks=true', '-p:NuGetAudit=false', '-nologo', '-v', 'q',
           '--artifacts-path', str(WORK),
           '-p:GameReferences=' + str(references / 'game'), '-p:BepInExReferences=' + str(references / 'bepinex')]
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
    print('building %s ...' % tree)
    r = subprocess.run(cmd, cwd=str(tree), capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        errors = [l for l in (r.stdout + r.stderr).splitlines() if ': error ' in l]
        fail('build failed:\n' + '\n'.join(errors[:30] or (r.stdout + r.stderr).splitlines()[-30:]))
    return built()


def built():
    plugins = list((WORK / 'bin' / 'OfflineProbe').rglob('SoloStone.Plugin.dll'))
    if len(plugins) != 1:
        fail('expected exactly one built SoloStone.Plugin.dll under %s, found %d' % (WORK, len(plugins)))
    plugin = plugins[0]
    engine = plugin.parent / 'SabberStoneCore.dll'
    if not engine.is_file():
        fail('the build did not produce SabberStoneCore.dll next to the plugin')
    if HOOK_MARK not in plugin.read_bytes():
        fail('the built plugin carries no test hooks (was TestHooks=true lost?)')
    return plugin, engine


def backup(p):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    dest = BACKUPS / stamp
    dest.mkdir(parents=True)
    for key in ('client_plugin', 'client_engine', 'payload_json', 'install_json'):
        shutil.copy2(p[key], dest / (key + '-' + p[key].name))
    return dest


def set_hashes(record, plugin_hash, engine_hash):
    record['pluginSha256'] = plugin_hash
    record['engineSha256'] = engine_hash
    record['files'][PLUGIN] = plugin_hash
    record['files'][ENGINE] = engine_hash


def deploy(p, plugin, engine):
    plugin_hash, engine_hash = sha(plugin), sha(engine)
    payload, install = read_json(p['payload_json']), read_json(p['install_json'])
    if plugin_hash == payload.get('pluginSha256') and engine_hash == payload.get('engineSha256'):
        print('already deployed: plugin %s, engine %s' % (plugin_hash[:12], engine_hash[:12]))
        return
    saved = backup(p)
    for src, keys in ((plugin, ('client_plugin', 'app_plugin')), (engine, ('client_engine', 'app_engine'))):
        for key in keys:
            shutil.copy2(src, p[key])
    set_hashes(payload, plugin_hash, engine_hash)
    set_hashes(install['payload'], plugin_hash, engine_hash)
    write_json(p['payload_json'], payload, indent=4)
    write_json(p['install_json'], install, indent=None)
    for key, want in (('client_plugin', plugin_hash), ('app_plugin', plugin_hash), ('client_engine', engine_hash), ('app_engine', engine_hash)):
        if sha(p[key]) != want:
            fail('%s did not land intact; run --rollback' % key)
    print('deployed plugin %s, engine %s (backup: %s)' % (plugin_hash[:12], engine_hash[:12], saved))


def rollback(p):
    stamps = sorted(d for d in BACKUPS.iterdir() if d.is_dir()) if BACKUPS.is_dir() else []
    if not stamps:
        fail('no backups in %s' % BACKUPS)
    src = stamps[-1]
    files = {key: src / (key + '-' + p[key].name) for key in ('client_plugin', 'client_engine', 'payload_json', 'install_json')}
    for f in files.values():
        if not f.is_file():
            fail('backup %s is incomplete (%s)' % (src, f.name))
    for key in ('client_plugin', 'client_engine', 'payload_json', 'install_json'):
        shutil.copy2(files[key], p[key])
    shutil.copy2(files['client_plugin'], p['app_plugin'])
    shutil.copy2(files['client_engine'], p['app_engine'])
    shutil.move(str(src), str(src) + '-restored')
    print('restored %s' % src.name)


def status(p):
    payload = read_json(p['payload_json'])
    print('payload.json  plugin %s engine %s' % (payload['pluginSha256'][:12], payload['engineSha256'][:12]))
    print('client copy   plugin %s engine %s' % (sha(p['client_plugin'])[:12], sha(p['client_engine'])[:12]))
    plugins = list((WORK / 'bin' / 'OfflineProbe').rglob('SoloStone.Plugin.dll'))
    if len(plugins) == 1 and (plugins[0].parent / 'SabberStoneCore.dll').is_file():
        plugin, engine = plugins[0], plugins[0].parent / 'SabberStoneCore.dll'
        print('last build    plugin %s engine %s (%s)' % (sha(plugin)[:12], sha(engine)[:12], plugin.parent))
    else:
        print('last build    none')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tree', type=Path, default=DEFAULT_TREE)
    ap.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    ap.add_argument('--references', type=Path, default=DEFAULT_REFERENCES)
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--rollback', action='store_true')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.status:
        status(paths(a.root))
        return
    p = preflight(a.root)
    if a.rollback:
        rollback(p)
        return
    plugin, engine = built() if a.no_build else build(a.tree, a.references)
    deploy(p, plugin, engine)


if __name__ == '__main__':
    main()
