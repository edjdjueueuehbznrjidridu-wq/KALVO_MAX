import unittest
from apex.market_replay import CandleReplay

class ReplayTests(unittest.TestCase):
    def test_step_back_reset_and_finish(self):
        data = [{"close": i} for i in range(60)]
        replay = CandleReplay(data, visible=10)
        self.assertEqual(len(replay.current_window()), 10)
        replay.step(3)
        self.assertEqual(replay.index, 13)
        replay.back(2)
        self.assertEqual(replay.index, 11)
        replay.step(1000)
        self.assertTrue(replay.finished)
        self.assertEqual(replay.progress, 1.0)
        replay.reset()
        self.assertEqual(replay.index, 10)

    def test_empty_replay(self):
        replay = CandleReplay([])
        self.assertTrue(replay.finished)
        self.assertEqual(replay.progress, 0.0)

if __name__ == "__main__":
    unittest.main()
