import sqlite3
import threading
import logging
import os
from contextlib import contextmanager
from datetime import datetime

from config import DATABASE_PATH

DB_PATH = DATABASE_PATH

logger = logging.getLogger(__name__)

# Serializa escritas neste processo para evitar condições de corrida do tipo
# "select para checar se existe, depois insert/update" (ex.: criar o mesmo
# usuário duas vezes, ou duplicar uma posição da carteira).
_write_lock = threading.Lock()


@contextmanager
def _connection():
    """Abre uma conexão, garante commit em sucesso, rollback em erro e sempre fecha."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Inicializa o banco de dados com as tabelas necessárias"""
    with _connection() as conn:
        cursor = conn.cursor()

        # Tabela de usuários
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabela de ativos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT UNIQUE NOT NULL,
                name TEXT,
                type TEXT,
                current_price REAL,
                variation_percent REAL,
                variation_value REAL,
                last_update TIMESTAMP,
                market_cap TEXT,
                volume INTEGER
            )
        ''')

        # Tabela de carteira
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                quantity REAL,
                average_price REAL,
                total_value REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE (user_id, ticker)
            )
        ''')

        # Tabela de transações
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                type TEXT,
                quantity REAL,
                price REAL,
                total_value REAL,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                profit_loss REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')

        # Tabela de recomendações
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                recommendation TEXT,
                confidence_score REAL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tabela de histórico de preços
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date TIMESTAMP,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER
            )
        ''')

        # Migração: a tabela recommendations nunca teve UNIQUE(ticker), então
        # "INSERT OR REPLACE" em save_recommendation não substituía nada — cada
        # execução do scheduler (a cada 15 min) inseria uma linha nova. Isso
        # acumulou milhares de duplicatas e fazia get_recommendation()/
        # get_recommendations() devolverem entradas antigas e aleatórias em vez
        # da recomendação atual. Deduplica mantendo a linha mais recente por
        # ticker e passa a impedir duplicatas novas.
        cursor.execute('''
            DELETE FROM recommendations
            WHERE id NOT IN (SELECT MAX(id) FROM recommendations GROUP BY ticker)
        ''')
        cursor.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendations_ticker ON recommendations(ticker)'
        )


def get_user_or_create(username='default_user'):
    """Obtém ou cria um usuário padrão (protegido contra corrida entre threads)."""
    with _write_lock:
        with _connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            if user:
                return user[0]

            try:
                cursor.execute(
                    'INSERT INTO users (username, email) VALUES (?, ?)',
                    (username, f'{username}@regulador.local')
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
                return cursor.fetchone()[0]


def update_asset(ticker, name, asset_type, current_price, variation_percent,
                 variation_value, market_cap, volume):
    """Atualiza ou insere um ativo no banco"""
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO assets
            (ticker, name, type, current_price, variation_percent, variation_value,
             last_update, market_cap, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, name, asset_type, current_price, variation_percent,
              variation_value, datetime.now().isoformat(), market_cap, volume))


def get_all_assets():
    """Retorna todos os ativos"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM assets ORDER BY ticker')
        return [dict(asset) for asset in cursor.fetchall()]


def get_asset(ticker):
    """Retorna um ativo específico"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM assets WHERE ticker = ?', (ticker,))
        asset = cursor.fetchone()
        return dict(asset) if asset else None


def search_assets(query):
    """Busca ativos por ticker ou nome"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        like_query = f'%{query}%'
        cursor.execute('''
            SELECT * FROM assets
            WHERE ticker LIKE ? OR name LIKE ?
            ORDER BY ticker
        ''', (like_query, like_query))
        return [dict(asset) for asset in cursor.fetchall()]


def add_transaction(user_id, ticker, trans_type, quantity, price, profit_loss=None, date=None):
    """Adiciona uma transação (sem tocar na carteira). Veja add_transaction_and_update_portfolio
    para a operação atômica usada em compra/venda."""
    with _connection() as conn:
        cursor = conn.cursor()
        total_value = abs(quantity) * price
        transaction_date = date if date else datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO transactions
            (user_id, ticker, type, quantity, price, total_value, profit_loss, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, ticker, trans_type, quantity, price, total_value, profit_loss, transaction_date))


def get_transactions(user_id):
    """Retorna todas as transações de um usuário"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM transactions
            WHERE user_id = ?
            ORDER BY date DESC
        ''', (user_id,))
        return [dict(t) for t in cursor.fetchall()]


