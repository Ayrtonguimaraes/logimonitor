"""LogiMonitor: regras logísticas, API HTTP e persistência em SQLite ou PostgreSQL."""
import argparse
import json
import os
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

import assistant

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'dados.sqlite3'
DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL')
HOSTED = os.getenv('VERCEL') == '1'
OPERATION_TZ = ZoneInfo('America/Recife') if HOSTED else None
DATABASE_LOCK = 926518430
INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row
    INTEGRITY_ERRORS += (psycopg.IntegrityError,)
STATIC_FILES = {'app.css': 'text/css; charset=utf-8', 'app.js': 'application/javascript; charset=utf-8'}
LOCAL_HOSTS = ('localhost', '127.0.0.1')

TREATED = 'Ocorrência tratada'
INCIDENTS = ['Avaria', 'Extravio', 'Tentativa sem sucesso']
EVENTS = ['Coletado', 'Em trânsito', 'Saiu para entrega', 'Entregue'] + INCIDENTS + [TREATED]

# Parâmetros didáticos definidos pelo grupo (aparecem na aba "Conceitos e regras").
STALE_HOURS = 24            # sem movimentação há mais de X horas
RISK_HOURS = 12             # prazo vence em até X horas e a entrega ainda não saiu para entrega
CRITICAL_OVERDUE_HOURS = 24  # atraso acima de X horas é crítico
TARGETS = {
    'otd': {'label': 'Entregas no prazo (OTD)', 'good': 95, 'warn': 85, 'higher_is_better': True, 'unit': '%'},
    'incidents': {'label': 'Taxa de ocorrências', 'good': 5, 'warn': 10, 'higher_is_better': False, 'unit': '%'},
    'late': {'label': 'Entregas em atraso', 'good': 0, 'warn': 3, 'higher_is_better': False, 'unit': ''},
}
SEVERITY_RANK = {'warning': 1, 'high': 2, 'critical': 3}

# Rotas planejadas a partir do CD de Recife: (local, evento ao chegar, horas previstas desde a parada anterior).
# Dados fictícios: servem para comparar o tempo real de cada trecho com o previsto.
ROUTES = {
    'João Pessoa/PB': [('CD Recife/PE', 'Coletado', 0), ('Hub Recife/PE', 'Em trânsito', 8),
                       ('Hub João Pessoa/PB', 'Em trânsito', 10), ('Hub João Pessoa/PB', 'Saiu para entrega', 12),
                       ('João Pessoa/PB', 'Entregue', 6)],
    'Natal/RN': [('CD Recife/PE', 'Coletado', 0), ('Hub Recife/PE', 'Em trânsito', 8),
                 ('Hub João Pessoa/PB', 'Em trânsito', 10), ('Hub Natal/RN', 'Em trânsito', 12),
                 ('Hub Natal/RN', 'Saiu para entrega', 12), ('Natal/RN', 'Entregue', 6)],
    'Fortaleza/CE': [('CD Recife/PE', 'Coletado', 0), ('Hub Recife/PE', 'Em trânsito', 8),
                     ('Hub Campina Grande/PB', 'Em trânsito', 12), ('Hub Mossoró/RN', 'Em trânsito', 14),
                     ('Hub Fortaleza/CE', 'Em trânsito', 12), ('Hub Fortaleza/CE', 'Saiu para entrega', 10),
                     ('Fortaleza/CE', 'Entregue', 6)],
    'Maceió/AL': [('CD Recife/PE', 'Coletado', 0), ('Hub Recife/PE', 'Em trânsito', 8),
                  ('Hub Palmares/PE', 'Em trânsito', 8), ('Hub Maceió/AL', 'Em trânsito', 10),
                  ('Hub Maceió/AL', 'Saiu para entrega', 10), ('Maceió/AL', 'Entregue', 6)],
}
MOVES = {'Coletado', 'Em trânsito', 'Saiu para entrega', 'Entregue'}  # eventos que avançam no trajeto
TOLERANCE_HOURS = 2  # atraso de trecho abaixo disso é considerado dentro do previsto
SEED_NOTES = {'Coletado': 'Carga recebida no centro de distribuição.', 'Em trânsito': 'Chegada e triagem no hub.',
              'Saiu para entrega': 'Carga em rota de entrega ao cliente.', 'Entregue': 'Recebimento confirmado pelo cliente.'}
