import requests
import math
import logging
import json
import os
import threading
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from market_data import is_fii

logger = logging.getLogger(__name__)

# O Status Invest (fonte antiga de fundamentos/dividendos) passou a bloquear
# as requisições com um desafio anti-bot do Cloudflare (HTTP 403,
# `cf-mitigated: challenge`) — não é algo contornável trocando User-Agent, é
# uma verificação de navegador de verdade.
#
# Avaliei a brapi.dev (API pública) como alternativa, mas na prática o acesso
# sem token só funciona para uma vitrine de poucos tickers "demo" (PETR4,
# VALE3, ITUB4...) — qualquer outro ticker real da carteira (BBAS3, CMIG4,
# ABEV3, WEGE3, todos os FIIs...) responde 401 "Token não fornecido". Como o
# objetivo é não depender de criar conta em lugar nenhum, a fonte usada é o
# investidor10.com.br: não tem API pública, mas os indicadores (P/L, P/VP,
# LPA, VPA, ROE, DY) e o histórico de proventos já vêm renderizados na própria
# página HTML, então dá pra extrair sem login/token/JS — tanto para ações
# quanto para FIIs. É mais frágil que uma API de verdade (quebra se o site
# mudar a marcação), mas não exige nenhum cadastro.
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

_INVESTIDOR10_STOCK_URL = 'https://investidor10.com.br/acoes/{ticker}/'
_INVESTIDOR10_FII_URL   = 'https://investidor10.com.br/fiis/{ticker}/'

_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'fund_cache.json')
_CACHE_TTL  = timedelta(hours=6)

# Cache em memória: {ticker_base: {'data': {...}, 'ts': iso_str}}
_mem_cache: dict = {}
# Protege a leitura+escrita do cache em disco: enrich_portfolio roda _cache_set
# de várias threads ao mesmo tempo, e sem lock a última a salvar sobrescreve
# (perde) as entradas gravadas pelas outras.
_disk_cache_lock = threading.Lock()


def _load_disk_cache():
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_disk_cache(cache):
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except Exception as e:
        logger.warning(f'Não foi possível salvar cache: {e}')


def _cache_get(ticker_base):
    entry = _mem_cache.get(ticker_base)
    if entry and datetime.fromisoformat(entry['ts']) + _CACHE_TTL > datetime.now():
        return entry['data']
    disk = _load_disk_cache()
    entry = disk.get(ticker_base)
    if entry and datetime.fromisoformat(entry['ts']) + _CACHE_TTL > datetime.now():
        _mem_cache[ticker_base] = entry
        return entry['data']
    return None


def _cache_set(ticker_base, data):
    entry = {'data': data, 'ts': datetime.now().isoformat()}
    _mem_cache[ticker_base] = entry
    with _disk_cache_lock:
        disk = _load_disk_cache()
        disk[ticker_base] = entry
        _save_disk_cache(disk)


def _parse_br_number(txt):
    """Converte '9,07%' / 'R$ 166,43' / '0,88' (formato pt-BR) para float."""
    if not txt:
        return None
    txt = txt.replace('R$', '').replace('%', '').strip()
    txt = txt.replace('.', '').replace(',', '.')
    try:
        return float(txt)
    except ValueError:
        return None


