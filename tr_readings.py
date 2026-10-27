"""
Turkish reading rules for things a TTS engine gets wrong in plain text.

Chirp 3 accepts no SSML, so every correction has to be made by rewriting the
text into what should actually be said. Each rule here was added because the
pattern was measured in the corpus, not because it might occur.
"""

import re

# ---------------------------------------------------------------- initialisms

# Turkish letter names, for spelling an initialism out loud.
LETTER_NAMES = {
    'A': 'a', 'B': 'be', 'C': 'ce', 'Ç': 'çe', 'D': 'de', 'E': 'e', 'F': 'fe',
    'G': 'ge', 'Ğ': 'yumuşak ge', 'H': 'he', 'I': 'ı', 'İ': 'i', 'J': 'je',
    'K': 'ke', 'L': 'le', 'M': 'me', 'N': 'ne', 'O': 'o', 'Ö': 'ö', 'P': 'pe',
    'Q': 'ku', 'R': 're', 'S': 'se', 'Ş': 'şe', 'T': 'te', 'U': 'u', 'Ü': 'ü',
    'V': 've', 'W': 'çift ve', 'X': 'iks', 'Y': 'ye', 'Z': 'ze',
}

# Spelled out letter by letter. Values are explicit rather than generated so an
# established pronunciation can override the letter names: TKP is set by ear,
# not by the "Te Ke Pe" the table would produce.
SPELLED = {
    'TKP': 'Te Ka Pe',
    'AKP': 'A Ke Pe',
    'ABD': 'A Be De',
    'CHP': 'Ce He Pe',
    'MHP': 'Me He Pe',
    'DEM': 'Dem',
    'HDP': 'He De Pe',
    'TİP': 'Tip',
    'TBMM': 'Te Be Me Me',
    'AB': 'A Be',
    'BM': 'Be Me',
    'HTŞ': 'He Te Şe',
    'LGS': 'Le Ge Se',
    'SSS': 'Se Se Se',
    'AEP': 'A E Pe',
    'YÖK': 'Yök',
    'MEB': 'Me E Be',
    'TÜİK': 'Tüik',
    'SGK': 'Se Ge Ka',
    'KHK': 'Ka He Ka',
    'OHAL': 'Ohal',
    'IMF': 'İ Me Fe',
    'PKK': 'Pe Ke Ke',
    'MİT': 'Mit',
    'CIA': 'Si Ay Ey',
    'ILO': 'İ Le O',
    'FBI': 'Ef Bi Ay',
    'BBC': 'Bi Bi Si',
    # Surfaced by the unknown-initialism report on 2026-09-01.
    'AİHM': 'A İ He Me',
    'AİHS': 'A İ He Se',
    'İHAM': 'İ He A Me',
    'AKPM': 'A Ke Pe Me',
    'ASM': 'A Se Me',
    'YKS': 'Ye Ke Se',
    'AP': 'A Pe',
    'TV': 'Te Ve',
    'KKTC': 'Ke Ke Te Ce',
    # Only when it is not next to an amount; there expand_money says "lira".
    'TL': 'Te Le',
}

# Read as ordinary words; must NOT be spelled out.
AS_WORD = {
    'NATO', 'DİSK', 'MESEM', 'KİT', 'IBAN', 'TÜRK', 'ODTÜ', 'TRT', 'ASELSAN',
    'TOKİ', 'SEKA', 'TÜPRAŞ', 'THY', 'KESK', 'DEV',
    # Ordinary Turkish words that happen to be written in capitals.
    'NOT', 'VAR', 'YOK', 'BİR', 'SON', 'TEK', 'ÇOK', 'AMA',
    'CİMER', 'CIMER',
}

_INITIALISM = re.compile(r'\b[A-ZÇĞİÖŞÜ]{2,6}\b')


