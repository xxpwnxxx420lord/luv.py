import re
import requests
from urllib.parse import urlparse, urljoin, parse_qs, unquote
from pathlib import Path
from bs4 import BeautifulSoup

BASE = "https://truffled.lol"
EJS_TAG = "4.0.9"
EJS_RAW = f"https://raw.githubusercontent.com/EmulatorJS/EmulatorJS/{EJS_TAG}/data"
EJS_CDN_CORES = f"https://cdn.emulatorjs.org/{EJS_TAG}/data/"
SENTINEL_FILE = ".ejs_downloaded"

# nipplejs.js is excluded here — it needs special post-processing (see download_ejs_sources)
EJS_SOURCE_FILES = [
    "emulator.js",
    "emulator.css",
    "shaders.js",
    "storage.js",
    "gamepad.js",
    "GameManager.js",
    "socket.io.min.js",
    "compression/libunrar.js",
    "compression/libunrar.wasm",
    "compression/extract7z.js",
    "localization/en.json",
]

# Appended to nipplejs.js after download.
NIPPLEJS_RESCUE = """
;(function () {
  if (typeof window.nipplejs === 'undefined') {
    if (typeof module !== 'undefined' && module && module.exports) {
      window.nipplejs = module.exports;
    } else if (typeof exports !== 'undefined' && exports && exports.nipplejs) {
      window.nipplejs = exports.nipplejs;
    }
  }
})();
"""

# ── Ruffle self-hosted CDN (latest stable) ──────────────────────────────────
RUFFLE_CDN = "https://unpkg.com/@ruffle-rs/ruffle"


def extract_game_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query)
    if "url" in qs:
        inner = unquote(qs["url"][0])
        inner_qs = parse_qs(urlparse(inner).query)
        if "url" in inner_qs:
            inner = unquote(inner_qs["url"][0])
        return BASE + inner
    return page_url if page_url.startswith("http") else BASE + page_url


