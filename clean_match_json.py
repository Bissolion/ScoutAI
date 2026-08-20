"""
clean_match_json.py
--------------------
Limpa um arquivo JSON de partida de futebol (formato Opta/WhoScored, com
estatísticas aninhadas minuto-a-minuto) e exporta os dados em CSVs prontos
para análise:

  1. match_info.csv          - dados gerais da partida (placar, local, árbitro...)
  2. players.csv              - escalação das duas equipes
  3. player_match_stats.csv   - estatísticas totais por jogador (gols, passes, notas...)
  4. team_match_stats.csv     - estatísticas totais por equipe (posse, finalizações...)
  5. events.csv                - todos os eventos da partida (passes, chutes, faltas...)
  6. match_incidents.csv      - gols, cartões e substituições em ordem cronológica
  7. formations.csv           - esquemas táticos usados por cada equipe
  8. starting_xi.csv          - os 11 titulares de cada equipe, com número da
                                 camisa, posição, coordenadas táticas (vertical/
                                 horizontal, úteis p/ desenhar a escalação) e capitão

USO:
    python3 clean_match_json.py caminho/para/Match.json [pasta_de_saida]

Se a pasta de saída não for informada, os CSVs são gravados em "./out".
"""

import json
import csv
import os
import sys


# ---------------------------------------------------------------
# Agregação das estatísticas minuto-a-minuto (jogador e equipe)
# ---------------------------------------------------------------
NON_SUM_FIELDS = {"ratings", "passSuccess", "aerialSuccess", "tackleSuccess",
                   "throwInAccuracy", "dribbleSuccess", "possession"}

# (coluna de saída, campo numerador, campo denominador) -> recalcula o % real
# a partir dos totais somados, em vez de somar percentuais minuto a minuto
PCT_FORMULAS = [
    ("passSuccessPct", "passesAccurate", "passesTotal"),
    ("aerialSuccessPct", "aerialsWon", "aerialsTotal"),
    ("tackleSuccessPct", "tackleSuccessful", "tacklesTotal"),
    ("throwInAccuracyPct", "throwInsAccurate", "throwInsTotal"),
    ("dribbleSuccessPct", "dribblesWon", "dribblesAttempted"),
    ("cornerAccuracyPct", "cornersAccurate", "cornersTotal"),
]


def aggregate_stats(stats_dict):
    """Transforma {estatistica: {minuto: valor}} em totais da partida."""
    agg = {}
    for stat_name, minute_map in stats_dict.items():
        if stat_name == "minutesWithStats" or not isinstance(minute_map, dict):
            continue
        if stat_name in NON_SUM_FIELDS:
            continue  # tratados à parte (nota final / percentuais recalculados)
        agg[stat_name] = round(sum(minute_map.values()), 2)

    # nota final = última nota média registrada (é uma média corrida)
    if stats_dict.get("ratings"):
        last_minute = max(int(m) for m in stats_dict["ratings"])
        agg["rating"] = round(stats_dict["ratings"][str(last_minute)], 2)

    # percentuais recalculados a partir dos totais (mais correto que somar %)
    for out_name, num_field, den_field in PCT_FORMULAS:
        num, den = agg.get(num_field, 0), agg.get(den_field, 0)
        agg[out_name] = round(num / den * 100, 1) if den else 0
    return agg


def build_goals_assists(events):
    """Conta gols e assistências por jogador a partir do log de eventos."""
    goals, assists = {}, {}
    for e in events:
        qual_names = {q["type"]["displayName"] for q in e.get("qualifiers", [])}
        if e["type"]["displayName"] == "Goal" and e["outcomeType"]["displayName"] == "Successful":
            if "OwnGoal" not in qual_names:
                pid = e.get("playerId")
                goals[pid] = goals.get(pid, 0) + 1
        if "IntentionalGoalAssist" in qual_names:
            pid = e.get("playerId")
            assists[pid] = assists.get(pid, 0) + 1
    return goals, assists


