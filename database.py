import sqlite3
import os
from datetime import datetime

DB_PATH = 'data/regulador.db'

def init_db():
    """Inicializa o banco de dados com as tabelas necessárias"""
    os.makedirs('data', exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
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
            FOREIGN KEY (user_id) REFERENCES users(id)
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
    
    conn.commit()
    conn.close()

def get_user_or_create(username='default_user'):
    """Obtém ou cria um usuário padrão"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute('INSERT INTO users (username, email) VALUES (?, ?)', 
                      (username, f'{username}@regulador.local'))
        conn.commit()
        user_id = cursor.lastrowid
    else:
        user_id = user[0]
    
    conn.close()
    return user_id

def update_asset(ticker, name, asset_type, current_price, variation_percent, 
                 variation_value, market_cap, volume):
    """Atualiza ou insere um ativo no banco"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO assets 
        (ticker, name, type, current_price, variation_percent, variation_value, 
         last_update, market_cap, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (ticker, name, asset_type, current_price, variation_percent, 
          variation_value, datetime.now(), market_cap, volume))
    
    conn.commit()
    conn.close()

def get_all_assets():
    """Retorna todos os ativos"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM assets ORDER BY ticker')
    assets = cursor.fetchall()
    
    conn.close()
    return [dict(asset) for asset in assets]

def get_asset(ticker):
    """Retorna um ativo específico"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM assets WHERE ticker = ?', (ticker,))
    asset = cursor.fetchone()
    
    conn.close()
    return dict(asset) if asset else None

def search_assets(query):
    """Busca ativos por ticker ou nome"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = f'%{query}%'
    cursor.execute('''
        SELECT * FROM assets 
        WHERE ticker LIKE ? OR name LIKE ?
        ORDER BY ticker
    ''', (query, query))
    
    assets = cursor.fetchall()
    conn.close()
    return [dict(asset) for asset in assets]

def add_transaction(user_id, ticker, trans_type, quantity, price, profit_loss=None, date=None):
    """Adiciona uma transação"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    total_value = abs(quantity) * price
    transaction_date = date if date else datetime.now().isoformat()
    
    cursor.execute('''
        INSERT INTO transactions 
        (user_id, ticker, type, quantity, price, total_value, profit_loss, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, ticker, trans_type, quantity, price, total_value, profit_loss, transaction_date))
    
    conn.commit()
    conn.close()

def get_transactions(user_id):
    """Retorna todas as transações de um usuário"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM transactions 
        WHERE user_id = ?
        ORDER BY date DESC
    ''', (user_id,))
    
    transactions = cursor.fetchall()
    conn.close()
    return [dict(t) for t in transactions]

def update_portfolio(user_id, ticker, quantity, average_price):
    """Atualiza a carteira do usuário"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT quantity, average_price FROM portfolio 
        WHERE user_id = ? AND ticker = ?
    ''', (user_id, ticker))
    
    existing = cursor.fetchone()
    
    if existing:
        old_qty, old_price = existing
        new_qty = old_qty + quantity
        
        if new_qty > 0:
            new_avg_price = ((old_qty * old_price) + (quantity * average_price)) / new_qty
            total_value = new_qty * new_avg_price
            
            cursor.execute('''
                UPDATE portfolio 
                SET quantity = ?, average_price = ?, total_value = ?
                WHERE user_id = ? AND ticker = ?
            ''', (new_qty, new_avg_price, total_value, user_id, ticker))
        else:
            cursor.execute('''
                DELETE FROM portfolio 
                WHERE user_id = ? AND ticker = ?
            ''', (user_id, ticker))
    else:
        if quantity > 0:
            total_value = quantity * average_price
            cursor.execute('''
                INSERT INTO portfolio 
                (user_id, ticker, quantity, average_price, total_value)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, ticker, quantity, average_price, total_value))
    
    conn.commit()
    conn.close()

def get_portfolio(user_id):
    """Retorna a carteira do usuário"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT p.*, a.current_price, a.variation_percent
        FROM portfolio p
        LEFT JOIN assets a ON p.ticker = a.ticker
        WHERE p.user_id = ? AND p.quantity > 0
        ORDER BY p.ticker
    ''', (user_id,))
    
    portfolio = cursor.fetchall()
    conn.close()
    return [dict(p) for p in portfolio]

def get_portfolio_summary(user_id):
    """Retorna resumo da carteira"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            SUM(total_value) as total_invested,
            COUNT(DISTINCT ticker) as total_assets
        FROM portfolio 
        WHERE user_id = ? AND quantity > 0
    ''', (user_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return {
        'total_invested': result[0] or 0,
        'total_assets': result[1] or 0
    }

def update_transaction(tid, user_id, ticker, trans_type, quantity, price, date=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total_value = abs(quantity) * price
    transaction_date = date if date else datetime.now().isoformat()
    cursor.execute('''
        UPDATE transactions
        SET ticker=?, type=?, quantity=?, price=?, total_value=?, date=?
        WHERE id=? AND user_id=?
    ''', (ticker, trans_type, quantity, price, total_value, transaction_date, tid, user_id))
    conn.commit()
    conn.close()

def delete_transaction(tid, user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM transactions WHERE id=? AND user_id=?', (tid, user_id))
    conn.commit()
    conn.close()

def set_portfolio_position(user_id, ticker, quantity, average_price):
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()

def delete_portfolio_position(user_id, ticker):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM portfolio WHERE user_id=? AND ticker=?', (user_id, ticker))
    conn.commit()
    conn.close()


    """Salva uma recomendação"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO recommendations 
        (ticker, recommendation, confidence_score, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (ticker, recommendation, confidence_score, reason, datetime.now()))
    
    conn.commit()
    conn.close()

def save_recommendation(ticker, recommendation, confidence_score, reason):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO recommendations 
        (ticker, recommendation, confidence_score, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (ticker, recommendation, confidence_score, reason, datetime.now()))
    conn.commit()
    conn.close()

def get_recommendations():
    """Retorna todas as recomendações"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM recommendations 
        ORDER BY confidence_score DESC
    ''')
    
    recommendations = cursor.fetchall()
    conn.close()
    return [dict(r) for r in recommendations]

def get_recommendation(ticker):
    """Retorna recomendação de um ativo"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM recommendations WHERE ticker = ?', (ticker,))
    rec = cursor.fetchone()
    
    conn.close()
    return dict(rec) if rec else None

def save_price_history(ticker, date, open_price, high, low, close, volume):
    """Salva histórico de preços"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO price_history 
        (ticker, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (ticker, date, open_price, high, low, close, volume))
    
    conn.commit()
    conn.close()

def get_price_history(ticker, days=30):
    """Retorna histórico de preços"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM price_history 
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT ?
    ''', (ticker, days))
    
    history = cursor.fetchall()
    conn.close()
    return [dict(h) for h in history]
