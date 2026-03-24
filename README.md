# Regulador Financeiro

Aplicação web de monitoramento de mercado financeiro em tempo real (atraso de 15 min), com compra/venda de ações e FIIs, carteira, recomendações e análise técnica.

---

## Comandos Rápidos

```bash
# 1. Instalação (apenas na primeira vez)
./install.sh

# 2. Iniciar a aplicação
./run.sh
```

Acesse em: **http://localhost:5000**

---

## Stack

- **Backend**: Python 3.8+, Flask, SQLite3, yfinance, pandas, APScheduler
- **Frontend**: HTML5, CSS3, JavaScript vanilla
- **Banco de dados**: SQLite (arquivo local em `data/regulador.db`)

---

## Estrutura

```
reguladorfinanceiro/
├── app.py                  # Aplicação Flask principal
├── database.py             # Gerenciamento do banco de dados
├── market_data.py          # Integração com yfinance
├── portfolio.py            # Lógica de carteira e transações
├── recommendations.py      # Engine de recomendações
├── config.py               # Configurações
├── test.py                 # Testes automatizados
├── install.sh              # Instalação rápida
├── run.sh                  # Início rápido
├── requirements.txt        # Dependências Python
├── exemplo_importacao.csv  # Exemplo de importação em lote
├── templates/              # Páginas HTML
│   ├── index.html          # Dashboard
│   ├── market.html         # Mercado
│   ├── portfolio.html      # Carteira
│   ├── transactions.html   # Transações
│   └── recommendations.html# Recomendações
├── static/
│   ├── style.css           # Estilização (tema B3)
│   └── script.js           # JavaScript
└── data/
    └── regulador.db        # Banco de dados (criado automaticamente)
```

---

## Funcionalidades

- **Dashboard**: resumo da carteira, ativos em destaque, recomendações e últimas transações
- **Mercado**: listagem de ações e FIIs com preço, variação (R$ e %), última atualização, busca e filtros
- **Carteira**: compra e venda de ativos, cálculo automático de preço médio, ganho/perda por ativo e total
- **Importação em lote**: upload de CSV com múltiplos ativos
- **Transações**: histórico completo com filtros por tipo e ticker
- **Recomendações**: COMPRA / VENDA / MANUTENÇÃO baseadas em variação, volume e RSI, com score de confiança
- **Atualização automática**: dados de mercado atualizados a cada 15 minutos via APScheduler

---

## API Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/market` | Lista todos os ativos |
| GET | `/api/market/<ticker>` | Detalhes de um ativo |
| GET | `/api/market/search?q=` | Busca por ticker ou nome |
| GET | `/api/analysis/<ticker>` | Análise técnica |
| GET | `/api/portfolio` | Carteira do usuário |
| POST | `/api/portfolio/buy` | Comprar ativo |
| POST | `/api/portfolio/sell` | Vender ativo |
| GET | `/api/portfolio/transactions` | Histórico de transações |
| POST | `/api/import/csv` | Importação em lote via CSV |
| GET | `/api/recommendations` | Lista recomendações |
| GET | `/api/recommendations/<ticker>` | Recomendação de um ativo |
| GET | `/api/health` | Health check |

---

## Banco de Dados

| Tabela | Descrição |
|--------|-----------|
| `users` | Usuários da aplicação |
| `assets` | Ativos monitorados com preço e variação |
| `portfolio` | Carteira do usuário (quantidade, preço médio) |
| `transactions` | Histórico de compras e vendas |
| `recommendations` | Recomendações geradas |
| `price_history` | Histórico de preços (OHLCV) |

---

## Ativos Monitorados (padrão)

**Ações**: PETR4.SA, VALE3.SA, ITUB4.SA, BBDC4.SA, ABEV3.SA, WEGE3.SA

**FIIs**: HGLG11.SA, KNRI11.SA, MXRF11.SA, XPLG11.SA

Para adicionar ou remover ativos, edite a lista `MONITORED_ASSETS` em `market_data.py`.

---

## Importação CSV

Formato esperado:

```csv
ticker,quantity,price,date
PETR4.SA,100,25.50,2024-01-15
VALE3.SA,50,80.00,2024-01-15
HGLG11.SA,10,150.00,2024-01-16
```

Veja o arquivo `exemplo_importacao.csv` para referência.

---

## Cálculos

**Preço médio**
```
preço_médio = (qtd_anterior × preço_anterior + qtd_nova × preço_novo) / (qtd_anterior + qtd_nova)
```

**Ganho/Perda**
```
ganho_perda = (preço_venda - preço_médio) × quantidade
percentual  = (ganho_perda / (preço_médio × quantidade)) × 100
```

**Score de recomendação**
```
score = (variação_7d × 0.4) + (volume_trend × 0.3) + (RSI × 0.3)
score > 5  → COMPRA
score < -5 → VENDA
demais     → MANUTENÇÃO
```

---

## Testes

```bash
source venv/bin/activate
python test.py
```

---

## Troubleshooting

| Erro | Solução |
|------|---------|
| `ModuleNotFoundError: flask` | Execute `./install.sh` |
| `Address already in use` | Altere a porta em `app.py` (padrão: 5000) |
| Banco de dados corrompido | `rm data/regulador.db && ./run.sh` |
| Preços não atualizam | Verifique a conexão com a internet |
| Aplicação lenta | Reduza ativos em `market_data.py` |

---

## Backup

```bash
cp data/regulador.db data/regulador.db.backup
```

---

## Roadmap

- [ ] Gráficos interativos (Chart.js)
- [ ] Alertas de preço
- [ ] Análise técnica avançada (RSI, MACD, Bollinger Bands)
- [ ] Autenticação de usuários (JWT)
- [ ] Exportação de relatórios (PDF)
- [ ] Integração com corretoras

---

**Versão**: 1.0.0 | **Licença**: MIT
