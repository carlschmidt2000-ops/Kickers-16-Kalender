import json
import re
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# KONFIGURATION
# ============================================================

TEAMS = {
    "erste": {
        "name": "Spvgg. Kickers 1916 Ffm – 1. Mannschaft",
        "id": "011MIE16QO000000VTVG0001VTR8C1K7",
    },
    "zweite": {
        "name": "Spvgg. Kickers 1916 Ffm – 2. Mannschaft",
        "id": "0313IQTOQC000000VS5489BRVVV10ESU",
    },
}

START_DATE = "2026-07-01"
END_DATE = "2027-07-31"

OUTPUT_FILES = {
    "erste": "erste.ics",
    "zweite": "zweite.ics",
}

# FUSSBALL.DE verwendet diesen Endpunkt laut eigener
# HTML-Seite für den dynamischen Spielplan.
BASE_URL = (
    "https://www.fussball.de/ajax.team.matchplan/-/"
    "datum-von/{start}/"
    "datum-bis/{end}/"
    "max/100/"
    "match-type/-1/"
    "wettkampftyp/-1/"
    "show-venues/true/"
    "mime-type/JSON/"
    "team-id/{team_id}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "Kickers16-Kalender/1.0)"
    ),
    "Accept": "application/json,text/plain,*/*",
}


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    # HTML-Entities entfernen
    import html
    value = html.unescape(value)

    # Überflüssige Whitespaces
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def ical_escape(value):
    if not value:
        return ""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ical_line(line, limit=75):
    """
    RFC-5545-konformes Falten langer iCalendar-Zeilen.
    """

    if len(line) <= limit:
        return [line]

    result = []

    while len(line) > limit:
        result.append(line[:limit])
        line = " " + line[limit:]

    result.append(line)

    return result


def parse_date(value):
    """
    Versucht verschiedene Datumsformate.
    """

    if not value:
        return None

    value = clean_text(value)

    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt,
            )
        except ValueError:
            pass

    return None


def find_value(data, names):
    """
    Rekursive Suche nach einem Wert in einem JSON-Objekt.
    """

    if isinstance(data, dict):

        # Zuerst direkte Treffer
        for name in names:

            if name in data:
                return data[name]

        # Groß-/Kleinschreibung ignorieren
        lower_names = {
            str(name).lower()
            for name in names
        }

        for key, value in data.items():

            if str(key).lower() in lower_names:
                return value

        # Rekursiv suchen
        for value in data.values():

            result = find_value(
                value,
                names,
            )

            if result not in (None, ""):
                return result

    elif isinstance(data, list):

        for item in data:

            result = find_value(
                item,
                names,
            )

            if result not in (None, ""):
                return result

    return None


def find_all_match_objects(data):
    """
    Sucht rekursiv nach JSON-Objekten, die wie
    Fußballspiele aussehen.
    """

    result = []

    if isinstance(data, dict):

        keys = {
            str(key).lower()
            for key in data.keys()
        }

        # Typische Schlüssel eines Spiels
        has_date = any(
            key in keys
            for key in [
                "matchmoment",
                "datetime",
                "date",
                "datum",
                "match-date",
            ]
        )

        has_teams = (
            any(
                key in keys
                for key in [
                    "hometeam",
                    "home-team",
                    "home",
                    "heim",
                    "heimteam",
                ]
            )
            and
            any(
                key in keys
                for key in [
                    "awayteam",
                    "away-team",
                    "away",
                    "gast",
                    "gastteam",
                ]
            )
        )

        if has_date and has_teams:
            result.append(data)

        for value in data.values():

            result.extend(
                find_all_match_objects(value)
            )

    elif isinstance(data, list):

        for item in data:

            result.extend(
                find_all_match_objects(item)
            )

    return result


def extract_team_name(value):
    """
    Extrahiert einen Mannschaftsnamen aus verschiedenen
    möglichen JSON-Strukturen.
    """

    if value is None:
        return ""

    if isinstance(value, str):
        return clean_text(value)

    if isinstance(value, dict):

        for key in [
            "name",
            "teamName",
            "team-name",
            "clubName",
            "club-name",
            "shortName",
        ]:

            if key in value:
                return clean_text(
                    value[key]
                )

        # Rekursive Suche
        result = find_value(
            value,
            [
                "name",
                "teamName",
                "clubName",
                "shortName",
            ],
        )

        if result:
            return clean_text(result)

    return clean_text(value)


