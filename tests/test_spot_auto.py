import unittest
from unittest.mock import patch
from apex import spot_auto


class SpotAutoTests(unittest.TestCase):
    def test_stale_market_price_never_creates_spot_demo_order(self):
        with patch.object(spot_auto.market, 'get_ticker', return_value={
            'price': 100.0, 'ts': 1, 'base': 'BTC'
        }), patch.object(spot_auto.store, 'spot_market_order') as order:
            result = spot_auto.scan_once('BTC', now=1000)
        self.assertEqual(result['action'], 'HOLD')
        order.assert_not_called()

    def test_aligned_strong_long_frames_only_create_a_bounded_demo_order(self):
        stamp = 100_000
        strong = {'ready': True, 'signal': 'LONG_BIAS', 'confluence_score': 82,
                  'volatility_ratio': 1.2}
        bundle = {
            'trade_ready': True, 'provider_consistent': True, 'quality_score': 98,
            'price': 100.0, 'provider': 'binance',
            'quote': {'price': 100.0, 'bid': 99.99, 'ask': 100.01, 'source': 'binance',
                      'status': 'fresh', 'quote_valid': True, 'provider_ts': 1_800_000_000, 'spread_pct': 0.02},
            'timeframes': {'4hour': dict(strong), '1day': dict(strong)},
            'problems': [], 'generated_at': stamp,
        }
        with patch.object(spot_auto.data_hub, 'get_market_bundle', return_value=bundle), \
             patch.object(spot_auto.store, 'get_spot_balances', return_value=[
                 {'asset': 'USDT', 'free': 1000.0}, {'asset': 'BTC', 'free': 0.0}
             ]), patch.object(spot_auto.store, 'get_setting', side_effect=lambda k, d='': {
                 'ai_gate': 'autopilot', 'spot_auto_last_BTC': '0', 'news_trade_enabled': '0'
             }.get(k, d)), \
             patch.object(spot_auto, '_cloud_spot_gate', return_value={
                 'ok': True, 'verdict': 'approve', 'confidence': 90,
                 'size_mult': 1.0, 'side': 'LONG'
             }), \
             patch.object(spot_auto.store, 'spot_market_order', return_value={'quantity': 0.8}) as order, \
             patch.object(spot_auto.store, 'set_setting'):
            result = spot_auto.scan_once('BTC', now=stamp)
        self.assertEqual(result['action'], 'BUY')
        self.assertEqual(order.call_args.args[1], 'BUY')
        self.assertEqual(order.call_args.args[2], 80.0)  # 8% of virtual USDT balance

    def test_weak_signal_never_triggers_auto_spot_order(self):
        stamp = 100_000
        weak = {'ready': True, 'signal': 'LONG_BIAS', 'confluence_score': 45,
                'volatility_ratio': 1.1}
        bundle = {
            'trade_ready': True, 'provider_consistent': True, 'quality_score': 98,
            'price': 100.0, 'provider': 'binance',
            'quote': {'price': 100.0, 'bid': 99.99, 'ask': 100.01, 'source': 'binance',
                      'status': 'fresh', 'quote_valid': True, 'provider_ts': 1_800_000_000, 'spread_pct': 0.02},
            'timeframes': {'4hour': dict(weak), '1day': dict(weak)},
            'problems': [], 'generated_at': stamp,
        }
        with patch.object(spot_auto.data_hub, 'get_market_bundle', return_value=bundle), \
             patch.object(spot_auto.store, 'get_spot_balances', return_value=[
                 {'asset': 'USDT', 'free': 1000.0}, {'asset': 'BTC', 'free': 0.0}
             ]), patch.object(spot_auto.store, 'get_setting', return_value='0'), \
             patch.object(spot_auto.store, 'spot_market_order') as order:
            result = spot_auto.scan_once('BTC', now=stamp)
        self.assertEqual(result['action'], 'HOLD')
        order.assert_not_called()


class SpotAutoAIGateTests(unittest.TestCase):
    def test_ai_unavailable_never_creates_spot_order(self):
        stamp = 200_000
        strong = {'ready': True, 'signal': 'LONG_BIAS', 'confluence_score': 84,
                  'volatility_ratio': 1.0, 'rsi14': 55}
        bundle = {
            'trade_ready': True, 'provider_consistent': True, 'quality_score': 98,
            'price': 100.0, 'provider': 'binance',
            'quote': {'price': 100.0, 'bid': 99.99, 'ask': 100.01, 'source': 'binance',
                      'status': 'fresh', 'quote_valid': True, 'provider_ts': 1_800_000_000, 'spread_pct': 0.02},
            'timeframes': {'4hour': dict(strong), '1day': dict(strong)},
            'problems': [], 'generated_at': stamp,
        }
        with patch.object(spot_auto.data_hub, 'get_market_bundle', return_value=bundle), \
             patch.object(spot_auto.store, 'get_spot_balances', return_value=[
                 {'asset': 'USDT', 'free': 1000.0}, {'asset': 'BTC', 'free': 0.0}
             ]), patch.object(spot_auto.store, 'get_setting', side_effect=lambda k, d='': {
                 'ai_gate': 'autopilot', 'spot_auto_last_BTC': '0', 'news_trade_enabled': '0'
             }.get(k, d)), \
             patch.object(spot_auto, '_cloud_spot_gate', return_value={
                 'ok': False, 'reason': 'AI unavailable'
             }), \
             patch.object(spot_auto.store, 'spot_market_order') as order:
            result = spot_auto.scan_once('BTC', now=stamp)
        self.assertEqual(result['action'], 'HOLD')
        self.assertIn('AI unavailable', result['reason'])
        order.assert_not_called()

    def test_reduced_ai_size_scales_virtual_spot_buy(self):
        stamp = 300_000
        strong = {'ready': True, 'signal': 'LONG_BIAS', 'confluence_score': 85,
                  'volatility_ratio': 1.0, 'rsi14': 54}
        bundle = {
            'trade_ready': True, 'provider_consistent': True, 'quality_score': 98,
            'price': 100.0, 'provider': 'binance',
            'quote': {'price': 100.0, 'bid': 99.99, 'ask': 100.01, 'source': 'binance',
                      'status': 'fresh', 'quote_valid': True, 'provider_ts': 1_800_000_000, 'spread_pct': 0.02},
            'timeframes': {'4hour': dict(strong), '1day': dict(strong)},
            'problems': [], 'generated_at': stamp,
        }
        with patch.object(spot_auto.data_hub, 'get_market_bundle', return_value=bundle), \
             patch.object(spot_auto.store, 'get_spot_balances', return_value=[
                 {'asset': 'USDT', 'free': 1000.0}, {'asset': 'BTC', 'free': 0.0}
             ]), patch.object(spot_auto.store, 'get_setting', side_effect=lambda k, d='': {
                 'ai_gate': 'autopilot', 'spot_auto_last_BTC': '0', 'news_trade_enabled': '0'
             }.get(k, d)), \
             patch.object(spot_auto, '_cloud_spot_gate', return_value={
                 'ok': True, 'verdict': 'reduce', 'confidence': 88, 'size_mult': 0.5, 'side': 'LONG'
             }), \
             patch.object(spot_auto.store, 'spot_market_order', return_value={'quantity': 0.4}) as order, \
             patch.object(spot_auto.store, 'set_setting'):
            result = spot_auto.scan_once('BTC', now=stamp)
        self.assertEqual(result['action'], 'BUY')
        self.assertAlmostEqual(order.call_args.args[2], 40.0)