def expand_initialisms(text, unknown=None, spelled=None):
    """Rewrite known initialisms phonetically; leave unknown ones untouched.

    Leaving the unknown alone is deliberate. Auto-spelling every capitalised
    run would mangle acronyms that are read as words, and there is no reliable
    way to tell the two apart from the letters alone - so new ones get added to
    the table by hand, and `unknown` collects the candidates.

    `spelled` replaces SPELLED for one engine. A value of None in it means
    "this engine reads the letters itself": the token is passed through.
    """
    table = SPELLED if spelled is None else spelled

    def replace(match):
        token = match.group(0)
        if token in AS_WORD:
            return token
        if token in table:
            return table[token] if table[token] is not None else token
        if unknown is not None and len(token) >= 2:
            unknown.add(token)
        return token

    return _INITIALISM.sub(replace, text)


# ------------------------------------------------------------------- numerals

ONES = ['', 'bir', 'iki', 'üç', 'dört', 'beş', 'altı', 'yedi', 'sekiz', 'dokuz']
TENS = ['', 'on', 'yirmi', 'otuz', 'kırk', 'elli', 'altmış', 'yetmiş', 'seksen', 'doksan']
SCALES = [(10 ** 9, 'milyar'), (10 ** 6, 'milyon'), (1000, 'bin')]


