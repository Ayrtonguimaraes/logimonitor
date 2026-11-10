"""Assistente de insights: lê os dados da operação e escreve uma análise com recomendações.

Não usa IA. Cada insight é uma regra analítica explícita aplicada às entregas do filtro
(concentração de atrasos, simulação de troca de frota, tendência etc.). Por isso
o resultado é reproduzível e cada número pode ser conferido nas outras telas.
"""
from collections import Counter

LEVEL_ORDER = {'critico': 0, 'atencao': 1, 'oportunidade': 2}
MAX_CODES = 12
MAX_INSIGHTS = 6


def pt(number, digits=1):
    """Número com vírgula decimal e sem zeros à direita (82,8 · 80 · 100)."""
    text = f'{number:.{digits}f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text.replace('.', ',')


def plural(n, one, many):
    return one if n == 1 else many


def duration(hours):
    if hours < 1:
        return f'{max(1, round(hours * 60))} min'
    return f'{round(hours)} h' if hours < 48 else f'{pt(hours / 24)} dias'


def best_carrier(ctx):
    rated = [c for c in ctx['carriers'] if c['otd'] is not None and c['completed'] >= 3]
    return rated[0]['carrier'] if rated else None


def has(delivery, code):
    return any(a['code'] == code for a in delivery['alert_details'])


def insight(level, title, analysis, action, deliveries=(), carrier='', alert=''):
    ranked = sorted(deliveries, key=lambda d: -d['priority'])
    return {'nivel': level, 'titulo': title, 'analise': analysis, 'acao': action,
            'entregas': [d['code'] for d in ranked[:MAX_CODES]], 'frota': carrier, 'alerta': alert}


# ---------------------------------------------------------------- regras analíticas

def lost_cargo(ctx):
    lost = [d for d in ctx['deliveries'] if d['open_loss']]
    if not lost:
        return None
    n = len(lost)
    return insight('critico', f'{n} {plural(n, "carga extraviada", "cargas extraviadas")} sem solução',
                   'Falta na carga é a ocorrência mais grave: além do atraso, a mercadoria pode não chegar ao cliente.',
                   'Acionar hoje a frota e o seguro e combinar com o cliente o reenvio ou a indenização.',
                   lost)


def route_bottleneck(ctx):
    """Ponto do trajeto (hub ou trecho) que mais acumula atraso além do previsto na rota."""
    spots = ctx['bottlenecks']
    if not spots or spots[0]['count'] < 2:
        return None
    spot = spots[0]
    codes = set(spot['deliveries'])
    group = [d for d in ctx['deliveries'] if d['code'] in codes]
    stuck = spot['open']
    where = f'no {spot["label"]}' if '→' not in spot['label'] else f'no trecho {spot["label"]}'
    now_text = f' {stuck} {plural(stuck, "está parada", "estão paradas")} lá agora.' if stuck else ''
    return insight(
        'critico' if stuck >= 2 else 'atencao', f'Gargalo do trajeto: {spot["label"]}',
        f'{spot["count"]} entregas passaram mais tempo que o previsto {where}, em média {duration(spot["avg_excess_hours"])} '
        f'além do planejado.{now_text} É o ponto da rota que mais acumula atraso na operação.',
        f'Acionar o responsável {where} para entender a retenção (triagem, capacidade, documentação) e priorizar a '
        'saída das entregas mais antigas.',
        group)