DEMO_SIZE = 60
# Casos-problema da demonstração (as demais entregas seguem a rota dentro do previsto):
# destino, horas desde a coleta, parada onde a entrega ficou, atraso extra em um hub e ocorrência registrada.
DEMO_CASES = {
    7: dict(destination='João Pessoa/PB', hours_ago=110, stop_at=('Hub João Pessoa/PB', 'Em trânsito')),
    19: dict(destination='João Pessoa/PB', hours_ago=90, stop_at=('Hub João Pessoa/PB', 'Em trânsito'),
             incident=('Avaria', 3, 'Hub João Pessoa/PB', 'Embalagem danificada na triagem do hub; aguardando tratativa.')),
    26: dict(destination='Fortaleza/CE', hours_ago=50, stop_at=('Hub Campina Grande/PB', 'Em trânsito')),
    34: dict(destination='Natal/RN', hours_ago=64, stop_at=('Hub Natal/RN', 'Saiu para entrega'),
             incident=('Tentativa sem sucesso', 10, 'Natal/RN', 'Cliente ausente no endereço; nova tentativa a combinar.')),
    41: dict(destination='Natal/RN', hours_ago=200, delay=('Hub Natal/RN', 30)),
    52: dict(destination='Fortaleza/CE', hours_ago=160, delay=('Hub Mossoró/RN', 22)),
}
ACTIONS = {
    'late': 'Cobrar a transportadora e informar o cliente sobre a nova previsão.',
    'risk': 'Confirmar com a transportadora se a entrega ocorrerá dentro do prazo.',
    'stale': 'Solicitar atualização do transporte.',
    'incident': 'Investigar a ocorrência, registrar a tratativa e marcar como tratada.',
    'loss': 'Acionar a transportadora e o seguro e avisar o cliente.',
}


def now():
    return datetime.now(OPERATION_TZ).replace(tzinfo=None, microsecond=0)


def stamp(value):
    return value.isoformat(timespec='seconds')


def hours_between(start, end):
    return (end - start).total_seconds() / 3600


def connect():
    if DATABASE_URL:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5, prepare_threshold=None)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


@contextmanager
def session(write=False):
    """Abre uma conexão, confirma (ou desfaz) a transação e sempre fecha a conexão."""
    con = connect()
    try:
        with con:
            if write:
                if DATABASE_URL:
                    con.execute('SELECT pg_advisory_xact_lock(%s)', (DATABASE_LOCK,))
                else:
                    con.execute('BEGIN IMMEDIATE')
            yield con
    finally:
        con.close()


def execute(con, sql, params):
    return con.execute(sql.replace('?', '%s') if DATABASE_URL else sql, params)


def insert_delivery(con, values):
    sql = 'INSERT INTO deliveries(code,client,origin,destination,carrier,collected,deadline) VALUES(?,?,?,?,?,?,?)'
    if DATABASE_URL:
        return execute(con, sql + ' RETURNING id', values).fetchone()['id']
    return execute(con, sql, values).lastrowid


def add_event(con, delivery_id, time, kind, location, note):
    execute(con, 'INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',
                (delivery_id, time, kind, location, note))


def initialize():
    primary_key = 'INTEGER GENERATED BY DEFAULT AS IDENTITY' if DATABASE_URL else 'INTEGER'
    with session(write=True) as con:
        con.execute(f'''CREATE TABLE IF NOT EXISTS deliveries (
        id {primary_key} PRIMARY KEY, code TEXT UNIQUE NOT NULL, client TEXT NOT NULL,
        origin TEXT NOT NULL, destination TEXT NOT NULL, carrier TEXT NOT NULL,
        collected TEXT NOT NULL, deadline TEXT NOT NULL)''')
        con.execute(f'''CREATE TABLE IF NOT EXISTS events (
        id {primary_key} PRIMARY KEY, delivery_id INTEGER NOT NULL REFERENCES deliveries(id),
        time TEXT NOT NULL, kind TEXT NOT NULL, location TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')''')
        if not con.execute('SELECT 1 FROM deliveries LIMIT 1').fetchone():
            seed_demo(con)


def seed_demo(con):
    base = now()
    destinations = ['João Pessoa/PB', 'Natal/RN', 'Fortaleza/CE', 'Maceió/AL']
    carriers = ['Rota Nordeste', 'Expresso Litoral', 'Via Cargas']
    for i in range(1, DEMO_SIZE + 1):
        case = DEMO_CASES.get(i, {})
        destination = case.get('destination', destinations[i % 4])
        collected = base - timedelta(hours=case.get('hours_ago', 4 + i * 53 % 300))
        deadline = collected + timedelta(days=3)
        did = insert_delivery(con, (
            f'LOG{i:04}', f'Cliente {i:02}', 'Recife/PE', destination, carriers[i % 3],
            stamp(collected), stamp(deadline)))
        route = ROUTES[destination]
        add_event(con, did, stamp(collected), 'Coletado', route[0][0], SEED_NOTES['Coletado'])
        moment = collected
        for leg, (location, kind, planned) in enumerate(route[1:], 1):
            hours = planned * (0.85 + (i * 7 + leg * 3) % 6 / 20)  # 85% a 110% do previsto
            if 'delay' in case and case['delay'][0] == location and kind == 'Em trânsito':
                hours += case['delay'][1]
            moment += timedelta(hours=hours)
            if moment > base:
                break  # ainda em trânsito: os próximos eventos não aconteceram
            add_event(con, did, stamp(moment), kind, location, SEED_NOTES[kind])
            if case.get('stop_at') == (location, kind):
                if 'incident' in case:
                    incident, after, place, note = case['incident']
                    add_event(con, did, stamp(moment + timedelta(hours=after)), incident, place, note)
                break