def write_match_info(data, home, away, out_dir):
    ref = data.get("referee", {})
    info = {
        "matchDate": data.get("startDate", "").split("T")[0],
        "kickOffLocalTime": data.get("startTime", ""),
        "venue": data.get("venueName", ""),
        "attendance": data.get("attendance", ""),
        "referee": ref.get("name", ""),
        "homeTeam": home.get("name", ""),
        "homeTeamId": home.get("teamId", ""),
        "homeManager": home.get("managerName", ""),
        "awayTeam": away.get("name", ""),
        "awayTeamId": away.get("teamId", ""),
        "awayManager": away.get("managerName", ""),
        "finalScore": data.get("ftScore", data.get("score", "")),
        "halfTimeScore": data.get("htScore", ""),
        "homeGoals": home.get("scores", {}).get("fulltime", ""),
        "awayGoals": away.get("scores", {}).get("fulltime", ""),
        "status": data.get("elapsed", ""),
        "weatherCode": data.get("weatherCode", ""),
    }
    with open(f"{out_dir}/match_info.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["field", "value"])
        for k, v in info.items():
            w.writerow([k, v])


def write_players(home, away, out_dir):
    rows = []
    for side in (home, away):
        for p in side["players"]:
            rows.append({
                "playerId": p.get("playerId"), "name": p.get("name"),
                "team": side["name"], "teamId": side["teamId"], "field": p.get("field"),
                "shirtNo": p.get("shirtNo"), "position": p.get("position"), "age": p.get("age"),
                "heightCm": p.get("height"), "weightKg": p.get("weight"),
                "isFirstEleven": p.get("isFirstEleven"), "isManOfTheMatch": p.get("isManOfTheMatch"),
            })
    with open(f"{out_dir}/players.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_player_match_stats(data, home, away, out_dir):
    goals, assists = build_goals_assists(data["events"])

    per_player = []
    all_cols = set()
    for side in (home, away):
        for p in side["players"]:
            agg = aggregate_stats(p.get("stats", {}))
            all_cols.update(agg.keys())
            per_player.append((side, p, agg))

    cols = sorted(all_cols)
    fieldnames = ["playerId", "name", "team", "position", "goals", "assists"] + cols
    with open(f"{out_dir}/player_match_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for side, p, agg in per_player:
            row = {
                "playerId": p.get("playerId"), "name": p.get("name"), "team": side["name"],
                "position": p.get("position"),
                "goals": goals.get(p.get("playerId"), 0),
                "assists": assists.get(p.get("playerId"), 0),
            }
            for c in cols:
                row[c] = agg.get(c, 0)
            w.writerow(row)


def write_team_match_stats(home, away, out_dir):
    rows = []
    for side in (home, away):
        agg = aggregate_stats(side.get("stats", {}))
        row = {"team": side["name"], "teamId": side["teamId"], "field": side["field"],
               "averageAge": side.get("averageAge")}
        row.update(agg)
        rows.append(row)

    # posse de bola: soma dos contadores por minuto, convertida em % do total geral
    totals = [sum(side["stats"].get("possession", {}).values()) for side in (home, away)]
    grand_total = sum(totals)
    for row, total in zip(rows, totals):
        row["possessionPct"] = round(total / grand_total * 100, 1) if grand_total else 0

    fixed = ["team", "teamId", "field", "averageAge", "possessionPct"]
    other_cols = sorted({k for r in rows for k in r if k not in fixed})
    with open(f"{out_dir}/team_match_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fixed + other_cols)
        w.writeheader()
        w.writerows(rows)


def write_events(data, team_name, player_names, out_dir):
    qual_cols = ["Zone", "PassEndX", "PassEndY", "Length", "Angle"]
    fieldnames = (["id", "eventId", "minute", "second", "period", "team", "teamId",
                   "playerId", "playerName", "type", "outcome", "x", "y", "endX", "endY",
                   "isTouch"] + qual_cols + ["otherQualifiers"])
    with open(f"{out_dir}/events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in data["events"]:
            qual_map, other = {}, []
            for q in e.get("qualifiers", []):
                qname, qval = q["type"]["displayName"], q.get("value", "")
                if qname in qual_cols:
                    qual_map[qname] = qval
                else:
                    other.append(qname if qval == "" else f"{qname}={qval}")
            pid = e.get("playerId")
            row = {
                "id": e.get("id"), "eventId": e.get("eventId"), "minute": e.get("minute"),
                "second": e.get("second"), "period": e.get("period", {}).get("displayName"),
                "team": team_name.get(e.get("teamId"), ""), "teamId": e.get("teamId"),
                "playerId": pid, "playerName": player_names.get(str(pid), ""),
                "type": e.get("type", {}).get("displayName"),
                "outcome": e.get("outcomeType", {}).get("displayName"),
                "x": e.get("x"), "y": e.get("y"), "endX": e.get("endX", ""), "endY": e.get("endY", ""),
                "isTouch": e.get("isTouch"), "otherQualifiers": "; ".join(other),
            }
            row.update({c: qual_map.get(c, "") for c in qual_cols})
            w.writerow(row)


def write_incidents(home, away, player_names, out_dir):
    rows = []
    for side in (home, away):
        for e in side.get("incidentEvents", []):
            rows.append({
                "minute": e.get("minute"), "second": e.get("second"), "team": side["name"],
                "player": e.get("playerName", player_names.get(str(e.get("playerId")), "")),
                "type": e.get("type", {}).get("displayName"),
                "cardType": e.get("cardType", {}).get("displayName", ""),
                "description": e.get("text", ""),
            })
    rows.sort(key=lambda r: (r["minute"] or 0, r["second"] or 0))
    with open(f"{out_dir}/match_incidents.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["minute", "second", "team", "player", "type", "cardType", "description"])
        w.writeheader()
        w.writerows(rows)


def write_formations(home, away, player_names, out_dir):
    with open(f"{out_dir}/formations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["team", "formationName", "period", "startMinute", "endMinute", "captain"])
        w.writeheader()
        for side in (home, away):
            for form in side.get("formations", []):
                w.writerow({
                    "team": side["name"], "formationName": form.get("formationName"),
                    "period": form.get("period"), "startMinute": form.get("startMinuteExpanded"),
                    "endMinute": form.get("endMinuteExpanded"),
                    "captain": player_names.get(str(form.get("captainPlayerId")), ""),
                })


def write_starting_xi(home, away, player_names, out_dir):
    """Os 11 titulares de cada equipe, com posição tática (formação inicial).

    A formação usada é a que começa no minuto 0 (kickoff). Nela, os 11
    primeiros jogadores de 'playerIds' já vêm alinhados por índice com
    'formationSlots' (1 a 11) e 'formationPositions' (coordenadas vertical/
    horizontal do esquema tático), então basta pegar os 11 primeiros de cada.
    """
    rows = []
    for side in (home, away):
        formations = side.get("formations", [])
        if not formations:
            continue
        # formação do apito inicial = a de menor startMinuteExpanded
        starting_formation = min(formations, key=lambda f: f.get("startMinuteExpanded", 0))
        player_ids = starting_formation.get("playerIds", [])
        jersey_numbers = starting_formation.get("jerseyNumbers", [])
        slots = starting_formation.get("formationSlots", [])
        positions = starting_formation.get("formationPositions", [])
        captain_id = starting_formation.get("captainPlayerId")

        # mapa auxiliar playerId -> dados de bio (posição, nome já sabemos)
        bio_by_id = {p.get("playerId"): p for p in side["players"]}

        for i, pid in enumerate(player_ids):
            slot = slots[i] if i < len(slots) else 0
            if not slot or slot < 1:
                continue  # 0 = reserva, não jogou como titular nesta formação
            pos = positions[slot - 1] if slot - 1 < len(positions) else {}
            bio = bio_by_id.get(pid, {})
            rows.append({
                "team": side["name"],
                "formation": starting_formation.get("formationName"),
                "slot": slot,
                "playerId": pid,
                "name": player_names.get(str(pid), bio.get("name", "")),
                "shirtNo": jersey_numbers[i] if i < len(jersey_numbers) else bio.get("shirtNo"),
                "position": bio.get("position", ""),
                "vertical": pos.get("vertical", ""),
                "horizontal": pos.get("horizontal", ""),
                "isCaptain": pid == captain_id,
            })

    rows.sort(key=lambda r: (r["team"], r["slot"]))
    fieldnames = ["team", "formation", "slot", "playerId", "name", "shirtNo",
                  "position", "vertical", "horizontal", "isCaptain"]
    with open(f"{out_dir}/starting_xi.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 clean_match_json.py caminho/para/Match.json [pasta_de_saida]")
        sys.exit(1)

    src = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "out"
    os.makedirs(out_dir, exist_ok=True)

    with open(src, encoding="utf-8-sig") as f:
        data = json.load(f)

    home, away = data["home"], data["away"]
    player_names = data["playerIdNameDictionary"]
    team_name = {home["teamId"]: home["name"], away["teamId"]: away["name"]}

    write_match_info(data, home, away, out_dir)
    write_players(home, away, out_dir)
    write_player_match_stats(data, home, away, out_dir)
    write_team_match_stats(home, away, out_dir)
    write_events(data, team_name, player_names, out_dir)
    write_incidents(home, away, player_names, out_dir)
    write_formations(home, away, player_names, out_dir)
    write_starting_xi(home, away, player_names, out_dir)

    print(f"Concluído. CSVs gravados em: {out_dir}")
    for fn in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, fn)
        with open(path, encoding="utf-8") as f:
            n = sum(1 for _ in f) - 1
        print(f" - {fn}: {n} linhas")


if __name__ == "__main__":
    main()