def late_by_destination(ctx):
    """Atrasos concentrados em um destino apontam gargalo de rota, não de frota."""
    deliveries = ctx['deliveries']
    late = [d for d in deliveries if has(d, 'late')]
    if len(late) < 2:
        return None
    destination, count = Counter(d['destination'] for d in late).most_common(1)[0]
    share = 100 * count / len(late)
    if share < 50:
        return None
    group = [d for d in late if d['destination'] == destination]
    carriers = sorted({d['carrier'] for d in group})
    volume = 100 * sum(1 for d in deliveries if d['destination'] == destination) / len(deliveries)
    if len(carriers) > 1:
        cause = (f'O padrão aparece em {len(carriers)} frotas diferentes, então o gargalo provável é a rota '
                 'ou a entrega local, e não uma frota específica.')
    else:
        cause = f'Todas são da {carriers[0]}, o que aponta para a operação dessa frota nessa rota.'
    return insight(
        'critico', f'{pt(share, 0)}% dos atrasos vão para {destination}',
        f'{count} das {len(late)} entregas atrasadas têm destino {destination}, que representa só {pt(volume, 0)}% '
        f'do volume. {cause} A mais antiga está atrasada há {duration(max(d["overdue_hours"] for d in group))}.',
        f'Levantar com as frotas o que está travando as entregas em {destination} (acesso, janela de '
        'recebimento, hub) e avisar esses clientes sobre a nova previsão.',
        group)


def late_by_carrier(ctx):
    """Frota com fatia de atrasos bem maior que sua fatia de volume."""
    deliveries = ctx['deliveries']
    late = [d for d in deliveries if has(d, 'late')]
    if len(late) < 3:
        return None
    best = None
    for carrier in {d['carrier'] for d in late}:
        late_c = [d for d in late if d['carrier'] == carrier]
        late_share = 100 * len(late_c) / len(late)
        volume_share = 100 * sum(1 for d in deliveries if d['carrier'] == carrier) / len(deliveries)
        gap = late_share - volume_share
        if len(late_c) >= 2 and gap >= 15 and (best is None or gap > best[0]):
            best = (gap, carrier, late_c, late_share, volume_share)
    if not best:
        return None
    _, carrier, group, late_share, volume_share = best
    analysis = ('A participação nos atrasos é desproporcional ao volume transportado, o que indica problema de nível '
                'de serviço dessa frota.')
    if carrier == best_carrier(ctx):
        analysis += (' Cuidado: ela tem o melhor OTD entre as entregas concluídas, mas os atrasos em aberto ainda não '
                     'entram nesse cálculo. O OTD dela deve cair quando essas entregas forem concluídas.')
    return insight(
        'atencao', f'{carrier} concentra {pt(late_share, 0)}% dos atrasos com {pt(volume_share, 0)}% do volume',
        analysis,
        f'Cobrar de {carrier} um plano de recuperação dos atrasos e acompanhar o desempenho semanalmente.',
        group, carrier=carrier)


def incidents_blocking(ctx):
    open_ = [d for d in ctx['deliveries'] if d['open_incident'] and not d['open_loss']]
    if not open_:
        return None
    blocked = [d for d in open_ if has(d, 'late')]
    kinds = Counter(next(e['kind'] for e in reversed(d['events']) if e['kind'] in ctx['incident_types']) for d in open_)
    kinds_text = ', '.join(f'{kind} ({n})' for kind, n in kinds.most_common())
    n = len(open_)
    if blocked:
        analysis = (f'Tipos: {kinds_text}. {len(blocked)} dessas entregas já passaram do prazo: '
                    'ocorrência sem tratativa costuma travar a entrega.')
    else:
        analysis = f'Tipos: {kinds_text}. Nenhuma passou do prazo ainda, então dá tempo de resolver.'
    return insight(
        'critico' if blocked and 2 * len(blocked) >= n else 'atencao',
        f'{n} {plural(n, "ocorrência", "ocorrências")} sem tratativa' + (f', {len(blocked)} já em atraso' if blocked else ''),
        analysis,
        'Abrir tratativa para cada ocorrência (contato com a frota, reembalagem ou reenvio) e registrar '
        '"Ocorrência tratada" no histórico.',
        open_, alert='incident')


