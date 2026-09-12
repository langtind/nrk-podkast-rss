# nrk-podkast-rss

Fulle RSS-feeder for NRKs podkaster. NRKs egne feeder på `podkast.nrk.no` er
kuttet til de siste 3–5 episodene; disse inneholder hele arkivet.

**Siden serverer kun XML.** `<enclosure>` peker rett på `podkast.nrk.no`, så
NRK leverer lyden og får trafikken. Ingenting speiles.

## Slik virker det

Tre udokumenterte, men åpne endepunkt på `psapi.nrk.no` — ingen nøkkel, ingen
innlogging, ingen DRM:

| Steg | Endepunkt |
|---|---|
| Katalog | `/radio/search/categories/podcast?take=100&skip=N` |
| Episoder | `/radio/catalog/podcast/{id}/episodes?page=N&pageSize=50` |
| Lydfil | `/playback/manifest/podcast/{episodeId}` |

MP3-URL-en kan **ikke** utledes av episode-ID-en — den bruker en annen UUID, og
minst tre navnekonvensjoner er i bruk:

```
.../fil/abels_taarn/fa893938-…_1_ID192MP3.mp3
.../fil/kringkastingsorkestret_/44aed594-…_ID192MP3.mp3
.../fil/skampod/skampod_2017-06-26_1359_1961.MP3
```

Derfor ett manifest-kall per episode. Resultatet caches permanent i `cache/`,
så bare nye episoder koster noe. `<enclosure length>` regnes ut som
`durationInSeconds × 24000` (192 kbps CBR, verifisert mot faktisk
`Content-Length` — treffer innenfor 0,2 %), som sparer et HTTP-kall per episode.

## Omfang

235 serier, ~59 500 episoder, ~90 MB XML.

| Jobb | Kall | Tid |
|---|---|---|
| Katalog-crawl | 1 326 | ~60 s |
| Manifest-backfill (engangs) | 59 518 | ~25 min |
| Daglig delta | ~1 400 | under 2 min |

## Kom i gang

1. Fork/push dette repoet.
2. **Settings → Pages → Source: GitHub Actions.**
3. Kjør workflowen manuelt én gang (`Actions → update feeds → Run workflow`).
   Den første kjøringen tar ~25 min fordi cachen er tom.
4. Feedene ligger på `https://<bruker>.github.io/nrk-podkast-rss/f/<serie>.xml`.

Egen domene? Sett repo-variabelen `BASE_URL`.

### Lokalt

```bash
python build.py --base-url http://localhost:8000 --series abels_taarn --out site
python -m http.server -d site
```

## URL-format

```
/f/{seriesId}.xml         siste 300 episoder
/f/{seriesId}-full.xml    hele arkivet (kun der det er over 300)
/feeds.json               maskinlesbar liste over alt
```

`seriesId` er det samme som i NRKs egne URL-er:
`radio.nrk.no/podkast/abels_taarn` → `/f/abels_taarn.xml`.

Hovedfeeden er kuttet til 300 med vilje: Ekko har 7 661 episoder, og
podkastklienter laster ned hele XML-en ved hver poll.

## Rettigheter

Alt innhold tilhører NRK. Dette er en interoperabilitetsgenerator — den lager
en standard feed av data NRK allerede publiserer åpent og ubeskyttet. Den
omgår ingen betaling, ingen innlogging og ingen DRM, og den distribuerer ikke
lyd.

NRK kuttet feedene bevisst for å flytte lyttere til appen. De kan be om at
dette tas ned, eller stenge for det teknisk. `psapi` er dessuten et internt
API uten kompatibilitetsgaranti — det kan endres uten varsel.

NRKs `robots.txt` forbyr tekst- og datautvinning til trening av språkmodeller.
Dette prosjektet gjør ikke det, men vær klar over grensen.
