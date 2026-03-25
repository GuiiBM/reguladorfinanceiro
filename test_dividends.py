#!/usr/bin/env python3
"""
Script de teste para a aba de dividendos.
Popula o banco de dados com dados de exemplo para visualizar a aba.
"""

import sqlite3
from datetime import datetime, timedelta
import random

DB_PATH = 'data/regulador.db'

def populate_test_data():
    """Popula o banco com dados de teste para dividendos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Limpar dados antigos (opcional)
    # cursor.execute('DELETE FROM transactions')
    # cursor.execute('DELETE FROM portfolio')
    
    user_id = 1  # Usuário padrão
    
    # Dados de teste: ativos com dividendos
    test_assets = [
        ('PETR4.SA', 100, 25.50),
        ('VALE3.SA', 50, 80.00),
        ('HGLG11.SA', 10, 150.00),
        ('KNRI11.SA', 20, 120.00),
        ('MXRF11.SA', 15, 10.50),
        ('ITUB4.SA', 75, 30.00),
    ]
    
    # Adicionar à carteira
    for ticker, qty, price in test_assets:
        cursor.execute('''
            INSERT OR REPLACE INTO portfolio 
            (user_id, ticker, quantity, average_price, total_value)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, ticker, qty, price, qty * price))
    
    # Adicionar transações (compras) com datas variadas
    base_date = datetime.now() - timedelta(days=365)
    for ticker, qty, price in test_assets:
        for i in range(3):
            trans_date = base_date + timedelta(days=i * 120)
            cursor.execute('''
                INSERT INTO transactions 
                (user_id, ticker, type, quantity, price, total_value, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, ticker, 'compra', qty, price, qty * price, trans_date.isoformat()))
    
    conn.commit()
    conn.close()
    print("✅ Dados de teste adicionados com sucesso!")
    print("\nAtivos adicionados à carteira:")
    for ticker, qty, price in test_assets:
        print(f"  - {ticker}: {qty} ações @ R$ {price:.2f}")
    print("\nAgora acesse http://localhost:5000/dividends para visualizar a aba!")

if __name__ == '__main__':
    try:
        populate_test_data()
    except Exception as e:
        print(f"❌ Erro: {e}")
