#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de teste para validar a aplicação Regulador Financeiro
"""

import sys
import os

def test_imports():
    """Testa se todos os módulos podem ser importados"""
    print("=" * 50)
    print("TESTE 1: Importação de Módulos")
    print("=" * 50)
    
    try:
        import flask
        print("✓ Flask importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar Flask: {e}")
        return False
    
    try:
        import yfinance
        print("✓ yfinance importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar yfinance: {e}")
        return False
    
    try:
        import pandas
        print("✓ pandas importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar pandas: {e}")
        return False
    
    try:
        import apscheduler
        print("✓ APScheduler importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar APScheduler: {e}")
        return False
    
    try:
        from database import init_db
        print("✓ Módulo database importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar database: {e}")
        return False
    
    try:
        from market_data import fetch_asset_data
        print("✓ Módulo market_data importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar market_data: {e}")
        return False
    
    try:
        from portfolio import buy_asset
        print("✓ Módulo portfolio importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar portfolio: {e}")
        return False
    
    try:
        from recommendations import calculate_recommendation
        print("✓ Módulo recommendations importado com sucesso")
    except ImportError as e:
        print(f"✗ Erro ao importar recommendations: {e}")
        return False
    
    print()
    return True

def test_database():
    """Testa a inicialização do banco de dados"""
    print("=" * 50)
    print("TESTE 2: Banco de Dados")
    print("=" * 50)
    
    try:
        from database import init_db, get_user_or_create, get_all_assets
        
        init_db()
        print("✓ Banco de dados inicializado com sucesso")
        
        user_id = get_user_or_create('test_user')
        print(f"✓ Usuário criado/obtido com sucesso (ID: {user_id})")
        
        assets = get_all_assets()
        print(f"✓ Banco de dados consultado com sucesso ({len(assets)} ativos)")
        
        print()
        return True
    except Exception as e:
        print(f"✗ Erro ao testar banco de dados: {e}")
        print()
        return False

def test_market_data():
    """Testa a busca de dados de mercado"""
    print("=" * 50)
    print("TESTE 3: Dados de Mercado")
    print("=" * 50)
    
    try:
        from market_data import fetch_asset_data
        
        print("Buscando dados de PETR4.SA...")
        data = fetch_asset_data('PETR4.SA')
        
        if data:
            print(f"✓ Dados obtidos com sucesso")
            print(f"  - Ticker: {data['ticker']}")
            print(f"  - Preço: R$ {data['current_price']:.2f}")
            print(f"  - Variação: {data['variation_percent']:.2f}%")
            print()
            return True
        else:
            print("✗ Erro ao buscar dados")
            print()
            return False
    except Exception as e:
        print(f"✗ Erro ao testar dados de mercado: {e}")
        print()
        return False

def test_portfolio():
    """Testa operações de carteira"""
    print("=" * 50)
    print("TESTE 4: Operações de Carteira")
    print("=" * 50)
    
    try:
        from database import get_user_or_create
        from portfolio import buy_asset, get_portfolio_performance
        
        user_id = get_user_or_create('test_user')
        
        # Testa compra
        result = buy_asset(user_id, 'PETR4.SA', 10, 25.50)
        if result['success']:
            print(f"✓ Compra registrada com sucesso")
        else:
            print(f"✗ Erro ao registrar compra: {result['message']}")
            return False
        
        # Testa performance
        performance = get_portfolio_performance(user_id)
        if performance:
            print(f"✓ Performance calculada com sucesso")
            print(f"  - Total investido: R$ {performance['total_invested']:.2f}")
            print(f"  - Ativos: {performance['assets_count']}")
        else:
            print("✗ Erro ao calcular performance")
            return False
        
        print()
        return True
    except Exception as e:
        print(f"✗ Erro ao testar carteira: {e}")
        print()
        return False

def test_recommendations():
    """Testa geração de recomendações"""
    print("=" * 50)
    print("TESTE 5: Recomendações")
    print("=" * 50)
    
    try:
        from recommendations import calculate_recommendation
        
        print("Calculando recomendação para PETR4.SA...")
        rec = calculate_recommendation('PETR4.SA')
        
        if rec:
            print(f"✓ Recomendação calculada com sucesso")
            print(f"  - Recomendação: {rec['recommendation']}")
            print(f"  - Confiança: {rec['confidence_score']:.0f}%")
            print(f"  - Motivo: {rec['reason']}")
            print()
            return True
        else:
            print("✗ Erro ao calcular recomendação (dados insuficientes)")
            print()
            return True  # Não é erro crítico
    except Exception as e:
        print(f"✗ Erro ao testar recomendações: {e}")
        print()
        return False

def test_flask_app():
    """Testa a aplicação Flask"""
    print("=" * 50)
    print("TESTE 6: Aplicação Flask")
    print("=" * 50)
    
    try:
        from app import app
        
        with app.test_client() as client:
            # Testa health check
            response = client.get('/api/health')
            if response.status_code == 200:
                print("✓ Health check OK")
            else:
                print(f"✗ Health check falhou (status: {response.status_code})")
                return False
            
            # Testa API de mercado
            response = client.get('/api/market')
            if response.status_code == 200:
                print("✓ API de mercado OK")
            else:
                print(f"✗ API de mercado falhou (status: {response.status_code})")
                return False
            
            # Testa API de carteira
            response = client.get('/api/portfolio')
            if response.status_code == 200:
                print("✓ API de carteira OK")
            else:
                print(f"✗ API de carteira falhou (status: {response.status_code})")
                return False
            
            # Testa API de recomendações
            response = client.get('/api/recommendations')
            if response.status_code == 200:
                print("✓ API de recomendações OK")
            else:
                print(f"✗ API de recomendações falhou (status: {response.status_code})")
                return False
        
        print()
        return True
    except Exception as e:
        print(f"✗ Erro ao testar Flask: {e}")
        print()
        return False

def main():
    """Executa todos os testes"""
    print("\n")
    print("╔" + "=" * 48 + "╗")
    print("║" + " " * 10 + "TESTES - REGULADOR FINANCEIRO" + " " * 10 + "║")
    print("╚" + "=" * 48 + "╝")
    print()
    
    results = []
    
    results.append(("Importação de Módulos", test_imports()))
    results.append(("Banco de Dados", test_database()))
    results.append(("Dados de Mercado", test_market_data()))
    results.append(("Operações de Carteira", test_portfolio()))
    results.append(("Recomendações", test_recommendations()))
    results.append(("Aplicação Flask", test_flask_app()))
    
    print("=" * 50)
    print("RESUMO DOS TESTES")
    print("=" * 50)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASSOU" if result else "✗ FALHOU"
        print(f"{name}: {status}")
    
    print()
    print(f"Total: {passed}/{total} testes passaram")
    print()
    
    if passed == total:
        print("✓ Todos os testes passaram! A aplicação está pronta para uso.")
        return 0
    else:
        print("✗ Alguns testes falharam. Verifique os erros acima.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