def extract_matches(data, team_id):

    match_objects = find_all_match_objects(data)

    print(
        f"🔎 JSON: {len(match_objects)} mögliche Spiele gefunden."
    )

    matches = []

    for item in match_objects:

        match_id = find_value(
            item,
            [
                "id",
                "matchId",
                "match-id",
                "gameId",
                "game-id",
            ],
        )

        date_value = find_value(
            item,
            [
                "matchMoment",
                "match-moment",
                "datetime",
                "date",
                "datum",
                "matchDate",
            ],
        )

        date = parse_date(
            date_value
        )

        if not date:
            continue

        home_value = find_value(
            item,
            [
                "homeTeam",
                "home-team",
                "hometeam",
                "home",
                "heim",
                "heimTeam",
            ],
        )

        away_value = find_value(
            item,
            [
                "awayTeam",
                "away-team",
                "awayteam",
                "away",
                "gast",
                "gastTeam",
            ],
        )

        home = extract_team_name(
            home_value
        )

        away = extract_team_name(
            away_value
        )

        if not home or not away:
            continue

        venue_value = find_value(
            item,
            [
                "venue",
                "location",
                "stadium",
                "spielort",
                "sportstaette",
                "venueName",
            ],
        )

        venue = extract_team_name(
            venue_value
        )

        competition_value = find_value(
            item,
            [
                "competition",
                "competitionName",
                "competition-name",
                "wettbewerb",
                "wettkampf",
            ],
        )

        competition = extract_team_name(
            competition_value
        )

        if not match_id:
            match_id = (
                f"{date.isoformat()}-"
                f"{home}-"
                f"{away}"
            )

        matches.append(
            {
                "id": str(match_id),
                "date": date,
                "home": home,
                "away": away,
                "venue": venue,
                "competition": competition,
                "team_id": team_id,
            }
        )

    return matches


# ============================================================
# FUSSBALL.DE ABRUF
# ============================================================

