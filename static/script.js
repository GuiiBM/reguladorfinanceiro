// Funções utilitárias globais

function formatCurrency(value) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL'
    }).format(value);
}

function formatPercent(value) {
    const sign = value >= 0 ? '+' : '';
    return sign + value.toFixed(2) + '%';
}

function formatDate(dateString) {
    return new Date(dateString).toLocaleString('pt-BR');
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${type === 'success' ? '#d4edda' : type === 'error' ? '#f8d7da' : '#d1ecf1'};
        color: ${type === 'success' ? '#155724' : type === 'error' ? '#721c24' : '#0c5460'};
        border-radius: 5px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        z-index: 9999;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

async function apiCall(endpoint, method = 'GET', data = null) {
    try {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };
        
        if (data) {
            options.body = JSON.stringify(data);
        }
        
        const response = await fetch(endpoint, options);
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.message || 'Erro na requisição');
        }
        
        return result;
    } catch (error) {
        console.error('Erro na API:', error);
        throw error;
    }
}

// Validações
function validateTicker(ticker) {
    return ticker && ticker.length > 0 && ticker.length <= 10;
}

function validateQuantity(quantity) {
    return quantity && quantity > 0;
}

function validatePrice(price) {
    return price && price > 0;
}

// Formatação de dados
function formatAssetData(asset) {
    return {
        ...asset,
        current_price: asset.current_price ? asset.current_price.toFixed(2) : '0.00',
        variation_percent: asset.variation_percent ? asset.variation_percent.toFixed(2) : '0.00',
        variation_value: asset.variation_value ? asset.variation_value.toFixed(2) : '0.00'
    };
}

function formatPortfolioData(portfolio) {
    return portfolio.map(asset => ({
        ...asset,
        quantity: asset.quantity.toFixed(2),
        average_price: asset.average_price.toFixed(2),
        total_value: asset.total_value.toFixed(2)
    }));
}

// Cache simples
const cache = {
    data: {},
    set(key, value, ttl = 60000) {
        this.data[key] = {
            value,
            expires: Date.now() + ttl
        };
    },
    get(key) {
        const item = this.data[key];
        if (!item) return null;
        if (Date.now() > item.expires) {
            delete this.data[key];
            return null;
        }
        return item.value;
    },
    clear() {
        this.data = {};
    }
};

// Exportar dados
function exportToCSV(data, filename = 'export.csv') {
    if (!data || data.length === 0) {
        alert('Nenhum dado para exportar');
        return;
    }
    
    const headers = Object.keys(data[0]);
    const csv = [
        headers.join(','),
        ...data.map(row => 
            headers.map(header => {
                const value = row[header];
                return typeof value === 'string' && value.includes(',') 
                    ? `"${value}"` 
                    : value;
            }).join(',')
        )
    ].join('\n');
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    window.URL.revokeObjectURL(url);
}

// Gráfico simples em texto
function createSimpleChart(values, height = 10, width = 50) {
    if (!values || values.length === 0) return '';
    
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    
    let chart = '';
    for (let h = height; h > 0; h--) {
        let line = '';
        for (let i = 0; i < values.length; i++) {
            const normalized = (values[i] - min) / range;
            const barHeight = Math.round(normalized * height);
            line += barHeight >= h ? '█' : ' ';
        }
        chart += line + '\n';
    }
    
    return chart;
}

// Debounce para busca
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Throttle para scroll
function throttle(func, limit) {
    let inThrottle;
    return function(...args) {
        if (!inThrottle) {
            func.apply(this, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// Inicialização
document.addEventListener('DOMContentLoaded', () => {
    console.log('Regulador Financeiro carregado');
});