def reset_demo():
    """Apaga todos os registros e recria os dados fictícios de demonstração."""
    with session(write=True) as con:
        con.execute('DELETE FROM events')
        con.execute('DELETE FROM deliveries')
        seed_demo(con)


# ---------------------------------------------------------------- regras logísticas

def incident_state(history):
    """Devolve (ocorrência em aberto, extravio em aberto) segundo o histórico."""
    is_open = lost = False
    for event in history:
        if event['kind'] in INCIDENTS:
            is_open = True
            lost = lost or event['kind'] == 'Extravio'
        elif event['kind'] == TREATED:
            is_open = lost = False
    return is_open, lost


def level(value, good, warn, higher_is_better):
    """Classifica um indicador em 'good', 'warn' ou 'bad' (semáforo)."""
    if value is None:
        return None
    if higher_is_better:
        return 'good' if value >= good else 'warn' if value >= warn else 'bad'
    return 'good' if value <= good else 'warn' if value <= warn else 'bad'


def target_level(key, value):
    target = TARGETS[key]
    return level(value, target['good'], target['warn'], target['higher_is_better'])


def trajectory(row, history, current):
    """Trajeto da entrega: tempo real de cada trecho comparado ao previsto na rota, gargalo e previsão de chegada."""
    plan = ROUTES.get(row['destination'])
    stops, cursor, delivered = [], 0, False
    for event in history:
        if event['kind'] not in MOVES:
            if stops:  # ocorrências ficam marcadas na última parada alcançada
                stops[-1]['incidents'].append({'kind': event['kind'], 'time': event['time']})
            continue
        planned = None
        if plan:
            match = next((j for j in range(cursor, len(plan)) if plan[j][1] == event['kind']
                          and (event['kind'] == 'Coletado' or plan[j][0] == event['location'])), None)
            if match is not None:
                planned = sum(p[2] for p in plan[cursor:match + 1]) if match else None  # soma trechos pulados
                cursor = match + 1
        actual = hours_between(datetime.fromisoformat(stops[-1]['time']), datetime.fromisoformat(event['time'])) if stops else None
        stops.append(dict(location=event['location'], kind=event['kind'], time=event['time'], status='done',
                          planned_hours=planned, actual_hours=None if actual is None else round(actual, 1),
                          excess_hours=None if actual is None or planned is None else round(actual - planned, 1),
                          incidents=[]))
        delivered = delivered or event['kind'] == 'Entregue'
    eta = None
    if not delivered and stops:
        last = stops[-1]
        elapsed = hours_between(datetime.fromisoformat(last['time']), current)
        upcoming = plan[cursor:] if plan else []
        planned = upcoming[0][2] if upcoming else None
        stops.append(dict(location=upcoming[0][0] if upcoming else 'Próxima parada', kind=upcoming[0][1] if upcoming else '',
                          time=None, status='current', planned_hours=planned, actual_hours=round(elapsed, 1),
                          excess_hours=None if planned is None else round(elapsed - planned, 1), incidents=[]))
        for location, kind, hours in upcoming[1:]:
            stops.append(dict(location=location, kind=kind, time=None, status='planned', planned_hours=hours,
                              actual_hours=None, excess_hours=None, incidents=[]))
        if upcoming:
            remaining = max(0, planned - elapsed) + sum(p[2] for p in upcoming[1:])
            eta = stamp(current + timedelta(hours=remaining))
    legs = [s for s in stops if s['actual_hours'] is not None]
    worst = max((s for s in legs if (s['excess_hours'] or 0) > TOLERANCE_HOURS), key=lambda s: s['excess_hours'], default=None)
    if worst is None and not plan and legs:  # sem rota planejada: aponta o trecho mais longo
        worst = max(legs, key=lambda s: s['actual_hours'])
    bottleneck = None
    if worst is not None:
        index = stops.index(worst)
        origin = stops[index - 1]['location']
        waiting = worst['status'] == 'current' and worst['planned_hours'] is None  # sem rota: parada no local atual
        bottleneck = dict(label=origin if origin == worst['location'] or waiting else f'{origin} → {worst["location"]}',
                          stop=index, excess_hours=worst['excess_hours'], actual_hours=worst['actual_hours'],
                          planned_hours=worst['planned_hours'], open=worst['status'] == 'current')
    eta_vs_deadline = None if eta is None else round(hours_between(datetime.fromisoformat(row['deadline']), datetime.fromisoformat(eta)), 1)
    return dict(has_plan=bool(plan), stops=stops, bottleneck=bottleneck, eta=eta, eta_vs_deadline_hours=eta_vs_deadline,
                current_location=next((s['location'] for s in reversed(stops) if s['status'] == 'done'), None))


