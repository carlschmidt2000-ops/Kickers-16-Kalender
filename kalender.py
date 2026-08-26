import re
import html
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


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

# Saison 2026/27
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
    "mime-type/HTML/"
    "team-id/{team_id}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "Kickers16-Kalender/1.0; "
        "+https://github.com/carlschmidt2000-ops/kickers16-kalender)"
    )
}


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(text):
    """Entfernt überflüssige Leerzeichen und HTML-Entities."""
    text = html.unescape(text or "")
    return " ".join(text.split()).strip()


def ical_escape(text):
    """Escaping für iCalendar-Text."""
    if not text:
        return ""

    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ical_line(line, limit=75):
    """
    Faltet lange iCalendar-Zeilen gemäß RFC 5545.
    """
    if len(line) <= limit:
        return [line]

    result = []

    while len(line) > limit:
        result.append(line[:limit])
        line = " " + line[limit:]

    result.append(line)

    return result


def extract_date(text):
    """
    Versucht ein Datum mit Uhrzeit aus einem Text zu lesen.

    Beispiele:
    30.08.2026 - 13:00
    30.08.2026
    """

    text = clean_text(text)

    match = re.search(
        r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}:\d{2})",
        text,
    )

    if match:
        return datetime.strptime(
            f"{match.group(1)} {match.group(2)}",
            "%d.%m.%Y %H:%M",
        )

    match = re.search(
        r"(\d{2}\.\d{2}\.\d{4})",
        text,
    )

    if match:
        return datetime.strptime(
            match.group(1),
            "%d.%m.%Y",
        )

    return None


def find_match_id(row):
    """
    Sucht die FUSSBALL.DE-Spiel-ID in einer Tabellenzeile.
    """

    # Zuerst nach Links auf eine Spielseite suchen
    for link in row.find_all("a", href=True):
        href = link["href"]

        match = re.search(
            r"/spiel/[^/]+/[^/]+/-/spiel/(\d+)",
            href,
        )

        if match:
            return match.group(1)

        # Allgemeiner Fallback: letzte längere Zahl aus dem Link
        numbers = re.findall(r"\d{7,}", href)

        if numbers:
            return numbers[-1]

    # Fallback: nach data-Attributen suchen
    for tag in row.find_all(True):
        for key, value in tag.attrs.items():
            if isinstance(value, str):
                match = re.search(
                    r"(?:match|spiel)[-_]?(?:id)?[=:/-]?(\d{7,})",
                    value,
                    re.IGNORECASE,
                )

                if match:
                    return match.group(1)

    return None


def extract_teams(row):
    """
    Versucht Heim- und Gastmannschaft aus einer Spielzeile zu lesen.
    """

    names = []

    for element in row.select(".club-name"):
        name = clean_text(element.get_text(" ", strip=True))

        if name and name not in names:
            names.append(name)

    if len(names) >= 2:
        return names[0], names[1]

    # Fallback: alternative Schreibweisen
    for selector in [
        ".column-club .club-name",
        ".colum-club .club-name",
        ".club-name",
    ]:
        names = []

        for element in row.select(selector):
            name = clean_text(element.get_text(" ", strip=True))

            if name and name not in names:
                names.append(name)

        if len(names) >= 2:
            return names[0], names[1]

    return None, None


def extract_venue(row):
    """
    Versucht den Spielort / die Spielstätte zu finden.
    """

    selectors = [
        ".venue",
        ".club-matchplan-venue",
        ".column-venue",
        ".column-location",
        ".venue-name",
        ".location",
    ]

    for selector in selectors:
        element = row.select_one(selector)

        if element:
            value = clean_text(element.get_text(" ", strip=True))

            if value:
                return value

    # Allgemeiner Fallback anhand typischer Klassen
    for element in row.find_all(True):
        classes = " ".join(element.get("class", []))

        if any(
            word in classes.lower()
            for word in ["venue", "location", "spielort"]
        ):
            value = clean_text(element.get_text(" ", strip=True))

            if value:
                return value

    return ""


def extract_competition(row):
    """
    Ermittelt möglichst den Wettbewerb.
    """

    selectors = [
        ".column-competition",
        ".competition",
        ".club-matchplan-competition",
    ]

    for selector in selectors:
        element = row.select_one(selector)

        if element:
            value = clean_text(element.get_text(" ", strip=True))

            if value:
                return value

    # Fallback: Text der Zeile
    text = clean_text(row.get_text(" ", strip=True))

    for keyword in [
        "Kreisliga",
        "Kreispokal",
        "Pokal",
        "Meisterschaft",
        "Freundschaft",
    ]:
        if keyword.lower() in text.lower():
            return keyword

    return ""


