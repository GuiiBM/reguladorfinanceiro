import requests
import json
import os
from datetime import datetime
from database import update_asset, save_price_history, get_price_history
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TICKERS_FILE = os.path.join(os.path.dirname(__file__), 'tickers.json')


def load_tickers():
    """Carrega tickers do arquivo tickers.json."""
    with open(TICKERS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['assets']


def save_tickers(assets):
    """Salva lista de tickers no arquivo tickers.json."""
    with open(TICKERS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'assets': assets}, f, ensure_ascii=False, indent=2)


def get_monitored_list():
    """Retorna lista de tickers base (sem .SA)."""
    return [a['ticker'] for a in load_tickers()]


def get_asset_names():
    """Retorna dict ticker -> nome."""
    return {a['ticker']: a['name'] for a in load_tickers()}


# Aliases para compatibilidade
MONITORED_ASSETS = property(get_monitored_list) if False else get_monitored_list()
ASSET_NAMES = get_asset_names()

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# Fontes de dados
_B3_URL = 'https://cotacao.b3.com.br/mds/api/v1/instrumentQuotation/{ticker}'
_STATUS_INVEST_URL = 'https://statusinvest.com.br/acao/tickerprice?ticker={ticker}&type=4&currences[]=1'


def _get(url, timeout=15):
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.error(f"Erro HTTP {url[-60:]}: {e}")
        return None


def fetch_asset_data(ticker):
    """Busca cotação atual via API da B3."""
    ticker_base = ticker.replace('.SA', '')
    r = _get(_B3_URL.format(ticker=ticker_base))
    if not r:
        return None

    try:
        data = r.json()
        trad = data.get('Trad', [{}])[0].get('scty', {})
        qtn = trad.get('SctyQtn', {})
        desc = trad.get('desc', ticker_base)

        current_price = float(qtn.get('curPrc', 0))
        variation_percent = float(qtn.get('prcFlcn', 0))
        open_price = float(qtn.get('opngPric', current_price))
        variation_value = current_price - open_price
        names = get_asset_names()

        return {
            'ticker': f'{ticker_base}.SA',
            'name': names.get(ticker_base, desc),
            'type': 'FII' if '11' in ticker_base else 'Ação',
            'current_price': round(current_price, 2),
            'variation_percent': round(variation_percent, 2),
            'variation_value': round(variation_value, 2),
            'market_cap': 'N/A',
            'volume': int(data.get('Trad', [{}])[0].get('ttlQty', 0)),
        }
    except Exception as e:
        logger.error(f"Erro ao processar dados B3 de {ticker_base}: {e}")
        return None


def fetch_historical_data(ticker, period='1y'):
    """Busca histórico de preços via Status Invest."""
    ticker_base = ticker.replace('.SA', '')
    ticker_sa = f'{ticker_base}.SA'

    r = _get(_STATUS_INVEST_URL.format(ticker=ticker_base))
    if not r or not r.text.strip().startswith('['):
        logger.warning(f"Histórico indisponível para {ticker_base}")
        return False

    try:
        data = r.json()
        prices = data[0].get('prices', [])

        existing = {h['date'][:10] for h in get_price_history(ticker_sa, days=1500)}
        inserted = 0

        for entry in prices:
            try:
                # Formato: "23/03/26 00:00" → dia 23, mês 03, ano 2026
                raw_date = entry['date']
                dt = datetime.strptime(raw_date, '%d/%m/%y %H:%M')
                date_str = dt.strftime('%Y-%m-%d')

                if date_str in existing:
                    continue

                price = float(entry['price'])
                save_price_history(ticker_sa, date_str, price, price, price, price, 0)
                inserted += 1
            except Exception:
                continue

        logger.info(f"Histórico de {ticker_sa}: {inserted} novos registros")
        return True

    except Exception as e:
        logger.error(f"Erro ao processar histórico de {ticker_base}: {e}")
        return False


def update_all_assets():
    """Atualiza cotações de todos os ativos via B3."""
    logger.info("Iniciando atualização de ativos...")
    monitored = get_monitored_list()

    for ticker in monitored:
        data = fetch_asset_data(ticker)
        if data:
            update_asset(
                data['ticker'], data['name'], data['type'],
                data['current_price'], data['variation_percent'],
                data['variation_value'], data['market_cap'], data['volume']
            )
            logger.info(f"Atualizado: {data['ticker']} - R$ {data['current_price']} ({data['variation_percent']:+.2f}%)")
        else:
            logger.warning(f"Sem dados para {ticker}")
        time.sleep(0.2)

    logger.info("Atualização de ativos concluída")


def get_asset_analysis(ticker):
    """Retorna análise técnica básica de um ativo."""
    try:
        history = get_price_history(ticker, days=90)
        if not history:
            return None

        closes = [h['close'] for h in reversed(history)]
        if len(closes) < 2:
            return None

        variation_1d = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) > 1 else 0
        variation_7d = ((closes[-1] - closes[-7]) / closes[-7] * 100) if len(closes) > 7 else 0
        variation_30d = ((closes[-1] - closes[-30]) / closes[-30] * 100) if len(closes) > 30 else 0
        window = closes[-30:] if len(closes) >= 30 else closes

        return {
            'ticker': ticker,
            'current_price': closes[-1],
            'variation_1d': round(variation_1d, 2),
            'variation_7d': round(variation_7d, 2),
            'variation_30d': round(variation_30d, 2),
            'high_30d': round(max(window), 2),
            'low_30d': round(min(window), 2),
            'average_30d': round(sum(window) / len(window), 2),
        }
    except Exception as e:
        logger.error(f"Erro ao analisar {ticker}: {e}")
        return None


def fetch_any_asset(ticker):
    """Busca cotação de qualquer ticker na B3, mesmo fora da lista monitorada."""
    return fetch_asset_data(ticker)


def initialize_market_data():
    """Inicializa dados de mercado para todos os ativos."""
    logger.info("Inicializando dados de mercado...")
    update_all_assets()
    monitored = get_monitored_list()
    for ticker in monitored:
        fetch_historical_data(f'{ticker}.SA')
        time.sleep(0.5)
    logger.info("Inicialização de dados de mercado concluída")