def number_to_words(n):
    """1920 -> 'bin dokuz yüz yirmi'. Turkish drops the leading 'bir'."""
    if n == 0:
        return 'sıfır'
    parts = []
    for value, name in SCALES:
        if n >= value:
            count = n // value
            n %= value
            # "bin" not "bir bin"; "iki bin" keeps its count.
            prefix = '' if (count == 1 and name == 'bin') else number_to_words(count)
            parts.append((prefix + ' ' + name).strip())
    if n >= 100:
        hundreds = n // 100
        n %= 100
        parts.append(('' if hundreds == 1 else ONES[hundreds] + ' ') + 'yüz')
    if n >= 10:
        parts.append(TENS[n // 10])
        n %= 10
    if n:
        parts.append(ONES[n])
    return ' '.join(p for p in parts if p)


# Ordinal suffix follows the last vowel of the spoken number.
_ORDINAL_SUFFIX = {'a': 'ıncı', 'ı': 'ıncı', 'e': 'inci', 'i': 'inci',
                   'o': 'uncu', 'u': 'uncu', 'ö': 'üncü', 'ü': 'üncü'}
_ORDINAL_IRREGULAR = {'dört': 'dördüncü', 'bir': 'birinci', 'iki': 'ikinci',
                      'altı': 'altıncı', 'yedi': 'yedinci', 'on': 'onuncu',
                      'yirmi': 'yirminci', 'yetmiş': 'yetmişinci',
                      'bin': 'bininci', 'yüz': 'yüzüncü'}


def number_to_ordinal(n):
    """104 -> 'yüz dördüncü'. Only the final word takes the suffix."""
    words = number_to_words(n).split()
    last = words[-1]
    if last in _ORDINAL_IRREGULAR:
        words[-1] = _ORDINAL_IRREGULAR[last]
    else:
        vowels = [c for c in last if c in _ORDINAL_SUFFIX]
        suffix = _ORDINAL_SUFFIX[vowels[-1]] if vowels else 'inci'
        if last[-1] in 'aeıioöuü':
            suffix = 'n' + suffix[1:] if suffix[0] in 'ıiuü' else suffix
        words[-1] = last + suffix
    return ' '.join(words)


# 1920'de / 1920de / 1990lı - a number carrying a Turkish case suffix.
_NUM_SUFFIX = re.compile(r'\b(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,9}(?:,\d+)?)[’\'ʼ]?([a-zçğıöşü]{1,6})\b')
# "104. madde" - an ordinal, told apart from a full stop by the lowercase word
# that follows it.
_ORDINAL = re.compile(r'\b(\d{1,4})\.(?=\s+[a-zçğıöşü])')
# A bare year or count keeps its digits: engines read those correctly, and
# expanding them would also catch dates and article numbers. Separators are the
# exception - "1.250.000" and "2,5" are where an engine reads the punctuation
# English-style ("bir nokta iki yüz elli...").
_BARE_GROUPED = re.compile(r'(?<![\d.,])\d{1,3}(?:\.\d{3})+(?:,\d+)?(?!\d|[.,]\d)')
_BARE_DECIMAL = re.compile(r'(?<![\d.,])\d{1,9},\d+(?!\d|[.,]\d)')


def decimal_to_words(token, half=False):
    """'2,5' -> 'iki virgül beş'; '1.000' -> 'bin'.

    Turkish uses the two separators the opposite way round to English: the dot
    groups thousands and the comma marks the decimal. Treating them alike turned
    "1.000" into "bir virgül sıfır".

    `half` reads ",5" as "buçuk", the way it is said aloud ("iki buçuk milyar").
    Only for a bare number: a written suffix was harmonised with "beş", and
    "buçuk" would need a different one ("2,5'i" is "beşi", not "buçuğu").
    """
    whole, _, frac = token.partition(',')
    whole = whole.replace('.', '')            # thousands separator, not spoken
    words = number_to_words(int(whole))
    if half and frac == '5' and int(whole):
        return words + ' buçuk'
    if frac:
        words += f" virgül {number_to_words(int(frac))}"
    return words


def expand_numbers(text):
    text = _ORDINAL.sub(lambda m: number_to_ordinal(int(m.group(1))), text)
    # Decimals are matched whole. Without this the pattern took only the digits
    # after the comma, turning "2,5'i" into "2,beşi".
    text = _NUM_SUFFIX.sub(
        lambda m: f"{decimal_to_words(m.group(1))}{m.group(2)}", text)
    # Whatever carries a separator and no suffix is left over for these two.
    text = _BARE_GROUPED.sub(lambda m: decimal_to_words(m.group(0), half=True), text)
    text = _BARE_DECIMAL.sub(lambda m: decimal_to_words(m.group(0), half=True), text)
    return text


_BACK_VOWELS = 'aıou'
_FRONT_VOWELS = 'eiöü'
_HARD_CONSONANTS = 'çfhkpsşt'


def locative(word):
    """'dört' -> 'dörtte', 'altı' -> 'altıda': the -DA suffix on a spoken word."""
    vowels = [c for c in word if c in _BACK_VOWELS + _FRONT_VOWELS]
    a = 'a' if vowels and vowels[-1] in _BACK_VOWELS else 'e'
    d = 't' if word[-1] in _HARD_CONSONANTS else 'd'
    return word + d + a


# 14.30 / 14:30 / 09.00'da. Two digits after the dot cannot be a thousands
# group (three) or a Turkish decimal (comma), so inside the hour range it is a
# clock time. Without this, the suffix rule took "30'da" alone: "14.otuzda".
_TIME = re.compile(r'(?<![\d.,])([01]?\d|2[0-3])[.:]([0-5]\d)(?!\d|[.,]\d)')


def time_to_words(hour, minute):
    """'14', '30' -> 'on dört otuz'; '09', '00' -> 'dokuz'; '9', '05' -> 'dokuz sıfır beş'."""
    words = number_to_words(int(hour))
    minute = int(minute)
    if minute:
        words += ' ' + ('sıfır ' if minute < 10 else '') + number_to_words(minute)
    return words


def expand_times(text):
    # A suffix stays where it was ("on dört otuz'da"): the writer harmonised it
    # with the spoken minute, and the apostrophe is joined later.
    return _TIME.sub(lambda m: time_to_words(*m.groups()), text)


# 2019-2023, 10–15 Ekim. The dash is otherwise voiced as "eksi" or dropped,
# running the two numbers together. "ila" is the written-out form of exactly
# this dash. Ascending pairs only, without leading zeros: a score ("3-0") or a
# phone number ("0212-...") is not a range.
_RANGE = re.compile(r'(?<![\w.,/:–—-])([1-9]\d{0,3})\s?[-–—]\s?([1-9]\d{0,3})(?!\d|[.,/:–—-]\d)')


def expand_ranges(text):
    def replace(match):
        low, high = match.groups()
        if int(low) >= int(high):
            return match.group(0)
        return f"{low} ila {high}"
    return _RANGE.sub(replace, text)


# 3/4 -> "dörtte üç". A slash between numbers is otherwise read "bölü". Proper
# fractions with a small denominator only: "24/7" or a date like "15/7" is not
# a fraction, and the larger a denominator the likelier a date it is.
# A suffix is written for the spoken numerator ("3/4'ü" is "dörtte üçü"), so it
# stays on it.
_FRACTION = re.compile(r'(?<![\w/.,])(\d{1,2})/(\d{1,2})'
                       r'(?:[’\'ʼ]([a-zçğıöşü]+))?(?![\w/]|[.,]\d)')


def expand_fractions(text):
    def replace(match):
        num, den = int(match.group(1)), int(match.group(2))
        suffix = match.group(3) or ''
        if not (0 < num < den <= 10):
            return match.group(0)
        if (num, den) == (1, 2):
            # "yarım asır" before a noun; with a suffix, "yarısı" - written
            # for "bir" ("1/2'si"), so its vowels move back for "yarı".
            return 'yarı' + suffix.translate(_TO_BACK) if suffix else 'yarım'
        spoken = number_to_words(num)
        # After a vowel-final numerator the buffer letter is often left out in
        # writing ("2/5'i" for "beşte ikisi"); say it anyway.
        if suffix and spoken[-1] in 'aeıioöuü' and suffix[0] in 'aeıioöuü':
            if suffix[0] in 'ıiuü' and not suffix.startswith(('s', 'y', 'n')):
                suffix = 's' + suffix
            elif suffix[0] in 'ae':
                suffix = 'y' + suffix
        return f"{locative(number_to_words(den))} {spoken}{suffix}"
    return _FRACTION.sub(replace, text)


# ---------------------------------------------------------------------- money

CURRENCY_WORDS = {'$': 'dolar', '€': 'avro', '£': 'sterlin', '₺': 'lira', 'TL': 'lira'}
_SCALE_WORD = r'(?:bin|milyon|milyar|trilyon)'
_AMOUNT = rf'(?:\d+(?:[.,]\d+)*(?:\s{_SCALE_WORD})?|\b{_SCALE_WORD})'
# "5.000 TL", "2,5 milyar $", "milyon TL". A following suffix ("TL'lik") is left
# alone: it was harmonised with "Te Le", not with "lira".
_CURRENCY_AFTER = re.compile(rf'({_AMOUNT})\s?(TL|[$€£₺])(?![\w’\'ʼ])')
# "$5", "€ 300" - English order, spoken the Turkish way round.
_CURRENCY_BEFORE = re.compile(r'([$€£₺])\s?(\d+(?:[.,]\d+)*(?:\s(?:bin|milyon|milyar|trilyon))?)')


def expand_money(text):
    """Currency signs are skipped or voiced in English; say the currency name.

    Runs before the initialism pass, so the "TL" of an amount becomes "lira"
    rather than "Te Le".
    """
    text = _CURRENCY_BEFORE.sub(lambda m: f"{m.group(2)} {CURRENCY_WORDS[m.group(1)]}", text)
    text = _CURRENCY_AFTER.sub(lambda m: f"{m.group(1)} {CURRENCY_WORDS[m.group(2)]}", text)
    # "TL'ye" -> "liraya". The suffix was written for "Te Le"; both end in a
    # vowel, so the buffer letters carry over and only the harmony moves back.
    text = _TL_SUFFIXED.sub(
        lambda m: f"{m.group(1)} lira{m.group(2).translate(_TO_BACK)}", text)
    return text


_TL_SUFFIXED = re.compile(rf'({_AMOUNT})\s?TL[’\'ʼ]([a-zçğıöşü]+)')
_TO_BACK = str.maketrans('eiöü', 'aıou')


# --------------------------------------------------------------------- roman

_ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10}
_ROMAN = re.compile(r'(?<![A-Za-zÇĞİÖŞÜçğıöşü])(X{0,2}(?:IX|IV|V?I{0,3}))\.?'
                    r'(?![A-Za-zÇĞİÖŞÜçğıöşü0-9])')


def _roman_to_int(s):
    total = 0
    for i, c in enumerate(s):
        value = _ROMAN_VALUES[c]
        total += -value if i + 1 < len(s) and value < _ROMAN_VALUES[s[i + 1]] else value
    return total


def tr_capitalize(word):
    """Turkish-aware: the capital of 'i' is 'İ', not 'I'."""
    if not word:
        return word
    first = 'İ' if word[0] == 'i' else word[0].upper()
    return first + word[1:]


def expand_roman(text):
    """'özeti II' -> 'özeti İkinci'. Roman numerals in headings read as letters."""
    def replace(match):
        token = match.group(1)
        if not token or token == 'I':      # a lone I is more likely a typo than a numeral
            return match.group(0)
        return tr_capitalize(number_to_ordinal(_roman_to_int(token)))

    return _ROMAN.sub(replace, text)


# ----------------------------------------------------------------- shorthand

# These can close a sentence, so their dot may be doing double duty.
TRAILING_ABBREVIATIONS = {'vb': 've benzeri', 'vs': 'vesaire', 'yy': 'yüzyıl'}

# These always introduce something - a name, a reference - so a capital letter
# after them is the normal case, not a sentence boundary. Their dot is purely an
# abbreviation mark and is dropped.
LEADING_ABBREVIATIONS = {
    'bkz': 'bakınız', 'ör': 'örneğin', 'Dr': 'Doktor', 'Prof': 'Profesör',
    'Doç': 'Doçent', 'Sn': 'Sayın', 'Av': 'Avukat',
    # Sentence-initial forms, and the longer "örn." spelling.
    'Bkz': 'Bakınız', 'Ör': 'Örneğin', 'örn': 'örneğin', 'Örn': 'Örneğin',
    'çev': 'çeviren', 'Yrd': 'Yardımcı',
}

# Marks that only mean something in front of a number or a date. Matched with
# that context so "s." at the end of an ordinary word is never touched. Runs
# before the number pass, which would otherwise turn the digits into words and
# hide the context.
_REFERENCE_MARKS = [
    (re.compile(r'\bs\.\s?(?=\d)'), 'sayfa '),
    (re.compile(r'\bNo\.\s?(?=\d)'), 'numara '),
    (re.compile(r'\bM\.\s?Ö\.'), 'Milattan önce'),
    (re.compile(r'\bM\.\s?S\.(?=\s*\d|\s+[a-zçğıöşü])'), 'Milattan sonra'),
]


def expand_reference_marks(text):
    for pattern, spoken in _REFERENCE_MARKS:
        text = pattern.sub(spoken, text)
    return text

# The dot after an abbreviation is doing two jobs at once when the abbreviation
# ends a sentence - "... vs." is both the abbreviation mark and the full stop.
# Expanding it away merged two paragraphs into one 837-character sentence, which
# Chirp 3 rejects outright ("sentences that are too long"), and that one article
# then failed every night. So the dot is kept whenever what follows looks like a
# new sentence: end of text, a line break, or a capital letter.
_SENTENCE_FOLLOWS = re.compile(r'\s*(?:$|\n|["“(]?[A-ZÇĞİÖŞÜ])')


def expand_abbreviations(text):
    for short, full in TRAILING_ABBREVIATIONS.items():
        def replace(match, full=full):
            # match.string, not the enclosing `text`: the loop rebinds that.
            tail = match.string[match.end():]
            return full + ('.' if _SENTENCE_FOLLOWS.match(tail) else '')
        text = re.sub(rf'\b{re.escape(short)}\.', replace, text)

    for short, full in LEADING_ABBREVIATIONS.items():
        text = re.sub(rf'\b{re.escape(short)}\.', full, text)
    return text


# Chirp 3 refuses a sentence beyond roughly 880 characters (measured against the
# API). The cap here is well under that: a run-on in the source should be split
# rather than cost the whole column its narration.
MAX_SENTENCE_CHARS = 600
_CLAUSE_BREAK = re.compile(r',\s+|;\s+|\s+-\s+')


def split_long_sentences(text, limit=MAX_SENTENCE_CHARS):
    """Break sentences the engine would reject, at the nearest clause boundary.

    A comma becomes a full stop, which the listener hears as a slightly firmer
    pause - the only lever available, since Chirp 3 takes no SSML and the limit
    is per sentence, not per request. Every word is kept.
    """
    out = []
    for sentence in re.findall(r'[^.!?…]*[.!?…]+|[^.!?…]+$', text):
        while len(sentence) > limit:
            breaks = [m.end() for m in _CLAUSE_BREAK.finditer(sentence)
                      if m.end() <= limit]
            if not breaks:
                break                      # nothing to split on; send as-is
            cut = breaks[-1]
            head = sentence[:cut].rstrip().rstrip(',;-').rstrip()
            out.append(head + '. ')
            sentence = tr_capitalize(sentence[cut:].lstrip())
        out.append(sentence)
    return ''.join(out)


def fix_symbols(text):
    """Symbols an engine either spells out or skips entirely."""
    # Turkish writes the percent sign first but says it first too: %50 -> yüzde 50.
    text = re.sub(r'%\s?(\d+(?:[.,]\d+)?)', r'yüzde \1', text)
    # A slash between words is an "or", not the word "bölü".
    text = re.sub(r'(?<=[^\W\d_])/(?=[^\W\d_])', ' ya da ', text)
    # Ellipsis and doubled punctuation: keep one mark, drop the rest.
    text = re.sub(r'…|\.{2,}', '.', text)
    text = re.sub(r'([!?])[!?]+', r'\1', text)
    text = re.sub(r'([,;:])[,;:]+', r'\1', text)
    # "(!)" is an aside the engine cannot voice; the comma keeps its beat.
    text = re.sub(r'\s*\(\s*[!?]+\s*\)', ',', text)
    # Substitutions above can leave a space in front of punctuation, which some
    # engines voice as a hesitation.
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    # Between words "&" is "ve"; left alone it is voiced in English or skipped.
    text = re.sub(r'\s?&\s?', ' ve ', text)
    return text


# A full address is unlistenable - the path especially. Keep the host, said
# the way a presenter would ("sol nokta org nokta tr"), and drop the rest. The
# lookahead leaves sentence punctuation after the link in place.
_URL = re.compile(r'(?:https?://|www\.)(?:www\.)?([^\s/?#]+?)(?:[/?#][^\s]*?)?(?=[.,;:!?)»”"]*(?:\s|$))')
_HANDLE = re.compile(r'(?<![\w@#])[@#](\w+)')


def expand_links(text):
    text = _URL.sub(lambda m: m.group(1).replace('.', ' nokta '), text)
    # @kullanici and #etiket: the sign is noise, the name is the content.
    return _HANDLE.sub(r'\1', text)


def apply_all(text, unknown=None, spelled=None):
    """Order matters: links first, before anything reads their dots and digits;
    money before initialisms, so "TL" is "lira" next to an amount; roman
    numerals before initialisms (II is both shapes); numbers before symbols so
    %50 is not split first; reference marks, times, ranges and fractions before
    the number pass, which would erase the digits they need to see.

    `spelled` swaps in an engine-specific initialism table (see eleven_prep).
    """
    text = expand_links(text)
    text = expand_money(text)
    text = expand_roman(text)
    text = expand_initialisms(text, unknown, spelled)
    text = fix_symbols(text)
    text = expand_reference_marks(text)
    text = expand_times(text)
    text = expand_ranges(text)
    text = expand_fractions(text)
    text = expand_numbers(text)
    text = expand_abbreviations(text)
    # Last, so it sees the text exactly as the engine will.
    text = split_long_sentences(text)
    return text
