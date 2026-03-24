from database import (
    add_transaction, update_portfolio, get_portfolio, 
    get_asset, get_transactions, get_portfolio_summary
)
import logging

logger = logging.getLogger(__name__)

def buy_asset(user_id, ticker, quantity, price, date=None):
    """Registra compra de um ativo"""
    try:
        asset = get_asset(ticker)
        if not asset:
            return {'success': False, 'message': f'Ativo {ticker} não encontrado'}
        
        if quantity <= 0:
            return {'success': False, 'message': 'Quantidade deve ser maior que zero'}
        
        total_value = quantity * price
        add_transaction(user_id, ticker, 'compra', quantity, price, date=date)
        update_portfolio(user_id, ticker, quantity, price)
        
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
        if quantity > asset_in_portfolio['quantity']:
            return {'success': False, 'message': f'Quantidade insuficiente. Você possui {asset_in_portfolio["quantity"]}'}
        if quantity <= 0:
            return {'success': False, 'message': 'Quantidade deve ser maior que zero'}
        
        cost_price = asset_in_portfolio['average_price']
        profit_loss = (price - cost_price) * quantity
        profit_loss_percent = ((price - cost_price) / cost_price * 100) if cost_price != 0 else 0
        total_value = quantity * price
        
        add_transaction(user_id, ticker, 'venda', -quantity, price, profit_loss, date=date)
        update_portfolio(user_id, ticker, -quantity, cost_price)
        
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
            return {
                'total_invested': 0,
                'total_current_value': 0,
                'total_profit_loss': 0,
                'total_profit_loss_percent': 0,
                'assets_count': 0
            }
        
        total_invested = 0
        total_current_value = 0
        
        for asset in portfolio:
            invested = asset['quantity'] * asset['average_price']
            current = asset['quantity'] * (asset['current_price'] or asset['average_price'])
            
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
        return None

def import_csv(user_id, csv_data):
    """Importa dados de carteira via CSV"""
    try:
        lines = csv_data.strip().split('\n')
        
        if not lines:
            return {'success': False, 'message': 'CSV vazio'}
        
        # Pula header
        header = lines[0].lower()
        if 'ticker' not in header or 'quantity' not in header or 'price' not in header:
            return {'success': False, 'message': 'Formato CSV inválido. Esperado: ticker,quantity,price,date'}
        
        imported = 0
        errors = []
        
        for i, line in enumerate(lines[1:], start=2):
            try:
                parts = line.strip().split(',')
                if len(parts) < 3:
                    errors.append(f"Linha {i}: Dados insuficientes")
                    continue
                
                ticker = parts[0].strip().upper()
                quantity = float(parts[1].strip())
                price = float(parts[2].strip())
                date = parts[3].strip() if len(parts) > 3 else None
                
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