def _upsert_portfolio(cursor, user_id, ticker, quantity, average_price):
    """Aplica um delta de quantidade na posição do usuário usando um cursor já aberto
    (permite compor com outras escritas na mesma transação)."""
    cursor.execute('''
        SELECT quantity, average_price FROM portfolio
        WHERE user_id = ? AND ticker = ?
    ''', (user_id, ticker))
    existing = cursor.fetchone()

    if existing:
        old_qty, old_price = existing
        new_qty = old_qty + quantity

        if new_qty <= 1e-9:
            cursor.execute(
                'DELETE FROM portfolio WHERE user_id = ? AND ticker = ?',
                (user_id, ticker)
            )
        else:
            # PM só muda em compras (quantity > 0); vendas apenas reduzem a quantidade
            if quantity > 0:
                new_avg_price = ((old_qty * old_price) + (quantity * average_price)) / new_qty
            else:
                new_avg_price = old_price
            total_value = new_qty * new_avg_price
            cursor.execute('''
                UPDATE portfolio
                SET quantity = ?, average_price = ?, total_value = ?
                WHERE user_id = ? AND ticker = ?
            ''', (new_qty, new_avg_price, total_value, user_id, ticker))
    else:
        if quantity > 0:
            total_value = quantity * average_price
            cursor.execute('''
                INSERT INTO portfolio
                (user_id, ticker, quantity, average_price, total_value)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, ticker, quantity, average_price, total_value))
        else:
            logger.warning(
                f'Ignorada tentativa de reduzir posição inexistente: user={user_id} ticker={ticker} qty={quantity}'
            )


def update_portfolio(user_id, ticker, quantity, average_price):
    """Atualiza a carteira do usuário (delta de quantity + preço de referência)."""
    with _write_lock:
        with _connection() as conn:
            cursor = conn.cursor()
            _upsert_portfolio(cursor, user_id, ticker, quantity, average_price)


def add_transaction_and_update_portfolio(user_id, ticker, trans_type, quantity, price,
                                          portfolio_qty_delta, portfolio_ref_price,
                                          profit_loss=None, date=None):
    """Registra a transação e atualiza a posição na carteira em uma única transação
    atômica (mesma conexão): se qualquer etapa falhar, nada é gravado — evita que o
    livro de transações e a posição na carteira fiquem divergentes."""
    with _write_lock:
        with _connection() as conn:
            cursor = conn.cursor()
            total_value = abs(quantity) * price
            transaction_date = date if date else datetime.now().isoformat()
            cursor.execute('''
                INSERT INTO transactions
                (user_id, ticker, type, quantity, price, total_value, profit_loss, date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, ticker, trans_type, quantity, price, total_value, profit_loss, transaction_date))
            _upsert_portfolio(cursor, user_id, ticker, portfolio_qty_delta, portfolio_ref_price)


def get_portfolio(user_id):
    """Retorna a carteira do usuário"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # a.name estava faltando: get_portfolio_health (dashboard) e outras
        # telas que dependem de pos['name'] mostravam o nome em branco (o
        # frontend cobre parcialmente caindo para o ticker, mas o dado correto
        # nunca chegava).
        cursor.execute('''
            SELECT p.*, a.name, a.current_price, a.variation_percent
            FROM portfolio p
            LEFT JOIN assets a ON p.ticker = a.ticker
            WHERE p.user_id = ? AND p.quantity > 0
            ORDER BY p.ticker
        ''', (user_id,))
        return [dict(p) for p in cursor.fetchall()]


def get_portfolio_summary(user_id):
    """Retorna resumo da carteira"""
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT
                SUM(total_value) as total_invested,
                COUNT(DISTINCT ticker) as total_assets
            FROM portfolio
            WHERE user_id = ? AND quantity > 0
        ''', (user_id,))
        result = cursor.fetchone()
        return {
            'total_invested': result[0] or 0,
            'total_assets': result[1] or 0
        }


def update_transaction(tid, user_id, ticker, trans_type, quantity, price, date=None):
    with _connection() as conn:
        cursor = conn.cursor()
        total_value = abs(quantity) * price
        transaction_date = date if date else datetime.now().isoformat()
        cursor.execute('''
            UPDATE transactions
            SET ticker=?, type=?, quantity=?, price=?, total_value=?, date=?
            WHERE id=? AND user_id=?
        ''', (ticker, trans_type, quantity, price, total_value, transaction_date, tid, user_id))


def delete_transaction(tid, user_id):
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM transactions WHERE id=? AND user_id=?', (tid, user_id))


def set_portfolio_position(user_id, ticker, quantity, average_price):
    with _write_lock:
        with _connection() as conn:
            cursor = conn.cursor()
            if quantity <= 0:
                cursor.execute('DELETE FROM portfolio WHERE user_id=? AND ticker=?', (user_id, ticker))
            else:
                total_value = quantity * average_price
                cursor.execute('SELECT id FROM portfolio WHERE user_id=? AND ticker=?', (user_id, ticker))
                row = cursor.fetchone()
                if row:
                    cursor.execute('''
                        UPDATE portfolio SET quantity=?, average_price=?, total_value=?
                        WHERE user_id=? AND ticker=?
                    ''', (quantity, average_price, total_value, user_id, ticker))
                else:
                    cursor.execute('''
                        INSERT INTO portfolio (user_id, ticker, quantity, average_price, total_value)
                        VALUES (?,?,?,?,?)
                    ''', (user_id, ticker, quantity, average_price, total_value))


def delete_portfolio_position(user_id, ticker):
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM portfolio WHERE user_id=? AND ticker=?', (user_id, ticker))


def save_recommendation(ticker, recommendation, confidence_score, reason):
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO recommendations
            (ticker, recommendation, confidence_score, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (ticker, recommendation, confidence_score, reason, datetime.now().isoformat()))


def get_recommendations():
    """Retorna todas as recomendações"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recommendations ORDER BY confidence_score DESC')
        return [dict(r) for r in cursor.fetchall()]


def get_recommendation(ticker):
    """Retorna recomendação de um ativo"""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recommendations WHERE ticker = ?', (ticker,))
        rec = cursor.fetchone()
        return dict(rec) if rec else None


def save_price_history(ticker, date, open_price, high, low, close, volume):
    """Salva histórico de preços"""
    with _connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO price_history
            (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, date, open_price, high, low, close, volume))


def get_price_history(ticker, days=30):
    """Retorna histórico de preços (as `days` linhas mais recentes)."""
    with _connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM price_history
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT ?
        ''', (ticker, days))
        return [dict(h) for h in cursor.fetchall()]
