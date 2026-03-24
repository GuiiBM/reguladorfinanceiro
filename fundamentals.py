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
