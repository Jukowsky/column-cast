"""Which columns get an EMA Lightning comparison copy.

    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ema_compare  # noqa: E402


def entry(aid, engine, stages, published='2026-10-01'):
    return {'id': aid, 'tts_engine': engine, 'published': published,
            'stages': dict.fromkeys(stages, '2026-10-01T06:00:00')}


class Waiting(unittest.TestCase):
    def ids(self, *entries):
        state = {'articles': {e['id']: e for e in entries}}
        return [e['id'] for e in ema_compare.waiting(state)]

    def test_only_elevenlabs_columns(self):
        self.assertEqual(self.ids(entry('1', 'elevenlabs', ['scraped', 'tts']),
                                  entry('2', 'chirp3', ['scraped', 'tts'])), ['1'])

    def test_skips_columns_already_copied(self):
        self.assertEqual(self.ids(entry('1', 'elevenlabs', ['scraped', 'tts', 'ema'])), [])

    def test_waits_for_the_narration(self):
        # Not voiced yet: the engine is decided at TTS time, so nothing to copy.
        self.assertEqual(self.ids(entry('1', None, ['scraped'])), [])

    def test_oldest_first(self):
        self.assertEqual(self.ids(entry('new', 'elevenlabs', ['tts'], '2026-10-20'),
                                  entry('old', 'elevenlabs', ['tts'], '2026-10-02')),
                         ['old', 'new'])


if __name__ == '__main__':
    unittest.main()
