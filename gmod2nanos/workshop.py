"""Récupération des addons depuis le Steam Workshop de Garry's Mod (appid 4000)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

GMOD_APPID = 4000
API = "https://api.steampowered.com/ISteamRemoteStorage"
MAX_COLLECTION_ITEMS = int(os.environ.get("GMOD2NANOS_MAX_COLLECTION", "50"))


@dataclass
class WorkshopItem:
    id: str
    title: str
    file_url: str = ""
    file_size: int = 0
    preview_url: str = ""
    creator: str = ""
    app_id: int = 0
    description: str = ""
    time_updated: int = 0


def parse_id(text: str) -> str:
    text = text.strip()
    m = re.search(r"[?&]id=(\d+)", text) or re.fullmatch(r"(\d{3,20})", text)
    if not m:
        raise ValueError("lien ou ID Workshop invalide (ex. https://steamcommunity.com/sharedfiles/filedetails/?id=123456)")
    return m.group(1)


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True, headers={"User-Agent": "gmod2nanos"})


def _post(client: httpx.Client | None, url: str, data: dict) -> httpx.Response:
    if client is None:
        with _client() as c:
            return _post(c, url, data)
    try:
        r = client.post(url, data=data)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"impossible de contacter l'API Steam ({e})") from e
    return r


def get_details(ids: list[str], client: httpx.Client | None = None) -> list[WorkshopItem]:
    data = {"itemcount": len(ids)} | {f"publishedfileids[{i}]": v for i, v in enumerate(ids)}
    r = _post(client, f"{API}/GetPublishedFileDetails/v1/", data)
    items = []
    for d in r.json().get("response", {}).get("publishedfiledetails", []):
        if d.get("result") != 1:
            raise ValueError(f"objet Workshop {d.get('publishedfileid')} introuvable ou privé")
        items.append(
            WorkshopItem(
                id=str(d["publishedfileid"]),
                title=d.get("title", ""),
                file_url=d.get("file_url", "") or "",
                file_size=int(d.get("file_size", 0) or 0),
                preview_url=d.get("preview_url", "") or "",
                creator=str(d.get("creator", "")),
                app_id=int(d.get("consumer_app_id", 0) or 0),
                description=(d.get("description", "") or "")[:500],
                time_updated=int(d.get("time_updated", 0) or 0),
            )
        )
    return items


def get_collection_children(item_id: str, client: httpx.Client | None = None) -> list[str]:
    data = {"collectioncount": 1, "publishedfileids[0]": item_id}
    r = _post(client, f"{API}/GetCollectionDetails/v1/", data)
    det = r.json().get("response", {}).get("collectiondetails", [{}])[0]
    if det.get("result") != 1:
        return []
    return [str(ch["publishedfileid"]) for ch in det.get("children", []) if ch.get("filetype", 0) == 0]


def resolve(text: str) -> tuple[WorkshopItem, list[WorkshopItem]]:
    """Retourne l'objet demandé et la liste des addons à télécharger (plusieurs si c'est une collection)."""
    item_id = parse_id(text)
    with _client() as c:
        root = get_details([item_id], c)[0]
        children = get_collection_children(item_id, c)
        if children:
            children = children[:MAX_COLLECTION_ITEMS]
            items = get_details(children, c)
        else:
            items = [root]
    for it in items:
        if it.app_id and it.app_id != GMOD_APPID:
            raise ValueError(f"« {it.title} » n'est pas un addon Garry's Mod (appid {it.app_id})")
    return root, items


def _cache_dir() -> Path:
    d = TOOLS_DIR / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trim_cache(keep: Path) -> None:
    """Supprime les plus anciens addons du cache au-delà de CACHE_MAX octets."""
    entries = sorted(_cache_dir().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for p in entries:
        size = p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        total += size
        if total > CACHE_MAX and p != keep:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def download(item: WorkshopItem, dest: Path, max_size: int, log=lambda m: None, progress=lambda m: None) -> Path:
    """Télécharge l'addon (ou le reprend du cache). Retourne un .gma, ou un dossier déjà extrait."""
    with _locks_guard:
        lock = _item_locks.setdefault(item.id, threading.Lock())
    with lock:  # deux conversions du même addon : la seconde attend et reprend le cache
        return _download(item, dest, max_size, log, progress)


def _download(item: WorkshopItem, dest: Path, max_size: int, log, progress) -> Path:
    if item.file_size and item.file_size > max_size:
        raise ValueError(f"« {item.title} » est trop volumineux ({item.file_size // 1_000_000} Mo)")
    cached = _cache_dir() / f"{item.id}_{item.time_updated}"
    for cand in (cached.with_suffix(".gma"), cached):
        if CACHE_MAX > 0 and cand.exists():
            log("addon déjà téléchargé : repris du cache")
            os.utime(cand)
            return cand
    if item.file_url:
        result = _http_download(item, cached.with_suffix(".part"), max_size, progress)
    else:
        with _steamcmd_lock:  # SteamCMD ne supporte pas deux instances sur le même dossier
            result = _steamcmd(item, log, progress)
    target = cached.with_suffix(".gma") if result.is_file() else cached
    shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
    shutil.move(str(result), str(target))
    if CACHE_MAX > 0:
        _trim_cache(target)
    else:  # cache désactivé : on garde le fichier dans le dossier de la conversion
        dest.mkdir(parents=True, exist_ok=True)
        final = dest / target.name
        shutil.move(str(target), str(final))
        return final
    return target