def parse_matches(html_content, team_id):
    """
    Liest den Spielplan aus der FUSSBALL.DE-HTML-Antwort.
    """

    soup = BeautifulSoup(html_content, "html.parser")

    matches = []

    # FUSSBALL.DE gruppiert Spiele über row-competition.
    rows = soup.select("tr.row-competition")

    if not rows:
        # Fallback für mögliche spätere HTML-Änderungen
        rows = soup.select("tr")

    for row in rows:
        text = clean_text(row.get_text(" ", strip=True))

        if not text:
            continue

        # Datum suchen
        date = extract_date(text)

        if not date:
            # Manche Versionen verteilen Datum und Uhrzeit
            date_element = row.select_one(".column-date")

            if date_element:
                date = extract_date(
                    date_element.get_text(" ", strip=True)
                )

        if not date:
            continue

        home, away = extract_teams(row)

        # Bei der alten Struktur können die Mannschaftsnamen
        # in der folgenden Zeile stehen.
        if not home or not away:
            next_row = row.find_next("tr")

            if next_row:
                home2, away2 = extract_teams(next_row)

                if home2 and away2:
                    home = home2
                    away = away2

                    combined_text = (
                        row.get_text(" ", strip=True)
                        + " "
                        + next_row.get_text(" ", strip=True)
                    )

                    match_id = (
                        find_match_id(row)
                        or find_match_id(next_row)
                    )

                    venue = (
                        extract_venue(row)
                        or extract_venue(next_row)
                    )

                    competition = (
                        extract_competition(row)
                        or extract_competition(next_row)
                    )

                    text = clean_text(combined_text)

                else:
                    match_id = find_match_id(row)
                    venue = extract_venue(row)
                    competition = extract_competition(row)
            else:
                match_id = find_match_id(row)
                venue = extract_venue(row)
                competition = extract_competition(row)
        else:
            match_id = find_match_id(row)
            venue = extract_venue(row)
            competition = extract_competition(row)

        if not home or not away:
            print(
                f"⚠️ Mannschaften konnten nicht erkannt werden: {text}"
            )
            continue

        # "spielfrei" soll kein Kalendertermin werden
        if "spielfrei" in home.lower() or "spielfrei" in away.lower():
            continue

        # Eine stabile ID ist für Kalender-Updates wichtig.
        # Wenn FUSSBALL.DE keine ID liefert, erzeugen wir eine.
        if not match_id:
            match_id = (
                f"{date.isoformat()}-"
                f"{home}-{away}"
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


def fetch_team(team_id):
    """
    Ruft den kompletten Saison-Spielplan einer Mannschaft ab.
    """

    url = BASE_URL.format(
        start=START_DATE,
        end=END_DATE,
        team_id=team_id,
    )

    print(f"🌐 Lade Spielplan: {team_id}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(
            "FUSSBALL.DE hat eine leere Antwort geliefert."
        )

    matches = parse_matches(
        response.text,
        team_id,
    )

    if not matches:
        raise RuntimeError(
            "Es wurden keine Spiele gefunden. "
            "Möglicherweise hat FUSSBALL.DE seine HTML-Struktur geändert "
            "oder liefert momentan keine Daten."
        )

    return matches


def deduplicate_matches(matches):
    """
    Entfernt doppelte Spiele.
    """

    unique = {}

    for match in matches:
        unique[match["id"]] = match

    return list(unique.values())


def create_ics(team_name, matches):
    """
    Erstellt eine vollständige iCalendar-Datei.
    """

    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kickers 1916 Frankfurt//Spielplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ical_escape(team_name)}",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for match in sorted(
        matches,
        key=lambda x: x["date"],
    ):
        date = match["date"]

        # Deutschland: Kalendertermine werden mit Zeitzone
        # Europe/Berlin gespeichert.
        dtstart = date.strftime("%Y%m%dT%H%M%S")

        uid = (
            f"fussball-de-{match['id']}"
            f"@kickers16-kalender"
        )

        summary = (
            f"{match['home']} - {match['away']}"
        )

        description_parts = []

        if match["competition"]:
            description_parts.append(
                f"Wettbewerb: {match['competition']}"
            )

        description_parts.append(
            "Quelle: FUSSBALL.DE"
        )

        description = "\\n".join(
            description_parts
        )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{ical_escape(uid)}",
                f"DTSTAMP:{now}",
                f"DTSTART;TZID=Europe/Berlin:{dtstart}",
                f"SUMMARY:{ical_escape(summary)}",
                f"DESCRIPTION:{ical_escape(description)}",
            ]
        )

        if match["venue"]:
            lines.append(
                f"LOCATION:{ical_escape(match['venue'])}"
            )

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    # RFC-konforme Zeilenumbrüche
    output = []

    for line in lines:
        output.extend(
            fold_ical_line(line)
        )

    return "\r\n".join(output) + "\r\n"


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():
    print("========================================")
    print("Kickers 1916 – Spielplan-Kalender")
    print("========================================")

    for key, team in TEAMS.items():
        print()
        print(f"⚽ {team['name']}")

        matches = fetch_team(team["id"])

        matches = deduplicate_matches(matches)

        print(
            f"   {len(matches)} Spiele gefunden."
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
            f"   ✅ gespeichert: {output_file}"
        )

    print()
    print("✅ Beide Kalender wurden erfolgreich erstellt.")


if __name__ == "__main__":
    main()