def fetch_team(team_id):

    url = BASE_URL.format(
        start=START_DATE,
        end=END_DATE,
        team_id=team_id,
    )

    print()
    print("🌐 Lade:")
    print(url)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60,
    )

    print(
        f"HTTP-Status: {response.status_code}"
    )

    print(
        f"Antwortgröße: {len(response.content)} Bytes"
    )

    print(
        "Content-Type:",
        response.headers.get(
            "Content-Type",
            "",
        ),
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "FUSSBALL.DE hat eine leere Antwort geliefert."
        )

    # --------------------------------------------------------
    # FUSSBALL.DE liefert JSON.
    # Darin befindet sich der eigentliche Spielplan
    # im Feld "html".
    # --------------------------------------------------------

    try:
        data = response.json()

    except Exception as error:

        print()
        print("❌ Antwort ist kein gültiges JSON.")
        print(response.text[:2000])

        raise RuntimeError(
            "FUSSBALL.DE liefert kein gültiges JSON."
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            "Unerwartete JSON-Struktur von FUSSBALL.DE."
        )

    print(
        "JSON-Schlüssel:",
        list(data.keys())
    )

    if not data.get("success"):
        raise RuntimeError(
            "FUSSBALL.DE meldet success=false."
        )

    html_content = data.get("html")

    if not html_content:
        raise RuntimeError(
            "FUSSBALL.DE hat kein HTML im JSON geliefert."
        )

    print(
        f"Spielplan-HTML: {len(html_content)} Zeichen"
    )

    # --------------------------------------------------------
    # Jetzt das HTML aus dem JSON parsen.
    # --------------------------------------------------------

    def extract_matches_from_html(content, team_id):

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        content,
        "html.parser",
    )

    matches = []

    # --------------------------------------------------------
    # FUSSBALL.DE:
    #
    # row-competition = Zeile mit Datum/Wettbewerb
    # folgende Zeile = Heim- und Gastmannschaft
    # --------------------------------------------------------

    rows = soup.select(
        "div.club-matchplan-table tr.row-competition"
    )

    print(
        f"🔎 {len(rows)} Spieltag-Zeilen gefunden."
    )

    # Fallback, falls sich die äußere DIV-Struktur ändert
    if not rows:

        rows = soup.select(
            "tr.row-competition"
        )

        print(
            f"🔎 Fallback: {len(rows)} Zeilen gefunden."
        )

    for row in rows:

        # ----------------------------------------------------
        # DATUM
        # ----------------------------------------------------

        date_cell = row.select_one(
            "td.column-date"
        )

        if not date_cell:

            # Fallback: irgendeine Zelle mit Datum
            date_cell = row.find(
                "td"
            )

        if not date_cell:
            continue

        date_text = clean_text(
            date_cell.get_text(
                " ",
                strip=True,
            )
        )

        date = None

        # z.B.
        # 30.08.2026 - 13:00
        # oder
        # 30.08.2026 13:00

        match = re.search(
            r"(\d{2}\.\d{2}\.\d{4})"
            r"(?:\s*[-–]\s*|\s+)"
            r"(\d{2}:\d{2})",
            date_text,
        )

        if match:

            try:

                date = datetime.strptime(
                    (
                        f"{match.group(1)} "
                        f"{match.group(2)}"
                    ),
                    "%d.%m.%Y %H:%M",
                )

            except ValueError:
                date = None

        # Nur Datum ohne Uhrzeit
        if not date:

            match = re.search(
                r"(\d{2}\.\d{2}\.\d{4})",
                date_text,
            )

            if match:

                try:

                    date = datetime.strptime(
                        match.group(1),
                        "%d.%m.%Y",
                    )

                except ValueError:
                    date = None

        if not date:
            continue

        # ----------------------------------------------------
        # NÄCHSTE TABELLENZEILE = MANNSCHAFTEN
        # ----------------------------------------------------

        team_row = row.find_next_sibling(
            "tr"
        )

        if not team_row:

            # Fallback
            team_row = row.find_next(
                "tr"
            )

        if not team_row:
            continue

        # ----------------------------------------------------
        # MANNSCHAFTSNAMEN
        # ----------------------------------------------------

        clubs = team_row.select(
            "td.colum-club div.club-name"
        )

        # Alternative Schreibweise
        if len(clubs) < 2:

            clubs = team_row.select(
                "td.column-club div.club-name"
            )

        # Noch allgemeiner
        if len(clubs) < 2:

            clubs = team_row.select(
                "div.club-name"
            )

        # ----------------------------------------------------
        # Falls die Mannschaften nicht gefunden werden:
        # suche in der gesamten Zeile nach Links/
        # Mannschaftselementen.
        # ----------------------------------------------------

        if len(clubs) < 2:

            possible_names = []

            for element in team_row.find_all(
                ["div", "span", "a"]
            ):

                classes = " ".join(
                    element.get(
                        "class",
                        []
                    )
                ).lower()

                text = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if (
                    text
                    and (
                        "club" in classes
                        or "team" in classes
                    )
                    and text not in possible_names
                ):

                    possible_names.append(
                        text
                    )

            if len(possible_names) >= 2:

                home = possible_names[0]
                away = possible_names[1]

            else:
                continue

        else:

            home = clean_text(
                clubs[0].get_text(
                    " ",
                    strip=True,
                )
            )

            away = clean_text(
                clubs[1].get_text(
                    " ",
                    strip=True,
                )
            )

        if not home or not away:
            continue

        # ----------------------------------------------------
        # SPIEL-ID
        # ----------------------------------------------------

        match_id = None

        # Suche Links in der Spielzeile
        for link in team_row.find_all(
            "a",
            href=True,
        ):

            href = link["href"]

            # Typische FUSSBALL.DE-Spiel-ID
            numbers = re.findall(
                r"\d{7,}",
                href,
            )

            if numbers:

                match_id = numbers[-1]
                break

        # Auch in der Wettbewerbzeile suchen
        if not match_id:

            for link in row.find_all(
                "a",
                href=True,
            ):

                numbers = re.findall(
                    r"\d{7,}",
                    link["href"],
                )

                if numbers:

                    match_id = numbers[-1]
                    break

        # ----------------------------------------------------
        # WETTBEWERB
        # ----------------------------------------------------

        competition = ""

        # Letzte Spalte enthält laut dokumentierter Struktur
        # Spieltyp und Spielnummer.
        cells = row.find_all("td")

        if cells:

            last_cell = cells[-1]

            competition = clean_text(
                last_cell.get_text(
                    " ",
                    strip=True,
                )
            )

        # ----------------------------------------------------
        # SPIELORT
        # ----------------------------------------------------

        venue = ""

        venue_selectors = [
            ".club-matchplan-venue",
            ".column-venue",
            ".column-location",
            ".venue",
            ".location",
            ".club-matchplan-location",
        ]

        for selector in venue_selectors:

            venue_element = team_row.select_one(
                selector
            )

            if venue_element:

                venue = clean_text(
                    venue_element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if venue:
                    break

        # Auch die Wettbewerbzeile prüfen
        if not venue:

            for selector in venue_selectors:

                venue_element = row.select_one(
                    selector
                )

                if venue_element:

                    venue = clean_text(
                        venue_element.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if venue:
                        break

        # ----------------------------------------------------
        # SPIEL SPEICHERN
        # ----------------------------------------------------

        if not match_id:

            match_id = (
                f"{date.isoformat()}-"
                f"{home}-"
                f"{away}"
            )

        matches.append(
            {
                "id": str(match_id),
                "date": date,
                "home": home,
                "away": away,
                "venue": venue,
                "competition": competition,
                "team_id": team_id,
            }
        )

    return matches
# ============================================================
# KALENDER
# ============================================================

def deduplicate_matches(matches):

    unique = {}

    for match in matches:

        unique[
            match["id"]
        ] = match

    return sorted(
        unique.values(),
        key=lambda x: x["date"],
    )


def create_ics(team_name, matches):

    now = datetime.now(
        ZoneInfo("UTC")
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kickers 1916 Frankfurt//Spielplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ical_escape(team_name)}",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for match in matches:

        date = match["date"]

        dtstart = date.strftime(
            "%Y%m%dT%H%M%S"
        )

        uid = (
            f"fussball-de-{match['id']}"
            "@kickers16-kalender"
        )

        summary = (
            f"{match['home']} - "
            f"{match['away']}"
        )

        description = (
            "Quelle: FUSSBALL.DE"
        )

        if match["competition"]:

            description = (
                f"Wettbewerb: "
                f"{match['competition']}\\n"
                "Quelle: FUSSBALL.DE"
            )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{ical_escape(uid)}",
                f"DTSTAMP:{now}",
                (
                    "DTSTART;TZID=Europe/Berlin:"
                    f"{dtstart}"
                ),
                f"SUMMARY:{ical_escape(summary)}",
                (
                    "DESCRIPTION:"
                    f"{ical_escape(description)}"
                ),
            ]
        )

        if match["venue"]:

            lines.append(
                "LOCATION:"
                f"{ical_escape(match['venue'])}"
            )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    output = []

    for line in lines:

        output.extend(
            fold_ical_line(line)
        )

    return (
        "\r\n".join(output)
        + "\r\n"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "========================================"
    )
    print(
        "Kickers 1916 – Spielplan-Kalender"
    )
    print(
        "========================================"
    )

    for key, team in TEAMS.items():

        print()
        print(
            f"⚽ {team['name']}"
        )

        matches = fetch_team(
            team["id"]
        )

        matches = deduplicate_matches(
            matches
        )

        print(
            f"📅 {len(matches)} Spiele gefunden."
        )

        for match in matches:

            print(
                "   ",
                match["date"].strftime(
                    "%d.%m.%Y %H:%M"
                ),
                "|",
                match["home"],
                "-",
                match["away"],
            )

        ics = create_ics(
            team["name"],
            matches,
        )

        output_file = Path(
            OUTPUT_FILES[key]
        )

        output_file.write_text(
            ics,
            encoding="utf-8",
            newline="",
        )

        print(
            f"✅ {output_file} erstellt."
        )

    print()
    print(
        "🎉 Beide Kalender wurden erstellt."
    )


if __name__ == "__main__":
    main()
