from database import (
    get_price_history, save_recommendation, get_recommendation,
    get_all_assets, get_portfolio, get_recommendations
)
import logging

logger = logging.getLogger(__name__)


# ── Indicadores técnicos ──────────────────────────────────────────────────────

def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return round(100 - 100 / (1 + ag / al), 2)


def _sma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _macd(closes):
    """Retorna (macd_line, signal_line). Positivo = momentum de alta."""
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if ema12 is None or ema26 is None:
        return None, None
    macd_line = ema12 - ema26
    # Signal = EMA9 do MACD — simplificado: usa os últimos 9 valores de MACD
    if len(closes) < 35:
        return macd_line, None
    macd_series = []
    for i in range(9, len(closes) + 1):
        e12 = _ema(closes[:i], 12)
        e26 = _ema(closes[:i], 26)
        if e12 and e26:
            macd_series.append(e12 - e26)
    signal = _ema(macd_series, 9) if len(macd_series) >= 9 else None
    return macd_line, signal


def _bollinger(closes, period=20):
    """Retorna (upper, middle, lower)."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    std = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return round(mid + 2 * std, 4), round(mid, 4), round(mid - 2 * std, 4)


# ── Recomendação principal ────────────────────────────────────────────────────

def calculate_recommendation(ticker):
    """
    Calcula recomendação técnica com RSI, MACD, Bollinger, médias móveis e
    variação. Thresholds calibrados para o mercado brasileiro atual.
    """
    try:
        history = sorted(
            get_price_history(ticker, days=120),
            key=lambda x: x['date']
        )
        if len(history) < 14:
            return None

        closes  = [h['close']  for h in history]
        volumes = [h['volume'] for h in history]
        price   = closes[-1]

        # Filtra ativos sem cotação real
        if price <= 0:
            return None

        # ── Variações ──
        var_7d  = (price - closes[-7])  / closes[-7]  * 100 if len(closes) >= 7  and closes[-7]  else 0
        var_30d = (price - closes[-30]) / closes[-30] * 100 if len(closes) >= 30 and closes[-30] else 0

        # ── Indicadores ──
        rsi_val  = _rsi(closes)
        sma20    = _sma(closes, 20)
        sma50    = _sma(closes, 50)
        sma200   = _sma(closes, 200)
        macd_l, macd_s = _macd(closes)
        bb_up, bb_mid, bb_low = _bollinger(closes)

        avg_vol = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 20 else None
        vol_trend = (volumes[-1] - avg_vol) / avg_vol * 100 if avg_vol and avg_vol > 0 else 0

        score   = 0.0
        reasons = []

        # ── RSI (sobrevendido = oportunidade, sobrecomprado = cautela) ──
        if rsi_val < 25:
            score += 3.5; reasons.append(f'RSI extremamente sobrevendido ({rsi_val:.0f}) — forte sinal de reversão')
        elif rsi_val < 30:
            score += 2.5; reasons.append(f'RSI sobrevendido ({rsi_val:.0f}) — possível reversão')
        elif rsi_val < 40:
            score += 1.0; reasons.append(f'RSI baixo ({rsi_val:.0f})')
        elif rsi_val > 75:
            score -= 2.5; reasons.append(f'RSI extremamente sobrecomprado ({rsi_val:.0f}) — cautela máxima')
        elif rsi_val > 70:
            score -= 2.0; reasons.append(f'RSI sobrecomprado ({rsi_val:.0f}) — cautela')
        elif rsi_val > 60:
            score -= 0.5; reasons.append(f'RSI elevado ({rsi_val:.0f})')

        # ── MACD ──
        if macd_l is not None and macd_s is not None:
            if macd_l > macd_s and macd_l > 0:
                score += 1.5; reasons.append('MACD acima da linha de sinal (momentum positivo)')
            elif macd_l > macd_s and macd_l <= 0:
                score += 0.5; reasons.append('MACD cruzando para cima')
            elif macd_l < macd_s and macd_l < 0:
                score -= 1.5; reasons.append('MACD abaixo da linha de sinal (momentum negativo)')
            elif macd_l < macd_s:
                score -= 0.5; reasons.append('MACD cruzando para baixo')

        # ── Médias móveis ──
        if sma20 and sma50:
            if price > sma20 > sma50:
                score += 1.5; reasons.append('preço acima de SMA20 e SMA50 (tendência de alta)')
            elif price < sma20 < sma50:
                score -= 1.5; reasons.append('preço abaixo de SMA20 e SMA50 (tendência de baixa)')
            elif price > sma20 and sma20 < sma50:
                score += 0.5; reasons.append('preço acima de SMA20 (recuperação)')
            elif price < sma20 and sma20 > sma50:
                score -= 0.5; reasons.append('preço abaixo de SMA20 (correção)')

        # SMA200 só conta se tiver histórico suficiente (>= 180 dias)
        if sma200 and len(closes) >= 180:
            if price > sma200:
                score += 1.0; reasons.append('acima da média de 200 dias (tendência longa positiva)')
            else:
                score -= 1.0; reasons.append('abaixo da média de 200 dias (tendência longa negativa)')

        # ── Bollinger Bands ──
        if bb_low and bb_up and bb_mid:
            if price <= bb_low:
                score += 2.0; reasons.append('abaixo da banda inferior de Bollinger (sobrevendido)')
            elif price < bb_low * 1.015:
                score += 1.0; reasons.append('próximo da banda inferior de Bollinger')
            elif price >= bb_up:
                score -= 2.0; reasons.append('acima da banda superior de Bollinger (sobrecomprado)')
            elif price > bb_up * 0.985:
                score -= 1.0; reasons.append('próximo da banda superior de Bollinger')

        # ── Volume ──
        if vol_trend > 30:
            score += 0.5; reasons.append(f'volume acima da média (+{vol_trend:.0f}%)')
        elif vol_trend < -30:
            score -= 0.5; reasons.append(f'volume abaixo da média ({vol_trend:.0f}%)')

        # ── Variação 30d (tendência de médio prazo) ──
        if var_30d > 15:
            score += 1.0; reasons.append(f'alta de {var_30d:.1f}% em 30 dias')
        elif var_30d > 5:
            score += 0.5; reasons.append(f'alta de {var_30d:.1f}% em 30 dias')
        elif var_30d < -15:
            score -= 1.0; reasons.append(f'queda de {abs(var_30d):.1f}% em 30 dias')
        elif var_30d < -5:
            score -= 0.5; reasons.append(f'queda de {abs(var_30d):.1f}% em 30 dias')

        # ── Thresholds calibrados ──
        # Score máximo teórico ~12, mínimo ~-12
        # COMPRA: score >= 2.5  |  VENDA: score <= -2.5  |  demais: MANUTENÇÃO
        if score >= 2.5:
            rec = 'COMPRA'
        elif score <= -2.5:
            rec = 'VENDA'
        else:
            rec = 'MANUTENÇÃO'

        # Confiança: normaliza score para 0-100
        confidence = min(abs(score) / 8.0 * 100, 100)
        if rec == 'MANUTENÇÃO':
            confidence = max(30, 50 - abs(score) * 5)

        return {
            'ticker':         ticker,
            'recommendation': rec,
            'confidence_score': round(confidence, 1),
            'reason':         ' | '.join(reasons) if reasons else 'sem sinal técnico claro',
            'score':          round(score, 2),
            'rsi':            rsi_val,
            'variation_7d':   round(var_7d, 2),
            'variation_30d':  round(var_30d, 2),
            'volume_trend':   round(vol_trend, 2),
            'sma20':          round(sma20, 2) if sma20 else None,
            'sma50':          round(sma50, 2) if sma50 else None,
            'sma200':         round(sma200, 2) if sma200 else None,
            'macd':           round(macd_l, 4) if macd_l is not None else None,
            'macd_signal':    round(macd_s, 4) if macd_s is not None else None,
            'bb_upper':       bb_up,
            'bb_lower':       bb_low,
            'price':          round(price, 2),
        }
    except Exception as e:
        logger.error(f'Erro ao calcular recomendação para {ticker}: {e}')
        return None


# ── Atualização em lote ───────────────────────────────────────────────────────

def update_all_recommendations():
    logger.info('Atualizando recomendações...')
    updated = 0
    for asset in get_all_assets():
        rec = calculate_recommendation(asset['ticker'])
        if rec:
            save_recommendation(rec['ticker'], rec['recommendation'],
                                rec['confidence_score'], rec['reason'])
            updated += 1
    logger.info(f'Recomendações atualizadas: {updated}')


def get_top_recommendations(limit=10):
    recs = get_recommendations()
    buy  = sorted([r for r in recs if r['recommendation'] == 'COMPRA'],
                  key=lambda x: x['confidence_score'], reverse=True)
    sell = sorted([r for r in recs if r['recommendation'] == 'VENDA'],
                  key=lambda x: x['confidence_score'], reverse=True)
    hold = [r for r in recs if r['recommendation'] == 'MANUTENÇÃO']
    return (buy[:limit // 3] + sell[:limit // 3] + hold[:limit // 3])[:limit]


# ── Oportunidades de mercado ──────────────────────────────────────────────────

def get_market_opportunities(limit=5):
    """Retorna os melhores ativos do mercado monitorado para compra agora."""
    opportunities = []
    for asset in get_all_assets():
        if not asset.get('current_price') or asset['current_price'] <= 0:
            continue
        rec = calculate_recommendation(asset['ticker'])
        if not rec or rec['recommendation'] != 'COMPRA':
            continue
        opportunities.append({
            'ticker':        asset['ticker'].replace('.SA', ''),
            'name':          asset.get('name', ''),
            'price':         asset.get('current_price', rec['price']),
            'variation_pct': asset.get('variation_percent', 0),
            'recommendation': rec['recommendation'],
            'confidence':    rec['confidence_score'],
            'reason':        rec['reason'],
            'rsi':           rec['rsi'],
            'variation_7d':  rec['variation_7d'],
            'score':         rec['score'],
        })
    opportunities.sort(key=lambda x: x['confidence'], reverse=True)
    return opportunities[:limit]


# ── Saúde da carteira ─────────────────────────────────────────────────────────

def get_portfolio_health(user_id):
    """
    Classifica cada ativo da carteira como 'bem', 'neutro' ou 'mal'
    com base em rentabilidade, RSI, tendência e variação 7d.
    """
    portfolio = get_portfolio(user_id)
    if not portfolio:
        return {'well': [], 'neutral': [], 'bad': [], 'summary': {}}

    well, neutral, bad = [], [], []

    for pos in portfolio:
        ticker = pos['ticker']
        avg    = pos['average_price']
        cur    = pos.get('current_price') or avg
        qty    = pos['quantity']

        if avg <= 0:
            continue

        profit_pct = (cur - avg) / avg * 100
        pl         = qty * (cur - avg)

        rec        = calculate_recommendation(ticker)
        rsi_val    = rec['rsi']        if rec else 50
        var_7d     = rec['variation_7d'] if rec else 0
        rec_reason = rec['reason']     if rec else ''

        health = 0.0
        reasons = []

        if profit_pct > 15:
            health += 3; reasons.append(f'+{profit_pct:.1f}% desde a compra')
        elif profit_pct > 5:
            health += 1.5; reasons.append(f'+{profit_pct:.1f}% desde a compra')
        elif profit_pct > 0:
            health += 0.5; reasons.append(f'+{profit_pct:.1f}% desde a compra')
        elif profit_pct < -15:
            health -= 3; reasons.append(f'{profit_pct:.1f}% desde a compra')
        elif profit_pct < -5:
            health -= 1.5; reasons.append(f'{profit_pct:.1f}% desde a compra')
        else:
            health -= 0.5; reasons.append(f'{profit_pct:.1f}% desde a compra')

        if var_7d > 4:
            health += 1.5; reasons.append(f'alta de {var_7d:.1f}% em 7d')
        elif var_7d > 1:
            health += 0.5
        elif var_7d < -4:
            health -= 1.5; reasons.append(f'queda de {abs(var_7d):.1f}% em 7d')
        elif var_7d < -1:
            health -= 0.5

        if rsi_val < 35:
            health += 1; reasons.append(f'RSI sobrevendido ({rsi_val:.0f}) — oportunidade de reforço')
        elif rsi_val > 70:
            health -= 1; reasons.append(f'RSI sobrecomprado ({rsi_val:.0f}) — considere realizar')

        entry = {
            'ticker':      ticker.replace('.SA', ''),
            'name':        pos.get('name', ''),
            'quantity':    qty,
            'avg_price':   round(avg, 2),
            'cur_price':   round(cur, 2),
            'profit_pct':  round(profit_pct, 2),
            'profit_loss': round(pl, 2),
            'rsi':         rsi_val,
            'var_7d':      var_7d,
            'score':       round(health, 2),
            'reasons':     reasons,
            'rec_reason':  rec_reason,
        }

        if health >= 1.5:
            well.append(entry)
        elif health <= -1.5:
            bad.append(entry)
        else:
            neutral.append(entry)

    well.sort(key=lambda x: x['profit_pct'], reverse=True)
    bad.sort(key=lambda x: x['profit_pct'])

    total_pl = sum(p['profit_loss'] for p in well + neutral + bad)

    return {
        'well':    well,
        'neutral': neutral,
        'bad':     bad,
        'summary': {
            'total':    len(portfolio),
            'well':     len(well),
            'neutral':  len(neutral),
            'bad':      len(bad),
            'total_pl': round(total_pl, 2),
        }
    }