def predicted_late(ctx):
    """Entregas ainda dentro do prazo cuja previsão de chegada, pelo ritmo do trajeto, já passa do prazo."""
    late_eta = [d for d in ctx['deliveries'] if not d['delivered'] and not has(d, 'late')
                and (d['route']['eta_vs_deadline_hours'] or 0) > 0]
    if not late_eta:
        return None
    n = len(late_eta)
    worst = max(d['route']['eta_vs_deadline_hours'] for d in late_eta)
    return insight(
        'atencao', f'{n} {plural(n, "entrega ainda está", "entregas ainda estão")} no prazo, mas com chegada prevista depois dele',
        f'Pelo ritmo atual do trajeto, a previsão de chegada já passa do prazo (em até {duration(worst)}). '
        'O atraso ainda não aconteceu, então dá para agir antes.',
        'Pedir à frota prioridade no trecho atual ou avisar o cliente com antecedência sobre a nova previsão.',
        late_eta)


def at_risk(ctx):
    risk = [d for d in ctx['deliveries'] if has(d, 'risk')]
    if not risk:
        return None
    n = len(risk)
    soonest = min(d['hours_to_deadline'] for d in risk)
    return insight(
        'atencao', f'{n} {plural(n, "entrega vence", "entregas vencem")} nas próximas {ctx["rules"]["risk_hours"]} h sem ter saído para entrega',
        f'O prazo mais próximo vence em {duration(soonest)}. Ainda é possível evitar o atraso.',
        'Confirmar hoje com a frota a saída para entrega; sem garantia, avisar o cliente antes do prazo vencer.',
        risk, alert='risk')


def stale_tracking(ctx):
    stale = [d for d in ctx['deliveries'] if has(d, 'stale')]
    if not stale:
        return None
    n = len(stale)
    carrier, count = Counter(d['carrier'] for d in stale).most_common(1)[0]
    return insight(
        'atencao', f'{n} {plural(n, "entrega", "entregas")} sem atualização há mais de {ctx["rules"]["stale_hours"]} h',
        f'{count} {plural(count, "é", "são")} da {carrier}. Sem registro de movimentação não dá para saber se a carga '
        'está andando, e a rastreabilidade fica comprometida.',
        f'Cobrar de {carrier} a atualização dessas entregas e combinar uma frequência mínima de registro.',
        stale, carrier=carrier, alert='stale')


def carrier_switch(ctx):
    """Simulação: e se as entregas da pior frota tivessem a pontualidade da melhor?"""
    rated = [c for c in ctx['carriers'] if c['otd'] is not None and c['completed'] >= 3]
    kpis = ctx['kpis']
    if len(rated) < 2 or not kpis['completed']:
        return None
    best, worst = rated[0], rated[-1]
    if best['otd'] - worst['otd'] < 5:
        return None
    punctual = kpis['punctual'] - worst['punctual'] + worst['completed'] * best['otd'] / 100
    new_otd = 100 * punctual / kpis['completed']
    goal = ctx['targets']['otd']['good']
    verdict = (f'Ainda ficaria abaixo da meta de {goal}%: redistribuir volume ajuda, mas não resolve sozinho.'
               if new_otd < goal else 'Isso já alcançaria a meta.')
    pending_late = sum(1 for d in ctx['deliveries'] if d['carrier'] == best['carrier'] and has(d, 'late'))
    if pending_late:
        verdict += (f' Antes de redistribuir, considere que a {best["carrier"]} tem {pending_late} '
                    f'{plural(pending_late, "entrega atrasada", "entregas atrasadas")} em aberto.')
    return insight(
        'oportunidade', f'Simulação: com o desempenho da {best["carrier"]}, o OTD iria de {pt(kpis["otd"])}% para {pt(new_otd)}%',
        f'{best["carrier"]} entrega {pt(best["otd"])}% no prazo; {worst["carrier"]}, {pt(worst["otd"])}% '
        f'(com {pt(worst["incidents"])}% de ocorrências). Se as {worst["completed"]} entregas concluídas da '
        f'{worst["carrier"]} tivessem a pontualidade da {best["carrier"]}, o OTD geral subiria '
        f'{pt(new_otd - kpis["otd"])} pontos. {verdict}',
        f'Direcionar cargas com prazo crítico para a {best["carrier"]} e renegociar com a {worst["carrier"]} '
        'um nível de serviço com penalidade por atraso.',
        carrier=worst['carrier'])