def bottlenecks(deliveries, limit=5):
    """Pontos do trajeto que mais acumulam atraso além do previsto, somando todas as entregas."""
    spots = {}
    for d in deliveries:
        stops = d['route']['stops']
        for index, stop in enumerate(stops):
            if (stop['excess_hours'] or 0) <= TOLERANCE_HOURS:
                continue
            origin = stops[index - 1]['location']
            label = origin if origin == stop['location'] else f'{origin} → {stop["location"]}'
            spot = spots.setdefault(label, {'label': label, 'deliveries': [], 'excess_hours': 0, 'open': 0})
            spot['deliveries'].append(d['code'])
            spot['excess_hours'] += stop['excess_hours']
            spot['open'] += stop['status'] == 'current'
    ranked = sorted(spots.values(), key=lambda s: -s['excess_hours'])[:limit]
    for spot in ranked:
        spot['count'] = len(spot['deliveries'])
        spot['avg_excess_hours'] = round(spot['excess_hours'] / spot['count'], 1)
        spot['excess_hours'] = round(spot['excess_hours'], 1)
    return ranked


STAGE_PLAYBOOK = {
    'origin': ('Atraso na saída do CD de origem', 'CD de origem', [
        'Confirmar com o CD se a carga foi expedida e em qual veículo.',
        'Se ainda estiver no CD, priorizar a expedição no próximo carregamento.',
        'Avisar o cliente caso a previsão passe do prazo.']),
    'transfer': ('Atraso na transferência entre hubs', 'Transportadora', [
        'Pedir à transportadora a posição do veículo e o motivo da demora (fiscalização, problema mecânico, estrada).',
        'Se o veículo estiver parado, solicitar transbordo para outro veículo.',
        'Atualizar o cliente com a nova previsão de entrega.']),
    'hub': ('Retenção no hub de destino', 'Hub de destino e transportadora', [
        'Ligar para o hub e pedir prioridade na roteirização de saída desta entrega.',
        'Verificar se há pendência impedindo a saída (documentação, endereço, avaria).',
        'Informar o cliente sobre a nova previsão.']),
    'last_mile': ('Atraso na última milha', 'Transportadora e cliente', [
        'Confirmar com o motorista se houve tentativa de entrega e o motivo.',
        'Validar endereço e janela de recebimento com o cliente.',
        'Reagendar a entrega com prioridade.']),
    'stale': ('Sem atualização de rastreio', 'Transportadora', [
        'Solicitar à transportadora o registro da posição atual da carga.',
        'Combinar uma frequência mínima de atualização do rastreio.',
        'Se não houver resposta, tratar como possível extravio.']),
}
INCIDENT_PLAYBOOK = {
    'Avaria': ('Transportadora e cliente', [
        'Pedir fotos e laudo da avaria à transportadora.',
        'Decidir com o cliente: seguir com reembalagem ou enviar reposição.',
        'Registrar a tratativa e marcar "Ocorrência tratada" no histórico.']),
    'Tentativa sem sucesso': ('Cliente e transportadora', [
        'Contatar o cliente para confirmar endereço, horário e quem vai receber.',
        'Reagendar a nova tentativa com a transportadora.',
        'Registrar a tratativa e marcar "Ocorrência tratada" no histórico.']),
    'Extravio': ('Transportadora, seguradora e cliente', [
        'Abrir o processo de extravio com a transportadora e acionar o seguro.',
        'Combinar com o cliente o reenvio ou o reembolso.',
        'Registrar a tratativa no histórico.']),
}


def leg_stage(prev, stop):
    """Classifica um trecho do trajeto: saída do CD, transferência, espera no hub ou última milha."""
    if stop['kind'] == 'Entregue' and prev['kind'] == 'Saiu para entrega':
        return 'last_mile'
    if prev['location'] == stop['location'] or not stop['kind']:
        return 'hub'
    return 'origin' if prev['kind'] == 'Coletado' and stop['kind'] == 'Em trânsito' else 'transfer'


def leg_place(prev, stop, stage):
    if stage == 'hub':
        return prev['location']
    if stage == 'last_mile':
        return f'entrega em {stop["location"]}'
    return f'{prev["location"]} → {stop["location"]}'


