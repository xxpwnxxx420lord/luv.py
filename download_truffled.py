import re
import requests
from urllib.parse import urlparse, urljoin, parse_qs, unquote
from bs4 import BeautifulSoup
from pathlib import Path

BASE = "https://truffled.lol"

def extract_game_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query)

    if "url" in qs:
        inner = unquote(qs["url"][0])
        inner_parsed = urlparse(inner)
        inner_qs = parse_qs(inner_parsed.query)
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

    src = iframe["src"]
    return extract_game_url(urljoin(BASE, src))


def resolve_game_url(user_url: str) -> str:
    parsed = urlparse(user_url)

    if "iframe.html" in parsed.path or "mobile.html" in parsed.path:
        return extract_game_url(user_url)

    if parsed.path.startswith("/gamefile/") or re.match(r"^/games/", parsed.path):
        return extract_game_url(user_url)

    return scrape_game_url_from_page(user_url)


def get_game_name(game_index_url: str) -> str:
    # Try to get name from the page title
    try:
        resp = requests.get(game_index_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find("title")
        if title and title.text.strip():
            name = title.text.strip()
            name = re.sub(r'[\\/*?:"<>|]', "", name)
            name = name.strip().lower().replace(" ", "_")
            return name
    except Exception:
        pass

    # Fall back to the folder name in the URL path
    parts = [p for p in urlparse(game_index_url).path.split("/") if p]
    for part in reversed(parts):
        if part != "index.html":
            return part.lower()

    return "unknown_game"


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

    def fetch_and_save(url: str, save_path: Path):
        if url in visited:
            return None
        visited.add(url)

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [!] Failed to fetch {url}: {e}")
            return None

        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)
        print(f"  [+] {url.replace(BASE, '')} -> {save_path.relative_to(out_path)}")
        return resp

    def collect_assets_from_text(text: str, base_url: str) -> list:
        assets = []

        for match in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', text):
            if not match.startswith(("http", "data:", "#")):
                full = urljoin(base_url, match.split("?")[0])
                if full.startswith(BASE):
                    assets.append((full, url_to_local_path(full)))

        for match in re.findall(
            r'["\`\']([\w./\-]+\.(?:wasm|data|ogg|mp3|wav|png|jpg|jpeg|gif|svg|ttf|woff2?|json|lua|lovejs|love))["\`\']',
            text
        ):
            if not match.startswith(("http", "data:", "#")):
                full = urljoin(base_url, match.split("?")[0])
                if full.startswith(BASE):
                    assets.append((full, url_to_local_path(full)))

        for match in re.findall(r'["\']([^"\']*game\.[a-z0-9]+)["\']', text):
            clean = match.split("?")[0]
            if not clean.startswith(("http", "data:", "#")):
                full = urljoin(base_url, clean)
                if full.startswith(BASE):
                    assets.append((full, url_to_local_path(full)))

        return assets

    index_resp = fetch_and_save(game_index_url, out_path / "index.html")
    if not index_resp:
        raise RuntimeError("Failed to download game index.html")

    soup = BeautifulSoup(index_resp.text, "html.parser")
    assets = []

    for tag in soup.find_all(["script", "link", "img", "source", "audio", "video"]):
        for attr in ["src", "href", "data-src"]:
            val = tag.get(attr)
            if val and not val.startswith(("http", "//", "data:", "#", "javascript:")):
                full = urljoin(base_game_url, val.split("?")[0])
                if full.startswith(BASE):
                    assets.append((full, url_to_local_path(full)))

    assets += collect_assets_from_text(index_resp.text, base_game_url)

    print(f"[*] Found {len(assets)} initial assets...")

    for url, path in assets:
        resp = fetch_and_save(url, path)
        if not resp:
            continue

        ext = url.split("?")[0].rsplit(".", 1)[-1].lower()

        if ext in ("css", "js"):
            try:
                text = resp.content.decode("utf-8", errors="ignore")
                nested = collect_assets_from_text(text, url.rsplit("/", 1)[0] + "/")
                for n_url, n_path in nested:
                    fetch_and_save(n_url, n_path)
            except Exception:
                pass

    for js_file in out_path.rglob("*.js"):
        try:
            text = js_file.read_text(encoding="utf-8", errors="ignore")
            patched = re.sub(r'(\.(data|wasm|ogg|mp3|wav|png|jpg|json|love|css))\?[^"\'&\s]+', r'\1', text)
            if patched != text:
                js_file.write_text(patched, encoding="utf-8")
                print(f"  [~] Patched cache busters in {js_file.relative_to(out_path)}")
        except Exception:
            pass

    for fname in ["game.data", "game.wasm"]:
        explicit_url = base_game_url + fname
        explicit_path = out_path / fname
        if not explicit_path.exists():
            print(f"[*] Explicitly trying {fname}...")
            fetch_and_save(explicit_url, explicit_path)

    print(f"\n[✓] Game downloaded to: {out_path.resolve()}")


def run(url: str):
    game_url = resolve_game_url(url)
    download_game(game_url)