def trend_shift(ctx):
    """Compara a pontualidade dos 3 dias mais recentes com os dias anteriores."""
    trend = ctx['trend']
    if len(trend) < 4:
        return None
    recent, before = trend[-3:], trend[:-3]
    rc, bc = sum(t['completed'] for t in recent), sum(t['completed'] for t in before)
    if rc < 3 or bc < 3:
        return None
    r = 100 * sum(t['punctual'] for t in recent) / rc
    b = 100 * sum(t['punctual'] for t in before) / bc
    if r - b <= -10:
        return insight('atencao', f'Pontualidade caiu nos últimos 3 dias: de {pt(b)}% para {pt(r)}%',
                       'A queda recente indica um problema novo, que ainda pode ser contido antes de virar tendência.',
                       'Comparar as entregas concluídas fora do prazo nesses dias (frota e destino) para achar a causa.')
    if r - b >= 10:
        return insight('oportunidade', f'Pontualidade melhorou nos últimos 3 dias: de {pt(b)}% para {pt(r)}%',
                       'A melhora recente mostra que as ações em curso estão surtindo efeito.',
                       'Identificar o que mudou (frota, rota ou processo) e manter.')
    return None


def lead_time(ctx):
    kpis = ctx['kpis']
    avg, planned = kpis['average_hours'], kpis['planned_hours']
    if avg is None:
        return None
    if avg <= planned:
        delay = (f' As que passaram do prazo atrasaram em média {pt(kpis["avg_delay_hours"])} h.'
                 if kpis['avg_delay_hours'] is not None else '')
        return insight(
            'oportunidade', f'Prazo prometido é viável: tempo médio de {pt(avg)} h contra {pt(planned)} h prometidas',
            f'Em média sobram {pt(planned - avg)} h de folga. Os atrasos vêm de exceções, e não de um prazo mal '
            f'dimensionado.{delay}',
            'Manter o prazo prometido e concentrar a gestão por exceção nas entregas sinalizadas.')
    return insight(
        'atencao', f'Tempo médio de {pt(avg)} h passa do prazo prometido de {pt(planned)} h',
        'O prazo oferecido ao cliente é menor do que a operação consegue cumprir, então os atrasos tendem a se repetir.',
        'Revisar o prazo prometido aos clientes ou exigir lead time menor das frotas.')


def expiring_lots(ctx):
    """Lote no CD perto do vencimento: aplicar FEFO na próxima carga."""
    lots = sorted((l for l in ctx['lots'] if l['level'] != 'good'), key=lambda l: l['days_left'])
    if not lots:
        return None
    lot = lots[0]
    boxes = sum(l['stock'] for l in lots)
    return insight('critico' if lot['level'] == 'bad' else 'atencao',
                   f'Lote {lot["lot"]} vence em {lot["days_left"]} dias com {lot["stock"]} caixas no CD',
                   f'{len(lots)} {plural(len(lots), "lote vence", "lotes vencem")} nos próximos {ctx["rules"]["expiry_warn_days"]} dias '
                   f'({boxes} caixas). O mais urgente é {lot["product"]}. Cerveja vencida vira perda total.',
                   'Priorizar esses lotes na separação das próximas cargas (FEFO) e, se sobrar, oferecer em promoção aos PDVs de maior giro.')


