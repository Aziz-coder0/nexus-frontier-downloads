"""Index the Android and iOS builds attached to a public Nexus release."""
import json
import os
import plistlib
import re
import subprocess
from pathlib import Path
from zipfile import ZipFile

repo = os.environ["GITHUB_REPOSITORY"]
tag = os.environ["RELEASE_TAG"]
if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
    raise SystemExit("Expected a version tag such as v1.3.0")
version = tag[1:]


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def download(release_tag, name, directory):
    directory.mkdir(exist_ok=True)
    gh("release", "download", release_tag, "-R", repo, "--pattern", name, "--dir", str(directory))
    return directory / name


release = json.loads(gh("release", "view", tag, "-R", repo, "--json", "name,body,assets"))
required = (release["name"] or "").casefold().startswith("[required]")
notes = (release["body"] or "").strip()[:1000]
asset_names = {asset["name"] for asset in release["assets"]}
android = f"Nexus-Frontier-{version}.apk"
ios = f"Nexus-Frontier-{version}-sideload.ipa"

history = json.loads(gh("api", f"/repos/{repo}/releases?per_page=30"))
previous = next((item for item in history if item["tag_name"] != tag and not item["draft"] and
                 not item["prerelease"] and any(asset["name"] == "nexus-release.json" for asset in item["assets"])), None)
platforms = {}
if previous:
    prior = download(previous["tag_name"], "nexus-release.json", Path("previous"))
    platforms = json.loads(prior.read_text(encoding="utf-8"))["platforms"]

builds = {}
if android in asset_names:
    apk = download(tag, android, Path("assets"))
    tools = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ["ANDROID_HOME"]) / "build-tools/36.0.0"
    subprocess.run([str(tools / "apksigner"), "verify", str(apk)], check=True)
    badging = subprocess.check_output([str(tools / "aapt"), "dump", "badging", str(apk)], text=True)
    package = re.search(r"package: name='tech\.ccasso\.nexus' versionCode='(\d+)' versionName='([^']+)'", badging)
    if not package or package[2] != version or "application-debuggable" in badging:
        raise SystemExit("Android release version, package, or build type does not match")
    builds["android"] = (int(package[1]), android)
if ios in asset_names:
    ipa = download(tag, ios, Path("assets"))
    with ZipFile(ipa) as archive:
        path = next(name for name in archive.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name))
        info = plistlib.loads(archive.read(path))
    if info["CFBundleIdentifier"] != "tech.ccasso.nexus" or info["CFBundleShortVersionString"] != version:
        raise SystemExit("iOS release version or bundle identifier does not match")
    builds["ios"] = (int(info["CFBundleVersion"]), ios)

if not builds:
    raise SystemExit("Attach the version-matched Android APK or iPhone IPA before publishing.")

for platform, (build, asset) in builds.items():
    old = platforms.get(platform, {})
    if build > old.get("build", 0):
        platforms[platform] = {
            "version": version,
            "build": build,
            "minimum_build": build if required else old.get("minimum_build", 0),
            "download_url": f"https://github.com/{repo}/releases/download/{tag}/{asset}",
            "notes": notes,
        }

manifest = Path("nexus-release.json")
manifest.write_text(json.dumps({"platforms": platforms}, indent=2) + "\n", encoding="utf-8")
gh("release", "upload", tag, str(manifest), "-R", repo)

