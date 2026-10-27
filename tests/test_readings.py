"""Spoken-form tests for the shared Turkish rules and the ElevenLabs layer.

    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eleven_prep                                   # noqa: E402
import tr_readings                                   # noqa: E402
from text_normalize import normalize_for_tts as say  # noqa: E402


class SharedRules(unittest.TestCase):
    def check(self, cases):
        for source, spoken in cases:
            with self.subTest(source=source):
                self.assertEqual(say(source), spoken)

    def test_existing_behaviour_kept(self):
        self.check([
            ("Cumhuriyet’in 1920’de kurulan TKP’nin %40’ı.",
             "Cumhuriyetin bin dokuz yüz yirmide kurulan Te Ka Penin yüzde kırkı."),
            ("XX. yüzyıl, 3. kez, Dr. Ahmet vb. kişiler.",
             "Yirminci yüzyıl, üçüncü kez, Doktor Ahmet ve benzeri kişiler."),
            ("2,5’i", "iki virgül beşi"),
        ])

    def test_space_before_punctuation_keeps_the_mark(self):
        # Regression: a literal \x01 replaced the mark instead of keeping it.
        out = say("Bu iş , böyle .")
        self.assertEqual(out, "Bu iş, böyle.")
        self.assertNotIn("\x01", out)

    def test_dash_leaves_no_orphan_comma(self):
        self.check([
            ("“Halk” dedi — ama gitti.", '"Halk" dedi, ama gitti.'),
            ("— Ama neden?", "Ama neden?"),
            ("Bitti —.", "Bitti."),
        ])

    def test_times(self):
        self.check([
            ("Saat 14.30’da başladı.", "Saat on dört otuzda başladı."),
            ("Toplantı 09.00’da.", "Toplantı dokuzda."),
            ("9:05’te çıktı.", "dokuz sıfır beşte çıktı."),
        ])

    def test_time_does_not_eat_thousands(self):
        self.check([("12.000 kişi", "on iki bin kişi")])

    def test_ranges(self):
        self.check([
            ("2019-2023 arasında", "2019 ila 2023 arasında"),
            ("10–15 Ekim", "10 ila 15 Ekim"),
        ])

    def test_not_ranges(self):
        self.check([
            ("Maç 3-0 bitti.", "Maç 3-0 bitti."),
            ("COVID-19 salgını", "COVID-19 salgını"),
            ("0212-555", "0212-555"),
        ])

    def test_grouped_and_decimal_numbers(self):
        self.check([
            ("1.250.000 kişi", "bir milyon iki yüz elli bin kişi"),
            ("2,5 milyar", "iki buçuk milyar"),
            ("0,5 puan", "sıfır virgül beş puan"),
            ("3,75 oranında", "üç virgül yetmiş beş oranında"),
        ])

    def test_money(self):
        self.check([
            ("1.250.000 TL ödendi.", "bir milyon iki yüz elli bin lira ödendi."),
            ("2,5 milyar $ borç", "iki buçuk milyar dolar borç"),
            ("$5 ve €300", "5 dolar ve 300 avro"),
            ("10 milyon € harcandı", "10 milyon avro harcandı"),
            ("500 TL’lik zam", "500 liralık zam"),
            ("1.000 TL’ye varan", "bin liraya varan"),
            ("milyon TL’nin", "milyon liranın"),
            ("TL’nin değeri", "Te Lenin değeri"),
        ])

    def test_fractions(self):
        self.check([
            ("3/4 oranı", "dörtte üç oranı"),
            ("1/2 asırlık", "yarım asırlık"),
            ("1/2’si", "yarısı"),
            ("3/4’ü", "dörtte üçü"),
            ("2/5 kadarı", "beşte iki kadarı"),
            ("2/5’i", "beşte ikisi"),
            ("2/5’e", "beşte ikiye"),
            ("24/7 açık", "24/7 açık"),
            ("15/7 gecesi", "15/7 gecesi"),
        ])

    def test_reference_marks(self):
        self.check([
            ("Bkz. s. 45", "Bakınız sayfa 45"),
            ("M.Ö. 500’de", "Milattan önce beş yüzde"),
            ("No. 5", "numara 5"),
            ("Örn. bu.", "Örneğin bu."),
        ])

    def test_links_and_handles(self):
        self.check([
            ("https://haber.sol.org.tr/yazarlar/x adresinde.",
             "haber nokta sol nokta org nokta tr adresinde."),
            ("Bkz. www.sol.org.tr.", "Bakınız sol nokta org nokta tr."),
            ("@solhaber ve #grev", "solhaber ve grev"),
            ("Ali & Veli", "Ali ve Veli"),
        ])

    def test_locative(self):
        for word, expected in [("dört", "dörtte"), ("altı", "altıda"),
                               ("on", "onda"), ("üç", "üçte"), ("yüz", "yüzde")]:
            self.assertEqual(tr_readings.locative(word), expected)

    def test_engine_table_override(self):
        table = {**tr_readings.SPELLED, 'TKP': None, 'CHP': 'Cehape'}
        self.assertEqual(say("TKP ve CHP", spelled=table), "TKP ve Cehape")


class ElevenLabsLayer(unittest.TestCase):
    RAW = ("Bir başlık\n\nYazar imzalı köşe yazısı.\n\nSpot cümlesi\n\n"
           "Birinci paragraf burada.\n\nİkinci paragraf, devam ediyor\n\n"
           "Son paragraf.")

    def test_lines_are_closed(self):
        prepared = eleven_prep.prepare(self.RAW)
        for paragraph in prepared.split('\n\n'):
            self.assertIn(paragraph.rstrip('"\')»”’]')[-1], '.!?…:;')

    def test_close_line(self):
        self.assertEqual(eleven_prep.close_line('Başlık'), 'Başlık.')
        self.assertEqual(eleven_prep.close_line('"Alıntı"'), '"Alıntı".')
        self.assertEqual(eleven_prep.close_line('devam,'), 'devam.')
        self.assertEqual(eleven_prep.close_line('Şöyle:'), 'Şöyle:')

    def test_breaks_between_paragraphs(self):
        chunks = eleven_prep.chunk(eleven_prep.prepare(self.RAW))
        self.assertEqual(len(chunks), 1)
        text = chunks[0]
        self.assertIn('Bir başlık.\n<break time="1.0s" />\nYazar', text)
        self.assertIn('<break time="0.5s" />', text)
        self.assertLessEqual(text.count('<break'), eleven_prep.MAX_BREAKS_PER_CHUNK)

    def test_no_breaks_for_v3(self):
        prepared = eleven_prep.prepare(self.RAW)
        self.assertFalse(eleven_prep.supports_breaks('eleven_v3'))
        self.assertNotIn('<break', ''.join(eleven_prep.chunk(prepared, breaks=False)))

    def test_passed_through_initialisms(self):
        out = eleven_prep.prepare("TKP’nin, TBMM’de ve KKTC ile AKP’nin")
        self.assertEqual(out, "TKP'nin, TBMM'de ve KKTC ile A Ke Penin.")
        # Chirp keeps its spelled forms.
        self.assertIn("Te Ka Penin", say("TKP’nin"))

    def test_angle_brackets_are_not_markup(self):
        self.assertNotIn('<', eleven_prep.prepare("a <b> c"))

    def test_chunks_respect_limit_and_end_on_sentences(self):
        paragraph = ' '.join(f"Bu {i}. cümledir ve biraz uzundur." for i in range(1, 120))
        raw = '\n\n'.join([paragraph] * 3)
        chunks = eleven_prep.chunk(eleven_prep.prepare(raw), limit=800)
        self.assertGreater(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 800)
            self.assertIn(c.rstrip()[-1], '.!?…')

    def test_context_is_plain_text(self):
        chunks = ['Bir.\n<break time="0.5s" />\nİki üç dört.', 'Beş altı.', 'Yedi sekiz dokuz.']
        before, after = eleven_prep.context(chunks, 1)
        self.assertNotIn('<', before)
        self.assertTrue(before.endswith('dört.'))
        self.assertTrue(after.startswith('Yedi'))
        self.assertEqual(eleven_prep.context(chunks, 0)[0], '')

    def test_billed_chars_counts_what_is_sent(self):
        prepared = eleven_prep.prepare(self.RAW)
        sent = eleven_prep.chunk(prepared)
        self.assertEqual(eleven_prep.billed_chars(prepared, 'eleven_multilingual_v2'),
                         sum(map(len, sent)))


if __name__ == '__main__':
    unittest.main()