def at_place(place):
    """Preposição certa para o local: 'no Hub X', 'no trecho A → B', 'na entrega em Y'."""
    if '→' in place:
        return f'no trecho {place}'
    if place.startswith('entrega'):
        return f'na {place}'
    return f'no {place}' if place.startswith(('Hub', 'CD')) else f'em {place}'


def diagnose(row, current):
    """Onde a entrega está atrasando, qual é o cenário e o que fazer. None quando está tudo no previsto."""
    route, stops = row['route'], row['route']['stops']
    late_eta = (route['eta_vs_deadline_hours'] or 0) > 0
    b = route['bottleneck']
    slow_leg = bool(b) and (b['excess_hours'] or 0) > TOLERANCE_HOURS  # algum trecho acima do previsto
    if row['delivered'] and row['on_time']:
        return None
    if not row['delivered'] and not (row['alert_details'] or late_eta or slow_leg):
        return None
    d = assistant.duration
    target = stops[b['stop']] if b and (b['excess_hours'] or 0) > TOLERANCE_HOURS else \
        next((s for s in stops if s['status'] == 'current'), None)
    index = stops.index(target) if target else None
    stage = leg_stage(stops[index - 1], target) if index else 'hub'
    place = leg_place(stops[index - 1], target, stage) if index else row['route']['current_location']
    lost = target and target['planned_hours'] is not None and target['actual_hours'] is not None
    if row['delivered']:
        return dict(level='warn', scenario='Entregue com atraso', where=place, owner=row['carrier'],
                    detail=f'Chegou {d(row["delay_hours"])} depois do prazo. O tempo foi perdido {at_place(place)}'
                           + (f': {d(target["actual_hours"])} contra {d(target["planned_hours"])} previstos.' if lost else '.'),
                    impact=None, actions=[
                        f'Registrar o atraso no desempenho da {row["carrier"]}.',
                        f'Se houver novo atraso {at_place(place)}, revisar o tempo previsto ou a operação desse ponto.',
                        'Fazer contato de pós-venda com o cliente.'])
    scenario, owner, actions = STAGE_PLAYBOOK[stage]
    if target is None or (not lost and any(a['code'] == 'stale' for a in row['alert_details'])):
        scenario, owner, actions = STAGE_PLAYBOOK['stale']
    if lost and target['status'] == 'current':
        detail = {'hub': f'Parada {at_place(place)} há {d(target["actual_hours"])}, aguardando saída para entrega; o previsto era {d(target["planned_hours"])}.',
                  'last_mile': f'Saiu para entrega há {d(target["actual_hours"])}; o previsto era {d(target["planned_hours"])}.'
                  }.get(stage, f'Em trânsito no trecho {place} há {d(target["actual_hours"])}; o previsto era {d(target["planned_hours"])}.')
    elif lost:
        detail = f'Perdeu {d(target["excess_hours"])} {at_place(place)} ({d(target["actual_hours"])} contra {d(target["planned_hours"])} previstos).'
    else:
        detail = f'Último registro em {route["current_location"]}; o trecho atual não tem tempo previsto para comparar.'
    if row['open_incident']:
        incident = next(e for e in reversed(row['events']) if e['kind'] in INCIDENTS)
        owner, actions = INCIDENT_PLAYBOOK[incident['kind']]
        scenario, place = f'{incident["kind"]} em {incident["location"]}', incident['location']
        note = incident['note'].rstrip('.') or 'sem observação'
        detail = f'{incident["kind"]} registrada em {datetime.fromisoformat(incident["time"]).strftime("%d/%m às %H:%M")}: {note}. ' + detail
    if row['overdue_hours']:
        impact = f'Prazo vencido há {d(row["overdue_hours"])}.'
        if route['eta']:
            impact += f' Mesmo sem novos atrasos, chega em {d(max(0, hours_between(current, datetime.fromisoformat(route["eta"]))))}.'
    elif late_eta:
        impact = f'Ainda no prazo, mas a previsão de chegada passa {d(route["eta_vs_deadline_hours"])} do prazo.'
    else:
        impact = f'Ainda dá para cumprir o prazo ({d(row["hours_to_deadline"] or 0)} restantes) se os próximos trechos seguirem o previsto.'
    return dict(level='bad' if row['overdue_hours'] else 'warn', scenario=scenario, where=place, owner=owner,
                detail=detail, impact=impact, actions=list(actions))