def empties_holders(ctx):
    """PDVs que concentram vasilhame não devolvido (logística reversa)."""
    over = [b for b in ctx['empties_balance'] if b['level'] == 'bad']
    if not over:
        return None
    total = sum(b['pending'] for b in ctx['empties_balance'])
    top = over[0]
    group = [d for d in ctx['deliveries'] if d['client'] in {b['client'] for b in over} and not d['delivered']]
    return insight('atencao', f'{len(over)} {plural(len(over), "cliente segura", "clientes seguram")} vasilhame acima do limite',
                   f'{top["client"]} tem {top["pending"]} vasilhames em aberto, de {total} fora do CD. Casco parado no cliente '
                   'falta para o envase e é patrimônio da distribuidora.',
                   'Recolher o vasilhame na próxima entrega desses clientes e, se persistir, cobrar o casco no próximo pedido.',
                   group)


def partial_deliveries(ctx):
    """Entregas parciais: o OTIF cai mesmo com o caminhão chegando no horário."""
    partial = [d for d in ctx['deliveries'] if d['delivered'] and d['complete'] is False]
    if len(partial) < 2:
        return None
    on_time = sum(1 for d in partial if d['on_time'])
    return insight('atencao', f'{len(partial)} pedidos entregues com falta de caixas',
                   f'{on_time} deles chegaram no prazo, mas saíram do OTIF por estarem incompletos. O fill rate está em '
                   f'{pt(ctx["kpis"]["fill_rate"] or 0)}%. Consulte os itens faltantes e os eventos para identificar a causa.',
                   'Conferir a separação antes do carregamento (checklist por pedido) e verificar ruptura de estoque dos itens faltantes.',
                   partial)


RULES = [lost_cargo, route_bottleneck, late_by_destination, late_by_carrier, incidents_blocking, predicted_late, at_risk, stale_tracking,
         carrier_switch, trend_shift, lead_time, expiring_lots, empties_holders, partial_deliveries]


def summary(ctx, insights):
    kpis, goal = ctx['kpis'], ctx['targets']['otd']['good']
    if not kpis['total']:
        return 'Não há entregas no filtro selecionado.'
    state = {'bad': 'está fora da meta', 'warn': 'pede atenção', 'good': 'está dentro das metas'}.get(ctx['health']['level'], 'foi analisada')
    otd = f'OTD de {pt(kpis["otd"])}% (meta {goal}%)' if kpis['otd'] is not None else 'nenhuma entrega concluída ainda'
    late = kpis['late']
    parts = [f'A operação {state}: {otd}, {late} {plural(late, "entrega atrasada", "entregas atrasadas")} '
             f'e {pt(kpis["incidents"])}% de entregas com ocorrência.']
    focus = next((i for i in insights if i['nivel'] == 'critico'), None) or next(iter(insights), None)
    if focus:
        parts.append(f'Principal ponto: {focus["titulo"]}.')
        parts.append(f'Prioridade de hoje: {focus["acao"][0].lower()}{focus["acao"][1:]}')
    return ' '.join(parts)


def analyze(deliveries, kpis, health, carriers, trend, targets, rules, incident_types, bottlenecks=(), lots=(), empties_balance=()):
    ctx = dict(deliveries=deliveries, kpis=kpis, health=health, carriers=carriers, trend=trend,
               targets=targets, rules=rules, incident_types=incident_types, bottlenecks=list(bottlenecks),
               lots=list(lots), empties_balance=list(empties_balance))
    if not deliveries:
        return {'resumo': summary(ctx, []), 'insights': []}
    found = [result for rule in RULES if (result := rule(ctx))]
    if not found:
        found = [insight('oportunidade', 'Operação sob controle',
                         'Nenhuma regra de análise encontrou concentração de atrasos, ocorrências pendentes ou piora recente.',
                         'Manter o acompanhamento diário dos indicadores.')]
    found.sort(key=lambda i: LEVEL_ORDER[i['nivel']])
    found = found[:MAX_INSIGHTS]
    return {'resumo': summary(ctx, found), 'insights': found}
