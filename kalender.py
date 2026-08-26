import html
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


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
    "mime-type/JSON/"
    "team-id/{team_id}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 Kickers16-Kalender/1.0",
    "Accept": "application/json,text/plain,*/*",
}


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


def fold_line(line, limit=75):
    if len(line) <= limit:
        return [line]

    result = []

    while len(line) > limit:
        result.append(line[:limit])
        line = " " + line[limit:]

    result.append(line)

    return result


def parse_date(text):
    text = clean_text(text)

    patterns = [
        r"(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}:\d{2})",
        r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})",
        r"(\d{2}\.\d{2}\.\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            if len(match.groups()) == 2:
                return datetime.strptime(
                    f"{match.group(1)} {match.group(2)}",
                    "%d.%m.%Y %H:%M",
                )

            return datetime.strptime(
                match.group(1),
                "%d.%m.%Y",
            )

        except ValueError:
            pass

    return None


def extract_matches(html_content, team_id):
    soup = BeautifulSoup(
        html_content,
        "html.parser",
    )

    matches = []

    rows = soup.select(
        "div.club-matchplan-table tr.row-competition"
    )

    if not rows:
        rows = soup.select(
            "tr.row-competition"
        )

    print(
        f"🔎 {len(rows)} Spieltag-Zeilen gefunden."
    )

    for row in rows:

        # --------------------------------------------------
        # Datum
        # --------------------------------------------------

        date_cell = row.select_one(
            "td.column-date"
        )

        if not date_cell:
            date_cell = row.find("td")

        if not date_cell:
            continue

        date = parse_date(
            date_cell.get_text(
                " ",
                strip=True,
            )
        )

        if not date:
            continue

        # --------------------------------------------------
        # Mannschaftszeile
        # --------------------------------------------------

        team_row = row.find_next_sibling("tr")

        if not team_row:
            continue

        # --------------------------------------------------
        # Mannschaftsnamen
        # --------------------------------------------------

        clubs = team_row.select(
            "div.club-name"
        )

        names = []

        for club in clubs:

            name = clean_text(
                club.get_text(
                    " ",
                    strip=True,
                )
            )

            if name and name not in names:
                names.append(name)

        if len(names) < 2:

            # Fallback: Links mit Mannschaftsnamen
            for link in team_row.find_all(
                "a"
            ):

                name = clean_text(
                    link.get_text(
                        " ",
                        strip=True,
                    )
                )

                if (
                    name
                    and len(name) > 2
                    and name not in names
                ):
                    names.append(name)

        if len(names) < 2:
            continue

        home = names[0]
        away = names[1]

        # --------------------------------------------------
        # Spiel-ID
        # --------------------------------------------------

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

        if not match_id:
            match_id = (
                f"{date.isoformat()}-"
                f"{home}-"
                f"{away}"
            )

        # --------------------------------------------------
        # Spielort
        # --------------------------------------------------

        venue = ""

        for selector in [
            ".club-matchplan-venue",
            ".column-venue",
            ".column-location",
            ".venue",
            ".location",
        ]:

            element = team_row.select_one(
                selector
            )

            if element:

                venue = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if venue:
                    break

        # --------------------------------------------------
        # Wettbewerb
        # --------------------------------------------------

        competition = ""

        for selector in [
            ".competition",
            ".column-competition",
        ]:

            element = row.select_one(
                selector
            )

            if element:

                competition = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if competition:
                    break

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

    # FUSSBALL.DE liefert JSON,
    # dessen "html"-Feld den Spielplan enthält.

    try:
        data = response.json()
    except Exception as error:

        print(
            response.text[:2000]
        )

        raise RuntimeError(
            "Antwort von FUSSBALL.DE ist kein JSON."
        ) from error

    print(
        "JSON-Schlüssel:",
        list(data.keys())
        if isinstance(data, dict)
        else type(data),
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            "Unerwartete Antwort von FUSSBALL.DE."
        )

    if not data.get("success"):
        raise RuntimeError(
            "FUSSBALL.DE meldet success=false."
        )

    html_content = data.get("html")

    if not html_content:
        raise RuntimeError(
            "FUSSBALL.DE liefert kein HTML im JSON."
        )

    print(
        f"Spielplan-HTML: {len(html_content)} Zeichen"
    )

    matches = extract_matches(
        html_content,
        team_id,
    )

    if not matches:

        print()
        print(
            "❌ Keine Spiele erkannt."
        )

        print(
            "HTML-Ausschnitt:"
        )

        print(
            html_content[:5000]
        )

        raise RuntimeError(
            "Der Spielplan konnte nicht aus dem "
            "FUSSBALL.DE-HTML gelesen werden."
        )

    print(
        f"✅ {len(matches)} Spiele gelesen."
    )

    return matches


def deduplicate(matches):

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
            "@kickers16-kalender"
        )

        summary = (
            f"{match['home']} - "
            f"{match['away']}"
        )

        description = "Quelle: FUSSBALL.DE"

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
            fold_line(line)
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

        matches = deduplicate(
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