def analyze(row, history, current):
    """Enriquece uma entrega com situação, prazos, alertas e prioridade."""
    collected = datetime.fromisoformat(row['collected'])
    deadline = datetime.fromisoformat(row['deadline'])
    delivered = next((e['time'] for e in history if e['kind'] == 'Entregue'), None)
    open_incident, open_loss = incident_state(history)
    row.update(events=history, status=history[-1]['kind'], delivered=delivered,
               planned_hours=round(hours_between(collected, deadline), 1),
               had_incident=any(e['kind'] in INCIDENTS for e in history),
               open_incident=open_incident, open_loss=open_loss,
               alerts=[], alert_details=[], on_time=None, lead_hours=None,
               delay_hours=0, overdue_hours=0, hours_to_deadline=None, priority=0)
    row['route'] = trajectory(row, history, current)
    if delivered:
        delivered_at = datetime.fromisoformat(delivered)
        row['lead_hours'] = round(hours_between(collected, delivered_at), 2)
        row['on_time'] = delivered_at <= deadline
        if not row['on_time']:
            row['delay_hours'] = round(hours_between(deadline, delivered_at), 1)
        row['diagnosis'] = diagnose(row, current)
        return row

    def alert(code, severity, title, action):
        row['alerts'].append(title)
        row['alert_details'].append(dict(code=code, severity=severity, title=title, action=action))

    if deadline < current:
        overdue = hours_between(deadline, current)
        row['overdue_hours'] = round(overdue, 1)
        alert('late', 'critical' if overdue > CRITICAL_OVERDUE_HOURS else 'high', 'Entrega atrasada', ACTIONS['late'])
    else:
        remaining = hours_between(current, deadline)
        row['hours_to_deadline'] = round(remaining, 1)
        if remaining <= RISK_HOURS and row['status'] != 'Saiu para entrega':
            alert('risk', 'warning', 'Risco de atraso', ACTIONS['risk'])
    if current - datetime.fromisoformat(history[-1]['time']) > timedelta(hours=STALE_HOURS):
        alert('stale', 'warning', f'Sem atualização há mais de {STALE_HOURS}h', ACTIONS['stale'])
    if open_incident:
        alert('incident', 'critical' if open_loss else 'high', 'Ocorrência: verificar histórico',
              ACTIONS['loss'] if open_loss else ACTIONS['incident'])
    worst = max((SEVERITY_RANK[a['severity']] for a in row['alert_details']), default=0)
    row['priority'] = round(worst * 1000 + row['overdue_hours'], 1)
    row['diagnosis'] = diagnose(row, current)
    return row


def compute_kpis(deliveries):
    total = len(deliveries)
    completed = [d for d in deliveries if d['delivered']]
    n = len(completed)
    punctual = sum(1 for d in completed if d['on_time'])
    delays = [d['delay_hours'] for d in completed if not d['on_time']]
    incidents = sum(1 for d in deliveries if d['had_incident'])

    def count(code):
        return sum(1 for d in deliveries if any(a['code'] == code for a in d['alert_details']))

    result = dict(
        total=total, completed=n, in_progress=total - n, punctual=punctual,
        otd=round(100 * punctual / n, 1) if n else None,
        average_hours=round(sum(d['lead_hours'] for d in completed) / n, 1) if n else None,
        planned_hours=round(sum(d['planned_hours'] for d in completed) / n, 1) if n else None,
        avg_delay_hours=round(sum(delays) / len(delays), 1) if delays else None,
        late=count('late'), at_risk=count('risk'), stale=count('stale'),
        open_incidents=sum(1 for d in deliveries if d['open_incident']), incident_count=incidents,
        incidents=round(100 * incidents / total, 1) if total else None,
        attention=sum(1 for d in deliveries if d['alerts']))
    planned = result['planned_hours']
    result['levels'] = dict(
        otd=target_level('otd', result['otd']),
        average_hours=level(result['average_hours'], planned, planned * 1.25, False) if n else None,
        late=target_level('late', result['late']) if total else None,
        incidents=target_level('incidents', result['incidents']))
    return result


def scorecards(deliveries):
    """Desempenho por transportadora, do melhor para o pior (OTD, depois ocorrências)."""
    cards = []
    for name in sorted({d['carrier'] for d in deliveries}):
        card = compute_kpis([d for d in deliveries if d['carrier'] == name])
        card['carrier'] = name
        cards.append(card)
    cards.sort(key=lambda c: (c['otd'] is None, -(c['otd'] or 0), c['incidents'] or 0))
    for position, card in enumerate(cards, 1):
        card['rank'] = position
    return cards


def health(kpis):
    if not kpis['total']:
        return {'level': None, 'label': 'Sem dados', 'message': 'Nenhuma entrega no filtro selecionado.'}
    names = {'otd': 'OTD', 'average_hours': 'tempo médio', 'late': 'entregas em atraso', 'incidents': 'ocorrências'}
    bad = [names[k] for k, v in kpis['levels'].items() if v == 'bad']
    warn = [names[k] for k, v in kpis['levels'].items() if v == 'warn']
    if bad:
        return {'level': 'bad', 'label': 'Situação crítica',
                'message': 'Fora da meta: ' + ', '.join(bad) + '.'}
    if warn:
        return {'level': 'warn', 'label': 'Atenção',
                'message': 'Em alerta: ' + ', '.join(warn) + '.'}
    return {'level': 'good', 'label': 'Operação dentro das metas',
            'message': 'Todos os indicadores estão dentro das metas definidas.'}


