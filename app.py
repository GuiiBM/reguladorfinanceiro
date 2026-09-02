import os
import atexit
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import logging
import json
from threading import Thread

import config
from database import (
    init_db, get_user_or_create, get_all_assets, get_asset,
    search_assets, get_portfolio, get_transactions, get_recommendations,
    get_recommendation, get_price_history, update_asset,
    update_transaction, delete_transaction, set_portfolio_position, delete_portfolio_position
)
from market_data import (
    update_all_assets, get_asset_analysis, initialize_market_data,
    fetch_any_asset, load_tickers, save_tickers, is_fii
)
from portfolio import buy_asset, sell_asset, get_portfolio_performance, import_csv
from recommendations import (
    update_all_recommendations, get_top_recommendations, get_market_opportunities,
    get_portfolio_health, calculate_recommendation
)
from fundamentals import enrich_portfolio, clear_cache, portfolio_dividends, fetch_dividends_full

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_FILE_SIZE_MB * 1024 * 1024
# Precisa ser setado antes do bloco de inicialização abaixo (que checa
# app.debug) — só é lido de novo, não redefinido, no app.run() no final do arquivo.
app.debug = config.FLASK_DEBUG

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _to_float(value):
    """Converte para float com segurança; retorna None em vez de deixar o
    ValueError virar um 500 genérico nas rotas da API."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Inicializa o banco de dados estrutural
init_db()
user_id = get_user_or_create('default_user')


def startup_data_load():
    """Lê o tickers.json, puxa dados da API externa e calcula os indicadores técnicos.
    Só faz a carga pesada (initialize_market_data) se o banco ainda estiver vazio —
    evita repetir o fetch completo a cada reinício do servidor."""
    try:
        if not get_all_assets():
            logger.info("⚡ [Startup] Primeira execução — carregando ativos do tickers.json...")
            initialize_market_data()
        logger.info("⚡ [Startup] Calculando a matriz de recomendações técnicas (RSI, MACD, BB)...")
        update_all_recommendations()
        logger.info("✅ [Startup] Carga inicial de mercado e análises concluída com sucesso!")
    except Exception as e:
        logger.error(f"❌ [Startup] Falha crítica na carga síncrona inicial: {e}")


scheduler = BackgroundScheduler()


def scheduled_update():
    try:
        update_all_assets()
        update_all_recommendations()
    except Exception as e:
        logger.error(f"Erro na atualização agendada: {e}")


# Com debug=True, o reloader do Werkzeug reimporta este módulo em dois
# processos (monitor + worker); sem essa checagem a thread de startup e o
# scheduler eram iniciados em dobro, duplicando chamadas às APIs externas e
# gravações concorrentes no SQLite. WERKZEUG_RUN_MAIN só existe no processo
# worker real, então isso garante uma única inicialização em qualquer modo.
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    Thread(target=startup_data_load, daemon=True).start()
    scheduler.add_job(scheduled_update, 'interval', minutes=config.UPDATE_INTERVAL_MINUTES)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

# ==================== ROTAS HTML ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/market')
def market():
    return render_template('market.html')

@app.route('/portfolio')
def portfolio():
    return render_template('portfolio.html')

@app.route('/transactions')
def transactions():
    return render_template('transactions.html')

@app.route('/recommendations')
def recommendations():
    return render_template('recommendations.html')

@app.route('/dividends')
def dividends():
    return render_template('dividends.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')


# ==================== API - MERCADO ====================

@app.route('/api/market', methods=['GET'])
def api_market():
    try:
        assets = get_all_assets()
        return jsonify({'success': True, 'data': assets})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/market/search', methods=['GET'])
def api_market_search():
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'success': False, 'message': 'Query inválida'}), 400
        assets = search_assets(query)
        return jsonify({'success': True, 'data': assets})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/market/fetch/<ticker>', methods=['GET'])
def api_market_fetch(ticker):
    try:
        data = fetch_any_asset(ticker.upper())
        if not data:
            return jsonify({'success': False, 'message': f'Ticker {ticker} não encontrado na B3'}), 404
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/market/<ticker>', methods=['GET'])
def api_market_detail(ticker):
    try:
        asset = get_asset(ticker.upper())
        if not asset:
            return jsonify({'success': False, 'message': 'Ativo não encontrado'}), 404
        analysis = get_asset_analysis(ticker.upper())
        return jsonify({'success': True, 'data': {'asset': asset, 'analysis': analysis}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - TICKERS ====================

@app.route('/api/tickers', methods=['GET'])
def api_tickers_get():
    return jsonify({'success': True, 'data': load_tickers()})

@app.route('/api/tickers', methods=['POST'])
def api_tickers_add():
    try:
        body = request.get_json(silent=True) or {}
        ticker = body.get('ticker', '').upper().replace('.SA', '')
        name = body.get('name', '').strip()
        if not ticker:
            return jsonify({'success': False, 'message': 'Ticker obrigatório'}), 400
        asset_data = fetch_any_asset(ticker)
        if not asset_data:
            return jsonify({'success': False, 'message': f'{ticker} não encontrado na B3'}), 404
        assets = load_tickers()
        if any(a['ticker'] == ticker for a in assets):
            return jsonify({'success': False, 'message': f'{ticker} já está na lista'}), 409
        asset_type = 'FII' if is_fii(ticker) else 'Ação'
        assets.append({'ticker': ticker, 'name': name or asset_data['name'], 'type': asset_type})
        save_tickers(assets)
        update_asset(asset_data['ticker'], asset_data['name'], asset_data['type'],
                     asset_data['current_price'], asset_data['variation_percent'],
                     asset_data['variation_value'], asset_data['market_cap'], asset_data['volume'])
        return jsonify({'success': True, 'message': f'{ticker} adicionado', 'data': asset_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/tickers/<ticker>', methods=['DELETE'])
def api_tickers_delete(ticker):
    try:
        ticker = ticker.upper().replace('.SA', '')
        assets = load_tickers()
        new_assets = [a for a in assets if a['ticker'] != ticker]
        if len(new_assets) == len(assets):
            return jsonify({'success': False, 'message': 'Ticker não encontrado'}), 404
        save_tickers(new_assets)
        return jsonify({'success': True, 'message': f'{ticker} removido'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/tickers/refresh', methods=['POST'])
def api_tickers_refresh():
    try:
        update_all_assets()
        update_all_recommendations()
        return jsonify({'success': True, 'message': 'Dados atualizados'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - CARTEIRA ====================

@app.route('/api/portfolio', methods=['GET'])
def api_portfolio():
    try:
        portfolio_data = get_portfolio(user_id)
        performance = get_portfolio_performance(user_id)
        return jsonify({'success': True, 'data': {'portfolio': portfolio_data, 'performance': performance}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/detailed', methods=['GET'])
def api_portfolio_detailed():
    try:
        portfolio_data = get_portfolio(user_id)
        performance    = get_portfolio_performance(user_id)
        total_val      = performance.get('total_current_value', 0) or 1
        enriched = enrich_portfolio(portfolio_data, total_val)
        return jsonify({'success': True, 'data': {'portfolio': enriched, 'performance': performance}})
    except Exception as e:
        logger.error(f'Erro em /api/portfolio/detailed: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/stream', methods=['GET'])
def api_portfolio_stream():
    from fundamentals import _enrich_one
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def generate():
        portfolio_data = get_portfolio(user_id)
        performance    = get_portfolio_performance(user_id)
        total_val      = performance.get('total_current_value', 0) or 1
        yield f"data: {json.dumps({'type': 'performance', 'data': performance})}\n\n"
        yield f"data: {json.dumps({'type': 'total', 'count': len(portfolio_data)})}\n\n"
        if not portfolio_data:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_enrich_one, pos, total_val): pos for pos in portfolio_data}
            for future in as_completed(futures):
                try:
                    enriched = future.result()
                except Exception as e:
                    enriched = futures[future]
                    logger.error(f'Stream error: {e}')
                yield f"data: {json.dumps({'type': 'asset', 'data': enriched})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/portfolio/dividends', methods=['GET'])
def api_portfolio_dividends():
    try:
        portfolio_data = get_portfolio(user_id)
        result = portfolio_dividends(portfolio_data)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f'Erro em /api/portfolio/dividends: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/dividends/full', methods=['GET'])
def api_dividends_full():
    try:
        portfolio_data = get_portfolio(user_id)
        result = fetch_dividends_full(portfolio_data, user_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f'Erro em /api/dividends/full: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/fundamentals/clear-cache', methods=['POST'])
def api_clear_fundamentals_cache():
    """Força a próxima consulta de fundamentos/dividendos a ignorar o cache.
    A função já existia (importada de fundamentals) mas nunca tinha uma rota
    que a expusesse — não havia como o usuário invalidar o cache manualmente
    após, por exemplo, uma correção de dados ou um desdobramento de ações."""
    try:
        clear_cache()
        return jsonify({'success': True, 'message': 'Cache de fundamentos e dividendos limpo'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/buy', methods=['POST'])
def api_portfolio_buy():
    try:
        data = request.get_json(silent=True) or {}
        ticker = data.get('ticker', '').upper()
        quantity = _to_float(data.get('quantity'))
        price = _to_float(data.get('price'))
        date = data.get('date') or None
        if not ticker or quantity is None or price is None or quantity <= 0 or price <= 0:
            return jsonify({'success': False, 'message': 'Dados inválidos'}), 400
        if not get_asset(ticker):
            asset_data = fetch_any_asset(ticker)
            if asset_data:
                update_asset(asset_data['ticker'], asset_data['name'], asset_data['type'],
                             asset_data['current_price'], asset_data['variation_percent'],
                             asset_data['variation_value'], asset_data['market_cap'], asset_data['volume'])
        result = buy_asset(user_id, ticker, quantity, price, date)
        return jsonify(result) if result['success'] else (jsonify(result), 400)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/sell', methods=['POST'])
def api_portfolio_sell():
    try:
        data = request.get_json(silent=True) or {}
        ticker = data.get('ticker', '').upper()
        quantity = _to_float(data.get('quantity'))
        price = _to_float(data.get('price'))
        date = data.get('date') or None
        if not ticker or quantity is None or price is None or quantity <= 0 or price <= 0:
            return jsonify({'success': False, 'message': 'Dados inválidos'}), 400
        result = sell_asset(user_id, ticker, quantity, price, date)
        return jsonify(result) if result['success'] else (jsonify(result), 400)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/transactions', methods=['GET'])
def api_portfolio_transactions():
    try:
        transactions_data = get_transactions(user_id)
        return jsonify({'success': True, 'data': transactions_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/transactions/<int:tid>', methods=['PUT'])
def api_transaction_update(tid):
    try:
        data = request.get_json(silent=True) or {}
        quantity = _to_float(data.get('quantity', 0))
        price = _to_float(data.get('price', 0))
        if quantity is None or price is None:
            return jsonify({'success': False, 'message': 'Quantidade/preço inválidos'}), 400
        update_transaction(tid, user_id, data.get('ticker', '').upper(), data.get('type', ''),
                           quantity, price, data.get('date') or None)
        return jsonify({'success': True, 'message': 'Transação atualizada'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/transactions/<int:tid>', methods=['DELETE'])
def api_transaction_delete(tid):
    try:
        delete_transaction(tid, user_id)
        return jsonify({'success': True, 'message': 'Transação excluída'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/<ticker>', methods=['PUT'])
def api_portfolio_update(ticker):
    try:
        data = request.get_json(silent=True) or {}
        quantity = _to_float(data.get('quantity', 0))
        average_price = _to_float(data.get('average_price', 0))
        if quantity is None or average_price is None:
            return jsonify({'success': False, 'message': 'Quantidade/preço médio inválidos'}), 400
        set_portfolio_position(user_id, ticker.upper(), quantity, average_price)
        return jsonify({'success': True, 'message': 'Posição atualizada'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/<ticker>', methods=['DELETE'])
def api_portfolio_delete(ticker):
    try:
        delete_portfolio_position(user_id, ticker.upper())
        return jsonify({'success': True, 'message': 'Posição removida'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - IMPORTAÇÃO ====================

@app.route('/api/import/csv', methods=['POST'])
def api_import_csv():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'Arquivo não fornecido'}), 400
        file = request.files['file']
        if not file.filename.lower().endswith('.csv'):
            return jsonify({'success': False, 'message': 'Apenas arquivos CSV são aceitos'}), 400
        raw = file.read()
        try:
            # utf-8-sig também remove o BOM que o Excel costuma gravar
            csv_data = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            # Exportações do Excel/LibreOffice em pt-BR frequentemente usam latin-1
            csv_data = raw.decode('latin-1')
        result = import_csv(user_id, csv_data)
        return jsonify(result) if result['success'] else (jsonify(result), 400)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - RECOMENDAÇÕES ====================

@app.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    try:
        data = get_top_recommendations(limit=20)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/recommendations/full', methods=['GET'])
def api_recommendations_full():
    try:
        assets    = get_all_assets()
        portfolio = get_portfolio(user_id)
        port_map  = {p['ticker']: p for p in portfolio}
        result = []
        for asset in assets:
            if not asset.get('current_price') or asset['current_price'] <= 0:
                continue
            rec = calculate_recommendation(asset['ticker'])
            if not rec:
                continue
            pos = port_map.get(asset['ticker'])
            profit_pct = None
            if pos:
                avg = pos['average_price']
                cur = asset.get('current_price') if asset.get('current_price') is not None else avg
                profit_pct = round((cur - avg) / avg * 100, 2) if avg else None
            result.append({
                'ticker': asset['ticker'].replace('.SA', ''), 'name': asset.get('name', ''),
                'type': asset.get('type', ''), 'price': asset.get('current_price'),
                'variation_pct': asset.get('variation_percent'),
                'recommendation': rec['recommendation'], 'confidence': rec['confidence_score'],
                'score': rec['score'], 'reason': rec['reason'], 'rsi': rec['rsi'],
                'variation_7d': rec['variation_7d'], 'variation_30d': rec['variation_30d'],
                'volume_trend': rec['volume_trend'], 'sma20': rec.get('sma20'),
                'sma50': rec.get('sma50'), 'sma200': rec.get('sma200'),
                'macd': rec.get('macd'), 'macd_signal': rec.get('macd_signal'),
                'bb_upper': rec.get('bb_upper'), 'bb_lower': rec.get('bb_lower'),
                'in_portfolio': pos is not None, 'profit_pct': profit_pct,
            })
        result.sort(key=lambda x: (0 if x['recommendation']=='COMPRA' else 1 if x['recommendation']=='MANUTENÇÃO' else 2, -(x['confidence'] or 0)))
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f'Erro em /api/recommendations/full: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/recommendations/<ticker>', methods=['GET'])
def api_recommendation_detail(ticker):
    try:
        rec = get_recommendation(ticker.upper())
        if not rec:
            return jsonify({'success': False, 'message': 'Recomendação não encontrada'}), 404
        return jsonify({'success': True, 'data': rec})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/analysis/<ticker>', methods=['GET'])
def api_analysis(ticker):
    try:
        analysis = get_asset_analysis(ticker.upper())
        if not analysis:
            return jsonify({'success': False, 'message': 'Análise não disponível'}), 404
        history = get_price_history(ticker.upper(), days=30)
        return jsonify({'success': True, 'data': {'analysis': analysis, 'history': history}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - APORTE ====================

@app.route('/api/portfolio/next-buy', methods=['GET'])
def api_next_buy():
    """Sugere o próximo ativo. Usa apenas DB local — zero chamadas externas."""
    try:
        portfolio_data = get_portfolio(user_id)
        if not portfolio_data:
            return jsonify({'success': False, 'message': 'Carteira vazia'}), 400

        performance = get_portfolio_performance(user_id)
        total_val   = performance.get('total_current_value', 0) or 1

        # Recomendações já calculadas no DB (sem recalcular)
        recs_list = get_recommendations() or []
        recs_map  = {r['ticker']: r for r in recs_list}

        scored = []
        for pos in portfolio_data:
            asset = get_asset(pos['ticker']) or {}
            price = asset.get('current_price') if asset.get('current_price') is not None else pos['average_price']
            if not price or price <= 0:
                continue

            pct_port = round((pos['quantity'] * price) / total_val * 100, 2)

            rec = recs_map.get(pos['ticker']) or recs_map.get(pos['ticker'].replace('.SA',''))
            sig_action = ''
            sig_reason = ''
            if rec:
                action = rec.get('recommendation', '')
                sig_action = 'COMPRAR' if action == 'COMPRA' else ('VENDER' if action == 'VENDA' else 'MANTER')
                sig_reason = rec.get('reason', '')
                sig_cls    = 'green' if action == 'COMPRA' else ('red' if action == 'VENDA' else 'yellow')
            else:
                sig_cls = 'yellow'

            sig_bonus = 2 if sig_action == 'COMPRAR' else (1 if sig_action == 'MANTER' else 0)
            # Score simples: prioriza menor % na carteira (mais subrepresentado) + sinal
            score = (100 - pct_port) * 0.7 + sig_bonus * 0.3

            scored.append((score, {
                'ticker':      pos['ticker'],
                'name':        pos.get('name', pos['ticker'].replace('.SA','')),
                'current_price': price,
                'pct_portfolio': pct_port,
                'dy':          asset.get('dy'),
                'buy_signal':  {'action': sig_action, 'cls': sig_cls, 'reason': sig_reason} if rec else None,
            }))

        if not scored:
            return jsonify({'success': False, 'message': 'Sem dados suficientes'}), 400

        scored.sort(key=lambda x: -x[0])
        _, best = scored[0]
        price  = best['current_price']
        ticker = best['ticker'].replace('.SA', '')

        # Sugestões de quantidade progressivas
        second_price = scored[1][1]['current_price'] if len(scored) > 1 else None
        qty_steps, qty = [], 1
        while True:
            cost = round(qty * price, 2)
            if qty == 1:
                label = 'Mínimo — 1 cota'
            elif second_price and cost >= second_price * 2:
                label = f'Limite — com esse valor já cabem {int(cost//second_price)} cotas de {scored[1][1]["ticker"].replace(".SA","")}'
                qty_steps.append({'qty': qty, 'cost': cost, 'label': label})
                break
            elif qty <= 5:
                label = f'{qty} cotas — entrada gradual'
            elif qty <= 20:
                label = f'{qty} cotas — posição moderada'
            else:
                label = f'{qty} cotas — posição relevante'
            qty_steps.append({'qty': qty, 'cost': cost, 'label': label})
            if len(qty_steps) >= 6:
                break
            qty = qty + 1 if qty < 5 else qty * 2

        # Alternativas
        alternatives = []
        for _, a in scored[1:4]:
            sig = (a.get('buy_signal') or {}).get('action', '—')
            alternatives.append({
                'ticker':     a['ticker'].replace('.SA',''),
                'price':      a['current_price'],
                'dy':         a.get('dy'),
                'signal':     sig,
                'signal_cls': (a.get('buy_signal') or {}).get('cls', 'yellow'),
                'reason':     (a.get('buy_signal') or {}).get('reason','') or f"{a.get('pct_portfolio',0):.1f}% da carteira",
            })

        parts = [f"{best['pct_portfolio']:.1f}% da carteira — mais subrepresentado"]
        if best.get('dy'):
            parts.append(f"DY de {best['dy']:.2f}%")
        if (best.get('buy_signal') or {}).get('action') == 'COMPRAR':
            parts.append('sinal técnico de compra')

        return jsonify({'success': True, 'data': {
            'suggestion': {
                'ticker':      ticker,
                'name':        best.get('name', ticker),
                'price':       price,
                'dy':          best.get('dy'),
                'qty_steps':   qty_steps,
                'main_reason': ', '.join(parts),
            },
            'alternatives': alternatives,
            'strategy':     None,
        }})
    except Exception as e:
        logger.error(f'Erro em /api/portfolio/next-buy: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/portfolio/allocate', methods=['GET'])
def api_portfolio_allocate():
    """Recomenda compras para um valor fixo de aporte baseado nos ativos da carteira."""
    try:
        budget = _to_float(request.args.get('budget', 0))
        if budget is None or budget <= 0:
            return jsonify({'success': False, 'message': 'Informe um valor de aporte válido'}), 400

        portfolio_data = get_portfolio(user_id)
        if not portfolio_data:
            return jsonify({'success': False, 'message': 'Carteira vazia'}), 400

        performance = get_portfolio_performance(user_id)
        total_val   = performance.get('total_current_value', 0) or 1
        enriched    = enrich_portfolio(portfolio_data, total_val)

        # Filtra apenas ativos que vale negociar agora (abaixo do preço negociável)
        # Se nenhum estiver abaixo, relaxa e usa todos com sinal COMPRAR
        candidates = [a for a in enriched
                      if a.get('negotiable_price') and a['current_price'] <= a['negotiable_price']]
        if not candidates:
            candidates = [a for a in enriched
                          if a.get('buy_signal', {}).get('action') == 'COMPRAR']
        if not candidates:
            candidates = enriched  # fallback: todos

        # Ordena por DY decrescente (maior renda primeiro); sem DY vai para o fim
        candidates.sort(key=lambda a: a.get('dy') or 0, reverse=True)

        # Peso de cada ativo = ideal_income_pct ou DY ou 1 (igual)
        weights = [a.get('ideal_income_pct') or a.get('dy') or 1 for a in candidates]
        total_w = sum(weights) or 1

        remaining = budget
        suggestions = []
        near_miss   = []  # ativos interessantes que não couberam no budget

        for a, w in zip(candidates, weights):
            alloc   = budget * (w / total_w)
            price   = a['current_price']
            qty     = int(alloc // price)
            if qty < 1:
                qty = 1
            cost    = round(qty * price, 2)
            if cost > remaining:
                qty  = int(remaining // price)
                cost = round(qty * price, 2)
            if qty < 1:
                # Não coube — adiciona como cogitação se o preço for razoável
                near_miss.append({
                    'ticker':           a['ticker'].replace('.SA', ''),
                    'price':            price,
                    'missing':          round(price - remaining, 2),
                    'dy':               a.get('dy'),
                    'fair_price':       a.get('fair_price'),
                    'ceiling_price':    a.get('ceiling_price'),
                    'negotiable_price': a.get('negotiable_price'),
                    'buy_signal':       a.get('buy_signal', {}).get('action', '—'),
                    'reason':           a.get('buy_signal', {}).get('reason', ''),
                })
                continue
            remaining = round(remaining - cost, 2)
            suggestions.append({
                'ticker':           a['ticker'].replace('.SA', ''),
                'quantity':         qty,
                'price':            price,
                'cost':             cost,
                'dy':               a.get('dy'),
                'fair_price':       a.get('fair_price'),
                'ceiling_price':    a.get('ceiling_price'),
                'negotiable_price': a.get('negotiable_price'),
                'buy_signal':       a.get('buy_signal', {}).get('action', '—'),
                'reason':           a.get('buy_signal', {}).get('reason', ''),
            })

        # Se budget é pequeno demais para qualquer compra, todos viram cogitações
        # Inclui também ativos que não eram candidatos mas têm sinal positivo
        if not near_miss:
            for a in enriched:
                tk = a['ticker'].replace('.SA', '')
                if any(s['ticker'] == tk for s in suggestions):
                    continue
                sig = a.get('buy_signal', {}).get('action', '')
                if sig in ('COMPRAR', 'MANTER') and a['current_price'] > remaining:
                    near_miss.append({
                        'ticker':           tk,
                        'price':            a['current_price'],
                        'missing':          round(a['current_price'] - remaining, 2),
                        'dy':               a.get('dy'),
                        'fair_price':       a.get('fair_price'),
                        'ceiling_price':    a.get('ceiling_price'),
                        'negotiable_price': a.get('negotiable_price'),
                        'buy_signal':       sig,
                        'reason':           a.get('buy_signal', {}).get('reason', ''),
                    })

        near_miss.sort(key=lambda x: x.get('missing') or 0)

        return jsonify({
            'success': True,
            'data': {
                'budget':      budget,
                'total_used':  round(budget - remaining, 2),
                'remaining':   remaining,
                'suggestions': suggestions,
                'near_miss':   near_miss,
            }
        })
    except Exception as e:
        logger.error(f'Erro em /api/portfolio/allocate: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== API - DASHBOARD ====================

@app.route('/api/dashboard', methods=['GET'])
def api_dashboard():
    """Dados consolidados para o dashboard: performance, saúde da carteira, oportunidades e transações."""
    try:
        performance  = get_portfolio_performance(user_id)
        health       = get_portfolio_health(user_id)
        opportunities = get_market_opportunities(limit=5)
        transactions_data = get_transactions(user_id)
        return jsonify({
            'success': True,
            'data': {
                'performance':    performance,
                'health':         health,
                'opportunities':  opportunities,
                'transactions':   transactions_data[:6],
            }
        })
    except Exception as e:
        logger.error(f'Erro em /api/dashboard: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== HEALTH CHECK ====================

@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'success': True, 'status': 'online', 'timestamp': datetime.now().isoformat()})

@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': 'Recurso não encontrado'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'message': 'Erro interno do servidor'}), 500

if __name__ == '__main__':
    app.run(debug=config.FLASK_DEBUG, host=config.FLASK_HOST, port=config.FLASK_PORT)
