# Configuração da Aplicação Regulador Financeiro

# ==================== SERVIDOR ====================
FLASK_HOST = '0.0.0.0'
FLASK_PORT = 5000
FLASK_DEBUG = True

# ==================== BANCO DE DADOS ====================
DATABASE_PATH = 'data/regulador.db'

# ==================== ATUALIZAÇÃO DE DADOS ====================
# Intervalo de atualização em minutos
UPDATE_INTERVAL_MINUTES = 15

# ==================== ATIVOS MONITORADOS ====================
# Adicione ou remova tickers conforme necessário
MONITORED_ASSETS = [
    'PETR4.SA',  # Petrobras
    'VALE3.SA',  # Vale
    'ITUB4.SA',  # Itaú
    'BBDC4.SA',  # Bradesco
    'ABEV3.SA',  # Ambev
    'WEGE3.SA',  # WEG
    'HGLG11.SA', # FII Híbrido
    'KNRI11.SA', # FII Logístico
    'MXRF11.SA', # FII Multisegmento
    'XPLG11.SA', # FII Logístico
]

# ==================== RECOMENDAÇÕES ====================
# Limites para score de recomendação
RECOMMENDATION_BUY_THRESHOLD = 5
RECOMMENDATION_SELL_THRESHOLD = -5

# Pesos para cálculo de score
WEIGHT_VARIATION = 0.4
WEIGHT_VOLUME = 0.3
WEIGHT_RSI = 0.3

# ==================== HISTÓRICO ====================
# Número de dias de histórico a manter
HISTORY_DAYS = 365

# ==================== LIMITES ====================
# Tamanho máximo de arquivo para upload
MAX_FILE_SIZE_MB = 16

# Número máximo de transações a retornar
MAX_TRANSACTIONS = 1000

# ==================== LOGGING ====================
LOG_LEVEL = 'INFO'
LOG_FILE = 'logs/regulador.log'

# ==================== SEGURANÇA ====================
# Nota: Adicionar autenticação em produção
ENABLE_AUTH = False
SECRET_KEY = 'sua-chave-secreta-aqui'

# ==================== CACHE ====================
# Tempo de cache em segundos
CACHE_TTL = 300

# ==================== YFINANCE ====================
# Timeout para requisições ao yfinance (segundos)
YFINANCE_TIMEOUT = 30

# Número de tentativas para buscar dados
YFINANCE_RETRIES = 3
