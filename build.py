#!/usr/bin/env python3
"""Bygger fulle RSS-feeder for NRKs podkaster fra psapi.nrk.no.

Feedene peker rett på NRKs egen CDN. Vi serverer aldri lyd, kun XML.
Kun standardbiblioteket, slik at CI ikke trenger et pip-steg.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

API = "https://psapi.nrk.no"
SHARE = "https://radio.nrk.no/podkast"
UA = "nrk-podkast-rss/1.0 (+https://github.com/%s)" % (
    __import__("os").environ.get("GITHUB_REPOSITORY", "local")
)
BYTES_PER_SEC = 24000  # 192 kbps CBR, verifisert mot faktiske Content-Length

_calls = 0
_calls_lock = threading.Lock()


def fetch(path: str, tries: int = 5) -> dict:
    global _calls
    with _calls_lock:
        _calls += 1
    url = path if path.startswith("http") else API + path
    delay = 1.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                raise
            if attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def paginate(path: str):
    """Følger _links.next til slutten."""
    url = path
    while url:
        d = fetch(url)
        yield d
        nxt = d.get("_links", {}).get("next")
        url = nxt["href"] if nxt else None


# ---------------------------------------------------------------- katalog

def all_series() -> list[str]:
    """Alle seriesId-er i podkastkatalogen (dedupliserer sesong-oppføringer)."""
    ids, skip = [], 0
    seen = set()
    while True:
        d = fetch(f"/radio/search/categories/podcast?take=100&skip={skip}")
        items = next(
            (v for k, v in d.items()
             if k != "letters" and isinstance(v, list) and v and isinstance(v[0], dict)),
            [],
        )
        if not items:
            break
        for it in items:
            sid = it.get("seriesId")
            if sid and sid not in seen:
                seen.add(sid)
                ids.append(sid)
        skip += 100
        if not d.get("_links", {}).get("nextPage"):
            break
    return sorted(ids)


def catalog(sid: str) -> tuple[dict, list[dict]]:
    """Ett kall gir både seriemetadata og de 20 nyeste episodene (sort=desc)."""
    d = fetch(f"/radio/catalog/podcast/{sid}")
    s = d.get("series", {})

    def biggest(key):
        imgs = s.get(key) or []
        return imgs[-1]["url"] if imgs else None

    meta = {
        "id": sid,
        "title": (s.get("titles") or {}).get("title") or sid,
        "subtitle": ((s.get("titles") or {}).get("subtitle") or "").strip(),
        "category": (s.get("category") or {}).get("name") or "",
        "image": biggest("squareImage") or biggest("image"),
    }
    head = (d.get("_embedded", {}).get("episodes", {})
             .get("_embedded", {}).get("episodes", []))
    return meta, [trim(e) for e in head]


def series_episodes(sid: str, known: set[str] | None = None) -> list[dict]:
    """Alle episoder. Med `known` stopper vi på første side der alt er kjent."""
    out = []
    for page in paginate(f"/radio/catalog/podcast/{sid}/episodes?page=1&pageSize=50"):
        eps = [trim(e) for e in page["_embedded"]["episodes"]]
        out.extend(eps)
        if known is not None and all(e["episodeId"] in known for e in eps):
            break
    return out


def audio_url(episode_id: str) -> str | None:
    """Ett manifest-kall. URL-en kan ikke utledes av episode-ID-en."""
    try:
        m = fetch(f"/playback/manifest/podcast/{episode_id}")
    except urllib.error.HTTPError:
        return None
    if m.get("playability") != "playable":
        return None
    assets = (m.get("playable") or {}).get("assets") or []
    for a in assets:
        if a.get("mimeType", "").startswith("audio") and not a.get("encrypted"):
            return a["url"]
    return None


# ---------------------------------------------------------------- rss

ITUNES_CATEGORY = {
    "Nyheter": "News",
    "Sport": "Sports",
    "Humor": "Comedy",
    "Kultur og samfunn": "Society &amp; Culture",
    "Dokumentar og fakta": "Documentary",
    "Musikk": "Music",
    "Vitenskap": "Science",
    "Historie": "History",
    "Barn": "Kids &amp; Family",
    "Livsstil": "Leisure",
    "Drama og litteratur": "Fiction",
    "Religion og livssyn": "Religion &amp; Spirituality",
}


def hhmmss(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def rfc2822(iso: str) -> str:
    try:
        return format_datetime(datetime.fromisoformat(iso))
    except Exception:
        return format_datetime(datetime.now(timezone.utc))


def build_rss(meta: dict, episodes: list[tuple[str, dict]], feed_url: str) -> str:
    t = escape(meta["title"])
    desc = escape(meta["subtitle"] or meta["title"])
    cat = ITUNES_CATEGORY.get(meta["category"], "Society &amp; Culture")
    img = meta["image"] or ""
    p = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
        ' xmlns:atom="http://www.w3.org/2005/Atom"'
        ' xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{t}</title>",
        f"<link>{SHARE}/{meta['id']}</link>",
        f"<description>{desc}</description>",
        "<language>no</language>",
        f"<copyright>NRK © {datetime.now().year}</copyright>",
        "<generator>nrk-podkast-rss</generator>",
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
        f'<atom:link rel="self" type="application/rss+xml" href="{escape(feed_url)}"/>',
        f"<itunes:author>NRK</itunes:author>",
        f"<itunes:summary>{desc}</itunes:summary>",
        f'<itunes:category text="{cat}"/>',
        "<itunes:explicit>false</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
        "<itunes:owner><itunes:name>NRK</itunes:name>"
        "<itunes:email>podkast@nrk.no</itunes:email></itunes:owner>",
    ]
    if img:
        p.append(f'<itunes:image href="{escape(img)}"/>')
        p.append(
            f"<image><url>{escape(img)}</url><title>{t}</title>"
            f"<link>{SHARE}/{meta['id']}</link></image>"
        )

    for eid, e in episodes:
        url = e.get("u")
        if not url:
            continue
        title = escape(e.get("t") or "")
        sub = escape(e.get("s") or "")
        secs = e.get("n") or 0
        eimg = e.get("i") or img
        p += [
            "<item>",
            f"<title>{title}</title>",
            f'<guid isPermaLink="false">{eid}</guid>',
            f"<link>{SHARE}/{meta['id']}/{eid}</link>",
            f"<pubDate>{rfc2822(e.get('d') or '')}</pubDate>",
            f"<description>{sub}</description>",
            f"<itunes:summary>{sub}</itunes:summary>",
            f'<enclosure url="{escape(url)}" type="audio/mpeg"'
            f' length="{secs * BYTES_PER_SEC}"/>',
            f"<itunes:duration>{hhmmss(secs)}</itunes:duration>",
            "<itunes:explicit>false</itunes:explicit>",
        ]
        if eimg:
            p.append(f'<itunes:image href="{escape(eimg)}"/>')
        p.append("</item>")

    p += ["</channel>", "</rss>"]
    return "\n".join(p)


# ---------------------------------------------------------------- cache

CACHE = Path("cache")
CACHE_VERSION = 2


def trim(e: dict) -> dict:
    """Bare feltene RSS-en trenger, med korte nøkler — cachen blir 235 filer."""
    imgs = e.get("squareImage") or e.get("image") or []
    return {
        "episodeId": e["episodeId"],
        "t": (e.get("titles") or {}).get("title") or "",
        "s": (e.get("titles") or {}).get("subtitle") or "",
        "d": e.get("date") or "",
        "n": e.get("durationInSeconds") or 0,
        "i": imgs[-1]["url"] if imgs else None,
    }


def load_cache(sid: str) -> dict:
    """Returnerer {"meta":…, "episodes":{eid: rec}}. v1-cache oppgraderes."""
    f = CACHE / f"{sid}.json"
    if not f.exists():
        return {"v": CACHE_VERSION, "meta": None, "episodes": {}}
    try:
        d = json.loads(f.read_text())
    except Exception:
        return {"v": CACHE_VERSION, "meta": None, "episodes": {}}
    if d.get("v") == CACHE_VERSION:
        return d
    # v1 var {episodeId: url}. Behold URL-ene, men metadataen må hentes på nytt.
    return {"v": CACHE_VERSION, "meta": None,
            "episodes": {k: {"u": v} for k, v in d.items() if isinstance(v, str)}}


def save_cache(sid: str, c: dict) -> bool:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{sid}.json"
    txt = json.dumps(c, ensure_ascii=False, indent=0, sort_keys=True)
    if f.exists() and f.read_text() == txt:
        return False
    f.write_text(txt)
    return True


def sync(sid: str, full: bool) -> tuple[str, dict, bool]:
    """Ett katalog-kall. Dypere paginering bare når toppen har ukjente episoder."""
    c = load_cache(sid)
    eps = c["episodes"]
    try:
        meta, head = catalog(sid)
    except Exception as e:
        log(f"  ! {sid}: {e}")
        return sid, c, False

    had_meta = c.get("meta") is not None
    c["meta"] = meta
    stale = full or not had_meta or any(
        e["episodeId"] not in eps or "d" not in eps[e["episodeId"]] for e in head)

    if not stale:
        for e in head:
            eps[e["episodeId"]].update({k: v for k, v in e.items() if k != "episodeId"})
        return sid, c, True

    known = None if full else {k for k, v in eps.items() if "d" in v}
    try:
        fresh = series_episodes(sid, known)
    except Exception as e:
        log(f"  ! {sid}: {e}")
        return sid, c, False

    live = {e["episodeId"] for e in fresh} | {e["episodeId"] for e in head}
    for e in fresh:
        eid = e.pop("episodeId")
        eps.setdefault(eid, {}).update(e)
    if full:
        # Bare en full crawl kan vite at NRK har fjernet en episode.
        for gone in set(eps) - live:
            del eps[gone]
    return sid, c, True


# ---------------------------------------------------------------- index

def write_index(out: Path, base: str, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r["title"].lower())
    total = sum(r["episodes"] for r in rows)
    cards = "\n".join(
        f'<li data-t="{html.escape((r["title"] + " " + r["id"]).lower(), quote=True)}">'
        f'<img loading="lazy" src="{html.escape(r["image"] or "")}" alt="">'
        f'<div><h2>{html.escape(r["title"])}</h2>'
        f'<p>{r["episodes"]} episoder · {html.escape(r["category"])}</p>'
        f'<code>{base}/f/{r["id"]}.xml</code>'
        + (f'<a href="{base}/f/{r["id"]}-full.xml">hele arkivet ({r["episodes"]})</a>'
           if r.get("full") else "")
        + "</div>"
        f'<button data-u="{base}/f/{r["id"]}.xml">Kopier</button></li>'
        for r in rows
    )
    out.joinpath("index.html").write_text(
        INDEX_HTML.replace("{{CARDS}}", cards)
        .replace("{{N}}", str(len(rows)))
        .replace("{{EPISODES}}", f"{total:,}".replace(",", " "))
        .replace("{{BASE}}", base)
        .replace("{{DATE}}", datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")),
        encoding="utf-8",
    )
    out.joinpath("feeds.json").write_text(
        json.dumps(
            {"generated": datetime.now(timezone.utc).isoformat(), "base": base,
             "feeds": [{**r, "url": f"{base}/f/{r['id']}.xml"} for r in rows]},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )


INDEX_HTML = """<!doctype html>
<html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NRK podkast · fulle RSS-feeder</title>
<style>
:root{color-scheme:light dark;--bg:#fbfaf8;--fg:#1a1a19;--dim:#6b6a66;--line:#e4e1db;--card:#fff;--acc:#1f6feb}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#eeece6;--dim:#95938c;--line:#2c2b28;--card:#1c1b1a;--acc:#5c9dff}}
*{box-sizing:border-box}
body{margin:0;padding:0 16px;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:820px;margin:0 auto;padding-block:48px 72px}
h1{font-size:1.9rem;margin:0 0 .3em;letter-spacing:-.02em}
.lede{color:var(--dim);margin:0 0 2em;max-width:60ch}
.lede a{color:var(--acc)}
input{width:100%;padding:11px 14px;font:inherit;border:1px solid var(--line);
border-radius:9px;background:var(--card);color:var(--fg);margin-bottom:22px}
input:focus{outline:2px solid var(--acc);outline-offset:-1px;border-color:transparent}
ul{list-style:none;margin:0;padding:0;display:grid;gap:9px}
li{display:flex;gap:13px;align-items:center;padding:11px;background:var(--card);
border:1px solid var(--line);border-radius:11px}
li[hidden]{display:none}
.empty{color:var(--dim);padding:14px 2px}
li img{width:52px;height:52px;border-radius:7px;object-fit:cover;flex:none;background:var(--line)}
li div{min-width:0;flex:1}
h2{font-size:1rem;margin:0;font-weight:600}
li p{margin:1px 0 4px;color:var(--dim);font-size:.82rem}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);
word-break:break-all;display:block}
li a{font-size:.8rem;color:var(--acc);text-decoration:none}
li a:hover{text-decoration:underline}
button{font:inherit;font-size:.83rem;padding:7px 13px;border:1px solid var(--line);
border-radius:8px;background:transparent;color:var(--fg);cursor:pointer;flex:none}
button:hover{border-color:var(--acc);color:var(--acc)}
footer{margin-top:40px;padding-top:22px;border-top:1px solid var(--line);
color:var(--dim);font-size:.84rem}
@media(max-width:520px){li{flex-wrap:wrap}li button{width:100%}}
</style></head><body><main>
<h1>NRK podkast — fulle RSS-feeder</h1>
<p class="lede">NRKs egne feeder er kuttet til de siste par episodene. Disse inneholder
hele arkivet — {{EPISODES}} episoder fordelt på {{N}} serier. Lyden strømmes rett fra
NRKs egen CDN; denne siden serverer bare XML. Oppdatert {{DATE}}.</p>
<input id="q" type="search" placeholder="Søk blant {{N}} podkaster…" autocomplete="off">
<ul id="list">{{CARDS}}</ul>
<footer>Innholdet tilhører NRK. Maskinlesbar liste:
<a href="{{BASE}}/feeds.json">feeds.json</a>.</footer>
</main><script>
const q=document.getElementById('q'),list=document.getElementById('list'),
items=[...list.querySelectorAll('li')],
empty=Object.assign(document.createElement('p'),{className:'empty',hidden:true,
textContent:'Ingen treff.'});
list.after(empty);
q.addEventListener('input',()=>{const v=q.value.toLowerCase().trim();let n=0;
for(const li of items){const hit=!v||li.dataset.t.includes(v);li.hidden=!hit;if(hit)n++;}
empty.hidden=n>0;});
document.getElementById('list').addEventListener('click',e=>{
const b=e.target.closest('button'); if(!b) return;
navigator.clipboard.writeText(b.dataset.u).then(()=>{
b.textContent='Kopiert'; setTimeout(()=>b.textContent='Kopier',1400);});});
</script></body></html>"""


# ---------------------------------------------------------------- main

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site", type=Path)
    ap.add_argument("--base-url", required=True,
                    help="f.eks. https://bruker.github.io/nrk-podkast-rss")
    ap.add_argument("--series", default="", help="komma-separert delmengde")
    ap.add_argument("--limit", type=int, default=300,
                    help="episoder i hovedfeeden; 0 = alle")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--full", action="store_true",
                    help="full crawl av alle sider; fanger opp slettede episoder")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    feeds = args.out / "f"
    feeds.mkdir(parents=True, exist_ok=True)

    ids = [s.strip() for s in args.series.split(",") if s.strip()] or all_series()
    log(f"serier: {len(ids)} ({'full crawl' if args.full else 'inkrementell'})")

    # Fase 1 — ett katalog-kall per serie; dypere bare der toppen er ukjent.
    caches = {}
    with ThreadPoolExecutor(args.workers) as ex:
        for sid, c, ok in ex.map(lambda x: sync(x, args.full), ids):
            if ok and c.get("meta"):
                caches[sid] = c
    total = sum(len(c["episodes"]) for c in caches.values())
    log(f"episoder: {total}")

    # Fase 2 — manifest kun for episoder uten kjent lyd-URL.
    todo = [(sid, eid) for sid, c in caches.items()
            for eid, rec in c["episodes"].items() if not rec.get("u")]
    log(f"manifester å hente: {len(todo)}")
    if todo:
        done = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(args.workers) as ex:
            for sid, eid, url in ex.map(
                    lambda it: (it[0], it[1], audio_url(it[1])), todo):
                caches[sid]["episodes"][eid]["u"] = url
                with lock:
                    done += 1
                    if done % 500 == 0:
                        log(f"  {done}/{len(todo)}")

    # Fase 3 — skriv cache, feeder og indeks.
    changed = 0
    rows = []
    for sid, c in caches.items():
        changed += save_cache(sid, c)
        meta = c["meta"]
        playable = sorted(
            ((eid, r) for eid, r in c["episodes"].items() if r.get("u") and r.get("d")),
            key=lambda kv: kv[1]["d"], reverse=True)
        if not playable:
            continue

        url = f"{base}/f/{sid}.xml"
        head = playable if args.limit == 0 else playable[: args.limit]
        feeds.joinpath(f"{sid}.xml").write_text(
            build_rss(meta, head, url), encoding="utf-8")
        if args.limit and len(playable) > args.limit:
            full_url = f"{base}/f/{sid}-full.xml"
            feeds.joinpath(f"{sid}-full.xml").write_text(
                build_rss({**meta, "title": meta["title"] + " (hele arkivet)"},
                          playable, full_url), encoding="utf-8")
        rows.append({**meta, "episodes": len(playable),
                     "full": bool(args.limit and len(playable) > args.limit)})

    # Heartbeat med ukesgranularitet: garanterer én commit i uka slik at
    # GitHub ikke deaktiverer cron-jobben, uten å skitne til hver kjøring.
    CACHE.mkdir(exist_ok=True)
    hb = CACHE / ".heartbeat"
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    if not hb.exists() or hb.read_text().strip() != week:
        hb.write_text(week + "\n")

    write_index(args.out, base, rows)
    args.out.joinpath("robots.txt").write_text(
        "User-agent: *\nAllow: /\n", encoding="utf-8")
    args.out.joinpath(".nojekyll").write_text("", encoding="utf-8")
    log(f"ferdig: {len(rows)} feeder, {changed} cache-filer endret, {_calls} api-kall")

    out_file = __import__("os").environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a") as fh:
            fh.write(f"changed={changed}\nfeeds={len(rows)}\ncalls={_calls}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
