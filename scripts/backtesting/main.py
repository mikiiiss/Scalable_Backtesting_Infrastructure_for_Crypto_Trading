# backtesting/main.py
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yfinance as yf
import backtrader as bt
from scripts.backtesting.analyzers.metrics_analyzer import MetricsAnalyzer

def run_backtest(config):
    initial_cash = config['initial_cash']
    start_date = config['start_date']
    end_date = config['end_date']
    ticker = config['ticker']
    indicator = config['indicator']

    # Fetch historical data
    try:
        data = yf.download(ticker, start=start_date, end=end_date)
        data_feed = bt.feeds.PandasData(dataname=data)
    except Exception as e:
        raise RuntimeError(f"Error fetching data: {e}")

    # Initialize Cerebro engine
    cerebro = bt.Cerebro()
    cerebro.adddata(data_feed)
    cerebro.broker.setcash(initial_cash)
    cerebro.addanalyzer(MetricsAnalyzer, _name='metrics')

    # Add strategy based on selected indicator
    if indicator == 'SMA':
        from scripts.backtesting.strategies.sma_strategy import SMAStrategy
        cerebro.addstrategy(SMAStrategy)
    elif indicator == 'LSTM':
        from strategies.lstm_strategy import LSTMStrategy
        cerebro.addstrategy(LSTMStrategy)
    elif indicator == 'MACD':
        from strategies.macd_strategy import MACDStrategy
        cerebro.addstrategy(MACDStrategy)
    elif indicator == 'RSI':
        from scripts.backtesting.strategies.rsi_strategy import RSIStrategy
        cerebro.addstrategy(RSIStrategy)
    elif indicator == 'Bollinger Bands':
        from strategies.bollinger_bands_strategy import BollingerBandsStrategy
        cerebro.addstrategy(BollingerBandsStrategy)

    # Run backtest
    results = cerebro.run()
    metrics = results[0].analyzers.metrics.get_analysis()
    return metrics