def _fetch_i10_page(url):
    """Busca e parseia uma página do investidor10.com.br. Retorna None (sem
    lançar exceção) se a requisição falhar."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        if r.status_code != 200:
            logger.warning(f'investidor10 {url}: HTTP {r.status_code}')
            return None
        return BeautifulSoup(r.text, 'html.parser')
    except Exception as e:
        logger.warning(f'Erro ao buscar {url} no investidor10: {e}')
        return None


def _parse_i10_dividends_table(soup):
    """Extrai a tabela de histórico de proventos (#table-dividends-history),
    presente tanto nas páginas de ações quanto de FIIs no mesmo formato:
    tipo | data com (ex-dividendo) | pagamento | valor por ação/cota."""
    now = datetime.now()
    events = []
    table = soup.find('table', id='table-dividends-history')
    if not table or not table.find('tbody'):
        return events
    for row in table.find('tbody').find_all('tr'):
        cells = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cells) < 4:
            continue
        try:
            tipo, data_com, pagamento, valor_txt = cells[:4]
            pay_dt = datetime.strptime(pagamento, '%d/%m/%Y')
            ex_dt  = datetime.strptime(data_com, '%d/%m/%Y') if data_com else None
            value  = _parse_br_number(valor_txt)
            if not value or value <= 0:
                continue
            events.append({
                'pay_date': pay_dt.strftime('%Y-%m-%d'),
                'ex_date':  ex_dt.strftime('%Y-%m-%d') if ex_dt else None,
                'value':    round(value, 6),
                'type':     tipo or 'Dividendo',
                'status':   'futuro' if pay_dt > now else 'pago',
            })
        except Exception:
            continue
    return events


def _extract_i10_card(soup, css_class):
    """Extrai o valor de um card de indicador no topo da página
    (ex.: <div class="_card dy">...<span>9,07%</span></div>)."""
    el = soup.select_one(f'div._card.{css_class} ._card-body span')
    return _parse_br_number(el.get_text(strip=True)) if el else None


def _extract_i10_cell(soup, label):
    """Extrai o valor de uma linha da lista de indicadores de FIIs
    (ex.: 'VAL. PATRIMONIAL P/ COTA' -> 'R$ 166,43')."""
    for cell in soup.select('div.cell'):
        name = cell.select_one('.name')
        if name and ' '.join(name.get_text(strip=True).split()) == label:
            val = cell.select_one('.value span')
            if val:
                return _parse_br_number(val.get_text(strip=True))
    return None


def _extract_i10_indicator(soup, label):
    """Extrai o valor de um indicador na lista de fundamentos de ações
    (<article class="indicator-card"><...title>LABEL</...><...value>X</...>)."""
    for card in soup.select('article.indicator-card'):
        title = card.select_one('.indicator-card-title span')
        if title and title.get_text(strip=True) == label:
            val = card.select_one('.indicator-card-value span')
            if val:
                return _parse_br_number(val.get_text(strip=True))
    return None


def _fetch_stock_raw(ticker_base):
    """Busca fundamentos + histórico de dividendos de uma ação via
    investidor10.com.br (ver nota no topo do arquivo sobre a escolha da fonte)."""
    soup = _fetch_i10_page(_INVESTIDOR10_STOCK_URL.format(ticker=ticker_base.lower()))
    if soup is None:
        return None
    return {
        'dy':     _extract_i10_indicator(soup, 'Dividend Yield'),
        'lpa':    _extract_i10_indicator(soup, 'LPA'),
        'vpa':    _extract_i10_indicator(soup, 'VPA'),
        'p_l':    _extract_i10_indicator(soup, 'P/L'),
        'p_vp':   _extract_i10_indicator(soup, 'P/VP'),
        'roe':    _extract_i10_indicator(soup, 'ROE'),
        'dividend_events': _parse_i10_dividends_table(soup),
    }


def _fetch_fii_raw(ticker_base):
    """Busca fundamentos + histórico de proventos de um FII via
    investidor10.com.br (a página de FII usa uma marcação diferente da de
    ações para os indicadores, por isso os seletores são outros)."""
    soup = _fetch_i10_page(_INVESTIDOR10_FII_URL.format(ticker=ticker_base.lower()))
    if soup is None:
        return None
    return {
        'dy':     _extract_i10_card(soup, 'dy'),
        'lpa':    None,
        'vpa':    _extract_i10_cell(soup, 'VAL. PATRIMONIAL P/ COTA'),
        'p_l':    None,
        'p_vp':   _extract_i10_card(soup, 'vp'),
        'roe':    None,
        'dividend_events': _parse_i10_dividends_table(soup),
    }


def _fetch_raw(ticker_base):
    """Ponto único de acesso aos dados brutos (fundamentos + proventos),
    cacheado (mem + disco, TTL 6h) e compartilhado por fetch_fundamentals,
    fetch_dividends_detail e _fetch_all_events — antes cada um batia na fonte
    externa separadamente, triplicando requisições para o mesmo ticker."""
    cached = _cache_get(ticker_base)
    if cached is not None:
        return cached
    data = _fetch_fii_raw(ticker_base) if is_fii(ticker_base) else _fetch_stock_raw(ticker_base)
    if data is not None:
        _cache_set(ticker_base, data)
    return data


def _trailing_12m_dpa(events):
    """Soma os proventos pagos nos últimos 365 dias (dividendo por ação/cota
    "trailing twelve months"). Substitui a extrapolação por ano-calendário
    usada antes (que tinha um bug de virada de ano em outubro e podia
    superestimar muito cedo no ano) — agora é sempre um valor realmente pago,
    nunca uma projeção."""
    if not events:
        return None
    cutoff = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    total = sum(e['value'] for e in events if e['status'] == 'pago' and e.get('pay_date', '') >= cutoff)
    return round(total, 4) if total > 0 else None


def fetch_fundamentals(ticker):
    """Retorna dict com indicadores fundamentalistas do ativo (ações via
    brapi.dev, FIIs via investidor10.com.br — ver _fetch_raw)."""
    ticker_base = ticker.replace('.SA', '')
    raw = _fetch_raw(ticker_base)
    if raw is None:
        return {'dy': None, 'lpa': None, 'vpa': None, 'p_l': None, 'p_vp': None,
                'roe': None, 'dpa': None, 'graham': None, 'bazin': None}

    dy, lpa, vpa, p_l, p_vp, roe = (raw.get(k) for k in ('dy', 'lpa', 'vpa', 'p_l', 'p_vp', 'roe'))
    dpa = _trailing_12m_dpa(raw.get('dividend_events') or [])

    graham = None
    if lpa and vpa and lpa > 0 and vpa > 0:
        graham = round(math.sqrt(22.5 * lpa * vpa), 2)

    # FIIs exigem yield mínimo maior (8%) que ações (6%) no teto de Bazin,
    # mesmo critério já usado em _ideal_pct_by_income/_buy_signal.
    required_yield = 0.08 if is_fii(ticker_base) else 0.06
    bazin = round(dpa / required_yield, 2) if dpa and dpa > 0 else None

    return {
        'dy':     round(dy, 2)   if dy   is not None else None,
        'lpa':    round(lpa, 4)  if lpa  is not None else None,
        'vpa':    round(vpa, 4)  if vpa  is not None else None,
        'p_l':    round(p_l, 2)  if p_l  is not None else None,
        'p_vp':   round(p_vp, 2) if p_vp is not None else None,
        'roe':    round(roe, 2)  if roe  is not None else None,
        'dpa':    round(dpa, 4)  if dpa  is not None else None,
        'graham': graham,
        'bazin':  bazin,
    }


def _ideal_pct_by_income(dy, is_fii):
    if dy is None:
        return None
    threshold = 8.0 if is_fii else 6.0
    if dy <= 0:
        return 2.0
    return round(min(5.0 * (dy / threshold), 30.0), 1)


def _enrich_one(pos, total_current_value):
    """Enriquece uma única posição — pode ser chamado em paralelo."""
    ticker      = pos['ticker']
    ticker_base = ticker.replace('.SA', '')
    current_price = pos.get('current_price') if pos.get('current_price') is not None else pos['average_price']
    avg_price     = pos['average_price']
    quantity      = pos['quantity']

    invested      = quantity * avg_price
    current_val   = quantity * current_price
    profit_loss   = current_val - invested
    profit_pct    = (profit_loss / invested * 100) if invested else 0
    pct_portfolio = (current_val / total_current_value * 100) if total_current_value else 0

    fund = fetch_fundamentals(ticker)

    dy     = fund.get('dy')
    graham = fund.get('graham')
    bazin  = fund.get('bazin')
    dpa    = fund.get('dpa')

    yc = round((dpa / avg_price * 100), 2) if dpa and avg_price else None

    ticker_is_fii = is_fii(ticker_base)
    if ticker_is_fii:
        vpa = fund.get('vpa')
        fair_price = round(vpa, 2) if vpa else None
    else:
        fair_price = graham

    references       = [p for p in [fair_price, bazin] if p]
    negotiable_price = round(min(references) * 0.90, 2) if references else None
    ideal_income_pct = _ideal_pct_by_income(dy, ticker_is_fii)
    buy_signal       = _buy_signal(current_price, fair_price, bazin, dy, profit_pct, ticker_is_fii)

    return {
        **pos,
        'current_price':    round(current_price, 2),
        'invested':         round(invested, 2),
        'current_value':    round(current_val, 2),
        'profit_loss':      round(profit_loss, 2),
        'profit_pct':       round(profit_pct, 2),
        'pct_portfolio':    round(pct_portfolio, 2),
        'dy':               dy,
        'yc':               yc,
        'fair_price':       fair_price,
        'ceiling_price':    bazin,
        'graham':           graham,
        'p_l':              fund.get('p_l'),
        'p_vp':             fund.get('p_vp'),
        'roe':              fund.get('roe'),
        'buy_signal':       buy_signal,
        'negotiable_price': negotiable_price,
        'ideal_income_pct': ideal_income_pct,
    }


def enrich_portfolio(portfolio, total_current_value):
    """
    Enriquece todas as posições em paralelo (até 5 threads).
    Ativos já em cache retornam imediatamente; os demais são buscados
    simultaneamente em vez de sequencialmente.
    """
    if not portfolio:
        return []

    results = [None] * len(portfolio)

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {
            ex.submit(_enrich_one, pos, total_current_value): i
            for i, pos in enumerate(portfolio)
        }
        for future in futures:
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as e:
                logger.error(f'Erro ao enriquecer {portfolio[i]["ticker"]}: {e}')
                results[i] = portfolio[i]  # fallback: posição sem fundamentais

    return [r for r in results if r is not None]


def _buy_signal(price, fair, ceiling, dy, profit_pct, is_fii):
    signals = []

    if fair and price < fair * 0.9:
        signals.append('abaixo do preço justo')
    elif fair and price > fair * 1.1:
        signals.append('acima do preço justo')

    if ceiling and price < ceiling:
        signals.append('abaixo do teto Bazin')
    elif ceiling and price > ceiling:
        signals.append('acima do teto Bazin')

    if dy:
        if is_fii and dy >= 8:
            signals.append(f'DY atrativo ({dy:.1f}%)')
        elif not is_fii and dy >= 6:
            signals.append(f'DY atrativo ({dy:.1f}%)')

    buy_count  = sum(1 for s in signals if 'abaixo' in s or 'atrativo' in s)
    sell_count = sum(1 for s in signals if 'acima' in s)

    if buy_count >= 2:
        return {'action': 'COMPRAR', 'cls': 'green', 'reason': '; '.join(signals)}
    elif sell_count >= 2:
        return {'action': 'AGUARDAR', 'cls': 'red', 'reason': '; '.join(signals)}
    elif buy_count == 1 and sell_count == 0:
        return {'action': 'COMPRAR', 'cls': 'green', 'reason': '; '.join(signals)}
    else:
        # Inclui o empate 1x1 (ex.: abaixo do preço justo, mas acima do teto
        # Bazin): sinais contraditórios não devem resolver sempre para COMPRAR.
        return {'action': 'MANTER', 'cls': 'yellow', 'reason': '; '.join(signals) or 'preço neutro'}


def clear_cache():
    """Limpa o cache (mem + disco) de fundamentos/proventos. Antes existiam
    três caches separados (um por tipo de requisição ao Status Invest); com
    _fetch_raw unificado, um único cache cobre tudo."""
    global _mem_cache
    _mem_cache = {}
    try:
        if os.path.exists(_CACHE_FILE):
            os.remove(_CACHE_FILE)
    except Exception as e:
        logger.warning(f'Não foi possível remover cache em disco: {e}')


def fetch_dividends_detail(ticker):
    """
    Retorna dividendos mensais dos ultimos 12 meses e anual (trailing 12m).
    Separa FIIs (pagamento mensal garantido) de acoes (irregular).
    """
    ticker_base = ticker.replace('.SA', '')
    ticker_is_fii = is_fii(ticker_base)

    raw = _fetch_raw(ticker_base)
    events = (raw or {}).get('dividend_events') or []

    cutoff = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    monthly_payments = {}
    for e in events:
        if e['status'] == 'pago' and e.get('pay_date', '') >= cutoff:
            key = e['pay_date'][:7]
            monthly_payments[key] = monthly_payments.get(key, 0) + e['value']

    months_paid = len(monthly_payments)
    total_paid  = sum(monthly_payments.values())
    # Média mensal = total dos últimos 12 meses / 12 (janela fixa, igual para FIIs e ações)
    monthly_avg = round(total_paid / 12, 4) if monthly_payments else None
    annual_from_history = round(total_paid, 4) if monthly_payments else None

    return {
        'is_fii':              ticker_is_fii,
        # Antes existiam duas métricas diferentes ("estimado" via API externa
        # vs. "histórico" via soma real); agora ambas vêm da mesma fonte de
        # eventos reais, então são idênticas — mantidas as duas chaves para
        # não quebrar quem já consome este dict.
        'annual_estimated':    annual_from_history,
        'annual_from_history': annual_from_history,
        'monthly_avg':         monthly_avg,
        'months_paid':         months_paid,
        'monthly_payments':    monthly_payments,
    }


def portfolio_dividends(portfolio):
    """Calcula projecao de dividendos da carteira separando FIIs e acoes."""
    if not portfolio:
        return None

    fii_annual = stock_annual = fii_monthly = stock_monthly = 0.0
    details = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_dividends_detail, p['ticker']): p for p in portfolio}
        for future in futures:
            pos = futures[future]
            try:
                d = future.result()
            except Exception:
                continue
            qty = pos['quantity']
            annual  = (d['annual_from_history'] or d['annual_estimated'] or 0) * qty
            monthly = (d['monthly_avg'] or 0) * qty
            if d['is_fii']:
                fii_annual  += annual
                fii_monthly += monthly
            else:
                stock_annual  += annual
                stock_monthly += monthly
            details.append({
                'ticker':                pos['ticker'].replace('.SA', ''),
                'is_fii':               d['is_fii'],
                'quantity':             qty,
                'annual_per_share':      d['annual_from_history'] or d['annual_estimated'],
                'monthly_avg_per_share':  d['monthly_avg'],
                'annual_total':           round(annual, 2),
                'monthly_avg_total':      round(monthly, 2),
                'months_paid':            d['months_paid'],
                'paid_months':            sorted(d['monthly_payments'].keys()),
            })

    return {
        'total_annual':      round(fii_annual + stock_annual, 2),
        'total_monthly_avg': round(fii_monthly + stock_monthly, 2),
        'fii_annual':        round(fii_annual, 2),
        'fii_monthly':       round(fii_monthly, 2),
        'stock_annual':      round(stock_annual, 2),
        'stock_monthly_avg': round(stock_monthly, 2),
        'details':           sorted(details, key=lambda x: -x['annual_total']),
    }


def _fetch_all_events(ticker_base):
    """Retorna todos os eventos de proventos (pagos e futuros) do ticker, mais
    recentes primeiro. Usa o mesmo cache/fonte de _fetch_raw (brapi.dev para
    ações, investidor10.com.br para FIIs) — antes fazia duas requisições
    próprias ao Status Invest; agora reaproveita o que já foi buscado."""
    raw = _fetch_raw(ticker_base)
    events = list((raw or {}).get('dividend_events') or [])
    events.sort(key=lambda x: x['pay_date'] or '', reverse=True)
    return events


def _build_qty_history(transactions, ticker):
    """
    Reconstrói o histórico de quantidade do ativo ao longo do tempo.
    Retorna lista ordenada de (date_str, qty_acumulada).
    """
    events = sorted(
        [t for t in transactions if t['ticker'].replace('.SA','') == ticker],
        key=lambda x: x['date']
    )
    history = []  # [(date_str, qty)]
    qty = 0.0
    for t in events:
        if t['type'] == 'compra':
            qty += abs(t['quantity'])
        else:
            qty -= abs(t['quantity'])
        history.append((t['date'][:10], max(qty, 0.0)))
    return history


def _qty_at_date(qty_history, date_str):
    """
    Retorna a quantidade que o usuario tinha ANTES da data informada.
    Compras no mesmo dia nao contam (< estrito).
    Retorna 0 se nao havia nenhuma compra antes da data.
    """
    qty = 0.0
    for d, q in qty_history:
        if d < date_str:
            qty = q
        else:
            break
    return qty


def _ref_date_for_event(ev):
    """
    Retorna a data de referencia para verificar elegibilidade ao dividendo.
    Usa ex_date apenas quando e confiavel: entre 1 e 45 dias antes do pay_date.
    Fora dessa janela a ex_date da API e de um ciclo diferente (aprovacao anterior)
    e nao reflete a elegibilidade real — usa pay_date como fallback.
    """
    pd = ev.get('pay_date')
    ed = ev.get('ex_date')
    if pd and ed:
        try:
            diff = (datetime.strptime(pd, '%Y-%m-%d') - datetime.strptime(ed, '%Y-%m-%d')).days
            if 1 <= diff <= 45:
                return ed
        except Exception:
            pass
    return pd


def fetch_dividends_full(portfolio, user_id):
    """
    Retorna visao completa de dividendos da carteira.
    - Usa historico de transacoes para calcular quantidade correta em cada pagamento
    - Usa ex_date como referencia de elegibilidade apenas quando confiavel (1-45 dias antes do pay_date)
    - Medias mensais calculadas sobre janela de 12 meses completos anteriores ao mes atual
    - Proximos pagamentos filtrados: so inclui futuros com ex_date >= hoje (usuario ainda e elegivel)
    """
    if not portfolio:
        return None

    from database import get_transactions
    # Antes usava sempre user_id=1 direto no SQL, ignorando o usuário informado
    # pelo chamador — quebrava a reconstrução de quantidade para qualquer outro
    # usuário. Reaproveita a camada de acesso segura em vez de abrir uma conexão
    # sqlite própria com um caminho relativo hardcoded.
    all_transactions = get_transactions(user_id)

    now        = datetime.now()
    today      = now.strftime('%Y-%m-%d')
    # Janela de 12 meses: do inicio do mes de 12 meses atras ate ontem
    cutoff_12m = (now.replace(day=1) - timedelta(days=365)).strftime('%Y-%m-%d')
    port_map   = {p['ticker'].replace('.SA', ''): p for p in portfolio}

    all_events  = []
    by_ticker   = {}

    def _process(ticker_base):
        pos         = port_map[ticker_base]
        current_qty = pos['quantity']
        # Nome diferente de `is_fii` (a função importada de market_data):
        # atribuir a um nome igual ao de uma função importada faria o Python
        # tratá-lo como variável local em toda a função, quebrando a própria
        # chamada do lado direito com UnboundLocalError.
        tb_is_fii   = is_fii(ticker_base)
        qty_history = _build_qty_history(all_transactions, ticker_base)
        events      = _fetch_all_events(ticker_base)
        result      = []
        for ev in events:
            if ev['status'] == 'pago':
                ref = _ref_date_for_event(ev)
                if not ref:
                    continue
                qty = _qty_at_date(qty_history, ref)
                if qty <= 0:
                    continue
                total = round(ev['value'] * qty, 2)
                result.append({**ev, 'ticker': ticker_base, 'is_fii': tb_is_fii,
                               'quantity': qty, 'value_per_share': ev['value'], 'total': total})
            else:
                # Futuro: pay_date ainda nao chegou
                # Descarta se ex_date e muito antiga (> 90 dias antes de hoje) — dado incorreto da API
                ed = ev.get('ex_date')
                if ed:
                    try:
                        ex_days_ago = (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(ed, '%Y-%m-%d')).days
                        if ex_days_ago > 90:
                            continue
                    except Exception:
                        pass
                total = round(ev['value'] * current_qty, 2)
                result.append({**ev, 'ticker': ticker_base, 'is_fii': tb_is_fii,
                               'quantity': current_qty, 'value_per_share': ev['value'], 'total': total})
        return ticker_base, result

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_process, tb) for tb in port_map]
        for f in futures:
            try:
                tb, evs = f.result()
                by_ticker[tb] = evs
                all_events.extend(evs)
            except Exception as e:
                logger.error(f'fetch_dividends_full error: {e}')

    # Total de todo o periodo (sem filtro de data)
    all_paid = [ev for ev in all_events if ev['status'] == 'pago' and ev.get('pay_date')]
    total_all = round(sum(e['total'] for e in all_paid), 2)

    # Pagos nos ultimos 12 meses
    paid_12m = sorted(
        [ev for ev in all_events if ev['status'] == 'pago'
         and ev.get('pay_date', '') >= cutoff_12m],
        key=lambda x: x['pay_date'], reverse=True
    )
    total_12m = round(sum(e['total'] for e in paid_12m), 2)

    # Agrega por mes e ano (apenas pagos 12m)
    monthly_map = {}
    annual_map  = {}
    for ev in paid_12m:
        mo = ev['pay_date'][:7]
        yr = ev['pay_date'][:4]
        monthly_map[mo] = round(monthly_map.get(mo, 0) + ev['total'], 2)
        annual_map[yr]  = round(annual_map.get(yr, 0)  + ev['total'], 2)

    # Media mensal real: total dos 12 meses / 12
    avg_monthly = round(total_12m / 12, 2)

    # Media mensal separada por tipo (FII vs Acao) — mesma logica
    fii_total_12m   = round(sum(e['total'] for e in paid_12m if e['is_fii']), 2)
    stock_total_12m = round(sum(e['total'] for e in paid_12m if not e['is_fii']), 2)
    avg_monthly_fii   = round(fii_total_12m / 12, 2)
    avg_monthly_stock = round(stock_total_12m / 12, 2)

    # Proximos pagamentos: agrupa por pay_date somando totais do mesmo dia
    upcoming_raw = sorted(
        [ev for ev in all_events if ev['status'] == 'futuro' and ev.get('pay_date')],
        key=lambda x: x['pay_date']
    )
    upcoming_grouped = {}
    for ev in upcoming_raw:
        pd = ev['pay_date']
        if pd not in upcoming_grouped:
            upcoming_grouped[pd] = {
                'pay_date': pd,
                'tickers':  [],
                'total':    0.0,
                'items':    [],
            }
        upcoming_grouped[pd]['tickers'].append(ev['ticker'])
        upcoming_grouped[pd]['total'] = round(upcoming_grouped[pd]['total'] + ev['total'], 2)
        upcoming_grouped[pd]['items'].append(ev)
    upcoming = [
        {**v, 'tickers': v['tickers'], 'ticker': ', '.join(v['tickers'])}
        for v in sorted(upcoming_grouped.values(), key=lambda x: x['pay_date'])
    ]

    # Resumo por ativo
    summary = []
    for tb, evs in by_ticker.items():
        paid_asset  = [e for e in evs if e['status'] == 'pago' and e.get('pay_date', '') >= cutoff_12m]
        future_asset = [e for e in evs if e['status'] == 'futuro']
        total_paid_12m = round(sum(e['total'] for e in paid_asset), 2)
        next_pay = min((e['pay_date'] for e in future_asset if e.get('pay_date')), default=None)
        next_val = next(
            (e['total'] for e in future_asset if e.get('pay_date') == next_pay), None
        ) if next_pay else None
        paid_months = sorted({e['pay_date'][:7] for e in paid_asset})
        summary.append({
            'ticker':         tb,
            'is_fii':         is_fii(tb),
            'quantity':       port_map[tb]['quantity'],
            'total_12m':      total_paid_12m,
            'events_12m':     len(paid_asset),
            'paid_months':    paid_months,
            'next_pay_date':  next_pay,
            'next_pay_value': next_val,
            'dy':             fetch_fundamentals(port_map[tb]['ticker']).get('dy'),
        })
    summary.sort(key=lambda x: -x['total_12m'])

    # ── Projecao mensal futura por ativo ──────────────────────────────────
    # FIIs: media dos ultimos 12 pagamentos por cota * qty_atual
    # Acoes: agrupa pagamentos por mes (evita multiplas parcelas no mesmo mes),
    #        soma os meses dos ultimos 12m e divide por 12 * qty_atual
    fii_proj_monthly   = 0.0
    stock_proj_monthly = 0.0

    for tb, evs in by_ticker.items():
        tb_is_fii = is_fii(tb)
        cqty    = port_map[tb]['quantity']
        paid_tb = sorted(
            [e for e in evs if e['status'] == 'pago' and e.get('pay_date', '') >= cutoff_12m],
            key=lambda x: x['pay_date'], reverse=True
        )
        if not paid_tb:
            continue

        if tb_is_fii:
            # Media dos ultimos 3 pagamentos por cota * qty atual
            recent       = paid_tb[:3]
            avg_per_unit = sum(e['value_per_share'] for e in recent) / len(recent)
            fii_proj_monthly += avg_per_unit * cqty
        else:
            # Agrupa por mes (evita multiplas parcelas no mesmo mes)
            # soma os meses e divide por 12 * qty atual
            monthly_per_unit = {}
            for e in paid_tb:
                mo = e['pay_date'][:7]
                monthly_per_unit[mo] = monthly_per_unit.get(mo, 0.0) + e['value_per_share']
            total_per_unit = sum(monthly_per_unit.values())
            stock_proj_monthly += (total_per_unit / 12) * cqty

    fii_proj_monthly   = round(fii_proj_monthly, 2)
    stock_proj_monthly = round(stock_proj_monthly, 2)

    return {
        'summary':             summary,
        'upcoming':            upcoming,
        'paid_12m':            paid_12m,
        'monthly_map':         monthly_map,
        'annual_map':          annual_map,
        # historico
        'total_all':           total_all,
        'total_12m':           total_12m,
        'avg_monthly':         avg_monthly,
        'fii_total_12m':       fii_total_12m,
        'stock_total_12m':     stock_total_12m,
        'avg_monthly_fii':     avg_monthly_fii,
        'avg_monthly_stock':   avg_monthly_stock,
        # projecao futura
        'fii_proj_monthly':    fii_proj_monthly,
        'stock_proj_monthly':  stock_proj_monthly,
        'proj_monthly_total':  round(fii_proj_monthly + stock_proj_monthly, 2),
        'proj_annual_total':   round((fii_proj_monthly + stock_proj_monthly) * 12, 2),
    }