def trend(deliveries, days=14):
    """OTD por dia de conclusão (últimos `days` dias com entregas)."""
    buckets = {}
    for d in deliveries:
        if d['delivered']:
            bucket = buckets.setdefault(d['delivered'][:10], {'date': d['delivered'][:10], 'completed': 0, 'punctual': 0})
            bucket['completed'] += 1
            bucket['punctual'] += 1 if d['on_time'] else 0
    result = [buckets[k] for k in sorted(buckets)][-days:]
    for bucket in result:
        bucket['otd'] = round(100 * bucket['punctual'] / bucket['completed'], 1)
        bucket['level'] = target_level('otd', bucket['otd'])
    return result


def incident_kinds(deliveries):
    counts = Counter(e['kind'] for d in deliveries for e in d['events'] if e['kind'] in INCIDENTS)
    return [{'kind': kind, 'count': count} for kind, count in counts.most_common()]


def snapshot(carrier='', start='', end=''):
    with session() as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' if DATABASE_URL else 'BEGIN')
        rows = [dict(r) for r in con.execute('SELECT * FROM deliveries ORDER BY collected DESC')]
        events = {}
        for event in con.execute('SELECT * FROM events ORDER BY time,id'):
            events.setdefault(event['delivery_id'], []).append(dict(event))
    current = now()
    deliveries = []
    for row in rows:
        if carrier and row['carrier'] != carrier:
            continue
        if start and row['collected'][:10] < start:
            continue
        if end and row['collected'][:10] > end:
            continue
        deliveries.append(analyze(row, events.get(row['id'], []), current))
    kpis = compute_kpis(deliveries)
    cards = scorecards(deliveries)
    status = health(kpis)
    days = trend(deliveries)
    rules = {'stale_hours': STALE_HOURS, 'risk_hours': RISK_HOURS, 'critical_overdue_hours': CRITICAL_OVERDUE_HOURS,
             'tolerance_hours': TOLERANCE_HOURS}
    spots = bottlenecks(deliveries)
    return {'deliveries': deliveries, 'kpis': kpis, 'health': status, 'carriers': cards, 'trend': days,
            'bottlenecks': spots, 'routes': ROUTES,
            'assistant': assistant.analyze(deliveries, kpis, status, cards, days, TARGETS, rules, INCIDENTS, spots),
            'incident_kinds': incident_kinds(deliveries), 'targets': TARGETS, 'rules': rules,
            'updated': stamp(current)}


# ---------------------------------------------------------------- cadastro e validação

def required(data, key):
    value = str(data.get(key, '')).strip()
    if not value:
        raise ValueError('Preencha o campo: ' + key)
    if len(value) > 300:
        raise ValueError('Campo muito longo: ' + key)
    return value


def create_delivery(data):
    values = [required(data, k) for k in ['code', 'client', 'origin', 'destination', 'carrier', 'collected', 'deadline']]
    collected, deadline = map(datetime.fromisoformat, values[-2:])
    if collected > now():
        raise ValueError('A coleta não pode estar no futuro.')
    if deadline < collected:
        raise ValueError('O prazo deve ser igual ou posterior à coleta.')
    values[-2:] = [stamp(collected), stamp(deadline)]
    with session(write=True) as con:
        delivery_id = insert_delivery(con, values)
        add_event(con, delivery_id, values[-2], 'Coletado', values[2], 'Cadastro da entrega.')


def create_event(data):
    did = int(data['delivery_id'])
    kind = required(data, 'kind')
    when = datetime.fromisoformat(required(data, 'time'))
    if kind not in EVENTS:
        raise ValueError('Tipo de evento inválido.')
    location = required(data, 'location')
    note = str(data.get('note', '')).strip()[:1000]
    with session(write=True) as con:
        if not execute(con, 'SELECT 1 FROM deliveries WHERE id=?', (did,)).fetchone():
            raise ValueError('Entrega não encontrada.')
        history = [dict(r) for r in execute(con, 'SELECT * FROM events WHERE delivery_id=? ORDER BY time,id', (did,))]
        if not history:
            raise ValueError('A entrega não possui histórico.')
        last = history[-1]
        if last['kind'] == 'Entregue':
            raise ValueError('A entrega já foi concluída.')
        if when < datetime.fromisoformat(last['time']):
            raise ValueError('O evento deve ser posterior ou igual ao último registro.')
        if when > now():
            raise ValueError('O evento não pode estar no futuro.')
        if kind == TREATED and not incident_state(history)[0]:
            raise ValueError('Não há ocorrência em aberto nesta entrega.')
        add_event(con, did, stamp(when), kind, location, note)


