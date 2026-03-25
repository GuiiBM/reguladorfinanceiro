import requests
import math
import logging
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': 'https://statusinvest.com.br/',
}

_SI_INDICATORS = (
    'https://statusinvest.com.br/acao/indicatorhistoricallist'
    '?codes={ticker}&time=3&byQuarter=false&futureData=false'
)
_SI_DIVIDENDS = (
    'https://statusinvest.com.br/acao/companytickerprovents'
    '?ticker={ticker}&chartProventsType=2'
)

_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'fund_cache.json')
_CACHE_TTL  = timedelta(hours=6)

# Cache em memória: {ticker_base: {'data': {...}, 'ts': iso_str}}
_mem_cache: dict = {}


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
    disk = _load_disk_cache()
    disk[ticker_base] = entry
    _save_disk_cache(disk)


def _get_json(url):
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code == 200 and r.text.strip().startswith('{'):
            return r.json()
    except Exception as e:
        logger.warning(f'Erro ao buscar {url[-60:]}: {e}')
    return None


def fetch_fundamentals(ticker):
    """
    Retorna dict com indicadores fundamentalistas do ativo.
    Usa cache em disco (TTL 6h). Faz as 2 requisições em paralelo.
    """
    ticker_base = ticker.replace('.SA', '')

    cached = _cache_get(ticker_base)
    if cached is not None:
        return cached

    # Duas requisições em paralelo
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_ind = ex.submit(_get_json, _SI_INDICATORS.format(ticker=ticker_base))
        f_div = ex.submit(_get_json, _SI_DIVIDENDS.format(ticker=ticker_base))
        ind_data = f_ind.result()
        div_data = f_div.result()

    # ── Indicadores ──────────────────────────────────────
    indicators = {}
    if ind_data and 'data' in ind_data:
        key = list(ind_data['data'].keys())[0]
        for item in ind_data['data'].get(key, []):
            v = item.get('actual')
            if v is not None:
                indicators[item['key']] = float(v)

    dy   = indicators.get('dy')
    lpa  = indicators.get('lpa')
    vpa  = indicators.get('vpa')
    p_l  = indicators.get('p_l')
    p_vp = indicators.get('p_vp')
    roe  = indicators.get('roe')

    # ── DPA anual ─────────────────────────────────────────
    dpa = None
    if div_data:
        try:
            now      = datetime.now()
            pay_year = now.year + (1 if now.month >= 10 else 0)
            yearly   = {y['rank']: y['value']
                        for y in div_data.get('assetEarningsYearlyModels', [])}
            last_full       = yearly.get(pay_year - 1, 0)
            curr_partial    = yearly.get(pay_year, 0)
            annualized_curr = (curr_partial / (now.month / 12)) if curr_partial > 0 else 0
            raw = max(last_full, annualized_curr)
            dpa = raw if raw > 0 else None
        except (ValueError, KeyError, ZeroDivisionError):
            pass

    # ── Preços justos ─────────────────────────────────────
    graham = None
    if lpa and vpa and lpa > 0 and vpa > 0:
        graham = round(math.sqrt(22.5 * lpa * vpa), 2)

    bazin = round(dpa / 0.06, 2) if dpa and dpa > 0 else None

    result = {
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
    _cache_set(ticker_base, result)
    return result


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
    current_price = pos.get('current_price') or pos['average_price']
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

    is_fii = '11' in ticker_base
    if is_fii:
        vpa = fund.get('vpa')
        fair_price = round(vpa, 2) if vpa else None
    else:
        fair_price = graham

    references       = [p for p in [fair_price, bazin] if p]
    negotiable_price = round(min(references) * 0.90, 2) if references else None
    ideal_income_pct = _ideal_pct_by_income(dy, is_fii)
    buy_signal       = _buy_signal(current_price, fair_price, bazin, dy, profit_pct, is_fii)

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
    elif buy_count == 1:
        return {'action': 'COMPRAR', 'cls': 'green', 'reason': '; '.join(signals)}
    else:
        return {'action': 'MANTER', 'cls': 'yellow', 'reason': '; '.join(signals) or 'preço neutro'}


def clear_cache():
    global _mem_cache
    _mem_cache = {}
    try:
        if os.path.exists(_CACHE_FILE):
            os.remove(_CACHE_FILE)
    except Exception:
        pass


_SI_DIVIDENDS_MONTHLY = (
    'https://statusinvest.com.br/acao/companytickerprovents'
    '?ticker={ticker}&chartProventsType=1'
)

_div_cache: dict = {}
_DIV_CACHE_TTL = timedelta(hours=12)


def fetch_dividends_detail(ticker):
    """
    Retorna dividendos mensais dos ultimos 12 meses e anual estimado.
    Separa FIIs (pagamento mensal garantido) de acoes (irregular).
    """
    ticker_base = ticker.replace('.SA', '')
    is_fii = '11' in ticker_base

    entry = _div_cache.get(ticker_base)
    if entry and datetime.fromisoformat(entry['ts']) + _DIV_CACHE_TTL > datetime.now():
        return entry['data']

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_monthly = ex.submit(_get_json, _SI_DIVIDENDS_MONTHLY.format(ticker=ticker_base))
        f_yearly  = ex.submit(_get_json, _SI_DIVIDENDS.format(ticker=ticker_base))
        monthly_data = f_monthly.result()
        yearly_data  = f_yearly.result()

    now = datetime.now()

    monthly_payments = {}
    if monthly_data:
        try:
            for item in monthly_data.get('assetEarningsModels', []):
                try:
                    dt = datetime.strptime(item['pd'], '%d/%m/%Y')
                    if (now - dt).days <= 365:
                        key = dt.strftime('%Y-%m')
                        monthly_payments[key] = monthly_payments.get(key, 0) + float(item['v'])
                except Exception:
                    continue
        except Exception:
            pass

    annual_estimated = None
    if yearly_data:
        try:
            pay_year = now.year + (1 if now.month >= 10 else 0)
            yearly = {y['rank']: y['value']
                      for y in yearly_data.get('assetEarningsYearlyModels', [])}
            last_full    = yearly.get(pay_year - 1, 0)
            curr_partial = yearly.get(pay_year, 0)
            annualized   = (curr_partial / (now.month / 12)) if curr_partial > 0 else 0
            raw = max(last_full, annualized)
            annual_estimated = round(raw, 4) if raw > 0 else None
        except Exception:
            pass

    months_paid = len(monthly_payments)
    monthly_avg = round(sum(monthly_payments.values()) / 12, 4) if monthly_payments else None

    result = {
        'is_fii':           is_fii,
        'annual_estimated': annual_estimated,
        'monthly_avg':      monthly_avg,
        'months_paid':      months_paid,
        'monthly_payments': monthly_payments,
    }
    _div_cache[ticker_base] = {'data': result, 'ts': datetime.now().isoformat()}
    return result


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
            annual  = (d['annual_estimated'] or 0) * qty
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
                'annual_per_share':     d['annual_estimated'],
                'monthly_avg_per_share': d['monthly_avg'],
                'annual_total':         round(annual, 2),
                'monthly_avg_total':    round(monthly, 2),
                'months_paid':          d['months_paid'],
                'paid_months':           sorted(d['monthly_payments'].keys()),
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


# ── URL para buscar todos os eventos de proventos (com datas) ─────────────
_SI_PROVENTS = (
    'https://statusinvest.com.br/acao/companytickerprovents'
    '?ticker={ticker}&chartProventsType=2'
)
_SI_PROVENTS_TYPE1 = (
    'https://statusinvest.com.br/acao/companytickerprovents'
    '?ticker={ticker}&chartProventsType=1'
)
_SI_PROVENTS_TYPE2 = (
    'https://statusinvest.com.br/acao/companytickerprovents'
    '?ticker={ticker}&chartProventsType=2'
)

_full_div_cache: dict = {}
_FULL_DIV_TTL = timedelta(hours=6)


def _fetch_all_events(ticker_base):
    """Busca todos os eventos de proventos com datas de pagamento e ex-dividendo.
    Combina chartProventsType=1 (mensal/FII) e chartProventsType=2 (anual/acao)
    para garantir que todos os pagamentos sejam capturados independente do ativo.
    """
    entry = _full_div_cache.get(ticker_base)
    if entry and datetime.fromisoformat(entry['ts']) + _FULL_DIV_TTL > datetime.now():
        return entry['data']

    now = datetime.now()
    seen_keys = set()  # evita duplicatas por (pay_date, value)
    events = []

    for url_tpl in (_SI_PROVENTS_TYPE1, _SI_PROVENTS_TYPE2):
        try:
            r = requests.get(url_tpl.format(ticker=ticker_base), headers=_HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        for item in data.get('assetEarningsModels', []):
            try:
                pay_date = datetime.strptime(item['pd'], '%d/%m/%Y') if item.get('pd') else None
                ex_date  = datetime.strptime(item['ed'], '%d/%m/%Y') if item.get('ed') else None
                value    = float(item.get('v', 0))
                if value <= 0:
                    continue
                # chave de deduplicacao: mesma data de pagamento + mesmo valor
                key = (pay_date.strftime('%Y-%m-%d') if pay_date else None, round(value, 6))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                status = 'futuro' if (pay_date and pay_date > now) else 'pago'
                events.append({
                    'pay_date': pay_date.strftime('%Y-%m-%d') if pay_date else None,
                    'ex_date':  ex_date.strftime('%Y-%m-%d')  if ex_date  else None,
                    'value':    round(value, 6),
                    'type':     item.get('etd', 'Dividendo'),
                    'status':   status,
                })
            except Exception:
                continue

    events.sort(key=lambda x: x['pay_date'] or '', reverse=True)
    _full_div_cache[ticker_base] = {'data': events, 'ts': datetime.now().isoformat()}
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
            qty += t['quantity']
        else:
            qty -= t['quantity']
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


def fetch_dividends_full(portfolio):
    """
    Retorna visao completa de dividendos da carteira.
    - Usa historico de transacoes para calcular quantidade correta em cada pagamento
    - Usa ex_date como referencia de elegibilidade apenas quando confiavel (1-45 dias antes do pay_date)
    - Medias mensais calculadas sobre janela de 12 meses completos anteriores ao mes atual
    - Proximos pagamentos filtrados: so inclui futuros com ex_date >= hoje (usuario ainda e elegivel)
    """
    if not portfolio:
        return None

    import sqlite3
    conn = sqlite3.connect('data/regulador.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT ticker, type, quantity, date FROM transactions WHERE user_id=1 ORDER BY date')
    all_transactions = [dict(r) for r in cur.fetchall()]
    conn.close()

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
        is_fii      = '11' in ticker_base
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
                result.append({**ev, 'ticker': ticker_base, 'is_fii': is_fii,
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
                result.append({**ev, 'ticker': ticker_base, 'is_fii': is_fii,
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
            'is_fii':         '11' in tb,
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
        is_fii  = '11' in tb
        cqty    = port_map[tb]['quantity']
        paid_tb = sorted(
            [e for e in evs if e['status'] == 'pago' and e.get('pay_date', '') >= cutoff_12m],
            key=lambda x: x['pay_date'], reverse=True
        )
        if not paid_tb:
            continue

        if is_fii:
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
