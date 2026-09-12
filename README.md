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
| Serieliste | `/radio/search/categories/podcast?take=100&skip=N` |
| Serie + 20 nyeste | `/radio/catalog/podcast/{id}` |
| Dypere episoder | `/radio/catalog/podcast/{id}/episodes?page=N&pageSize=50` |
| Lydfil | `/playback/manifest/podcast/{episodeId}` |

Endringsdeteksjonen hviler på at `/radio/catalog/podcast/{id}` returnerer både
seriemetadata **og** de 20 nyeste episodene sortert `desc` i ett kall. Er alle
20 kjent fra før, rører vi ikke resten av arkivet. Først når noe ukjent dukker
opp i toppen pagineres det dypere — og da bare til første side der alt er kjent.

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

235 serier, 59 518 episoder, ~90 MB XML, 33 MB cache.

`hos_peder` ligger i NRKs podkastkategori, men `/radio/catalog/podcast/hos_peder`
svarer 404. Den hoppes over, så det blir 234 feeder.

| Jobb | Kall | Tid |
|---|---|---|
| Manifest-backfill (engangs) | 59 518 | ~36 min |
| Full crawl (`--full`, ukentlig) | 1 568 | ~34 s |
| Inkrementell, ingen endringer | **242** | **~4 s** |

Feedene bygges hvert 15. minutt fra den inkrementelle stien, altså ~23 000
API-kall i døgnet. Uten optimaliseringen hadde samme kadens kostet 150 000.

En full crawl kjører mandag natt. Det er den eneste måten å oppdage at NRK har
*fjernet* en episode — den inkrementelle stien ser bare toppen av lista.

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

## Cache

`cache/{seriesId}.json` holder seriemetadata og én linje per episode med
tittel, dato, varighet, bilde og lyd-URL. Den committes av jobben, og er det
som gjør 15-minutters kadens mulig — uten den måtte hele arkivet hentes på
nytt hver gang.

Formatet er versjonert (`"v": 2`); eldre cache oppgraderes automatisk.
`.heartbeat` skrives med ukesgranularitet, slik at det garantert blir minst én
commit i uka — ellers deaktiverer GitHub cron-jobben etter 60 dager.

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
