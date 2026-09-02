import csv
import io
import logging

from database import (
    add_transaction_and_update_portfolio, get_portfolio, get_asset
)

logger = logging.getLogger(__name__)

_EMPTY_PERFORMANCE = {
    'total_invested': 0,
    'total_current_value': 0,
    'total_profit_loss': 0,
    'total_profit_loss_percent': 0,
    'assets_count': 0
}


def buy_asset(user_id, ticker, quantity, price, date=None):
    """Registra compra de um ativo"""
    try:
        asset = get_asset(ticker)
        if not asset:
            return {'success': False, 'message': f'Ativo {ticker} não encontrado'}

        if quantity <= 0:
            return {'success': False, 'message': 'Quantidade deve ser maior que zero'}

        total_value = quantity * price
        add_transaction_and_update_portfolio(
            user_id, ticker, 'compra', quantity, price,
            portfolio_qty_delta=quantity, portfolio_ref_price=price, date=date
        )

        logger.info(f"Compra registrada: {ticker} x{quantity} @ R${price} em {date or 'hoje'}")
        return {
            'success': True,
            'message': f'Compra de {quantity} {ticker} registrada com sucesso',
            'total_value': total_value
        }
    except Exception as e:
        logger.error(f"Erro ao comprar {ticker}: {str(e)}")
        return {'success': False, 'message': f'Erro: {str(e)}'}


def sell_asset(user_id, ticker, quantity, price, date=None):
    """Registra venda de um ativo"""
    try:
        portfolio = get_portfolio(user_id)
        asset_in_portfolio = next((p for p in portfolio if p['ticker'] == ticker), None)

        if not asset_in_portfolio:
            return {'success': False, 'message': f'Você não possui {ticker}'}
        if quantity <= 0:
            return {'success': False, 'message': 'Quantidade deve ser maior que zero'}
        held_quantity = asset_in_portfolio['quantity']
        if quantity > held_quantity:
            return {'success': False, 'message': f'Quantidade insuficiente. Você possui {held_quantity}'}

        cost_price = asset_in_portfolio['average_price']
        profit_loss = (price - cost_price) * quantity
        profit_loss_percent = ((price - cost_price) / cost_price * 100) if cost_price != 0 else 0
        total_value = quantity * price

        add_transaction_and_update_portfolio(
            user_id, ticker, 'venda', quantity, price,
            portfolio_qty_delta=-quantity, portfolio_ref_price=cost_price,
            profit_loss=profit_loss, date=date
        )

        logger.info(f"Venda registrada: {ticker} x{quantity} @ R${price} | Ganho/Perda: R${profit_loss}")
        return {
            'success': True,
            'message': f'Venda de {quantity} {ticker} registrada com sucesso',
            'total_value': total_value,
            'profit_loss': round(profit_loss, 2),
            'profit_loss_percent': round(profit_loss_percent, 2)
        }
    except Exception as e:
        logger.error(f"Erro ao vender {ticker}: {str(e)}")
        return {'success': False, 'message': f'Erro: {str(e)}'}


def get_portfolio_performance(user_id):
    """Calcula performance da carteira"""
    try:
        portfolio = get_portfolio(user_id)

        if not portfolio:
            return dict(_EMPTY_PERFORMANCE)

        total_invested = 0
        total_current_value = 0

        for asset in portfolio:
            current_price = asset['current_price'] if asset['current_price'] is not None else asset['average_price']
            invested = asset['quantity'] * asset['average_price']
            current = asset['quantity'] * current_price

            total_invested += invested
            total_current_value += current

        total_profit_loss = total_current_value - total_invested
        total_profit_loss_percent = (total_profit_loss / total_invested * 100) if total_invested != 0 else 0

        return {
            'total_invested': round(total_invested, 2),
            'total_current_value': round(total_current_value, 2),
            'total_profit_loss': round(total_profit_loss, 2),
            'total_profit_loss_percent': round(total_profit_loss_percent, 2),
            'assets_count': len(portfolio)
        }
    except Exception as e:
        logger.error(f"Erro ao calcular performance: {str(e)}")
        return dict(_EMPTY_PERFORMANCE)


def import_csv(user_id, csv_data):
    """Importa dados de carteira via CSV. Colunas obrigatórias: ticker, quantity,
    price; coluna opcional: date. A ordem das colunas no cabeçalho é livre."""
    try:
        csv_data = csv_data.strip()
        if not csv_data:
            return {'success': False, 'message': 'CSV vazio'}

        rows = list(csv.reader(io.StringIO(csv_data)))
        if not rows:
            return {'success': False, 'message': 'CSV vazio'}

        header = [h.strip().lower() for h in rows[0]]
        required = ('ticker', 'quantity', 'price')
        if not all(col in header for col in required):
            return {'success': False, 'message': 'Formato CSV inválido. Esperado: ticker,quantity,price,date'}

        col_idx = {col: header.index(col) for col in required}
        date_idx = header.index('date') if 'date' in header else None

        imported = 0
        errors = []

        for i, parts in enumerate(rows[1:], start=2):
            if not parts or all(not p.strip() for p in parts):
                continue
            try:
                if len(parts) < len(header):
                    errors.append(f"Linha {i}: Dados insuficientes")
                    continue

                ticker = parts[col_idx['ticker']].strip().upper()
                quantity = float(parts[col_idx['quantity']].strip())
                price = float(parts[col_idx['price']].strip())
                date = None
                if date_idx is not None and date_idx < len(parts):
                    date = parts[date_idx].strip() or None

                result = buy_asset(user_id, ticker, quantity, price, date)
                if result['success']:
                    imported += 1
                else:
                    errors.append(f"Linha {i}: {result['message']}")
            except ValueError as e:
                errors.append(f"Linha {i}: Erro de conversão - {str(e)}")
            except Exception as e:
                errors.append(f"Linha {i}: {str(e)}")

        message = f"Importação concluída: {imported} ativos importados"
        if errors:
            message += f" com {len(errors)} erro(s)"

        return {
            'success': True,
            'message': message,
            'imported': imported,
            'errors': errors
        }
    except Exception as e:
        logger.error(f"Erro ao importar CSV: {str(e)}")
        return {'success': False, 'message': f'Erro: {str(e)}'}
