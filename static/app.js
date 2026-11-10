'use strict';

/* ------------------------------------------------------------ utilidades */
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmtDate = v => v ? new Date(v).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'}) : '—';
const fmtNum = (n, suffix = '') => n === null || n === undefined ? '—' : n.toLocaleString('pt-BR', {maximumFractionDigits: 1}) + suffix;
const dur = h => h < 1 ? `${Math.max(1, Math.round(h * 60))} min`
    : h < 48 ? `${Math.round(h)} h`
    : `${(h / 24).toLocaleString('pt-BR', {maximumFractionDigits: 1})} dias`;
const localInput = (offsetHours = 0) => {
    const d = new Date(Date.now() + offsetHours * 3600e3);
    const timeZone = state.data?.timezone;
    if (timeZone) {
        return new Intl.DateTimeFormat('sv-SE', {timeZone, dateStyle: 'short', timeStyle: 'short'})
            .format(d).replace(' ', 'T');
    }
    return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

const LEVEL_TEXT = {good: 'Dentro da meta', warn: 'Atenção', bad: 'Fora da meta'};
const SEVERITY = {critical: {label: 'Crítico', rank: 3}, high: {label: 'Alto', rank: 2}, warning: {label: 'Atenção', rank: 1}};
const INCIDENTS = ['Avaria', 'Extravio', 'Tentativa sem sucesso'];
const PAGES = ['dashboard', 'deliveries', 'alerts', 'concepts'];

const hasAlert = (d, code) => d.alert_details.some(a => a.code === code);
const CHIPS = [
    ['all', 'Todas', () => true],
    ['open', 'Em andamento', d => !d.delivered],
    ['late', 'Atrasadas', d => hasAlert(d, 'late')],
    ['risk', 'Em risco', d => hasAlert(d, 'risk')],
    ['stale', 'Sem atualização', d => hasAlert(d, 'stale')],
    ['incident', 'Ocorrência em aberto', d => hasAlert(d, 'incident')],
    ['done', 'Concluídas', d => Boolean(d.delivered)],
];
const ALERT_TYPES = [['late', 'Atrasadas', 'prazo vencido'], ['risk', 'Em risco', 'vencem em breve'],
                     ['stale', 'Sem atualização', 'sem registro recente'], ['incident', 'Ocorrências', 'sem tratativa']];

const state = {data: null, page: 'dashboard', chip: 'all', alertChip: 'all', assistIndex: 0, assistElapsed: 0, assistKey: null, carriers: new Set()};

/* ------------------------------------------------------------ carregamento */
function showNotice(text) {
    $('notice').textContent = text;
    $('notice').hidden = !text;
}

function toast(text) {
    const el = $('toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 4500);
}

async function load() {
    const start = $('start').value, end = $('end').value;
    if (start && end && start > end) { showNotice('A data inicial deve ser anterior à data final.'); return; }
    showNotice('');
    const params = new URLSearchParams();
    for (const id of ['carrier', 'start', 'end']) if ($(id).value) params.set(id, $(id).value);
    try {
        const response = await fetch('/api/data?' + params);
        if (!response.ok) throw Error('Falha ao carregar os dados.');
        state.data = await response.json();
        if (!state.shown) { state.shown = true; requestAnimationFrame(() => window.scrollTo(0, 0)); }
        state.data.deliveries.forEach(d => state.carriers.add(d.carrier));
        updateCarrierOptions();
        $('updated').textContent = 'Atualizado às ' + new Date(state.data.updated).toLocaleTimeString('pt-BR');
        render();
    } catch (error) {
        showNotice(error.message);
    }
}

function updateCarrierOptions() {
    const current = $('carrier').value;
    $('carrier').innerHTML = '<option value="">Todas</option>' +
        [...state.carriers].sort().map(c => `<option>${esc(c)}</option>`).join('');
    $('carrier').value = current;
}

/* ------------------------------------------------------------ navegação */
const PAGE_TEXT = {
    dashboard: ['Visão da operação', 'Visibilidade para decidir. Rastreabilidade para agir.'],
    deliveries: ['Entregas', 'Ordenadas por prioridade. Abra uma entrega para ver o trajeto e registrar a próxima movimentação.'],
    alerts: ['Alertas', 'Entregas que precisam de ação, da mais crítica para a menos crítica.'],
    concepts: ['Conceitos e regras', 'Como cada número, alerta e recomendação é calculado.'],
};

function setPage(page) {
    if (!PAGES.includes(page)) page = 'dashboard';
    state.page = page;
    [$('pagetitle').textContent, $('pagesub').textContent] = PAGE_TEXT[page];
    $('filters').hidden = page === 'concepts';
    for (const id of PAGES) {
        $(id).hidden = id !== page;
        $('tab-' + id).setAttribute('aria-selected', String(id === page));
        $('tab-' + id).tabIndex = id === page ? 0 : -1;
    }
    history.replaceState(null, '', '#/' + page);
}

async function go(action) {
    if (action.carrier) { $('carrier').value = action.carrier; await load(); }
    if (action.view === 'alerts') state.alertChip = action.filter || 'all';
    if (action.view === 'deliveries') state.chip = action.filter || 'all';
    setPage(action.view);
    render();
}

/* ------------------------------------------------------------ renderização */
function render() {
    const data = state.data;
    if (!data) return;
    const k = data.kpis;
    $('filterinfo').textContent = `${k.total} ${k.total === 1 ? 'entrega' : 'entregas'} no filtro`;
    $('clear').hidden = !['carrier', 'start', 'end'].some(id => $(id).value);
    renderHealth(data);
    renderInsights(data);
    renderCards(data);
    renderScorecard(data);
    renderTrend(data);
    renderBottlenecks(data);
    renderLiveMap(data);
    renderChips();
    renderTable();
    renderAlerts();
    renderConcepts(data);
    const alertTotal = data.deliveries.filter(d => d.alert_details.length).length;
    $('alertcount').textContent = alertTotal;
    $('alertcount').hidden = alertTotal === 0;
}

function renderHealth({health}) {
    const level = health.level || 'none';
    $('health').innerHTML = `<div class="health lv-${level}"><span class="dot" aria-hidden="true"></span>
        <strong>${esc(health.label)}</strong><span class="msg">${esc(health.message)}</span></div>`;
}

const ASSIST_LEVEL_TEXT = {critico: 'Urgente', atencao: 'Atenção', oportunidade: 'Oportunidade'};

function renderInsights({assistant, updated}) {
    const key = ['carrier', 'start', 'end'].map(id => $(id).value).join('|');
    if (state.assistKey !== key) { state.assistKey = key; state.assistIndex = 0; }
    $('assistfoot').textContent = `Análise automática dos dados do filtro, gerada às ${new Date(updated).toLocaleTimeString('pt-BR', {timeStyle: 'short'})}.`;
    showInsight(state.assistIndex);
}

function showInsight(index, announce = false) {
    const items = state.data.assistant.insights;
    const many = items.length > 1;
    for (const id of ['assistprev', 'assistnext', 'assistpause', 'assistpos']) $(id).hidden = !many;
    $('assistdots').hidden = !many;
    if (!items.length) {
        $('assistcard').innerHTML = '<p class="empty">Não há entregas no filtro para analisar.</p>';
        $('assistprogress').innerHTML = '';
        return;
    }
    state.assistIndex = (index + items.length) % items.length;
    const item = items[state.assistIndex];
    const n = item.entregas.length;
    const target = n ? `Ver ${n} ${n === 1 ? 'entrega' : 'entregas'}` : item.transportadora ? `Ver ${item.transportadora}` : item.alerta ? 'Ver alertas' : '';
    $('assistcard').setAttribute('aria-live', announce ? 'polite' : 'off');
    $('assistcard').innerHTML = `<article class="insight-card ${item.nivel}">
        <span class="tag-level ${item.nivel}">${ASSIST_LEVEL_TEXT[item.nivel]}</span>
        <h4>${esc(item.titulo)}</h4><p>${esc(item.analise)}</p>
        <p class="todo-line"><b>Ação:</b> ${esc(item.acao)}</p>
        ${target ? `<button type="button" class="ghost sm" data-assist="${state.assistIndex}">${esc(target)}</button>` : ''}</article>`;
    $('assistpos').textContent = `${state.assistIndex + 1} de ${items.length}`;
    $('assistdots').innerHTML = items.map((it, i) => `<button type="button" class="dot-btn ${it.nivel}" data-assist-go="${i}"
        aria-label="Insight ${i + 1}: ${esc(it.titulo)}" aria-current="${i === state.assistIndex}"></button>`).join('');
    state.assistElapsed = 0;
    $('assistprogress').innerHTML = many ? '<i></i>' : '';
}

/* Troca automática a cada 8 s. Fica parada com o botão de pausa, com o mouse sobre o quadro,
   com foco de teclado dentro dele, com um modal aberto ou fora do painel. */
const ROTATE_MS = 8000, TICK_MS = 250;
function assistHeld() {
    const box = $('assistant');
    return box.classList.contains('paused') || box.matches(':hover') || Boolean(box.querySelector(':focus-visible'))
        || $('modal').open || document.hidden || state.page !== 'dashboard';
}
setInterval(() => {
    if (!state.data || state.data.assistant.insights.length < 2 || assistHeld()) return;
    state.assistElapsed += TICK_MS;
    if (state.assistElapsed >= ROTATE_MS) showInsight(state.assistIndex + 1);
    else $('assistprogress').firstChild.style.transform = `scaleX(${state.assistElapsed / ROTATE_MS})`;
}, TICK_MS);

function toggleAssistPause() {
    const paused = $('assistant').classList.toggle('paused');
    $('assistpause').setAttribute('aria-pressed', String(paused));
    $('assistpause').setAttribute('aria-label', paused ? 'Retomar a troca automática' : 'Pausar a troca automática');
    $('assistpause').textContent = paused ? '▶' : '❚❚';
}

function assistDrill(index) {
    const item = state.data.assistant.insights[index];
    if (item.entregas.length) {
        const list = state.data.deliveries.filter(d => item.entregas.includes(d.code));
        openModal(`<h2 id="modaltitle">${esc(item.titulo)}</h2><p>${esc(item.analise)}</p>
            <p class="todo-box"><b>Ação recomendada:</b> ${esc(item.acao)}</p><div class="scroll">${listHtml(list)}</div>`);
    } else if (item.transportadora) {
        go({view: 'deliveries', carrier: item.transportadora});
    } else {
        go({view: 'alerts', filter: item.alerta});
    }
}

const KPI_CARDS = (k, T) => [
    {key: 'otd', title: 'Entregas no prazo (OTD)', value: fmtNum(k.otd, '%'), level: k.levels.otd, meta: `meta ≥ ${T.otd.good}%`,
     plain: k.otd === null ? 'nenhuma entrega concluída ainda' : `≈ ${fmtNum(k.otd / 10)} de cada 10 chegam no prazo`},
    {key: 'avg', title: 'Tempo médio de entrega', value: fmtNum(k.average_hours, ' h'), level: k.levels.average_hours,
     meta: k.planned_hours === null ? 'sem entregas concluídas' : `prazo médio ${fmtNum(k.planned_hours, ' h')}`,
     plain: k.average_hours === null ? 'nenhuma entrega concluída ainda' : `≈ ${fmtNum(k.average_hours / 24)} dias da coleta à entrega`},
    {key: 'late', title: 'Entregas em atraso', value: k.late, level: k.levels.late, meta: `meta ${T.late.good}`,
     plain: k.late ? 'passaram do prazo e ainda não chegaram' : 'nenhuma entrega com prazo vencido'},
    {key: 'incidents', title: 'Taxa de ocorrências', value: fmtNum(k.incidents, '%'), level: k.levels.incidents, meta: `meta ≤ ${T.incidents.good}%`,
     plain: k.incident_count ? `≈ 1 de cada ${Math.round(k.total / k.incident_count)} entregas teve problema` : 'nenhuma entrega com problema'},
];

function renderCards({kpis: k, targets: T}) {
    $('cards').innerHTML = KPI_CARDS(k, T).map(c => `<button type="button" class="card" data-drill="${c.key}" title="Clique para entender este número">
        <span class="title">${c.title}</span><strong class="lv-text-${c.level || 'none'}">${c.value}</strong>
        <small class="plain">${esc(c.plain)}</small>
        <span class="foot"><span class="pill lv-${c.level || 'none'}">${LEVEL_TEXT[c.level] || 'Sem dados'}</span><small>${esc(c.meta)}</small></span>
        </button>`).join('');
}

/* Entregas ao vivo: diagrama das rotas (estilo mapa de metrô). Cada linha é um destino; as estações ficam
   espaçadas pelas horas previstas desde a coleta. A posição de cada entrega é estimada pelo último registro
   e pelo tempo decorrido no trecho atual. */
const MAP = {left: 150, right: 858, top: 46, rowH: 78};
const shortPlace = place => place.replace(/^Hub /, '').replace(/\/[A-Z]{2}$/, '');

function liveStatus(d, stop) {
    if (hasAlert(d, 'late')) return 'bad';
    return (stop.excess_hours ?? 0) > state.data.rules.tolerance_hours ? 'warn' : 'good';
}

function renderLiveMap({routes, deliveries}) {
    const dests = Object.keys(routes);
    const longest = Math.max(...dests.map(k => routes[k].reduce((sum, st) => sum + st[2], 0)));
    const scale = (MAP.right - MAP.left) / longest;
    const height = MAP.top + dests.length * MAP.rowH - 10;
    const layers = {axis: '', legs: '', stations: '', items: ''};
    const groups = [];
    const count = {moving: 0, waiting: 0, late: 0, other: 0};

    for (let h = 0; h <= longest; h += 12) {
        const x = MAP.left + h * scale;
        layers.axis += `<line x1="${x}" y1="22" x2="${x}" y2="${height}"/><text x="${x}" y="14" text-anchor="middle">${h} h</text>`;
    }
    layers.axis += '<text x="0" y="14" class="m-axis-title">tempo previsto</text>';

    dests.forEach((dest, row) => {
        const plan = routes[dest];
        const y = MAP.top + row * MAP.rowH;
        const cum = plan.reduce((acc, st, i) => [...acc, i ? acc[i - 1] + st[2] : 0], []);
        const xs = cum.map(h => MAP.left + h * scale);
        const active = deliveries.filter(d => !d.delivered && d.destination === dest);
        const dots = [], atHub = {}, flowing = new Set();

        for (const d of active) {
            const stops = d.route.stops;
            const at = stops.findIndex(st => st.status === 'current');
            const leg = at > 0 ? plan.findIndex(p => p[1] === stops[at].kind && p[0] === stops[at].location) : -1;
            if (leg < 1) { count.other++; continue; }
            const status = liveStatus(d, stops[at]);
            if (status === 'bad') count.late++;
            if (stops[at - 1].location === stops[at].location) {   // parada no hub, aguardando saída
                count.waiting++;
                (atHub[leg - 1] ??= []).push({d, status});
            } else {
                count.moving++;
                flowing.add(leg);
                const t = Math.min(0.92, Math.max(0.08, stops[at].actual_hours / (stops[at].planned_hours || 1)));
                dots.push({d, status, x: xs[leg - 1] + (xs[leg] - xs[leg - 1]) * t});
            }
        }

        layers.stations += `<text x="0" y="${y + 4}" class="m-dest">${esc(shortPlace(dest))}</text>
            <text x="0" y="${y + 20}" class="m-sub">${active.length} em andamento</text>`;
        for (let i = 1; i < plan.length; i++) {
            const blocked = atHub[i - 1] && plan[i - 1][0] === plan[i][0];
            const worst = blocked && (atHub[i - 1].some(o => o.status === 'bad') ? 'bad' : atHub[i - 1].some(o => o.status === 'warn') ? 'warn' : '');
            layers.legs += `<line x1="${xs[i - 1]}" y1="${y}" x2="${xs[i]}" y2="${y}" class="m-leg ${worst ? 'blocked ' + worst : ''}"/>`;
            if (flowing.has(i)) layers.legs += `<line x1="${xs[i - 1]}" y1="${y}" x2="${xs[i]}" y2="${y}" class="m-flow"/>`;
        }
        plan.forEach((st, i) => {
            const minor = st[1] === 'Saiu para entrega';
            const label = i === 0 ? 'CD Recife' : st[1] === 'Entregue' ? 'Cliente' : minor ? '' : shortPlace(st[0]);
            layers.stations += `<g class="m-station ${minor ? 'minor' : ''}"><title>${esc(st[0])} · ${STEP_LABEL[st[1]]}${i ? ` · ${cum[i]} h previstas desde a coleta` : ''}</title>
                <circle cx="${xs[i]}" cy="${y}" r="${minor ? 3 : 5}"/>${label ? `<text x="${xs[i]}" y="${y + 21}" text-anchor="middle">${esc(label)}</text>` : ''}</g>`;
        });
        let lastX = -99, lift = 0;
        dots.sort((a, b) => a.x - b.x).forEach(dot => {
            lift = dot.x - lastX < 14 ? (lift ? 0 : -12) : 0;
            lastX = dot.x;
            const leg = dot.d.route.stops.find(st => st.status === 'current');
            const tip = `${dot.d.code} · ${dot.d.carrier} · a caminho de ${leg.location} há ${dur(leg.actual_hours)} (previsto ${dur(leg.planned_hours)})`;
            layers.items += `<g class="m-dot ${dot.status}" data-map-detail="${dot.d.id}" tabindex="0" role="button" aria-label="${esc(tip)}">
                <title>${esc(tip)}</title><circle class="ring" cx="${dot.x}" cy="${y + lift}" r="7"/><circle cx="${dot.x}" cy="${y + lift}" r="6"/></g>`;
        });
        Object.entries(atHub).forEach(([index, items]) => {
            const worst = items.some(o => o.status === 'bad') ? 'bad' : items.some(o => o.status === 'warn') ? 'warn' : 'good';
            const group = groups.push({place: plan[index][0], ids: items.map(o => o.d.id)}) - 1;
            const x = xs[index], n = items.length;
            const tip = `${n} ${n === 1 ? 'entrega parada' : 'entregas paradas'} em ${plan[index][0]}, aguardando saída para entrega`;
            layers.items += `<g class="m-hub ${worst}" data-map-group="${group}" tabindex="0" role="button" aria-label="${esc(tip)}"><title>${esc(tip)}</title>
                <line x1="${x}" y1="${y - 7}" x2="${x}" y2="${y - 13}"/><rect x="${x - 15}" y="${y - 32}" width="30" height="19" rx="9.5"/>
                <text x="${x}" y="${y - 18.5}" text-anchor="middle">${n}</text></g>`;
        });
    });

    state.mapGroups = groups;
    $('livemap').innerHTML = `<svg viewBox="0 0 900 ${height}" role="img" aria-label="Posição estimada das entregas em andamento em cada rota">
        <g class="m-axis">${layers.axis}</g><g>${layers.legs}</g><g>${layers.stations}</g><g>${layers.items}</g></svg>`;
    const parts = [`<b>${count.moving}</b> em movimento`, `<b>${count.waiting}</b> aguardando em hubs`, `<b>${count.late}</b> atrasadas`];
    if (count.other) parts.push(`${count.other} sem rota planejada`);
    $('livesummary').innerHTML = parts.join(' · ') + ' <span class="muted">· posição estimada pelo último registro e pelo tempo previsto de cada trecho</span>';
}

function mapActivate(target) {
    const el = target.closest('[data-map-detail],[data-map-group]');
    if (!el) return;
    if (el.dataset.mapDetail) { detail(Number(el.dataset.mapDetail)); return; }
    const group = state.mapGroups[Number(el.dataset.mapGroup)];
    const list = state.data.deliveries.filter(d => group.ids.includes(d.id));
    openModal(`<h2 id="modaltitle">${esc(group.place)}</h2><p>${list.length} ${list.length === 1 ? 'entrega está parada' : 'entregas estão paradas'} aqui aguardando a saída para entrega.</p>
        <p class="muted">Abra uma entrega para ver há quanto tempo está no hub e o impacto no prazo.</p><div class="scroll">${listHtml(list)}</div>`);
}

/* Onde as entregas atrasam: pontos do trajeto que mais acumulam tempo além do previsto. */
function renderBottlenecks({bottlenecks: spots}) {
    if (!spots.length) { $('bottlenecks').innerHTML = '<p class="empty">Nenhum ponto do trajeto acima do previsto.</p>'; return; }
    const max = spots[0].excess_hours;
    $('bottlenecks').innerHTML = spots.map((spot, i) => `<button type="button" class="spot" data-spot="${i}">
        <span class="spot-label">${esc(spot.label)}</span>
        <span class="bar lv-bad" aria-hidden="true"><i style="width:${100 * spot.excess_hours / max}%"></i></span>
        <small>${spot.count} ${spot.count === 1 ? 'entrega' : 'entregas'} · em média ${dur(spot.avg_excess_hours)} além do previsto${spot.open ? ` · <b>${spot.open} ${spot.open === 1 ? 'parada' : 'paradas'} agora</b>` : ''}</small>
        </button>`).join('') + '<p class="muted small">Soma do tempo além do previsto na rota. Clique para ver as entregas.</p>';
}

function spotDrill(index) {
    const spot = state.data.bottlenecks[index];
    const list = state.data.deliveries.filter(d => spot.deliveries.includes(d.code));
    openModal(`<h2 id="modaltitle">${esc(spot.label)}</h2>
        <p>${spot.count} ${spot.count === 1 ? 'entrega ficou' : 'entregas ficaram'} neste ponto mais tempo que o previsto na rota: em média
        <b>${dur(spot.avg_excess_hours)}</b> a mais, somando ${dur(spot.excess_hours)} de atraso.${spot.open ? ` ${spot.open} ${spot.open === 1 ? 'continua parada' : 'continuam paradas'} aqui agora.` : ''}</p>
        <p class="muted">Abra uma entrega para ver o trajeto completo e onde ela perdeu tempo.</p><div class="scroll">${listHtml(list)}</div>`);
}

function renderScorecard({carriers, targets: T}) {
    if (!carriers.length) { $('scorecard').innerHTML = '<p class="empty">Sem dados no filtro.</p>'; return; }
    const goal = T.otd.good;
    $('scorecard').innerHTML = `<table class="compact"><thead><tr><th>Transportadora</th><th>OTD</th>
        <th class="num">Atrasos</th><th class="num">Ocorr.</th></tr></thead><tbody>${carriers.map(c => `<tr>
        <td><button type="button" class="name" data-carrier="${esc(c.carrier)}">${esc(c.carrier)}</button></td>
        <td><div class="otd"><b class="lv-text-${c.levels.otd || 'none'}">${fmtNum(c.otd, '%')}</b>
            <span class="bar lv-${c.levels.otd || 'none'}" aria-hidden="true"><i style="width:${c.otd || 0}%"></i><span class="tick" style="left:${goal}%"></span></span>
            <span class="sr-only">${LEVEL_TEXT[c.levels.otd] || 'Sem dados'}</span></div></td>
        <td class="num">${c.late}</td><td class="num">${fmtNum(c.incidents, '%')}</td></tr>`).join('')}</tbody></table>
        <p class="muted small">Traço na barra = meta de ${goal}%. Clique no nome para filtrar.</p>`;
}

function renderTrend({trend, targets: T}) {
    if (!trend.length) { $('trend').innerHTML = '<p class="empty">Nenhuma entrega concluída no filtro.</p>'; return; }
    const label = t => t.date.slice(8, 10) + '/' + t.date.slice(5, 7);
    $('trend').innerHTML = `<div class="trend" role="img" aria-label="Pontualidade por dia de conclusão">
        <div class="goal" style="bottom:${T.otd.good}%"></div>
        ${trend.map(t => `<div class="col lv-${t.level}" title="${label(t)}: ${t.punctual} de ${t.completed} no prazo (${fmtNum(t.otd, '%')})"><i style="height:${t.otd}%"></i></div>`).join('')}
        </div><div class="trend-labels">${trend.map(t => `<span>${label(t)}</span>`).join('')}</div>`;
}

function incidentsHtml(kinds) {
    if (!kinds.length) return '';
    const max = Math.max(...kinds.map(k => k.count));
    return '<h3>Ocorrências por tipo</h3>' + kinds.map(k => `<div class="hbar"><span>${esc(k.kind)}</span>
        <div class="bar" aria-hidden="true"><i style="width:${100 * k.count / max}%"></i></div><b>${k.count}</b></div>`).join('');
}

function renderChips() {
    const list = state.data.deliveries;
    $('chips').innerHTML = CHIPS.map(([key, text, test]) =>
        `<button type="button" data-chip="${key}" aria-pressed="${state.chip === key}">${text} (${list.filter(test).length})</button>`).join('');
}

function topSeverity(d) {
    return d.alert_details.reduce((best, a) => !best || SEVERITY[a.severity].rank > SEVERITY[best].rank ? a.severity : best, null);
}

function deadlineHtml(d) {
    if (d.delivered) {
        return d.on_time ? '<span class="dl ok">Entregue no prazo</span>' : `<span class="dl late">Entregue com ${dur(d.delay_hours)} de atraso</span>`;
    }
    if (hasAlert(d, 'late')) return `<span class="dl late">Atrasada há ${dur(d.overdue_hours)}</span>`;
    if (d.hours_to_deadline !== null) {
        return `<span class="dl ${d.hours_to_deadline <= state.data.rules.risk_hours ? 'soon' : ''}">Vence em ${dur(d.hours_to_deadline)}</span>`;
    }
    return '';
}

function rowHtml(d) {
    const severity = topSeverity(d);
    return `<tr class="clickable">
        <td>${severity ? `<span class="sev ${severity}">${SEVERITY[severity].label}</span>` : '<span class="sev none">Em dia</span>'}</td>
        <td><b>${esc(d.code)}</b><br>${esc(d.client)}</td>
        <td>${esc(d.origin)} → ${esc(d.destination)}<br><small>${esc(d.carrier)}</small></td>
        <td>${fmtDate(d.deadline)}<br><small>${deadlineHtml(d)}</small></td>
        <td><span class="status ${d.delivered ? 'done' : ''}">${esc(d.status)}</span>${!d.delivered && d.route.current_location ? `<br><small>${esc(prep(d.route.current_location))}</small>` : ''}
            ${d.alert_details.length ? `<br><small>${d.alert_details.map(a => esc(a.title)).join(' · ')}</small>` : ''}</td>
        <td><button type="button" data-detail="${d.id}">${d.delivered ? 'Ver histórico' : 'Abrir e atualizar'}</button></td></tr>`;
}

const byPriority = (a, b) => b.priority - a.priority || b.collected.localeCompare(a.collected);

function renderTable() {
    const search = $('search').value.trim().toLowerCase();
    const test = CHIPS.find(c => c[0] === state.chip)[2];
    const list = state.data.deliveries.filter(test)
        .filter(d => `${d.code} ${d.client} ${d.destination}`.toLowerCase().includes(search)).sort(byPriority);
    $('rows').innerHTML = list.map(rowHtml).join('') || '<tr><td colspan="6" class="empty">Nenhuma entrega encontrada para este filtro.</td></tr>';
}

const hoursSince = time => (Date.now() - new Date(time)) / 36e5;
const lastIncident = d => [...d.events].reverse().find(e => INCIDENTS.includes(e.kind));

/* Frase curta de cada alerta, usada nas etiquetas e nas mensagens de cobrança. */
function issueText(d, code) {
    if (code === 'late') return `atrasada há ${dur(d.overdue_hours)}`;
    if (code === 'risk') return `vence em ${dur(d.hours_to_deadline)} e ainda não saiu para entrega`;
    if (code === 'stale') return `sem atualização há ${dur(hoursSince(d.events[d.events.length - 1].time))}`;
    const incident = lastIncident(d);
    return `com ${incident ? incident.kind.toLowerCase() : 'ocorrência'} aguardando tratativa`;
}

const flagged = () => state.data.deliveries.filter(d => d.alert_details.length
    && (state.alertChip === 'all' || hasAlert(d, state.alertChip))).sort(byPriority);

function renderAlerts() {
    const all = state.data.deliveries.filter(d => d.alert_details.length);
    const tile = (key, label, hint, n) => `<button type="button" class="tile ${key}" data-achip="${key}" aria-pressed="${state.alertChip === key}">
        <strong>${n}</strong><span>${label}</span><small>${hint}</small></button>`;
    $('alerttiles').innerHTML = tile('all', 'Todas', 'entregas com alerta', all.length)
        + ALERT_TYPES.map(([key, label, hint]) => tile(key, label, hint, all.filter(d => hasAlert(d, key)).length)).join('');

    const type = ALERT_TYPES.find(t => t[0] === state.alertChip);
    const sample = type && all.flatMap(d => d.alert_details).find(a => a.code === type[0]);
    $('playbook').hidden = !sample;
    if (sample) $('playbook').innerHTML = `<b>Como agir com ${type[1].toLowerCase()}:</b> ${esc(sample.action)}`;

    const list = flagged();
    if (!list.length) {
        $('alertlist').innerHTML = '<p class="empty">Nenhuma entrega com alerta neste filtro.</p>';
    } else {
        $('alertlist').innerHTML = ['critical', 'high', 'warning'].map(severity => {
            const group = list.filter(d => topSeverity(d) === severity);
            if (!group.length) return '';
            return `<h3 class="group-title"><span class="sev ${severity}">${SEVERITY[severity].label}</span> ${group.length} ${group.length === 1 ? 'entrega' : 'entregas'}</h3>
                ${group.map(d => `<div class="arow">
                    <div class="who"><span><b>${esc(d.code)}</b> · ${esc(d.client)}</span><small>${esc(d.carrier)} → ${esc(d.destination)}</small></div>
                    <div class="why">${d.diagnosis ? `<span class="dx-line"><b>${esc(d.diagnosis.scenario)}</b> · ${esc(d.diagnosis.where || '')}</span>` : ''}
                        ${d.alert_details.map(a => `<span class="atag ${a.severity} ${a.code === state.alertChip ? 'on' : ''}">${esc(issueText(d, a.code))}</span>`).join('')}
                        ${d.diagnosis ? `<span class="dx-next">Próximo passo: ${esc(d.diagnosis.actions[0])}</span>` : ''}</div>
                    <div class="do"><button type="button" class="sm" data-detail="${d.id}">Resolver</button>
                        <button type="button" class="ghost sm" data-copy="${d.id}">Copiar mensagem</button></div></div>`).join('')}`;
        }).join('');
    }

    const carriers = [...new Set(list.map(d => d.carrier))]
        .map(name => ({name, items: list.filter(d => d.carrier === name)})).sort((a, b) => b.items.length - a.items.length);
    $('carrieralerts').innerHTML = carriers.map(({name, items}) => {
        const late = items.filter(d => hasAlert(d, 'late')).length;
        return `<div class="carrier-row"><div><b>${esc(name)}</b><small>${items.length} ${items.length === 1 ? 'entrega' : 'entregas'}${late ? ` · ${late} ${late === 1 ? 'atrasada' : 'atrasadas'}` : ''}</small></div>
            <button type="button" class="ghost sm" data-copy-carrier="${esc(name)}">Copiar cobrança</button></div>`;
    }).join('') || '<p class="muted small">Nada a cobrar neste filtro.</p>';
}

function renderConcepts({targets: T, rules, routes}) {
    $('routesinfo').innerHTML = `<table><thead><tr><th>Destino</th><th>Trajeto planejado (horas previstas por trecho)</th><th class="num">Total</th></tr></thead><tbody>
        ${Object.entries(routes).map(([dest, stops]) => `<tr><td>${esc(dest)}</td><td>${stops.map((st, i) =>
            i ? `<span class="muted">→ ${st[2]} h →</span> ${esc(st[0])} <small>(${STEP_LABEL[st[1]]})</small>` : esc(st[0])).join(' ')}</td>
            <td class="num">${stops.reduce((sum, st) => sum + st[2], 0)} h</td></tr>`).join('')}</tbody></table>`;
    $('targets').innerHTML = `<table><thead><tr><th>Indicador</th><th>Verde</th><th>Amarelo</th><th>Vermelho</th></tr></thead><tbody>
        <tr><td>${T.otd.label}</td><td>≥ ${T.otd.good}%</td><td>${T.otd.warn}% a ${T.otd.good}% (exclusive)</td><td>&lt; ${T.otd.warn}%</td></tr>
        <tr><td>Tempo médio de entrega</td><td>≤ prazo prometido médio</td><td>até 25% acima do prazo</td><td>mais de 25% acima</td></tr>
        <tr><td>${T.late.label}</td><td>${T.late.good}</td><td>1 a ${T.late.warn}</td><td>&gt; ${T.late.warn}</td></tr>
        <tr><td>${T.incidents.label}</td><td>≤ ${T.incidents.good}%</td><td>${T.incidents.good}% a ${T.incidents.warn}% (exclusive)</td><td>&gt; ${T.incidents.warn}%</td></tr></tbody></table>`;
    $('alertrules').innerHTML = `<table><thead><tr><th>Alerta</th><th>Quando aparece</th><th>Nível</th></tr></thead><tbody>
        <tr><td>Entrega atrasada</td><td>Prazo vencido e entrega não concluída</td><td>Alto; Crítico se atrasada há mais de ${rules.critical_overdue_hours} h</td></tr>
        <tr><td>Risco de atraso</td><td>Faltam ${rules.risk_hours} h ou menos para o prazo e a entrega ainda não saiu para entrega</td><td>Atenção</td></tr>
        <tr><td>Sem atualização</td><td>Último registro há mais de ${rules.stale_hours} h</td><td>Atenção</td></tr>
        <tr><td>Ocorrência</td><td>Avaria, extravio ou tentativa sem sucesso ainda não tratada</td><td>Alto; Crítico no caso de extravio</td></tr></tbody></table>`;
}

/* ------------------------------------------------------------ modais */
function openModal(html) {
    $('modalbody').innerHTML = html;
    $('formerror').textContent = '';
    if (!$('modal').open) $('modal').showModal();
}

function listHtml(list) {
    if (!list.length) return '<p class="empty">Nenhuma entrega neste grupo.</p>';
    return `<table><thead><tr><th>Prioridade</th><th>Código / cliente</th><th>Rota / transportadora</th><th>Prazo</th><th>Situação</th><th>Ação</th></tr></thead>
        <tbody>${[...list].sort(byPriority).map(rowHtml).join('')}</tbody></table>`;
}

const DRILLS = {
    otd: ['Concluídas fora do prazo', 'São estas entregas que puxam o OTD para baixo.', d => d.delivered && !d.on_time],
    avg: ['Entregas concluídas', 'Entregas usadas no cálculo do tempo médio.', d => d.delivered],
    late: ['Entregas em atraso', 'Da mais crítica para a menos crítica.', d => hasAlert(d, 'late')],
    incidents: ['Entregas com ocorrência', 'Entregas com avaria, extravio ou tentativa sem sucesso.', d => d.had_incident],
};

const STAGES = [['first', 'Coleta até o hub de origem'], ['transfer', 'Transferência entre hubs'],
                ['hub', 'Espera no hub de destino'], ['last', 'Entrega ao cliente (última milha)']];

function stageOf(stops, i) {
    const kind = stops[i].kind;
    if (kind === 'Entregue') return 'last';
    if (kind === 'Saiu para entrega') return 'hub';
    return stops[i - 1].kind === 'Coletado' ? 'first' : 'transfer';
}

/* Tempo médio real e previsto por etapa do trajeto, nas entregas concluídas com rota planejada. */
function stageBreakdown(deliveries) {
    const done = deliveries.filter(d => d.delivered && d.route.has_plan);
    const totals = Object.fromEntries(STAGES.map(([key]) => [key, {actual: 0, planned: 0}]));
    for (const d of done) {
        d.route.stops.forEach((stop, i) => {
            if (i === 0 || stop.actual_hours === null || stop.planned_hours === null) return;
            totals[stageOf(d.route.stops, i)].actual += stop.actual_hours;
            totals[stageOf(d.route.stops, i)].planned += stop.planned_hours;
        });
    }
    return {count: done.length, stages: STAGES.map(([key, label]) => ({key, label,
        actual: done.length ? totals[key].actual / done.length : 0, planned: done.length ? totals[key].planned / done.length : 0}))};
}

const picto = (filled, total = 10) => `<div class="picto" aria-hidden="true">${'<i class="ok"></i>'.repeat(filled)}${'<i class="no"></i>'.repeat(total - filled)}</div>`;

function explainHtml(key) {
    const {kpis: k, targets: T, deliveries} = state.data;
    if (key === 'otd') {
        if (k.otd === null) return '<p>Ainda não há entregas concluídas neste filtro.</p>';
        const on = Math.round(k.otd / 10);
        return `${picto(on)}<p>De cada 10 entregas concluídas, cerca de <b>${on}</b> chegaram até o prazo combinado com o cliente.
            A meta é ${T.otd.good}%, ou seja, pelo menos ${fmtNum(T.otd.good / 10)} de cada 10.</p>
            <p class="formula">${k.punctual} no prazo ÷ ${k.completed} concluídas × 100 = <b>${fmtNum(k.otd, '%')}</b></p>
            <p class="muted">Por que importa: é o nível de serviço que o cliente percebe. Entregas em andamento não entram na conta;
            as atrasadas de hoje só vão pesar no OTD quando forem concluídas.</p>`;
    }
    if (key === 'avg') {
        if (k.average_hours === null) return '<p>Ainda não há entregas concluídas neste filtro.</p>';
        const slack = k.planned_hours - k.average_hours;
        const {count, stages} = stageBreakdown(deliveries);
        const scale = Math.max(...stages.map(s => Math.max(s.actual, s.planned)), 1);
        return `<p>Da coleta até a entrega, uma entrega leva em média <b>${fmtNum(k.average_hours, ' h')}</b> (≈ ${fmtNum(k.average_hours / 24)} dias).
            O prazo prometido médio é ${fmtNum(k.planned_hours, ' h')}: ${slack >= 0 ? `sobra uma folga de ${fmtNum(slack, ' h')}` : `falta ${fmtNum(-slack, ' h')}`}.</p>
            <p class="formula">soma das horas entre coleta e entrega ÷ ${k.completed} concluídas = <b>${fmtNum(k.average_hours, ' h')}</b></p>
            ${count ? `<h3>Onde o tempo passa</h3><p class="muted small">Média por entrega em cada etapa (${count} concluídas com rota planejada). O traço marca o previsto.</p>
            ${stages.map(st => `<div class="stage"><span>${st.label}</span>
                <span class="legbar ${st.actual - st.planned > state.data.rules.tolerance_hours ? 'bad' : 'ok'}"><i style="width:${100 * st.actual / scale}%"></i><b style="left:${100 * st.planned / scale}%"></b></span>
                <small>${fmtNum(st.actual, ' h')} <span class="muted">(previsto ${fmtNum(st.planned, ' h')})</span></small></div>`).join('')}` : ''}`;
    }
    if (key === 'late') {
        const late = deliveries.filter(d => hasAlert(d, 'late'));
        if (!late.length) return '<p>Nenhuma entrega com prazo vencido neste filtro.</p>';
        const where = Object.entries(late.reduce((acc, d) => ({...acc, [d.route.current_location || 'sem registro']: (acc[d.route.current_location || 'sem registro'] || 0) + 1}), {}))
            .sort((a, b) => b[1] - a[1]);
        const avg = late.reduce((sum, d) => sum + d.overdue_hours, 0) / late.length;
        return `<p><b>${late.length}</b> ${late.length === 1 ? 'entrega já passou' : 'entregas já passaram'} do prazo e ainda não ${late.length === 1 ? 'chegou' : 'chegaram'} ao cliente,
            com atraso médio de ${dur(avg)}. A meta é zero: cada uma delas vai virar uma entrega fora do prazo no OTD.</p>
            <h3>Onde elas estão agora</h3>${where.map(([place, n]) => `<div class="stage"><span>${esc(place)}</span>
                <span class="legbar bad"><i style="width:${100 * n / where[0][1]}%"></i></span><small>${n} ${n === 1 ? 'entrega' : 'entregas'}</small></div>`).join('')}`;
    }
    if (!k.incident_count) return '<p>Nenhuma entrega com ocorrência neste filtro.</p>';
    const per = Math.round(k.total / k.incident_count);
    return `${picto(1, Math.min(per, 20))}<p>Cerca de <b>1 de cada ${per}</b> entregas teve algum problema (avaria, extravio ou tentativa sem sucesso).
        A meta é no máximo ${T.incidents.good}%, ou seja, 1 de cada ${Math.round(100 / T.incidents.good)}.</p>
        <p class="formula">${k.incident_count} entregas com ocorrência ÷ ${k.total} entregas × 100 = <b>${fmtNum(k.incidents, '%')}</b></p>
        <p class="muted">Cada entrega conta uma vez, mesmo com vários problemas.</p>${incidentsHtml(state.data.incident_kinds)}`;
}

function drill(key) {
    const [title, text, test] = DRILLS[key];
    const card = KPI_CARDS(state.data.kpis, state.data.targets).find(c => c.key === key);
    const list = state.data.deliveries.filter(test);
    openModal(`<h2 id="modaltitle">${card.title}: ${card.value}</h2>
        <p><span class="pill lv-${card.level || 'none'}">${LEVEL_TEXT[card.level] || 'Sem dados'}</span> <span class="muted">${esc(card.meta)}</span></p>
        <section class="explain"><h3>Como ler este número</h3>${explainHtml(key)}</section>
        <h3>${title} (${list.length})</h3><p class="muted">${text}</p><div class="scroll">${listHtml(list)}</div>`);
}

const STEP_LABEL = {'Coletado': 'coleta', 'Em trânsito': 'chegada', 'Saiu para entrega': 'saída para entrega', 'Entregue': 'entrega ao cliente'};

function legHtml(stop) {
    if (stop.actual_hours === null) return '';
    const running = stop.status === 'current' ? 'em andamento há ' : '';
    if (stop.planned_hours === null) return `<span class="leg-text">${running}${dur(stop.actual_hours)} neste trecho</span>`;
    const scale = Math.max(stop.actual_hours, stop.planned_hours) || 1;
    const over = stop.excess_hours > state.data.rules.tolerance_hours;
    const cls = !over ? 'ok' : stop.excess_hours > stop.planned_hours / 2 ? 'bad' : 'warn';
    return `<span class="legbar ${cls}" aria-hidden="true"><i style="width:${100 * stop.actual_hours / scale}%"></i><b style="left:${100 * stop.planned_hours / scale}%"></b></span>
        <span class="leg-text">${running}${dur(stop.actual_hours)} · previsto ${dur(stop.planned_hours)} · ${over ? `<em class="bad">+${dur(stop.excess_hours)}</em>` : '<em class="ok">dentro do previsto</em>'}</span>`;
}

function tripHtml(d) {
    const r = d.route;
    const here = r.stops.map(s => s.status).lastIndexOf('done');
    return `<ol class="trip">${r.stops.map((stop, i) => {
        const worst = r.bottleneck && r.bottleneck.stop === i;
        const leg = i ? `<li class="leg ${stop.status} ${worst ? 'worst' : ''}">${legHtml(stop)}${worst ? `<span class="worst-tag">⚠ ${r.has_plan ? 'maior atraso do trajeto' : 'trecho mais longo'}</span>` : ''}</li>` : '';
        const when = stop.time ? fmtDate(stop.time) : stop.status === 'current' ? 'próxima parada' : 'previsto';
        const incidents = stop.incidents.map(e => `<span class="atag ${INCIDENTS.includes(e.kind) ? 'critical' : ''}">${esc(e.kind)} · ${fmtDate(e.time)}</span>`).join('');
        return `${leg}<li class="stop ${stop.status} ${i === here && !d.delivered ? 'here' : ''}"><span class="node" aria-hidden="true"></span>
            <div><b>${esc(stop.location)}</b> <small>${STEP_LABEL[stop.kind] || esc(stop.kind)}</small>${i === here && !d.delivered ? ' <span class="here-tag">agora aqui</span>' : ''}
            <small class="when">${when}</small>${incidents ? `<div class="stop-tags">${incidents}</div>` : ''}</div></li>`;
    }).join('')}</ol>`;
}

const prep = place => /^(Hub|CD)\b/.test(place) ? `no ${place}` : `em ${place}`;

function placeText(r, next, d) {
    const here = r.current_location || d.origin;
    if (!next) return `Último registro ${prep(here)}.`;
    const planned = next.planned_hours !== null ? ` (trecho previsto: ${dur(next.planned_hours)})` : '';
    if (next.location !== here && next.kind === 'Em trânsito') {
        return `Saiu ${prep(here).replace(/^no /, 'do ').replace(/^em /, 'de ')} há ${dur(next.actual_hours)}, a caminho ${prep(next.location).replace(/^no /, 'do ').replace(/^em /, 'de ')}${planned}.`;
    }
    return `${prep(here)[0].toUpperCase()}${prep(here).slice(1)} há ${dur(next.actual_hours)}, aguardando ${STEP_LABEL[next.kind] || 'a próxima movimentação'}${planned}.`;
}

function factsHtml(d) {
    const r = d.route;
    const next = r.stops.find(s => s.status === 'current');
    const where = d.delivered
        ? `Entregue em ${fmtDate(d.delivered)}, ${d.on_time ? 'dentro do prazo' : `com ${dur(d.delay_hours)} de atraso`}.`
        : placeText(r, next, d);
    const b = r.bottleneck;
    const worst = !b ? (r.has_plan ? 'Todos os trechos dentro do previsto.' : 'Sem trajeto registrado ainda.')
        : r.has_plan ? `<b>${esc(b.label)}</b>: ${dur(b.actual_hours)} contra ${dur(b.planned_hours)} previstos (+${dur(b.excess_hours)}).`
        : `<b>${esc(b.label)}</b>: ${dur(b.actual_hours)} (trecho mais longo; esta rota não tem tempos previstos).`;
    const eta = r.eta === null ? null : `${fmtDate(r.eta)}: ${r.eta_vs_deadline_hours > 0
        ? `<span class="dl late">${dur(r.eta_vs_deadline_hours)} depois do prazo</span>` : `<span class="dl ok">${dur(-r.eta_vs_deadline_hours)} antes do prazo</span>`}${next && next.excess_hours > 0 ? ' (se sair agora)' : ''}`;
    return `<div class="facts">
        <div><small>Onde está</small><p>${where}</p></div>
        <div><small>Prazo</small><p>${fmtDate(d.deadline)} · ${deadlineHtml(d)}</p></div>
        <div><small>Onde mais atrasou</small><p>${worst}</p></div>
        ${d.delivered ? '' : `<div><small>Previsão de chegada</small><p>${eta || 'Sem rota planejada para estimar.'}</p></div>`}</div>`;
}

function diagnosisHtml(d) {
    const dx = d.diagnosis;
    if (!dx) return '';
    const tags = d.alert_details.map(a => `<span class="sev ${a.severity}">${esc(a.title)}</span>`).join('');
    return `<section class="diagnosis ${dx.level}" aria-label="Diagnóstico">
        <span class="dx-kicker">Diagnóstico</span>
        <h3>${esc(dx.scenario)}</h3>
        ${dx.where ? `<p class="dx-where"><span>Onde está o atraso</span><b>${esc(dx.where)}</b></p>` : ''}
        <p>${esc(dx.detail)}</p>${dx.impact ? `<p class="dx-impact">${esc(dx.impact)}</p>` : ''}
        ${tags ? `<div class="dx-tags">${tags}</div>` : ''}
        <h4>${d.delivered ? 'O que fazer agora que foi entregue' : 'O que fazer neste cenário'}</h4>
        <ol>${dx.actions.map(a => `<li>${esc(a)}</li>`).join('')}</ol>
        <p class="dx-owner">Quem acionar: <b>${esc(dx.owner)}</b></p></section>`;
}

function detail(id) {
    const d = state.data.deliveries.find(x => x.id === id);
    if (!d) return;
    const r = d.route;
    const todo = diagnosisHtml(d);
    const events = d.events.map(e => `<div class="event ${INCIDENTS.includes(e.kind) ? 'bad' : e.kind === 'Entregue' ? 'done' : ''}">
        <b>${esc(e.kind)}</b> · ${fmtDate(e.time)}<br>${esc(e.location)}<p class="muted">${esc(e.note)}</p></div>`).join('');
    const next = r.stops.find(s => s.status === 'current');
    const kinds = ['Em trânsito', 'Saiu para entrega', 'Entregue', ...INCIDENTS, ...(d.open_incident ? ['Ocorrência tratada'] : [])];
    const presets = ['Em trânsito', 'Saiu para entrega', 'Entregue', ...(d.open_incident ? ['Ocorrência tratada'] : [])];
    const form = d.delivered ? '' : `<h3>Registrar próxima movimentação</h3>
        ${next && next.kind ? `<p class="muted small">Próximo passo previsto na rota: <b>${STEP_LABEL[next.kind]} em ${esc(next.location)}</b>. O formulário já vem preenchido com ele.</p>` : ''}
        <div class="presets" aria-label="Atalhos">${presets.map(p => `<button type="button" class="ghost" data-preset="${p}">${p}</button>`).join('')}</div>
        <form id="eventform"><input name="delivery_id" type="hidden" value="${d.id}"><div class="grid">
        <label>Evento<select name="kind" id="evkind">${kinds.map(k => `<option ${next && k === next.kind ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
        <label>Data e hora<input name="time" type="datetime-local" value="${localInput()}" max="${localInput()}" required></label>
        <label>Local<input name="location" id="evloc" maxlength="300" required placeholder="Ex.: Hub Salvador/BA"></label>
        <label>Observação<textarea name="note" maxlength="1000" rows="2"></textarea></label></div>
        <button class="submit">Salvar evento</button></form>`;
    openModal(`<h2 id="modaltitle">${esc(d.code)} <small class="muted">${esc(d.client)} · ${esc(d.carrier)}</small></h2>
        <p class="route-line">${esc(d.origin)} → ${esc(d.destination)}</p>
        ${factsHtml(d)}${todo}
        <h3>Trajeto ${r.has_plan ? '<small class="muted">real × previsto em cada trecho</small>' : ''}</h3>${tripHtml(d)}
        <details class="history"><summary>Todos os registros (${d.events.length})</summary>${events}</details>${form}`);
    const form_ = $('eventform');
    if (form_) {
        let suggestion = '';
        const suggest = kind => {
            const planned = r.stops.find(s => s.status !== 'done' && s.kind === kind);
            if (planned) return planned.location;
            if (kind === 'Entregue') return d.destination;
            return INCIDENTS.includes(kind) || kind === 'Ocorrência tratada' ? (r.current_location || '') : '';
        };
        const apply = () => {
            if ($('evloc').value && $('evloc').value !== suggestion) return;
            suggestion = suggest($('evkind').value);
            $('evloc').value = suggestion;
        };
        apply();
        $('evkind').onchange = apply;
        form_.onsubmit = e => submit(e, '/api/events', 'Evento registrado.');
        $('modalbody').querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
            $('evkind').value = b.dataset.preset;
            apply();
            $('evloc').focus();
        });
    }
}

function newDelivery() {
    const codes = state.data.deliveries.map(d => /^LOG(\d+)$/.exec(d.code)).filter(Boolean).map(m => Number(m[1]));
    const code = 'LOG' + String(Math.max(0, ...codes) + 1).padStart(4, '0');
    const origins = state.data.deliveries.map(d => d.origin);
    const origin = origins.sort((a, b) => origins.filter(o => o === b).length - origins.filter(o => o === a).length)[0] || '';
    const destinations = [...new Set(state.data.deliveries.map(d => d.destination))];
    const field = (name, label, extra = '') => `<label>${label}<input name="${name}" required ${extra}></label>`;
    openModal(`<h2 id="modaltitle">Cadastrar entrega</h2><p class="muted">O prazo sugerido é de 72 horas após a coleta. Ajuste conforme o combinado com o cliente.</p>
        <form id="deliveryform"><div class="grid">
        ${field('code', 'Código de rastreio', `maxlength="300" value="${code}"`)}
        ${field('client', 'Cliente', 'maxlength="300"')}
        ${field('origin', 'Origem', `maxlength="300" value="${esc(origin)}"`)}
        ${field('destination', 'Destino', 'maxlength="300" list="destlist"')}
        ${field('carrier', 'Transportadora', 'maxlength="300" list="carrierlist"')}
        <span></span>
        ${field('collected', 'Coleta', `type="datetime-local" value="${localInput()}" max="${localInput()}"`)}
        ${field('deadline', 'Prazo de entrega', `type="datetime-local" value="${localInput(72)}"`)}
        </div><datalist id="destlist">${destinations.map(x => `<option value="${esc(x)}">`).join('')}</datalist>
        <datalist id="carrierlist">${[...state.carriers].map(x => `<option value="${esc(x)}">`).join('')}</datalist>
        <button class="submit">Salvar entrega</button></form>`);
    $('deliveryform').onsubmit = e => submit(e, '/api/deliveries', 'Entrega cadastrada.');
}

async function submit(event, url, message) {
    event.preventDefault();
    const button = event.target.querySelector('button.submit');
    button.disabled = true;
    try {
        const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                           body: JSON.stringify(Object.fromEntries(new FormData(event.target)))});
        const data = await response.json();
        if (!response.ok) throw Error(data.error);
        $('modal').close();
        toast(message);
        await load();
    } catch (error) {
        $('formerror').textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

function confirmReset() {
    openModal(`<h2 id="modaltitle">Restaurar dados de demonstração</h2>
        <p>Todas as entregas e eventos atuais serão <b>apagados</b> e substituídos por 40 entregas fictícias com datas recentes. Faça uma cópia de <code>dados.sqlite3</code> antes, se precisar guardar os registros.</p>
        <button type="button" id="confirmreset">Apagar e restaurar</button> <button type="button" class="ghost" id="cancelreset">Cancelar</button>`);
    $('cancelreset').onclick = () => $('modal').close();
    $('confirmreset').onclick = async () => {
        const response = await fetch('/api/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        if (!response.ok) { $('formerror').textContent = 'Não foi possível restaurar os dados.'; return; }
        $('modal').close();
        toast('Dados de demonstração restaurados.');
        await load();
    };
}

/* ------------------------------------------------------------ ações auxiliares */
const atPlace = place => place.includes('→') ? `no trecho ${place}` : place.startsWith('entrega') ? `na ${place}`
    : /^(Hub|CD)\b/.test(place) ? `no ${place}` : `em ${place}`;

function joinIssues(parts) {
    return parts.length > 1 ? parts.slice(0, -1).join(', ') + ' e ' + parts[parts.length - 1] : parts[0];
}

function askFor(deliveries) {
    const codes = deliveries.flatMap(d => d.alert_details.map(a => a.code));
    const asks = [codes.some(c => c === 'late' || c === 'risk') ? 'a nova previsão de entrega' : 'a posição atual da carga'];
    if (codes.includes('incident')) asks.push('a tratativa da ocorrência');
    return asks.join(' e ');
}

function deliveryMessage(d) {
    const issues = joinIssues(d.alert_details.map(a => issueText(d, a.code)));
    const where = d.diagnosis && d.diagnosis.where ? ` O atraso está ${atPlace(d.diagnosis.where)}.` : '';
    return `Olá, equipe ${d.carrier}. A entrega ${d.code} (cliente ${d.client}, destino ${d.destination}) está ${issues}.${where} `
        + `Podem informar ${askFor([d])}?`;
}

function carrierMessage(name) {
    const items = flagged().filter(d => d.carrier === name);
    const lines = items.map(d => `- ${d.code} (${d.client}, ${d.destination}): ${d.alert_details.map(a => issueText(d, a.code)).join('; ')}`);
    return `Olá, equipe ${name}. Precisamos de posição sobre ${items.length} ${items.length === 1 ? 'entrega' : 'entregas'}:\n`
        + lines.join('\n') + `\nPodem informar ${askFor(items)} de cada uma?`;
}

async function copyText(text, done) {
    try {
        await navigator.clipboard.writeText(text);
        toast(done);
    } catch {
        openModal(`<h2 id="modaltitle">Mensagem para a transportadora</h2><textarea readonly rows="8" style="width:100%">${esc(text)}</textarea>`);
    }
}

function exportCsv() {
    const header = ['Código', 'Cliente', 'Origem', 'Destino', 'Transportadora', 'Coleta', 'Prazo', 'Situação', 'Entrega', 'Alertas'];
    const rows = state.data.deliveries.map(d => [d.code, d.client, d.origin, d.destination, d.carrier, d.collected, d.deadline,
                                                  d.status, d.delivered, d.alerts.join(' | ')]);
    const quote = v => '"' + String(v ?? '').replace(/^[=+@-]/, "'$&").replaceAll('"', '""') + '"';
    const csv = '\ufeff' + [header, ...rows].map(r => r.map(quote).join(';')).join('\r\n');
    const link = document.createElement('a');
    const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
    link.href = url;
    link.download = 'entregas.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ------------------------------------------------------------ eventos */
document.addEventListener('click', event => {
    const el = event.target.closest('button');
    if (!el) return;
    const ds = el.dataset;
    if (ds.page) { setPage(ds.page); }
    else if (ds.detail) detail(Number(ds.detail));
    else if (ds.drill) drill(ds.drill);
    else if (ds.assist) assistDrill(Number(ds.assist));
    else if (ds.spot) spotDrill(Number(ds.spot));
    else if (ds.assistGo) showInsight(Number(ds.assistGo), true);
    else if (el.id === 'assistprev') showInsight(state.assistIndex - 1, true);
    else if (el.id === 'assistnext') showInsight(state.assistIndex + 1, true);
    else if (el.id === 'assistpause') toggleAssistPause();
    else if (ds.carrier) { $('modal').open && $('modal').close(); go({view: 'deliveries', carrier: ds.carrier}); }
    else if (ds.chip) { state.chip = ds.chip; render(); }
    else if (ds.achip) { state.alertChip = ds.achip; render(); }
    else if (ds.copy) copyText(deliveryMessage(state.data.deliveries.find(d => d.id === Number(ds.copy))), 'Mensagem copiada. Cole no e-mail ou WhatsApp da transportadora.');
    else if (ds.copyCarrier) copyText(carrierMessage(ds.copyCarrier), `Cobrança para ${ds.copyCarrier} copiada.`);
});

$('livemap').addEventListener('click', event => mapActivate(event.target));
$('livemap').addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); mapActivate(event.target); }
});

$('tabs').addEventListener('keydown', event => {
    const step = {ArrowRight: 1, ArrowLeft: -1}[event.key];
    if (!step) return;
    const next = PAGES[(PAGES.indexOf(state.page) + step + PAGES.length) % PAGES.length];
    setPage(next);
    $('tab-' + next).focus();
});

for (const id of ['carrier', 'start', 'end']) $(id).onchange = load;
$('search').oninput = () => state.data && renderTable();
$('refresh').onclick = load;
$('clear').onclick = () => { for (const id of ['carrier', 'start', 'end', 'search']) $(id).value = ''; state.chip = 'all'; load(); };
$('new').onclick = newDelivery;
$('export').onclick = exportCsv;
$('reset').onclick = confirmReset;
$('close').onclick = () => $('modal').close();
$('filters').onsubmit = e => e.preventDefault();
setInterval(() => { if ($('auto').checked && !$('modal').open && !document.hidden) load(); }, 60000);

// O endereço (#/alerts) escolhe a aba sem coincidir com o id da seção, para o navegador não rolar a página.
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
setPage(location.hash.replace(/^#\/?/, ''));
load();