def scrape_game_url_from_page(page_url: str) -> str:
    resp = requests.get(page_url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    iframe = soup.find("iframe")
    if not iframe or not iframe.get("src"):
        raise ValueError("Could not find an iframe on the page.")
    return extract_game_url(urljoin(BASE, iframe["src"]))


def resolve_game_url(user_url: str) -> str:
    parsed = urlparse(user_url)
    if "iframe.html" in parsed.path or "mobile.html" in parsed.path:
        return extract_game_url(user_url)
    if parsed.path.startswith("/gamefile/") or re.match(r"^/games/", parsed.path):
        return extract_game_url(user_url)
    return scrape_game_url_from_page(user_url)


def parse_ejs_config(html: str) -> dict:
    config = {}
    for m in re.finditer(r'EJS_(\w+)\s*=\s*["\']([^"\']+)["\']', html):
        config[m.group(1)] = m.group(2)
    return config


def is_old_style_loader(loader_text: str) -> bool:
    return "createElement" in loader_text and "import(" not in loader_text


def ejs_already_downloaded(data_dir: Path) -> bool:
    if (data_dir / SENTINEL_FILE).exists():
        return True
    if (data_dir / "emulator.js").exists():
        return True
    return False


def download_ejs_sources(data_dir: Path, force: bool = False):
    if not force and ejs_already_downloaded(data_dir):
        print(f"  [~] EmulatorJS already present in {data_dir}, skipping.")
        return

    print(f"[*] Downloading EmulatorJS {EJS_TAG} source files from GitHub ...")
    data_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for rel_path in EJS_SOURCE_FILES:
        dest = data_dir / rel_path
        if dest.exists() and not force:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = requests.get(f"{EJS_RAW}/{rel_path}", timeout=30)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            print(f"  [+] {rel_path}")
        except Exception as e:
            print(f"  [!] Failed: {rel_path} — {e}")
            failed.append(rel_path)

    nipple_dest = data_dir / "nipplejs.js"
    if not nipple_dest.exists() or force:
        try:
            resp = requests.get(f"{EJS_RAW}/nipplejs.js", timeout=30)
            resp.raise_for_status()
            patched_content = resp.content.decode("utf-8", errors="ignore") + NIPPLEJS_RESCUE
            nipple_dest.write_text(patched_content, encoding="utf-8")
            print("  [+] nipplejs.js (patched: window.nipplejs rescue appended)")
        except Exception as e:
            print(f"  [!] Failed: nipplejs.js — {e}")
            failed.append("nipplejs.js")

    en_json = data_dir / "localization" / "en.json"
    en_us_json = data_dir / "localization" / "en-US.json"
    if en_json.exists() and (not en_us_json.exists() or force):
        en_us_json.write_bytes(en_json.read_bytes())
        print("  [+] localization/en-US.json (copied from en.json)")

    emulator_js = data_dir / "emulator.js"
    min_js = data_dir / "emulator.min.js"
    min_css = data_dir / "emulator.min.css"

    if emulator_js.exists() and (not min_js.exists() or force):
        min_js.write_bytes(emulator_js.read_bytes())
        print("  [+] emulator.min.js (copied from emulator.js)")

    if not min_css.exists() or force:
        min_css.write_text("@import url('emulator.css');\n", encoding="utf-8")
        print("  [+] emulator.min.css (shim)")

    if not failed:
        (data_dir / SENTINEL_FILE).write_text(EJS_TAG, encoding="utf-8")
        print(f"  [✓] EmulatorJS source files ready in {data_dir}")
    else:
        print(f"  [!] Completed with {len(failed)} failure(s): {failed}")


def resolve_rom_url(ejs_game_url: str, base_game_url: str) -> str:
    if ejs_game_url.startswith("http"):
        return ejs_game_url
    if ejs_game_url.startswith("/"):
        return BASE + ejs_game_url
    return urljoin(base_game_url, ejs_game_url)


def download_rom(rom_url: str, out_dir: Path) -> Path | None:
    rom_filename = rom_url.split("?")[0].rsplit("/", 1)[-1]
    rom_path = out_dir / rom_filename
    if rom_path.exists():
        print(f"  [~] ROM already exists: {rom_path.name}")
        return rom_path

    print(f"[*] Downloading ROM: {rom_url.replace(BASE, '')}")
    try:
        with requests.get(rom_url, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(rom_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        mb = downloaded / (1024 * 1024)
                        print(f"\r  [~] {pct:3d}%  {mb:.1f} / {total / (1024*1024):.1f} MB", end="", flush=True)
        print()
        print(f"  [+] ROM saved: {rom_path.name}")
        return rom_path
    except Exception as e:
        print(f"  [!] Failed to download ROM: {e}")
        return None


def patch_index_emulatorjs(index_path: Path, rom_filename: str, ejs_config: dict):
    html = index_path.read_text(encoding="utf-8", errors="ignore")

    html = re.sub(
        r'(EJS_gameUrl\s*=\s*)["\'][^"\']*["\']',
        f'\\1"{rom_filename}"',
        html,
    )

    if "pathtodata" in ejs_config:
        html = re.sub(r'(EJS_pathtodata\s*=\s*)["\'][^"\']*["\']', r'\1"data/"', html)
    else:
        html = html.replace(
            '<script src="loader.js"',
            '<script>var EJS_pathtodata = "data/";</script>\n<script src="loader.js"',
        )

    cores_cdn = EJS_CDN_CORES
    ejs_paths_script = f"""<script>
window.EJS_paths = window.EJS_paths || {{}};
var _c = "{cores_cdn}";
["ppsspp","ppsspp-thread","ppsspp-legacy",
 "nestopia","fceumm","snes9x","snes9x2010",
 "gambatte","mgba","vba_next",
 "genesis_plus_gx","picodrive",
 "mupen64plus_next","mupen64plus_next-thread","mupen64plus_next-legacy",
 "pcsx_rearmed","mednafen_pce","mednafen_pce_fast",
 "mesen","vice_x64","fbalpha2012_cps1"].forEach(function(c) {{
    window.EJS_paths["cores/"+c+"-wasm.data"] = _c+"cores/"+c+"-wasm.data";
    window.EJS_paths["cores/"+c+"-thread-wasm.data"] = _c+"cores/"+c+"-thread-wasm.data";
    window.EJS_paths["cores/"+c+"-legacy-wasm.data"] = _c+"cores/"+c+"-legacy-wasm.data";
}});
window.EJS_paths["cores/ppsspp-assets.zip"] = _c+"cores/ppsspp-assets.zip";
</script>
<script src="data/nipplejs.js"></script>
<script src="data/socket.io.min.js"></script>
<script src="data/storage.js"></script>
<script src="data/shaders.js"></script>
<script src="data/gamepad.js"></script>
<script src="data/GameManager.js"></script>
"""

    html = html.replace('<script src="loader.js"', ejs_paths_script + '<script src="loader.js"')
    index_path.write_text(html, encoding="utf-8")
    print(f"  [~] Patched index.html:")
    print(f"       EJS_pathtodata  → data/")
    print(f"       EJS_gameUrl     → {rom_filename}")
    print(f"       EJS_paths       → CDN ({cores_cdn})")
    print(f"       nipplejs        → synchronous <script> before loader.js")


def patch_index_ruffle(index_path: Path, swf_filename: str):
    """
    Rewrite the index.html for a Ruffle/Flash game so it loads from the
    self-hosted CDN instead of the missing /js/main.js reference, and
    points Ruffle at the locally-saved SWF.
    """
    html = index_path.read_text(encoding="utf-8", errors="ignore")

    # Remove broken references to /js/main.js (site-wide script, not needed)
    html = re.sub(r'<script[^>]+src=["\'][^"\']*\/js\/main\.js["\'][^>]*>\s*<\/script>', '', html)

    # Remove any existing ruffle load / window.RufflePlayer blocks so we
    # don't end up with two initialisation paths.
    html = re.sub(r'<script[^>]+src=["\'][^"\']*ruffle[^"\']*["\'][^>]*>\s*<\/script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<script[^>]*>.*?RufflePlayer.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)

    ruffle_snippet = f"""<script src="ruffle.js"></script>
<script>
  window.addEventListener("load", function () {{
    var player = window.RufflePlayer.newest().createPlayer();
    player.style.width  = "100%";
    player.style.height = "100vh";
    document.body.appendChild(player);
    player.load("{swf_filename}");
  }});
</script>"""

    # Inject just before </body>; fall back to appending if tag not found.
    if "</body>" in html:
        html = html.replace("</body>", ruffle_snippet + "\n</body>")
    else:
        html += "\n" + ruffle_snippet

    index_path.write_text(html, encoding="utf-8")
    print(f"  [~] Patched index.html (Ruffle):")
    print(f"       SWF             → {swf_filename}")
    print(f"       Ruffle loader   → ruffle.js (self-hosted)")
    print(f"       /js/main.js     → removed (site-only, not needed)")


def get_game_name(game_index_url: str) -> str:
    try:
        resp = requests.get(game_index_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find("title")
        if title and title.text.strip():
            name = re.sub(r'[\\/*?:"<>|]', "", title.text.strip())
            return name.strip().lower().replace(" ", "_")
    except Exception:
        pass
    parts = [p for p in urlparse(game_index_url).path.split("/") if p]
    for part in reversed(parts):
        if part != "index.html":
            return part.lower()
    return "unknown_game"


# ── Asset-discovery helpers ──────────────────────────────────────────────────

# Added 'swf' to the extension set so Flash game files are picked up.
_ASSET_EXTS = r"wasm|data|ogg|mp3|wav|png|jpg|jpeg|gif|svg|ttf|woff2?|json|lua|lovejs|love|swf"


def collect_assets_from_text(text: str, base_url: str, base_game_url: str, url_to_local_path) -> list:
    assets = []

    # CSS url(...) references
    for match in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', text):
        if not match.startswith(("http", "data:", "#")):
            full = urljoin(base_url, match.split("?")[0])
            if full.startswith(BASE):
                assets.append((full, url_to_local_path(full)))

    # Quoted asset paths by extension (now includes swf)
    for match in re.findall(
        rf'["\`\'][\w./\-]+\.(?:{_ASSET_EXTS})["\`\']',
        text
    ):
        clean = match.strip("\"'`").split("?")[0]
        if not clean.startswith(("http", "data:", "#")):
            full = urljoin(base_url, clean)
            if full.startswith(BASE):
                assets.append((full, url_to_local_path(full)))

    # Generic game.* filenames
    for match in re.findall(r'["\']([^"\']*game\.[a-z0-9]+)["\']', text):
        clean = match.split("?")[0]
        if not clean.startswith(("http", "data:", "#")):
            full = urljoin(base_url, clean)
            if full.startswith(BASE):
                assets.append((full, url_to_local_path(full)))

    return assets


def find_swf_in_html(html: str, base_game_url: str) -> str | None:
    """
    Try several strategies to locate the SWF URL from raw HTML:
      1. <param name="movie" value="...">
      2. Any quoted string ending in .swf
      3. Ruffle player.load("...") call
      4. src="...swf..." on any tag
    Returns an absolute URL or None.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1 – <param name="movie">
    param = soup.find("param", attrs={"name": re.compile(r"movie", re.I)})
    if param and param.get("value"):
        return urljoin(base_game_url, param["value"])

    # Strategy 2 – any quoted .swf path in the raw HTML
    m = re.search(r'["\']([^"\']*\.swf)["\']', html, re.IGNORECASE)
    if m:
        return urljoin(base_game_url, m.group(1).split("?")[0])

    # Strategy 3 – player.load("...")
    m = re.search(r'\.load\(["\']([^"\']+\.swf)["\']', html, re.IGNORECASE)
    if m:
        return urljoin(base_game_url, m.group(1).split("?")[0])

    # Strategy 4 – src attribute containing .swf
    tag = soup.find(src=re.compile(r"\.swf", re.I))
    if tag:
        return urljoin(base_game_url, tag["src"].split("?")[0])

    return None


def is_ruffle_game(html: str, soup: BeautifulSoup) -> bool:
    """Return True when the page uses Ruffle (Flash emulator)."""
    if soup.find("script", src=re.compile(r"ruffle", re.I)):
        return True
    if re.search(r"RufflePlayer|ruffle\.js", html, re.I):
        return True
    if soup.find(attrs={"type": re.compile(r"application/x-shockwave-flash", re.I)}):
        return True
    return False


# ── Main download orchestration ──────────────────────────────────────────────

def download_game(game_index_url: str, out_base: str = "./exported"):
    print(f"[*] Resolved game URL: {game_index_url}")
    game_name = get_game_name(game_index_url)
    print(f"[*] Game name: {game_name}")

    base_game_url = game_index_url.rsplit("/", 1)[0] + "/"
    out_path = Path(out_base) / game_name
    out_path.mkdir(parents=True, exist_ok=True)

    visited = set()

    def url_to_local_path(url: str) -> Path:
        clean_url = url.split("?")[0]
        rel = clean_url.replace(base_game_url, "").replace(BASE + "/", "")
        return out_path / rel

    def fetch_and_save(url: str, save_path: Path, silent_404: bool = False):
        if url in visited:
            return None
        visited.add(url)
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except requests.HTTPError as e:
            if not silent_404:
                print(f"  [!] Failed to fetch {url}: {e}")
            return None
        except Exception as e:
            print(f"  [!] Failed to fetch {url}: {e}")
            return None
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)
        print(f"  [+] {url.replace(BASE, '')} -> {save_path.relative_to(out_path)}")
        return resp

    # ── Download index.html ──────────────────────────────────────────────────
    index_resp = fetch_and_save(game_index_url, out_path / "index.html")
    if not index_resp:
        raise RuntimeError("Failed to download game index.html")

    html_text = index_resp.text
    soup = BeautifulSoup(html_text, "html.parser")

    # ── EmulatorJS branch ────────────────────────────────────────────────────
    ejs_config = parse_ejs_config(html_text)
    is_emulatorjs = bool(ejs_config.get("gameUrl") or ejs_config.get("core"))

    if is_emulatorjs:
        core = ejs_config.get("core", "unknown")
        print(f"[*] Detected EmulatorJS game (core: {core})")

        loader_url = urljoin(base_game_url, "loader.js")
        loader_resp = fetch_and_save(loader_url, out_path / "loader.js")
        loader_text = loader_resp.content.decode("utf-8", errors="ignore") if loader_resp else ""

        data_dir = out_path / "data"

        if is_old_style_loader(loader_text):
            print(f"[*] Loader style: classic script (4.0.9)")
            download_ejs_sources(data_dir)
        else:
            print(f"[*] Loader style: ES module (new) — CDN zip needed")
            print(f"  [!] Download emulator.min.zip manually:")
            print(f"      https://cdn.emulatorjs.org/stable/data/emulator.min.zip")
            print(f"      Extract to: {data_dir}")

        rom_url_raw = ejs_config.get("gameUrl", "")
        if rom_url_raw:
            rom_url = resolve_rom_url(rom_url_raw, base_game_url)
            rom_path = download_rom(rom_url, out_path)
            if rom_path:
                patch_index_emulatorjs(out_path / "index.html", rom_path.name, ejs_config)
        else:
            print("  [!] EJS_gameUrl not found — set it manually in index.html")

        print(f"\n[✓] {out_path.resolve()}")
        if core == "ppsspp":
            print(f"    Note: PPSSPP core streams from CDN on first launch (~60 MB)")
        return

    # ── Ruffle / Flash branch ────────────────────────────────────────────────
    if is_ruffle_game(html_text, soup):
        print("[*] Detected Ruffle (Flash) game")

        # Find the SWF URL
        swf_url = find_swf_in_html(html_text, base_game_url)

        # Fall back: check common filenames
        if not swf_url:
            for candidate in ["swf.swf", "game.swf", "flash.swf", "index.swf"]:
                test_url = base_game_url + candidate
                try:
                    r = requests.head(test_url, timeout=10)
                    if r.status_code == 200:
                        swf_url = test_url
                        print(f"  [~] SWF found via HEAD probe: {candidate}")
                        break
                except Exception:
                    pass

        if swf_url:
            swf_filename = swf_url.split("?")[0].rsplit("/", 1)[-1]
            swf_path = out_path / swf_filename
            print(f"[*] Downloading SWF: {swf_url.replace(BASE, '')}")
            resp = fetch_and_save(swf_url, swf_path)
            if not resp:
                print("  [!] SWF download failed — game may not work offline")
        else:
            swf_filename = "game.swf"
            print(f"  [!] Could not auto-detect SWF URL; set it manually in index.html")

        # Download self-hosted ruffle.js
        ruffle_dest = out_path / "ruffle.js"
        if not ruffle_dest.exists():
            print(f"[*] Downloading Ruffle from {RUFFLE_CDN} ...")
            try:
                r = requests.get(RUFFLE_CDN, timeout=30)
                r.raise_for_status()
                # unpkg redirects to the actual ruffle.js content
                ruffle_dest.write_bytes(r.content)
                print("  [+] ruffle.js")
            except Exception as e:
                # The game's own ruffle.js was already fetched in the asset loop
                # below; this is just a belt-and-suspenders download.
                print(f"  [~] Ruffle CDN fetch skipped ({e}); will use page's ruffle.js")

        patch_index_ruffle(out_path / "index.html", swf_filename)

        # Still grab any remaining page assets (CSS, images, the site's ruffle.js, etc.)
        _download_generic_assets(
            soup, html_text, base_game_url, out_path, url_to_local_path, fetch_and_save
        )

        print(f"\n[✓] {out_path.resolve()}")
        return

    # ── Generic HTML5 game branch ────────────────────────────────────────────
    print("[*] Detected generic HTML5 game")
    _download_generic_assets(
        soup, html_text, base_game_url, out_path, url_to_local_path, fetch_and_save
    )

    for fname in ["game.data", "game.wasm", "game.swf"]:
        explicit_path = out_path / fname
        if not explicit_path.exists():
            print(f"[*] Explicitly trying {fname}...")
            fetch_and_save(base_game_url + fname, explicit_path, silent_404=True)

    print(f"\n[✓] {out_path.resolve()}")


def _download_generic_assets(soup, html_text, base_game_url, out_path, url_to_local_path, fetch_and_save):
    """Download all linked assets found in a page's HTML and recurse into JS/CSS."""
    assets = []

    for tag in soup.find_all(["script", "link", "img", "source", "audio", "video"]):
        for attr in ["src", "href", "data-src"]:
            val = tag.get(attr)
            if val and not val.startswith(("http", "//", "data:", "#", "javascript:")):
                full = urljoin(base_game_url, val.split("?")[0])
                if full.startswith(BASE):
                    assets.append((full, url_to_local_path(full)))

    assets += collect_assets_from_text(html_text, base_game_url, base_game_url, url_to_local_path)
    print(f"[*] Found {len(assets)} initial assets...")

    for url, path in assets:
        resp = fetch_and_save(url, path)
        if not resp:
            continue
        if url.split("?")[0].rsplit(".", 1)[-1].lower() in ("css", "js"):
            try:
                text = resp.content.decode("utf-8", errors="ignore")
                for n_url, n_path in collect_assets_from_text(
                    text, url.rsplit("/", 1)[0] + "/", base_game_url, url_to_local_path
                ):
                    fetch_and_save(n_url, n_path)
            except Exception:
                pass

    for js_file in out_path.rglob("*.js"):
        try:
            text = js_file.read_text(encoding="utf-8", errors="ignore")
            patched = re.sub(r'(\.(data|wasm|ogg|mp3|wav|png|jpg|json|love|css|swf))\?[^"\'&\s]+', r'\1', text)
            if patched != text:
                js_file.write_text(patched, encoding="utf-8")
                print(f"  [~] Patched cache busters in {js_file.relative_to(out_path)}")
        except Exception:
            pass


def run(url: str):
    game_url = resolve_game_url(url)
    download_game(game_url)