def _http_download(item: WorkshopItem, out: Path, max_size: int, progress) -> Path:
    total = item.file_size or 0
    with _client() as c, c.stream("GET", item.file_url) as r:
        r.raise_for_status()
        size = 0
        with open(out, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                size += len(chunk)
                if size > max_size:
                    raise ValueError("téléchargement trop volumineux")
                f.write(chunk)
                progress(_progress_text(size, total))
    return out


def _progress_text(done: int, total: int) -> str:
    if total:
        return f"Téléchargement : {done / 1e6:.0f} / {total / 1e6:.0f} Mo ({min(100, done * 100 // total)} %)"
    return f"Téléchargement : {done / 1e6:.0f} Mo"


def _dir_size(*dirs: Path) -> int:
    total = 0
    for d in dirs:
        if d.exists():
            for f in d.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass
    return total


STEAMCMD_URLS = {
    "win32": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
    "linux": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
    "darwin": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz",
}
TOOLS_DIR = Path(os.environ.get("GMOD2NANOS_TOOLS", Path.home() / ".gmod2nanos"))
_install_lock = threading.Lock()
_steamcmd_lock = threading.Lock()
_locks_guard = threading.Lock()
_item_locks: dict[str, threading.Lock] = {}
CACHE_MAX = int(os.environ.get("GMOD2NANOS_CACHE_BYTES", str(10 * 1024**3)))


def find_or_install_steamcmd(log=lambda m: None) -> str:
    """Trouve SteamCMD, ou le télécharge automatiquement dans TOOLS_DIR la première fois."""
    exe = os.environ.get("STEAMCMD") or shutil.which("steamcmd") or shutil.which("steamcmd.sh")
    if exe:
        return exe
    platform = "win32" if sys.platform.startswith("win") else "darwin" if sys.platform == "darwin" else "linux"
    folder = TOOLS_DIR / "steamcmd"
    local = folder / ("steamcmd.exe" if platform == "win32" else "steamcmd.sh")
    with _install_lock:
        if local.exists():
            return str(local)
        log("SteamCMD absent : téléchargement automatique (une seule fois)…")
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / Path(STEAMCMD_URLS[platform]).name
        with _client() as c, c.stream("GET", STEAMCMD_URLS[platform]) as r:
            r.raise_for_status()
            with open(archive, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(folder)
        else:
            with tarfile.open(archive) as t:
                try:
                    t.extractall(folder, filter="data")
                except TypeError:  # Python < 3.11.4
                    t.extractall(folder)
        archive.unlink()
        if platform != "win32":
            local.chmod(0o755)
        log("SteamCMD installé. Sa première mise à jour peut prendre une ou deux minutes.")
        return str(local)


def _steamcmd(item: WorkshopItem, log=lambda m: None, progress=lambda m: None) -> Path:
    exe = find_or_install_steamcmd(log)
    install = (TOOLS_DIR / "workshop").resolve()
    ws = install / "steamapps" / "workshop"
    content = ws / "content" / str(GMOD_APPID) / item.id
    shutil.rmtree(content, ignore_errors=True)
    # pas de "validate" : il revérifie tout le contenu à chaque fois et double le temps
    cmd = [exe, "+force_install_dir", str(install), "+login", "anonymous",
           "+workshop_download_item", str(GMOD_APPID), item.id, "+quit"]
    log("téléchargement via SteamCMD…")
    out_file = install.parent / f"steamcmd_{item.id}.log"
    install.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        start = time.time()
        while proc.poll() is None:
            if time.time() - start > 3600:
                proc.kill()
                raise RuntimeError("SteamCMD a mis plus d'une heure, abandon")
            done = _dir_size(ws / "downloads" / str(GMOD_APPID), content)
            progress(_progress_text(done, item.file_size) if done else "Connexion à Steam (SteamCMD)…")
            time.sleep(1)
    if not content.exists() or not any(content.iterdir()):
        legacy = Path(exe).parent / "steamapps" / "workshop" / "content" / str(GMOD_APPID) / item.id
        if legacy.exists() and any(legacy.iterdir()):
            content = legacy
        else:
            tail = out_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            raise RuntimeError("SteamCMD n'a pas pu télécharger l'addon : " + " | ".join(tail))
    files = [p for p in content.iterdir() if p.is_file()]
    for p in files:
        with open(p, "rb") as f:
            if f.read(4) == b"GMAD":
                return p
    # anciens addons : un .bin compressé en LZMA
    bins = [p for p in files if p.suffix.lower() == ".bin"]
    return bins[0] if bins else content
