import html
import re
import requests
import xml.etree.ElementTree as ET
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

BASE_URL = (
    "https://www.fussball.de/ajax.team.matchplan/-/"
    "datum-von/{start}/"
    "datum-bis/{end}/"
    "max/100/"
    "match-type/-1/"
    "wettkampftyp/-1/"
    "show-venues/true/"
    "mime-type/XML/"
    "team-id/{team_id}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "Kickers16-Kalender/1.0)"
    ),
    "Accept": "application/xml,text/xml,text/html,*/*",
}


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
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
    Versucht verschiedene bekannte FUSSBALL.DE-Datumsformate.
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
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def find_value(element, possible_names):
    """
    Sucht einen Wert sowohl als XML-Element als auch als Attribut.
    """

    names_lower = {
        name.lower()
        for name in possible_names
    }

    # Attribute
    for key, value in element.attrib.items():
        if key.lower() in names_lower:
            return clean_text(value)

    # Unterelemente
    for child in element.iter():
        tag = child.tag.split("}")[-1].lower()

        if tag in names_lower and child.text:
            return clean_text(child.text)

    return ""


def extract_matches_from_xml(content, team_id):
    """
    Versucht die XML-Antwort möglichst tolerant auszulesen.

    Da FUSSBALL.DE seine XML-Struktur im Laufe der Jahre
    verändert hat, suchen wir nach Elementen, die wie
    Spiele/Matches aussehen.
    """

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []


    matches = []

    # Alle XML-Elemente durchsuchen
    for element in root.iter():

        tag = element.tag.split("}")[-1].lower()

        if tag not in {
            "match",
            "game",
            "spiel",
            "matchplanentry",
            "matchplan",
        }:
            continue

        match_id = find_value(
            element,
            [
                "id",
                "matchid",
                "match-id",
                "gameid",
                "game-id",
            ],
        )

        date_value = find_value(
            element,
            [
                "matchmoment",
                "match-moment",
                "datetime",
                "date",
                "datum",
                "start",
                "startdate",
            ],
        )

        date = parse_date(date_value)

        if not date:
            continue

        home = find_value(
            element,
            [
                "homeTeam",
                "home-team",
                "hometeam",
                "home",
                "heim",
                "heimteam",
            ],
        )

        away = find_value(
            element,
            [
                "awayTeam",
                "away-team",
                "awayteam",
                "away",
                "gast",
                "gastteam",
            ],
        )

        # Falls home/away als verschachtelte Objekte vorliegen,
        # Namen darin suchen.
        if not home or not away:

            team_names = []

            for child in element.iter():

                child_tag = (
                    child.tag.split("}")[-1].lower()
                )

                if child_tag in {
                    "name",
                    "teamname",
                    "team-name",
                } and child.text:

                    name = clean_text(child.text)

                    if name and name not in team_names:
                        team_names.append(name)

            if len(team_names) >= 2:
                home = team_names[0]
                away = team_names[1]

        if not home or not away:
            continue

        venue = find_value(
            element,
            [
                "venue",
                "location",
                "stadium",
                "spielort",
                "sportstaette",
            ],
        )

        competition = find_value(
            element,
            [
                "competition",
                "competitionname",
                "competition-name",
                "wettbewerb",
                "wettkampf",
            ],
        )

        matches.append(
            {
                "id": match_id or (
                    f"{date.isoformat()}-{home}-{away}"
                ),
                "date": date,
                "home": home,
                "away": away,
                "venue": venue,
                "competition": competition,
                "team_id": team_id,
            }
        )

    return matches


def extract_matches_from_html(content, team_id):
    """
    Fallback für HTML-Antworten.

    Die bekannte FUSSBALL.DE-Struktur verwendet:
        tr.row-competition
        danach eine Tabellenzeile mit den Mannschaften.
    """

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        content,
        "html.parser",
    )

    matches = []

    rows = soup.select(
        "div.club-matchplan-table tr.row-competition"
    )

    for row in rows:

        # Datum/Uhrzeit
        date_cell = row.select_one(
            "td.column-date"
        )

        if not date_cell:
            continue

        date_text = clean_text(
            date_cell.get_text(" ", strip=True)
        )

        date = None

        # Typisches Format:
        # 30.08.2026 - 13:00
        match = re.search(
            r"(\d{2}\.\d{2}\.\d{4})"
            r"(?:\s*-\s*(\d{2}:\d{2}))?",
            date_text,
        )

        if match:

            date_string = match.group(1)

            if match.group(2):
                date_string += (
                    " " + match.group(2)
                )

                date = datetime.strptime(
                    date_string,
                    "%d.%m.%Y %H:%M",
                )
            else:
                date = datetime.strptime(
                    date_string,
                    "%d.%m.%Y",
                )

        if not date:
            continue

        # Laut dokumentierter Struktur liegen die
        # Mannschaften in der folgenden Tabellenzeile.
        team_row = row.find_next_sibling("tr")

        if not team_row:
            continue

        clubs = team_row.select(
            "td.colum-club div.club-name"
        )

        if len(clubs) < 2:
            clubs = team_row.select(
                "div.club-name"
            )

        if len(clubs) < 2:
            continue

        home = clean_text(
            clubs[0].get_text(" ", strip=True)
        )

        away = clean_text(
            clubs[1].get_text(" ", strip=True)
        )

        if not home or not away:
            continue

        # Spiel-ID
        match_id = None

        for link in team_row.find_all(
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

        # Spielort
        venue = ""

        venue_element = team_row.select_one(
            ".club-matchplan-venue"
        )

        if venue_element:
            venue = clean_text(
                venue_element.get_text(
                    " ",
                    strip=True,
                )
            )

        matches.append(
            {
                "id": match_id or (
                    f"{date.isoformat()}-{home}-{away}"
                ),
                "date": date,
                "home": home,
                "away": away,
                "venue": venue,
                "competition": "",
                "team_id": team_id,
            }
        )

    return matches


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

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "FUSSBALL.DE hat eine leere Antwort geliefert."
        )

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    print(
        f"Content-Type: {content_type}"
    )

    # Zuerst XML versuchen
    matches = extract_matches_from_xml(
        response.content,
        team_id,
    )

    if matches:
        print(
            f"✅ XML-Parser: {len(matches)} Spiele"
        )
        return matches

    # Danach HTML versuchen
    matches = extract_matches_from_html(
        response.content,
        team_id,
    )

    if matches:
        print(
            f"✅ HTML-Parser: {len(matches)} Spiele"
        )
        return matches

    # Sehr wichtig:
    # Wenn FUSSBALL.DE aktuell eine Fehlerseite liefert,
    # zeigen wir einen Ausschnitt davon.
    preview = response.text[:1000]

    print()
    print("===== ANTWORT VON FUSSBALL.DE =====")
    print(preview)
    print("===================================")

    raise RuntimeError(
        "FUSSBALL.DE hat zwar eine Antwort geliefert, "
        "aber darin wurden keine Spiele gefunden."
    )


def deduplicate_matches(matches):

    unique = {}

    for match in matches:
        unique[match["id"]] = match

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
            f"@kickers16-kalender"
        )

        summary = (
            f"{match['home']} - "
            f"{match['away']}"
        )

        description = (
            f"Quelle: FUSSBALL.DE"
        )

        if match["competition"]:
            description = (
                f"Wettbewerb: "
                f"{match['competition']}\\n"
                f"Quelle: FUSSBALL.DE"
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

        ics_content = create_ics(
            team["name"],
            matches,
        )

        output_file = Path(
            OUTPUT_FILES[key]
        )

        output_file.write_text(
            ics_content,
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