# ---------------------------------------------------------------- servidor HTTP

# Entrada WSGI usada pelo runtime Python do Vercel.
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 20000
POST_ACTIONS = {'deliveries': create_delivery, 'events': create_event, 'reset': lambda _: reset_demo()}
startup_lock = Lock()
database_ready = False


@app.before_request
def protect_hosted_demo():
    if not HOSTED:
        return None
    password = os.getenv('LOGIMONITOR_PASSWORD')
    if not DATABASE_URL or not password:
        return jsonify(error='Configure DATABASE_URL e LOGIMONITOR_PASSWORD no Vercel.'), 503
    auth = request.authorization
    if (not auth or auth.type != 'basic' or auth.username != 'logimonitor'
            or not compare_digest((auth.password or '').encode(), password.encode())):
        return Response('Acesso à demonstração do LogiMonitor.', 401,
                        {'WWW-Authenticate': 'Basic realm="LogiMonitor", charset="UTF-8"'})
    if request.method == 'POST':
        origin = request.headers.get('Origin')
        if origin and (urlparse(origin).scheme != 'https' or urlparse(origin).netloc != request.host):
            return jsonify(error='Origem não permitida.'), 403


@app.after_request
def disable_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def ensure_database():
    global database_ready
    with startup_lock:
        if not database_ready:
            initialize()
            database_ready = True


@app.get('/')
def home():
    return send_file(ROOT / 'static/index.html')


@app.get('/static/<name>')
def web_asset(name):
    if name not in STATIC_FILES:
        return jsonify(error='Página não encontrada.'), 404
    return send_file(ROOT / 'static' / name, mimetype=STATIC_FILES[name])


@app.get('/api/data')
def web_data():
    ensure_database()
    result = snapshot(*(request.args.get(key, '') for key in ('carrier', 'start', 'end')))
    result['timezone'] = OPERATION_TZ.key if OPERATION_TZ else None
    return jsonify(result)


@app.post('/api/<action>')
def web_write(action):
    if action not in POST_ACTIONS:
        return jsonify(error='Rota não encontrada.'), 404
    if not request.is_json:
        return jsonify(error='Envie os dados em JSON.'), 415
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Corpo da requisição inválido.')
        ensure_database()
        POST_ACTIONS[action](data)
    except BadRequest:
        return jsonify(error='JSON inválido.'), 400
    except RequestEntityTooLarge:
        return jsonify(error='Requisição muito grande.'), 413
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    except INTEGRITY_ERRORS:
        return jsonify(error='Código de rastreio já cadastrado ou referência inválida.'), 400
    return jsonify(ok=True), 201


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return (self.headers.get('Host') or '').rsplit(':', 1)[0] in LOCAL_HOSTS

    def origin_ok(self):
        origin = self.headers.get('Origin')
        return origin is None or urlparse(origin).hostname in LOCAL_HOSTS

    def do_GET(self):
        url = urlparse(self.path)
        if not self.host_ok():
            return self.send(403, {'error': 'Acesso permitido apenas por localhost.'})
        if url.path == '/api/data':
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            return self.send(200, snapshot(query.get('carrier', ''), query.get('start', ''), query.get('end', '')))
        if url.path == '/':
            return self.send(200, (ROOT / 'static/index.html').read_bytes(), 'text/html; charset=utf-8')
        name = url.path.removeprefix('/static/') if url.path.startswith('/static/') else None
        if name in STATIC_FILES:
            return self.send(200, (ROOT / 'static' / name).read_bytes(), STATIC_FILES[name])
        self.send(404, {'error': 'Página não encontrada.'})

    def do_POST(self):
        try:
            if not self.host_ok() or not self.origin_ok():
                return self.send(403, {'error': 'Origem não permitida.'})
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.send(415, {'error': 'Envie os dados em JSON.'})
            length = int(self.headers.get('Content-Length', '0'))
            if length > 20000:
                raise ValueError('Requisição muito grande.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Corpo da requisição inválido.')
            if self.path == '/api/deliveries':
                create_delivery(data)
            elif self.path == '/api/events':
                create_event(data)
            elif self.path == '/api/reset':
                reset_demo()
            else:
                return self.send(404, {'error': 'Rota não encontrada.'})
            self.send(201, {'ok': True})
        except INTEGRITY_ERRORS:
            self.send(400, {'error': 'Código de rastreio já cadastrado ou referência inválida.'})
        except (ValueError, KeyError, TypeError) as exc:
            self.send(400, {'error': str(exc)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8501)
    args = parser.parse_args()
    initialize()
    print(f'LogiMonitor disponível em http://localhost:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
