# Monitor ETF — instrukcja konfiguracji

Ten "bot" sam sprawdza co jakiś czas dwie strony:

- Xtrackers MSCI Nordic UCITS ETF 1C (DWS)
- iShares MSCI Europe Health Care Sector UCITS ETF

...i zapisuje NAV, Net Assets oraz Shares Outstanding. Gdy coś się zmieni,
dostaniesz powiadomienie na telefon, a na stałej stronie (dashboardzie)
zobaczysz historię i procentową zmianę dzień do dnia.

Wszystko działa **za darmo**, w chmurze GitHuba — nie musisz niczego trzymać
włączonego na telefonie ani komputerze.

Poniższe kroki rób na komputerze (przez przeglądarkę). Telefon będzie Ci
potrzebny dopiero na końcu, do zainstalowania jednej aplikacji na powiadomienia.

---

## Krok 1 — Konto GitHub

1. Wejdź na [github.com](https://github.com) i załóż darmowe konto (jeśli
   jeszcze go nie masz) — wystarczy e-mail.

## Krok 2 — Nowe repozytorium

1. Kliknij zielony przycisk **New** (albo **+** w prawym górnym rogu → **New repository**).
2. Nazwa np. `etf-monitor`. Zostaw jako **Public**.
3. Kliknij **Create repository**.

## Krok 3 — Wgraj pliki

1. Na stronie nowego repozytorium kliknij **Add file → Upload files**.
2. Przeciągnij tam **całą zawartość** tego folderu, który ode mnie dostałaś/eś
   (z zachowaniem podfolderów `.github/workflows/` i `docs/` — GitHub sam je
   utworzy przy przeciąganiu folderu).
3. Kliknij **Commit changes**.

Jeśli przeglądarka nie pozwoli przeciągnąć podfolderów naraz, dodawaj pliki
pojedynczo przez **Add file → Create new file** i wklejaj zawartość, wpisując
pełną ścieżkę w polu nazwy (np. `.github/workflows/check.yml`).

## Krok 4 — Włącz uprawnienia zapisu dla Actions

Bot musi móc sam zapisywać nowe dane z powrotem do repozytorium.

1. **Settings → Actions → General**.
2. Sekcja **Workflow permissions** → zaznacz **Read and write permissions**.
3. **Save**.

## Krok 5 — Ustaw prywatny "temat" powiadomień (ntfy)

Powiadomienia idą przez darmowy, publiczny serwis ntfy.sh — dlatego temat
(nazwa kanału) powinien być unikalny, żeby nikt inny go nie zgadł.

1. Wymyśl sobie losowy ciąg znaków, np. `moje-etf-x7k2p9-monitor`.
2. W repozytorium: **Settings → Secrets and variables → Actions**.
3. **New repository secret**.
   - Name: `NTFY_TOPIC`
   - Secret: (Twój losowy ciąg z punktu 1)
4. **Add secret**.

## Krok 6 — Włącz GitHub Pages (Twój dashboard)

1. **Settings → Pages**.
2. **Source**: Deploy from a branch.
3. **Branch**: `main`, folder: `/docs`.
4. **Save**.
5. Po minucie-dwóch pod adresem `https://TWOJA-NAZWA.github.io/etf-monitor/`
   pojawi się Twój dashboard. Otwórz go na telefonie i wybierz
   **Dodaj do ekranu głównego** — będzie wyglądał jak zwykła apka.

## Krok 7 — Zainstaluj aplikację ntfy na telefonie

1. Zainstaluj **ntfy** ze Sklepu Play (albo z F-Droid, jeśli wolisz):
   <https://play.google.com/store/apps/details?id=io.heckel.ntfy>
2. Otwórz apkę → **+** (subskrybuj temat) → wpisz dokładnie ten sam ciąg,
   który wpisałaś/eś jako `NTFY_TOPIC` w kroku 5.
3. Gotowe — od teraz powiadomienia będą tu trafiać.

## Krok 8 — Pierwsze uruchomienie (test)

1. W repozytorium: zakładka **Actions**.
2. Kliknij workflow **Sprawdź ETF** → **Run workflow** → **Run workflow**
   (zielony przycisk).
3. Poczekaj ok. 1-2 minuty, odśwież stronę — powinieneś zobaczyć zielony ✓.
4. Odśwież dashboard i sprawdź telefon — powinno przyjść powiadomienie
   z pierwszymi wartościami ("pierwszy odczyt").

Od tej pory bot sam odpala się automatycznie 2x dziennie (7:15 i 17:15 UTC —
możesz zmienić godziny w pliku `.github/workflows/check.yml`, linijki z `cron`).

---

## Jeśli coś nie zadziała za pierwszym razem

Strona DWS/Xtrackers jest zbudowana w JavaScript i jej dokładna struktura
może się różnić od tego, co zakładałem pisząc bota — to najbardziej
prawdopodobne miejsce, gdzie coś może wymagać poprawki. Jeśli w zakładce
**Actions** zobaczysz czerwony ✗, albo dashboard pokaże "⚠" przy jednym
z funduszy — wklej mi po prostu treść błędu (albo fragment `debug_text`
z pliku `history.json`), a poprawię wzorzec dopasowania. To normalna,
jednorazowa rzecz przy tego typu automatyzacji, nie błąd w całej koncepcji.

## Co bot faktycznie robi (dla ciekawości)

- Dla iShares: pobiera dane z regionalnej strony BlackRock (te same dane,
  co na stronie iShares, ale bez ekranu z akceptacją regulaminu, który
  blokuje automatyczne pobieranie).
- Dla DWS: otwiera stronę w prawdziwej, niewidzialnej przeglądarce
  (Playwright/Chromium), bo dane tam ładują się dopiero po uruchomieniu
  JavaScriptu — zwykłe pobranie strony pokazuje pustą treść.
- Zapisuje odczyt do `history.json`, licząc różnicę procentową względem
  poprzedniego odczytu.
- Generuje na nowo `docs/index.html` (Twój dashboard).
- Wysyła powiadomienie przez ntfy.sh, jeśli coś się zmieniło.
