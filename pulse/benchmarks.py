"""Explicit benchmark choices. Synthetic series are illustrations, never ETF history."""
import re

PRESETS = {
    'IWDA.AS': 'MSCI World ETF',
    'VWCE.DE': 'Global All-Country ETF',
    'SXR8.DE': 'S&P 500 ETF',
}

def validate_ticker(value):
    ticker = str(value or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.\^=\-]{0,31}', ticker):
        raise ValueError('Enter a provider-compatible ticker, such as IWDA.AS or SXR8.DE (maximum 32 characters).')
    return ticker

def benchmark_name(ticker, synthetic=False):
    if ticker is None:
        return 'No comparison'
    name = PRESETS.get(ticker, f'Custom benchmark ({ticker})')
    return f'Synthetic {name} illustration' if synthetic else f'{name} ({ticker})'
