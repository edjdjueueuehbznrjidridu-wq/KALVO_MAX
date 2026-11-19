import unittest
from unittest.mock import patch
from apex import learning_memory


class LearningMemoryV2Tests(unittest.TestCase):
    def test_bayesian_prior_prevents_tiny_sample_overconfidence(self):
        rows = [{'pnl': 5}, {'pnl': 5}]
        result = learning_memory._posterior_summary(rows)
        self.assertEqual(result['n'], 2)
        self.assertGreater(result['posterior_win_rate'], 50)
        self.assertLess(result['posterior_win_rate'], 70)
        self.assertEqual(result['reliability'], 'low')

    def test_loss_sample_does_not_claim_high_win_rate(self):
        result = learning_memory._posterior_summary([{'pnl': -1} for _ in range(20)])
        self.assertLess(result['posterior_win_rate'], 40)
        self.assertEqual(result['reliability'], 'moderate')

    def test_context_separates_symbol_and_sides(self):
        trades = [
            {'symbol': 'BTC', 'side': 'LONG', 'pnl': 2, 'position_note': 'regime=trend'},
            {'symbol': 'BTC', 'side': 'SHORT', 'pnl': -1, 'position_note': 'regime=range'},
            {'symbol': 'ETH', 'side': 'LONG', 'pnl': 10, 'position_note': ''},
        ]
        with patch.object(learning_memory.storage, 'list_trades', return_value=trades):
            result = learning_memory.context_for_symbol('BTC')
        self.assertEqual(result['overall']['n'], 2)
        self.assertEqual(result['by_side']['LONG']['n'], 1)
        self.assertEqual(result['by_side']['SHORT']['n'], 1)
        self.assertEqual(set(result['by_regime']), {'trend', 'range'})

    def test_memory_never_claims_future_probability(self):
        with patch.object(learning_memory.storage, 'list_trades', return_value=[]):
            result = learning_memory.context_for_symbol('BTC')
        self.assertIn('not future win probabilities', result['sample_warning'])
        self.assertIn('never raises', result['risk_policy'])

    def test_wilson_interval_contains_observed_rate(self):
        low, high = learning_memory._wilson_interval(7, 10)
        self.assertLess(low, 70)
        self.assertGreater(high, 70)
