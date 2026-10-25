"""Termbase candidate extraction from a book's TranslationUnits (pure, deterministic).

Classic rule-based + statistical term extraction, no NLP model dependency:

* **Multi-word lowercase terms:** C-value (Frantzi, Ananiadou & Mima, "Automatic recognition
  of multi-word terms: the C-value/NC-value method", Int. J. Digital Libraries 3:115-130,
  2000, https://link.springer.com/article/10.1007/s007999900023, checked 2026-10-24) over
  stop-word-delimited n-grams (2-4 words). Without a POS tagger the linguistic filter is
  approximated by stop-word / verb / adverb lists. Kept with frequency >= 3 and spread >= 2
  sections.
* **Capitalised phrases** (technique names, proper names): maximal runs of capitalised
  words with internal connectors (of, for, and, ...). A sentence-initial word is ambiguous
  and is dropped when it is a function word or a lone word. A phrase must be capitalised in
  most of its mid-sentence occurrences (knowledge-free capitalised-word disambiguation in
  the spirit of Mikheev, ACL 1999, https://aclanthology.org/P99-1021/).
* **Defined terms:** "X is a technique/method/...", "called/known as/termed X",
  "the concept of 'X'". The defining sentence becomes ``definition_context``.
* **Acronyms:** Schwartz & Hearst, "A simple algorithm for identifying abbreviation
  definitions in biomedical text", PSB 2003 (https://psb.stanford.edu/psb-online/proceedings/
  psb03/schwartz.pdf): "long form (SF)", matched right-to-left, first SF letter at a word start.
* **Back-of-book index headwords** (units of kind ``index``) as concept evidence;
  "Surname, Given" headwords mark people.
* **Named entities:** typed by surface rules: honorifics, initials, reporting verbs and
  possessives (person); head nouns (organization / place / work); a small country and
  region list (place); quotation marks or a preceding "book/novel/..." (work).
* **Single-word domain concepts (v2):** keyness of a lowercase word in the book against a
  general-English reference, Kilgarriff's "simple maths" ratio
  ``(fpm_book + 1) / (fpm_reference + 1)`` (A. Kilgarriff, "Simple maths for keywords",
  Corpus Linguistics 2009, https://www.sketchengine.eu/wp-content/uploads/2015/04/
  2009-Simple-maths-for-keywords.pdf, checked 2026-10-24; smoothing n=1 favours rarer,
  specialised words). It is the smoothed form of the "weirdness" ratio of Ahmad, Gillam &
  Tostevin, TREC-8 1999 (https://trec.nist.gov/pubs/trec8/papers/surrey2.pdf, checked
  2026-10-24). A log-likelihood test (Dunning 1993, https://aclanthology.org/J93-1003/;
  Rayson & Garside 2000, https://aclanthology.org/W00-0901/) needs reference *counts*, which
  the reference list does not publish, so the ratio is used with frequency and spread floors
  instead. The reference is ``wordfreq`` 3.1.1 English (pinned, docs/04 section 5;
  https://pypi.org/project/wordfreq/3.1.1/). Without a POS tagger, verbs and evaluative
  adjectives are filtered by a nominal-context share (preceded by a determiner/preposition or
  followed by "of") and by suffixes; glued OCR tokens ("ofeach") are rejected when they
  split into two common words; a word mostly used inside a longer accepted term is dropped
  (the C-value nesting idea applied to unigrams).
* **Back-of-book index (v2):** a single-word headword is kept on its own evidence (the
  indexer judged it a concept) when it occurs at least twice and is not a very common word.
* **Possessives and question marks inside names (v2):** "Devil's Advocacy" is kept as one
  name when the possessor (almost) never occurs outside the phrase; "What If? Analysis" is
  joined when a technique head noun follows the "?".
* **Person typing (v2):** surnames of note / bibliography authors ("Given Surname, Title" and
  "Surname, Given") and surnames seen with reporting verbs type Title-Case names as people;
  "-ian/-ean/-ist/-ism" adjectives of a person's name ("Crocean") are kept as terms
  (signal ``eponym``); very common nationality adjectives ("Italian") are not terms.
* **Noise fixes (v3, D-039; measured on the 1959 provisional decisions, eval/reports/
  term-extraction-v3.md):**
  - citation fragments do not *discover* candidates (``mask_citations``): the state code of a
    publication place ("(Tysons, VA: ..."), short-title citations ("Vacca, Alternative
    Modernities, xii, 37."), quoted article titles of a reference and the journal title that
    follows them. Publishers and cited works that the review kept are deliberately not masked.
    Occurrences are still counted on the unmasked text;
  - months, weekdays, compass words, nationalities, languages and ethnonyms are never people;
  - a line-break hyphen ("Self- Critique") is joined and a slash joins two capitalised names
    ("High Impact/Low Probability Analysis");
  - a capitalised or C-value candidate is dropped when one longer kept candidate covers at
    least half of its occurrences (``NESTED_SHARE``, below the 0.75 used for single words);
  - a lowercase C-value phrase that starts or ends with a verb ("analysts avoid", "generate
    scenarios") or ends with a participle or a preposition ("techniques described", "driving
    forces behind") is not a term.

Occurrences are counted only in translatable units (prose, heading, list items, captions,
table cells, footnotes); references, index, toc and noise never count.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from b2c_contracts import TranslationUnit

from .model import source_key, TermCandidate

EXTRACTOR_VERSION = "termbase-extract-v3"

GeneralFrequency = Callable[[str], float]


@lru_cache(maxsize=200_000)
def general_english_frequency(word: str) -> float:
    """Share of English tokens that are ``word`` (0.0 when unseen), from wordfreq 3.1.1."""
    from wordfreq import word_frequency  # imported lazily: the word list loads in ~1 s

    return float(word_frequency(word, "en"))

TRANSLATABLE_KINDS = frozenset({"prose", "heading", "list_item", "caption", "table_cell", "footnote"})
_MAX_SAMPLE = 5
_CONTEXT_CHARS = 300

STOPWORDS = frozenset("""
a about above after again against all also although am among an and any are as at be because been
before being below between both but by can cannot could did do does doing down during each either
else even ever every few for from further had has have having he her here hers him his how however
i if in into is it its itself just least less may me might more most much must my neither no nor not
now of off often on once one only or other others otherwise our ours out over own per perhaps
rather same several shall she should since so some such than that the their theirs them then there
these they this those though through thus to too under until up upon us very via was we were what
whatever when where whether which while who whom whose why will with within without would yet you
your yours many first second third new two three four five six seven eight nine ten another way
ways also already still well quite always never sometimes usually often instead therefore hence
""".split())

# Words that make a lowercase n-gram unlikely to be a term (approximate POS filter).
NON_TERM_WORDS = frozenset("""
make makes made making use uses used using help helps helped provide provides provided give gives
given take takes taken get gets got see seen seems seem become becomes became come comes came go
goes went say says said know known think thinks thought want wants need needs needed show shows
shown tell tells find finds found keep keeps kept let lets put puts set like likely look looks
include includes including included consider considered allow allows allowed require requires
required tend tends try tries able unable important different various certain particular specific
example examples case cases chapter chapters figure figures table tables page pages part parts
section sections number numbers time times year years lot kind kinds sort type types thing things
people person world day days others example several whole least best better good great high low
large small long short last next early late major minor key main
""".split())

# Connectors allowed inside a capitalised run; "the" only right after of/for.
_CONNECTORS = frozenset({"of", "for", "the", "de", "la", "du", "von", "van", "der", "al", "&", "y",
                         "di", "del", "da"})
# Unambiguous titles only ("General", "Justice", "Saint" also start common phrases).
_HONORIFICS = frozenset({"Dr", "Mr", "Mrs", "Ms", "Prof", "Professor", "Sir", "Dame", "President",
                         "Senator", "Sen", "Rev", "Colonel", "Col", "Captain", "Capt", "Admiral"})
_NAME_SUFFIXES = frozenset({"Jr", "Sr", "II", "III", "IV"})
_REPORTING = frozenset("""
argued argues wrote writes claimed claims contended contends insisted insists believed believes
recalled recalls asserted asserts remarked remarks warned warns famously himself herself said says
observed explained noted
""".split())
_ORG_HEADS = frozenset("""
University Institute Agency Party Council Ministry Department Press Brotherhood Company Corporation
Corp Inc Ltd Committee Association Union Organization Organisation Bank Fund Foundation League Front
Office Bureau Service Services Commission Center Centre School College Academy Army Navy Government
Parliament Congress Senate Court Church Society Group Federation Alliance Coalition Network Board
Authority Administration Forum Movement Command Directorate Council Assembly Corps Laboratory
Laboratories Institution LLC Associates Partners Cooperative Syndicate Confederation
""".split())
_PLACE_HEADS = frozenset("""
Square Street Avenue Road River Mountains Mountain Mount Lake Sea Ocean City Province Valley Island
Islands Gulf Bay Desert Peninsula Strait Canal Bridge County District Region Republic Kingdom Emirates
Coast Plain Plains Highlands Delta Harbor Harbour Park Palace Quarter Village Town Prefecture
""".split())
_WORK_HEADS = frozenset("""
Notebooks Manifesto Journal Quarterly Times Gazette Handbook Dictionary Encyclopedia
Encyclopaedia Chronicle Herald Tribune Bulletin Magazine Letters Papers Writings Treatise Bible Quran
Koran Testament
""".split())
_WORK_CUES = frozenset({"book", "novel", "essay", "article", "journal", "newspaper", "magazine", "film",
                        "poem", "treatise", "pamphlet", "titled", "entitled", "memoir"})
# Countries, continents and large regions (generic geography, not book content).
PLACE_NAMES = frozenset("""
Afghanistan|Albania|Algeria|Andorra|Angola|Argentina|Armenia|Australia|Austria|Azerbaijan|Bahamas|
Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|Benin|Bhutan|Bolivia|Bosnia|Botswana|Brazil|
Brunei|Bulgaria|Burkina Faso|Burundi|Cambodia|Cameroon|Canada|Chad|Chile|China|Colombia|Comoros|
Congo|Costa Rica|Croatia|Cuba|Cyprus|Czechia|Czech Republic|Denmark|Djibouti|Dominica|Ecuador|Egypt|
El Salvador|Eritrea|Estonia|Eswatini|Ethiopia|Fiji|Finland|France|Gabon|Gambia|Georgia|Germany|Ghana|
Greece|Grenada|Guatemala|Guinea|Guyana|Haiti|Honduras|Hungary|Iceland|India|Indonesia|Iran|Iraq|
Ireland|Israel|Italy|Jamaica|Japan|Jordan|Kazakhstan|Kenya|Kiribati|Korea|North Korea|South Korea|
Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|Liberia|Libya|Liechtenstein|Lithuania|
Luxembourg|Madagascar|Malawi|Malaysia|Maldives|Mali|Malta|Mauritania|Mauritius|Mexico|Moldova|
Monaco|Mongolia|Montenegro|Morocco|Mozambique|Myanmar|Burma|Namibia|Nauru|Nepal|Netherlands|
Holland|New Zealand|Nicaragua|Niger|Nigeria|Norway|Oman|Pakistan|Palau|Palestine|Panama|
Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Qatar|Romania|Russia|Rwanda|Samoa|
San Marino|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|Slovakia|Slovenia|Somalia|
South Africa|South Sudan|Spain|Sri Lanka|Sudan|Suriname|Sweden|Switzerland|Syria|Taiwan|Tajikistan|
Tanzania|Thailand|Togo|Tonga|Tunisia|Turkey|Türkiye|Turkmenistan|Tuvalu|Uganda|Ukraine|
United Arab Emirates|United Kingdom|Britain|Great Britain|England|Scotland|Wales|United States|
America|Uruguay|Uzbekistan|Vanuatu|Vatican|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe|Soviet Union|USSR|
Yugoslavia|Africa|Asia|Europe|North America|South America|Latin America|Antarctica|Oceania|
Middle East|Near East|Far East|Balkans|Caribbean|Scandinavia|Mediterranean|Arabian Peninsula|
Gulf|Maghreb|Levant|Kurdistan|Sahara|Siberia|Caucasus|Central Asia|East Asia|Southeast Asia|
South Asia|West Africa|East Africa|North Africa|Sub-Saharan Africa|Western Europe|Eastern Europe|
Global South|Global North|Third World|Western Sahara|Gaza|West Bank|Jerusalem|London|Paris|Washington|
Moscow|Beijing|Cairo|Rome|Berlin|Tokyo|New York|Baghdad|Damascus|Beirut|Tehran|Istanbul|Madrid|
Turin|Milan|Naples|Sardinia|Sicily|Tunis|Algiers|Rabat|Amman|Riyadh|Doha|Dubai|Kabul|Delhi|Mumbai
""".replace("\n", "").split("|"))

# Words are Unicode letters (Frédéric, Chávez), digits allowed after the first letter.
_TOKEN = re.compile(r"[^\W\d_][^\W_]*(?:[-'’][^\W_]+)*\.?(?:™|®)?|[0-9]+(?:[.,][0-9]+)*|[^\s\w]|_")
_SENT_END = frozenset({".", "!", "?", ";", ":"})
_OPEN_QUOTES = frozenset({"'", "‘", '"', "“"})
_CLOSE_QUOTES = frozenset({"'", "’", '"', "”"})
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")
_HEADING_NUM = re.compile(r"^\s*(?:(?:chapter|part|section)\s+)?[0-9IVXLC]+(?:\.[0-9]+)*\.?\s+", re.I)

# Head nouns that may follow a "?" inside a technique title ("What If? Analysis").
_QUESTION_TITLE_HEADS = frozenset("""
Analysis Analyses Technique Techniques Method Approach Exercise Game Test Check Review Mapping
Matrix Diagram Session Workshop Framework Model Questions Scenario Scenarios
""".split())

# --- keyness of single lowercase words (see the module docstring) -------------------------
KEYNESS_SMOOTHING = 1.0          # Kilgarriff's n (per-million smoothing constant)
MIN_KEYNESS = 20.0               # (fpm_book + n) / (fpm_reference + n), word found by keyness alone
MIN_INDEX_KEYNESS = 5.0          # an index headword needs much less (the indexer chose it)
SPECIFIC_KEYNESS = 100.0         # so specific that adjective use is still a term ("subaltern")
MAX_REFERENCE_ZIPF = 4.5         # very common words (>= ~32 per million) are not termbase concepts
MAX_INDEX_ZIPF = 5.0             # an index headword is trusted up to here
MIN_NOMINAL_SHARE = 0.2          # share of mentions after a determiner/preposition or before "of"
MIN_HEAD_SHARE = 0.5             # share of mentions ending a phrase (not "vital role")
MIN_LOWER_SHARE = 0.6            # otherwise the word is part of a capitalised name
_NOMINAL_LEFT = frozenset("""
the a an this these those its their his her our your my of in on for with by from such any
each every no some many all other both between among into against
""".split())
_NON_NOMINAL_SUFFIX = re.compile(r"(?:ing|ed|ous|ful|less|able|ible)$")
_LEGAL_SUFFIX_ACRONYMS = frozenset({"LLC", "INC", "LTD", "PLC", "LLP"})

# --- v3 noise fixes (module docstring) -----------------------------------------------------
NESTED_SHARE = 0.5               # share of occurrences inside ONE longer kept candidate
MAX_ALONE = 1                    # ... and occurrences outside every longer candidate
VERB_SHARE = 0.25                # share of a word's lowercase mentions right after "to"
# Never a person: calendar words, compass words, nationalities / languages / ethnonyms.
NOT_PEOPLE = frozenset("""
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
north south east west northern southern eastern western northeast northwest southeast southwest
arctic antarctic
afghan albanian algerian american americans arab arabs arabic argentine argentinian armenian
asian australian austrian belgian bengali bolivian bosnian brazilian british bulgarian burmese
cambodian canadian chilean chinese colombian croatian cuban cypriot czech danish dutch
egyptian english estonian ethiopian european europeans filipino finnish flemish french georgian
german germans greek haitian hebrew hindi hungarian icelandic indian indonesian iranian iraqi
irish israeli italian italians jamaican japanese jewish jordanian kenyan korean kurd kurdish kurds
kuwaiti latin latvian lebanese libyan lithuanian malaysian mexican mongolian moroccan nepali
nigerian norwegian pakistani palestinian palestinians persian peruvian polish portuguese romanian
russian russians saudi scandinavian scottish serbian sicilian slovak slovenian somali soviet
spanish sudanese swedish swiss syrian taiwanese thai tunisian turkish ukrainian urdu uruguayan
venezuelan vietnamese welsh yemeni yiddish zimbabwean african africans latino latina latinos
""".split())
_PREPOSITIONS = frozenset("""
behind across beyond toward towards around along despite alongside amid amidst inside outside
besides unlike near onto beneath underneath throughout regarding concerning following versus
""".split())
_DEHYPHEN = re.compile(r"(?<=[A-Za-z])- (?=[A-Za-z])")         # "Self- Critique" (line-break hyphen)
_NOTE_KINDS = frozenset({"footnote", "list_item", "table_cell", "caption"})
# Page locators: roman or up to three digits (a four-digit number is more likely a year).
_PAGES = r"(?:[xivlc]{1,6}|\d{1,3})(?:\s*[-–]\s*(?:[xivlc]{1,6}|\d{1,3}))?"
_PAGE_TAIL = rf"{_PAGES}(?:\s*,\s*{_PAGES})*(?:\s+and\s+passim)?"
# "(Tysons, VA: Publisher, 2019)": only the state code of a publication place is masked.
_PUB_STATE = re.compile(r"(?<=\()(?P<city>[A-Z][\w.'’ \-]{1,40}),\s*(?P<state>[A-Z]{2})(?=\s*:)")
# A journal title right after a quoted article title and before its volume number:
# "'Title', Canadian Journal of Political Science/Revue ... 23, no. 1".
_JOURNAL = re.compile(r"[’'”\"],?\s+(?P<journal>[A-Z][^,;()\d'’\"”“‘]{2,100}?)\s+\d{1,4},\s*no\.\s*\d")
_TITLE_WORD = r"(?:[A-Z][\w'’\-]*|of|and|the|in|for|on|to|a|an|&|at|from|with)"
_SHORT_TITLE = rf"(?:(?:The|A|An)\s+)?[A-Z][\w'’\-]*(?:\s+{_TITLE_WORD}){{0,8}}"
_SURNAME = r"[A-Z][\w'’\-]+(?:\s+(?:and|&)\s+[A-Z][\w'’\-]+)?"
# "Vacca, Alternative Modernities, xii, 37." anywhere; in a note, also a ";"-separated list of
# short titles ("Boggs, Two Revolutions; Femia, Political Thought; Salamini, Political Praxis. 138.").
_SHORT_CITE_PAGES = re.compile(rf"(?<![\w])(?P<who>{_SURNAME}),\s+(?P<title>{_SHORT_TITLE}),\s*{_PAGE_TAIL}\.?(?=\s|$)")
_SHORT_CITE_LIST = re.compile(rf"(?:^|(?<=;\s))(?P<who>{_SURNAME}),\s+(?P<title>{_SHORT_TITLE})"
                              r"(?=\s*;|\.\s*\d)")
_SHORT_CITE_BEFORE_LIST = re.compile(rf"(?<![\w])(?P<who>{_SURNAME}),\s+(?P<title>{_SHORT_TITLE})(?=\s*;)")


def _blank(text: str, start: int, end: int) -> str:
    """Replace ``text[start:end]`` by a separator of the same length (breaks every run)."""
    return text[:start] + " ;" + " " * max(0, end - start - 2) + text[end:]


def _names_an_organization(title: str) -> bool:
    return any(w in _ORG_HEADS for w in title.split())   # publishers and institutions are kept


def mask_citations(text: str, kind: str = "prose") -> str:
    """``text`` with citation fragments replaced by separators (see the module docstring).

    Used only to *discover* candidates; frequencies and contexts use the original text. Cited
    works, publishers and institutions are not masked: the provisional review kept many of
    them, so masking them would lose accepted entries (measured, eval/reports/term-extraction-v3.md)."""
    out = text
    for m in _PUB_STATE.finditer(text):
        out = _blank(out, m.start("state"), m.end("state"))
    for m in _JOURNAL.finditer(text):
        if not _names_an_organization(m.group("journal")):
            out = _blank(out, m.start("journal"), m.end("journal"))
    patterns = [_SHORT_CITE_PAGES] + ([_SHORT_CITE_LIST, _SHORT_CITE_BEFORE_LIST] if kind in _NOTE_KINDS else [])
    for pattern in patterns:
        for m in pattern.finditer(text):
            if not _names_an_organization(m.group("title")):
                out = _blank(out, m.start("title"), m.end("title"))
    return out


def _zipf(freq: float) -> float:
    """Zipf scale of a token share: log10 of frequency per billion words (wordfreq's scale)."""
    return math.log10(freq * 1e9) if freq > 0 else 0.0


@dataclass
class _Tok:
    text: str       # without a trailing period (kept separately)
    start: int
    end: int        # end of the token text (excluding the trailing period)
    period: bool    # token was followed by its own period (abbreviation/initial or sentence end)
    sent_start: bool
    quoted_open: bool = False
    is_word: bool = True


@dataclass
class _Agg:
    surface: Counter = field(default_factory=Counter)
    signals: set = field(default_factory=set)
    mentions: int = 0       # capitalised mentions seen by the phrase chunker
    name_pattern: int = 0   # honorific or initials (Dr. X, Richards J. Heuer Jr.)
    reported: int = 0       # followed by a reporting verb / preceded by "according to"
    possessive: int = 0
    org_head: int = 0
    place_head: int = 0
    work_head: int = 0
    quoted: int = 0
    work_cue: int = 0
    definition: str | None = None
    expansion: str | None = None
    join_possessor: tuple[str, ...] | None = None   # "Devil's Advocacy" -> ("devil",)


@dataclass
class _WordStats:
    """Per casefolded word: lowercase / mid-sentence capitalised mentions, nominal contexts,
    noun-phrase-head positions, units and sections (prose-like units only: not headings, not
    all caps)."""
    lower: Counter = field(default_factory=Counter)
    cap_mid: Counter = field(default_factory=Counter)
    nominal: Counter = field(default_factory=Counter)   # after a determiner/preposition, or before "of"
    head: Counter = field(default_factory=Counter)      # followed by punctuation / a function word
    after_to: Counter = field(default_factory=Counter)  # lowercase mention right after "to" (v3 verb test)
    units: dict = field(default_factory=lambda: defaultdict(set))
    sections: dict = field(default_factory=lambda: defaultdict(set))
    tokens: int = 0

    def add(self, toks: list[_Tok], idx: int, section: tuple[str, ...]) -> None:
        prev = ""
        for n, tok in enumerate(toks):
            if not tok.is_word:
                prev = ""
                continue
            self.tokens += 1
            w, possessive = _possessive(tok.text)
            lw = w.casefold().replace("’", "'")
            if w.islower():
                self.lower[lw] += 1
                nxt = toks[n + 1] if n + 1 < len(toks) else None
                if prev in _NOMINAL_LEFT or (nxt is not None and nxt.text == "of"):
                    self.nominal[lw] += 1
                if prev == "to":
                    self.after_to[lw] += 1
                if (tok.period or possessive or nxt is None or not nxt.is_word
                        or nxt.text.casefold() in STOPWORDS):
                    self.head[lw] += 1
            elif not tok.sent_start and _is_cap(w):
                self.cap_mid[lw] += 1
            self.units[lw].add(idx)
            self.sections[lw].add(section)
            prev = lw

    def verb_like(self, w: str, *, gerund: bool = False) -> bool:
        """POS-free verb test (v3): the word, or its base form ("lacks" -> "lack", and with
        ``gerund`` "helping" -> "help"), follows "to" in at least ``VERB_SHARE`` of its lowercase
        mentions (twice or more). A final "-ing" word is a nominal head ("critical thinking"),
        so the gerund reading is only asked for the first word ("helping analysts")."""
        bases = {w}
        if w.endswith("es"):
            bases |= {w[:-1], w[:-2]}
        elif w.endswith("s"):
            bases.add(w[:-1])
        if gerund and w.endswith("ing") and len(w) > 5:
            stem = w[:-3]
            bases |= {stem, stem + "e"} | ({stem[:-1]} if len(stem) > 2 and stem[-1] == stem[-2] else set())
        return any(self.after_to[b] >= 2 and self.after_to[b] >= VERB_SHARE * self.lower[b] for b in bases)

    def lower_share(self, w: str) -> float:
        total = self.lower[w] + self.cap_mid[w]
        return self.lower[w] / total if total else 0.0

    def keyness(self, w: str, ref: GeneralFrequency) -> float:
        """Kilgarriff's simple-maths ratio of this book against the general reference."""
        fpm = 1e6 * (self.lower[w] + self.cap_mid[w]) / max(1, self.tokens)
        return (fpm + KEYNESS_SMOOTHING) / (1e6 * ref(w) + KEYNESS_SMOOTHING)

    def noun_like(self, w: str, ref: GeneralFrequency) -> bool:
        """POS-free noun test: often after a determiner/preposition (not a verb) and often the
        head of its phrase (not an attributive adjective); a very specific word passes the
        second test anyway ("subaltern", "hegemonic" are terms as adjectives too)."""
        low = self.lower[w]
        if not low or self.nominal[w] / low < MIN_NOMINAL_SHARE:
            return False
        return self.head[w] / low >= MIN_HEAD_SHARE or self.keyness(w, ref) >= SPECIFIC_KEYNESS


def _possessive(word: str) -> tuple[str, bool]:
    for suffix in ("'s", "’s"):
        if word.endswith(suffix):
            return word[: -len(suffix)], True
    if word.endswith(("s'", "s’")):
        return word[:-1], True
    return word, False


def _tokens(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    sent_start = True
    prev_quote = False
    for m in _TOKEN.finditer(text):
        raw = m.group(0)
        if raw[0].isalpha():
            period = raw.endswith(".")
            word = raw[:-1] if period else raw
            tok = _Tok(word, m.start(), m.start() + len(word), period, sent_start, quoted_open=prev_quote)
            out.append(tok)
            prev_quote = False
            abbreviation = period and (len(word) == 1 or word in _HONORIFICS or word in _NAME_SUFFIXES
                                       or word in {"e.g", "i.e", "etc", "vs", "No", "pp", "p", "ed", "eds",
                                                   "Vol", "vol", "Inc", "Ltd", "Co", "St"})
            sent_start = period and not abbreviation
        else:
            is_open = raw in _OPEN_QUOTES and (not out or out[-1].end < m.start() or not out[-1].is_word)
            out.append(_Tok(raw, m.start(), m.end(), False, False, is_word=False))
            if raw in _SENT_END:
                sent_start = True
            elif raw in ("(", "[", "—", "–") or raw in _OPEN_QUOTES or raw in _CLOSE_QUOTES:
                pass
            elif raw[0].isdigit():
                sent_start = False
            prev_quote = is_open
    return out


def _is_cap(word: str) -> bool:
    return word[:1].isupper()


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.6


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“‘(])", text)
    return [p.strip() for p in parts if p.strip()]


def _key(phrase: str) -> tuple[str, ...]:
    # possessive "'s" is dropped per word, exactly as in ``_Occurrences`` ("Devil's Advocacy")
    words = _WORD.findall(phrase.casefold().replace("’", "'"))
    return tuple(w[:-2] if w.endswith("'s") and len(w) > 2 else w for w in words)


class _Occurrences:
    """n-gram (1..8 words, casefolded) -> unit indices, for frequency and spread."""

    def __init__(self, units: Sequence[TranslationUnit]):
        self.units = units
        self.index: dict[tuple[str, ...], set[int]] = defaultdict(set)
        self.cap_mid: Counter = Counter()      # key -> mid-sentence capitalised occurrences
        self.lower_mid: Counter = Counter()    # key -> lowercase occurrences
        for i, unit in enumerate(units):
            words = _WORD.findall(unit.source_text.casefold().replace("’", "'"))
            words = [w[:-2] if w.endswith("'s") else w for w in words]
            for n in range(1, 9):
                for j in range(len(words) - n + 1):
                    self.index[tuple(words[j:j + n])].add(i)

    def units_of(self, key: tuple[str, ...]) -> set[int]:
        found = set(self.index.get(key, ()))
        if key:
            last = key[-1]
            for plural in (last + "s", last + "es"):
                found |= self.index.get(key[:-1] + (plural,), set())
        return found


def _schwartz_hearst(long_text: str, short: str) -> str | None:
    s_idx = len(short) - 1
    l_idx = len(long_text) - 1
    while s_idx >= 0:
        c = short[s_idx].lower()
        if not c.isalnum():
            s_idx -= 1
            continue
        while l_idx >= 0 and (long_text[l_idx].lower() != c
                              or (s_idx == 0 and l_idx > 0 and long_text[l_idx - 1].isalnum())):
            l_idx -= 1
        if l_idx < 0:
            return None
        l_idx -= 1
        s_idx -= 1
    start = long_text.rfind(" ", 0, l_idx + 1) + 1
    return long_text[start:].strip()


_ACRO_PAREN = re.compile(r"([A-Za-z][\w’'\- ]{2,120}?)\s*\(\s*([A-Z][A-Za-z0-9&\-]{1,9})\s*\)")
_ACRO_TOKEN = re.compile(r"\b[A-Z][A-Z0-9&]{1,6}s?\b")
_ROMAN = re.compile(r"^(?=[IVXLCDM]+$)M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_DEF_IS = re.compile(
    r"^(?:(?:The|A|An)\s+)?(?P<t>[A-Za-z][\w’'\-™]*(?:\s+[A-Za-z][\w’'\-™]*){0,5}?)\s+(?:is|are)\s+"
    r"(?:a|an|the)\s+(?:[\w\-]+\s+){0,3}?(?:technique|method|process|tool|approach|concept|term|"
    r"framework|theory|practice|strategy|procedure|exercise|form|type|model|notion|tactic|analysis)\b")
_DEF_CALLED = re.compile(
    r"\b(?:called|known as|termed|referred to as|dubbed)\s+(?:the\s+)?"
    r"(?:[\"“'‘](?P<q>[^\"”'’]{2,60})[\"”'’]|(?P<c>[A-Z][\w’'\-]*(?:\s+(?:of\s+|the\s+|and\s+)?[A-Z][\w’'\-]*){0,4}))")
_DEF_CONCEPT = re.compile(
    r"\b(?:the\s+)?(?:term|concept|notion|idea|category)\s+(?:of\s+)?[\"“'‘](?P<q>[^\"”'’]{2,60})[\"”'’]")
_INDEX_ENTRY = re.compile(r"^(?P<head>[A-Za-z][^,:;\d()]{1,80}?)\s*(?:[,:]\s*(?P<given>[A-Z][\w.\- ]{0,40}?))?"
                          r"\s*[,:]\s*(?:\d|see\b|See\b|and\b|in\b)")


def extract_candidates(units: Sequence[TranslationUnit], *, min_cvalue: float = 4.0,
                       min_term_frequency: int = 3, min_term_sections: int = 2,
                       general_frequency: GeneralFrequency | None = None,
                       min_keyness: float = MIN_KEYNESS) -> list[TermCandidate]:
    """Extract termbase candidates (terms, acronyms, named entities) from one book's units.

    ``general_frequency(word)`` is the share of general-English tokens that are ``word``
    (default: wordfreq English, ``general_english_frequency``); it drives the keyness of
    single words and the common-word filters.
    """
    ref = general_frequency or general_english_frequency
    trans = [u for u in units if u.kind in TRANSLATABLE_KINDS]
    occ = _Occurrences(trans)
    aggs: dict[tuple[str, ...], _Agg] = defaultdict(_Agg)
    index_people: set[str] = set()       # casefolded surnames from "Surname, Given" headwords
    index_heads: dict[tuple[str, ...], str] = {}
    stats = _WordStats()
    ref_people = _reference_people(units)

    # ---------------------------------------------------------- back-of-book index
    for unit in units:
        if unit.kind != "index":
            continue
        for line in re.split(r"\n|(?<=\d)[.;]\s+(?=[A-Za-z])", unit.source_text):
            m = _INDEX_ENTRY.match(line.strip())
            if not m:
                continue
            head = m.group("head").strip(" .")
            given = (m.group("given") or "").strip()
            if given and _is_cap(head) and len(head.split()) <= 2 and all(_is_cap(w) for w in given.split()):
                index_people.add(head.casefold())
                full = f"{given} {head}"
                aggs[_key(full)].surface[full] += 1
                aggs[_key(full)].signals.add("index")
                index_heads[_key(head)] = head
                continue
            head_words = head.split()
            while head_words and head_words[-1].casefold() in STOPWORDS:
                head_words.pop()
            head = " ".join(head_words)
            if 1 <= len(head_words) <= 4 and _key(head) and _key(head)[0] not in STOPWORDS:
                index_heads[_key(head)] = head

    lower_ngrams: Counter = Counter()

    for idx, unit in enumerate(trans):
        # discovery text (v3): citation fragments masked, line-break hyphens joined
        text = _DEHYPHEN.sub("-", mask_citations(unit.source_text, unit.kind))
        is_heading = unit.kind == "heading"
        if is_heading:
            text = _HEADING_NUM.sub("", text)
        upper = _mostly_upper(text)
        toks = _tokens(text)
        # -------------------------------------------------- capitalised phrases
        if not upper:
            _cap_phrases(text, toks, aggs, occ, is_heading)
        # -------------------------------------------------- lowercase n-grams (C-value input)
        if not is_heading and not upper:
            stats.add(toks, idx, tuple(unit.section_path))
            run: list[str] = []
            for tok in toks + [_Tok("", 0, 0, False, False, is_word=False)]:
                w = tok.text
                ok = (tok.is_word and w.islower() and len(w) >= 3 and w not in STOPWORDS
                      and w not in NON_TERM_WORDS and w not in _PREPOSITIONS and not w.endswith("ly")
                      and "'" not in w and "’" not in w)
                if ok:
                    run.append(w)
                if not ok or tok.period:
                    if len(run) >= 2:
                        for n in range(2, min(4, len(run)) + 1):
                            for j in range(len(run) - n + 1):
                                lower_ngrams[tuple(run[j:j + n])] += 1
                    run = []
        # -------------------------------------------------- acronyms (Schwartz & Hearst)
        for m in _ACRO_PAREN.finditer(text):
            short = m.group(2)
            short = short[:-1] if short.endswith("s") and short[:-1].isupper() else short
            if sum(c.isupper() for c in short) < 2 or _ROMAN.match(short):
                continue
            window = m.group(1).split()
            limit = min(len(short) + 5, len(short) * 2)
            long_form = _schwartz_hearst(" ".join(window[-limit:]), short)
            if (not long_form or len(long_form.split()) < 2
                    or long_form.split()[0].casefold() in STOPWORDS):
                continue
            long_form = long_form.strip(" ,;:")
            a = aggs[_key(short) + ("#acronym",)]
            a.surface[short] += 1
            a.signals.add("acronym_expansion")
            a.expansion = a.expansion or long_form
            lf = aggs[_key(long_form)]
            lf.surface[long_form] += 1
            lf.signals.add("acronym_expansion")
            lf.definition = lf.definition or _context_of(text, long_form)
        if not upper:
            for m in _ACRO_TOKEN.finditer(text):
                short = m.group(0)
                short = short[:-1] if short.endswith("s") and short[:-1].isupper() else short
                if len(short) < 2 or _ROMAN.match(short) or short.casefold() in STOPWORDS \
                        or any(c.isdigit() for c in short) or short in _LEGAL_SUFFIX_ACRONYMS:
                    continue
                aggs[_key(short) + ("#acronym",)].surface[short] += 1
        # -------------------------------------------------- defined terms
        for sentence in _sentences(text):
            for pattern in (_DEF_IS, _DEF_CALLED, _DEF_CONCEPT):
                for m in pattern.finditer(sentence):
                    term = (m.groupdict().get("t") or m.groupdict().get("q") or m.groupdict().get("c") or "").strip()
                    term = term.strip(" ,.;:'\"‘’“”")
                    words = term.split()
                    if not words or len(words) > 6 or words[0].casefold() in STOPWORDS - {"what"}:
                        continue
                    content = [w for w in words if w.casefold() not in _CONNECTORS]
                    if pattern is _DEF_IS and (len(content) < 2 or not all(_is_cap(w) for w in content)):
                        continue  # "X is a method": only capitalised multi-word names are reliable
                    if m.groupdict().get("c") and len(content) < 2:
                        continue
                    if len(term) < 3 or any(w.casefold() in NON_TERM_WORDS for w in content[-1:]):
                        continue
                    a = aggs[_key(term)]
                    a.surface[term] += 1
                    a.signals.add("defined")
                    a.definition = a.definition or sentence[:_CONTEXT_CHARS]

    # ---------------------------------------------------------- C-value over lowercase n-grams
    extensions: dict[tuple[str, ...], list[tuple[str, ...]]] = defaultdict(list)
    for gram in lower_ngrams:
        if len(gram) >= 3:
            extensions[gram[:-1]].append(gram)
            extensions[gram[1:]].append(gram)
    for key, value in _c_values(lower_ngrams).items():
        if value >= min_cvalue and not _mostly_nested(key, lower_ngrams, extensions) \
                and not _verb_fragment(key, stats):
            a = aggs[key]
            a.surface[" ".join(key)] += 1
            a.signals.add("c_value")

    # ---------------------------------------------------------- index headwords as concepts
    for key, head in index_heads.items():
        if key in aggs and "index" not in aggs[key].signals:
            aggs[key].signals.add("index")
        elif key not in aggs and occ.units_of(key):
            aggs[key].surface[head] += 1
            aggs[key].signals.add("index")

    # ---------------------------------------------------------- keyness of single words
    for word in _key_words(stats, ref, min_keyness, min_term_frequency, min_term_sections):
        a = aggs[(word,)]
        a.surface[word] += 1
        a.signals.add("keyness")

    # an author "surname" used mostly lowercase in the book, or a very common English word, is
    # more likely a title word ("House", "Popular") than a name
    ref_people = {p for p in ref_people
                  if stats.lower[p] <= stats.cap_mid[p] and _zipf(ref(p)) < MAX_REFERENCE_ZIPF}
    ctx = _Ctx(ref=ref, stats=stats, people=index_people | ref_people, aggs=aggs)
    return _finalise(aggs, occ, trans, ctx, min_term_frequency, min_term_sections)


@dataclass
class _Ctx:
    ref: GeneralFrequency
    stats: _WordStats
    people: set[str]      # casefolded surnames: index "Surname, Given" and note/bibliography authors
    aggs: dict            # all phrase evidence, keyed like ``_key``


def _key_words(stats: _WordStats, ref: GeneralFrequency, min_keyness: float, min_freq: int,
               min_sections: int) -> list[str]:
    """Lowercase single words that are domain-specific for this book (module docstring)."""
    out = []
    for w, low in stats.lower.items():
        if (low < min_freq or len(w) < 4 or w in STOPWORDS or w in NON_TERM_WORDS or "'" in w
                or w.endswith("ly") or _NON_NOMINAL_SUFFIX.search(w) or not w.replace("-", "").isalpha()):
            continue
        if len(stats.units[w]) < min_freq or len(stats.sections[w]) < min_sections:
            continue
        if stats.lower_share(w) < MIN_LOWER_SHARE or not stats.noun_like(w, ref):
            continue
        general = ref(w)
        if _zipf(general) >= MAX_REFERENCE_ZIPF:
            continue
        if general == 0.0 and "-" not in w and _glued(w, ref):
            continue
        if stats.keyness(w, ref) >= min_keyness:
            out.append(w)
    return sorted(out)


def _glued(word: str, ref: GeneralFrequency) -> bool:
    """An unseen word that splits into two common words is an OCR join ("ofeach", "adifferent")."""
    return any(_zipf(ref(word[:i])) >= 3.0 and _zipf(ref(word[i:])) >= 3.0 for i in range(1, len(word) - 1))


_NOTE_NUMBER = re.compile(r"^\s*(?:\d+\.\s*)?(?:(?:See|see|Cf\.|cf\.)(?:\s+also)?\s+)?")
_NAME_WORD = r"(?:[A-Z][\w’'\-]+|[A-Z]\.)"
_NOTE_AUTHOR = re.compile(rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}}?)(?:\s+(?:Jr|Sr)\.?)?"
                          r"(?=,|\s+and\s)")
_BIB_AUTHOR = re.compile(r"(?P<sur>[A-Z][\w’'\-]+(?:[ \-][A-Z][\w’'\-]+)?),\s+(?=[A-Z][a-z]+\b|[A-Z]\.)")


def _reference_people(units: Sequence[TranslationUnit]) -> set[str]:
    """Casefolded author surnames from notes / bibliography units: "Given [X.] Surname, Title"
    and "Surname, Given" at the start of an entry (entries split on ";"), plus co-authors
    joined by "and" / ",". Used only to type names that also occur in translatable text."""
    people: set[str] = set()
    for unit in units:
        if unit.kind != "reference":
            continue
        for entry in unit.source_text.split(";"):
            text = entry[_NOTE_NUMBER.match(entry).end():]
            m = _BIB_AUTHOR.match(text)
            if m and not any(w in _ORG_HEADS or w in _PLACE_HEADS for w in m.group("sur").split()):
                people.add(m.group("sur").split()[-1].casefold().replace("’", "'"))
                continue
            pos = 0
            while True:
                m = _NOTE_AUTHOR.match(text, pos)
                if not m:
                    break
                words = m.group("name").split()
                if any(w in _ORG_HEADS or w in _PLACE_HEADS or w.casefold() in STOPWORDS
                       or w.casefold() in NON_TERM_WORDS for w in words) or len(words[-1]) < 3:
                    break
                people.add(words[-1].casefold().replace("’", "'"))
                rest = re.match(r",?\s+and\s+", text[m.end():])
                if not rest:
                    break
                pos = m.end() + rest.end()
    return people


def _context_of(text: str, phrase: str) -> str:
    for sentence in _sentences(text):
        if phrase.casefold() in sentence.casefold():
            return sentence[:_CONTEXT_CHARS]
    return text[:_CONTEXT_CHARS]


def _mostly_nested(key: tuple[str, ...], freq: Counter, extensions, share: float = 0.75) -> bool:
    """True when one longer n-gram that extends ``key`` carries >= ``share`` of its
    occurrences (the fragment rarely stands alone, e.g. "subaltern social" in
    "subaltern social group"); plural forms count with their singular."""
    own = freq[key]
    if own == 0:
        return False
    longer: Counter = Counter({other: freq[other] for other in extensions.get(key, ())})
    for other, f in longer.items():
        stem = other if other[-1][-1:] != "s" else other[:-1] + (other[-1][:-1],)
        total = f + (longer.get(stem, 0) if stem != other else 0)
        if total >= share * own:
            return True
    return False


def _verb_fragment(key: tuple[str, ...], stats: _WordStats) -> bool:
    """A lowercase n-gram that is a clause fragment, not a noun phrase (v3): it starts or ends
    with a verb ("generate scenarios", "analysts avoid"), or ends with a participle or a
    preposition ("techniques described", "driving forces behind")."""
    first, last = key[0], key[-1]
    if last.endswith("ed") or last in _PREPOSITIONS:
        return True
    return stats.verb_like(first, gerund=True) or (not last.endswith("ing") and stats.verb_like(last))


def _c_values(freq: Counter) -> dict[tuple[str, ...], float]:
    """C-value (Frantzi et al. 2000): log2|a| * (f(a) - mean f of longer candidates nesting a)."""
    nested_in: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for longer, f in freq.items():
        n = len(longer)
        for m in range(2, n):
            for j in range(n - m + 1):
                nested_in[longer[j:j + m]].append(f)
    out = {}
    for cand, f in freq.items():
        nests = nested_in.get(cand)
        value = f - (sum(nests) / len(nests) if nests else 0.0)
        out[cand] = math.log2(len(cand)) * value
    return out


def _grow(toks: list[_Tok], i: int) -> int:
    """Last index of the run of capitalised words (joined by connectors) starting at ``i``."""
    n = len(toks)
    j = i
    k = i + 1
    while k < n:
        t = toks[k]
        if toks[k - 1].period and toks[k - 1].text not in _HONORIFICS and len(toks[k - 1].text) > 1 \
                and toks[k - 1].text not in _NAME_SUFFIXES:
            break
        if _possessive(toks[k - 1].text)[1]:
            break
        if t.is_word and _is_cap(t.text):
            j = k
            k += 1
        elif t.is_word and t.text.casefold() in _CONNECTORS and k + 1 < n and not t.period and (
                (toks[k + 1].is_word and _is_cap(toks[k + 1].text))
                or (toks[k + 1].text.casefold() == "the" and k + 2 < n and _is_cap(toks[k + 2].text))):
            k += 1
            if toks[k].text.casefold() == "the":
                k += 1
        elif (t.text == "?" and t.start == toks[k - 1].end and k + 1 < n
              and toks[k + 1].text in _QUESTION_TITLE_HEADS):
            k += 1   # "What If? Analysis": a question-form technique title
        elif (t.text == "/" and t.start == toks[k - 1].end and k + 2 < n and toks[k + 1].is_word
              and toks[k + 1].start == t.end and _is_cap(toks[k + 1].text) and not toks[k - 1].period
              and sum(x.is_word for x in toks[i:k]) >= 2 and toks[k + 2].is_word and _is_cap(toks[k + 2].text)
              and not toks[k + 1].period):
            # "High Impact/Low Probability Analysis": a slash joins two capitalised names of two
            # or more words each (v3); "CQ Press/SAGE" and "Marx/Engels" stay apart
            k += 1
        else:
            break
    return j


def _cap_phrases(text: str, toks: list[_Tok], aggs, occ: _Occurrences, is_heading: bool) -> None:
    i = 0
    n = len(toks)
    while i < n:
        tok = toks[i]
        if not (tok.is_word and _is_cap(tok.text)):
            i += 1
            continue
        j = _grow(toks, i)
        _emit_phrase(text, toks, i, j, aggs, occ, is_heading)
        if (_possessive(toks[j].text)[1] and j + 1 < n and toks[j + 1].is_word and _is_cap(toks[j + 1].text)
                and not toks[j].period):
            _emit_join(text, toks, i, j, _grow(toks, j + 1), aggs, is_heading)
        i = j + 1


def _emit_join(text: str, toks: list[_Tok], i: int, j: int, j2: int, aggs, is_heading: bool) -> None:
    """Record "Possessor's Name" ("Devil's Advocacy") as a possible single name; it is kept in
    ``_finalise`` only when the possessor rarely occurs outside the joined phrase."""
    while i <= j and (toks[i].text in _HONORIFICS or (toks[i].text.casefold() in STOPWORDS
                                                       and (toks[i].sent_start or is_heading))):
        i += 1
    while j2 > j and toks[j2].text.casefold() in _CONNECTORS:
        j2 -= 1
    if i > j or j2 <= j or _possessive(toks[j2].text)[1]:
        return
    possessor = [t.text for t in toks[i:j + 1] if t.is_word]
    if len(possessor) > 3 or len([t for t in toks[i:j2 + 1] if t.is_word]) > 6:
        return
    surface = " ".join(text[toks[i].start:toks[j2].end].split())
    key = _key(surface)
    a = aggs[key]
    a.surface[surface] += 1
    a.mentions += 1
    a.signals.add("heading" if is_heading else "capitalized")
    a.signals.add("possessive_join")
    a.join_possessor = _key(" ".join(possessor))


def _emit_phrase(text: str, toks: list[_Tok], i: int, j: int, aggs, occ: _Occurrences, is_heading: bool) -> None:
    # strip honorifics / leading function words
    honorific = False
    question_title = any(t.text == "?" for t in toks[i:j + 1])   # "What If? Analysis" keeps "What"
    while i <= j and (toks[i].text in _HONORIFICS or (toks[i].text.casefold() in STOPWORDS
                                                       and (toks[i].sent_start or is_heading)
                                                       and not question_title)):
        honorific = honorific or toks[i].text in _HONORIFICS
        i += 1
    while i <= j and toks[j].text.casefold() in _CONNECTORS:
        j -= 1
    if i > j:
        return
    span = [t for t in toks[i:j + 1] if t.is_word]
    words = [t.text for t in span]
    if len(words) > 7:
        return
    last_text, possessive = _possessive(toks[j].text)
    words[-1] = last_text
    end = toks[j].end - (len(toks[j].text) - len(last_text))
    if toks[j].period and toks[j].text in _NAME_SUFFIXES:
        end += 1
    if len(last_text) == 1 and len(words) > 1:
        return  # ends with an initial: a name split by the layout
    surface = " ".join(text[toks[i].start:end].split())
    single = len([w for w in words if w.casefold() not in _CONNECTORS]) == 1
    starts_sentence = toks[i].sent_start and not honorific
    after = next((t for t in toks[j + 1:j + 2] if t.is_word), None)
    reported = after is not None and after.text.casefold() in _REPORTING
    if single and starts_sentence and not reported:
        return  # a lone sentence-initial capitalised word is ambiguous
    if single and (words[0].casefold() in STOPWORDS or len(words[0]) < 2):
        return
    key = _key(surface)
    if not key:
        return
    a = aggs[key]
    a.surface[surface] += 1
    a.mentions += 1
    a.signals.add("heading" if is_heading else "capitalized")
    # ------------------------------------------------ entity evidence (counted per mention)
    has_initial = any(len(t.text) == 1 and t.period for t in span[:-1]) or \
        any(t.text in _NAME_SUFFIXES for t in span)
    names = [w for w in words if len(w) > 1 and w not in _NAME_SUFFIXES]
    plausible_name = (
        bool(names) and len(words) <= 5 and not re.match(r"^[A-Z]\.[A-Z]\.", surface)
        and not any(w.casefold() in STOPWORDS or w.casefold() in NON_TERM_WORDS or w.endswith("ing")
                    or w in _ORG_HEADS or w in _PLACE_HEADS for w in names)
        and not (len(span[0].text) == 1 and span[0].sent_start and len(names) < 2))
    if plausible_name and (honorific or (has_initial and len(words) >= 2)):
        a.name_pattern += 1
    a.reported += reported
    before = [t.text.casefold() for t in toks[max(0, i - 2):i] if t.is_word]
    a.reported += before[-2:] == ["according", "to"]
    a.possessive += possessive
    if len(words) > 1 and (words[-1] in _ORG_HEADS or any(w in _ORG_HEADS and nxt in ("of", "for")
                                                          for w, nxt in zip(words, words[1:]))):
        a.org_head += 1
    if (words[-1] in _PLACE_HEADS and len(words) > 1) or " ".join(words) in PLACE_NAMES:
        a.place_head += 1
    if words[-1] in _WORK_HEADS:
        a.work_head += 1
    opened = i > 0 and toks[i - 1].text in _OPEN_QUOTES and not toks[i - 1].is_word
    closed = j + 1 < len(toks) and toks[j + 1].text in _CLOSE_QUOTES
    if opened and closed and len(words) >= 2:
        a.quoted += 1
    if before and before[-1] in _WORK_CUES:
        a.work_cue += 1


_TECHNICAL_SUFFIX = re.compile(r"(?:ism|isms|ist|ists|ity|ities|ization|isation|ology|ics|ocracy|archy|ian|ians)$")
_NOT_A_TERM = re.compile(r"^(?:Chapter|Figure|Table|Part|Section|Page|Box|Appendix|Note|Notes|Step|Stage|Phase)\b"
                         r"|^[\d\W]+$")


def _finalise(aggs, occ: _Occurrences, trans, ctx: _Ctx, min_freq, min_sections) -> list[TermCandidate]:
    out: dict[tuple[tuple[str, ...], bool], TermCandidate] = {}
    hits_of: dict[tuple[tuple[str, ...], bool], list[int]] = {}
    deferred: list[tuple[tuple[str, ...], str, _Agg, list[int]]] = []   # possible eponyms
    for raw_key, a in aggs.items():
        acronym = raw_key[-1:] == ("#acronym",)
        key = raw_key[:-1] if acronym else raw_key
        if not key or not a.surface:
            continue
        surface = sorted(a.surface.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        hits = sorted(occ.units_of(key))
        if acronym:
            hits = [h for h in hits if re.search(rf"\b{re.escape(surface)}s?\b", trans[h].source_text)]
        freq = len(hits)
        if freq == 0 or _NOT_A_TERM.search(surface):
            continue
        if acronym:
            if not a.expansion and freq < 3:
                continue
            kind = "acronym"
        else:
            kind = _kind(key, surface, a, ctx, aggs)
            if not _keep(kind, key, surface, a, hits, trans, freq, min_freq, min_sections, ctx):
                if kind == "term" and len(key) == 1 and _EPONYM_SUFFIX.search(key[0]) and _is_cap(surface):
                    deferred.append((key, surface, a, hits))
                    continue
                if kind != "term" or _not_people(key) or not _proper_noun(key, surface, a, hits, trans):
                    continue
                kind = "person"  # most unresolved single proper nouns in books are surnames
                a.signals.add("kind_uncertain")
        if kind == "term" and len(key) == 1 and a.signals & {"keyness", "index"} \
                and ctx.stats.lower_share(key[0]) >= MIN_LOWER_SHARE:
            surface = key[0]    # "hegemony", not the heading / sentence-initial "Hegemony"
        dedupe = (key, acronym)
        if dedupe not in out or out[dedupe].frequency < freq:
            out[dedupe] = _candidate(surface, kind, a, hits, trans, acronym)
            hits_of[dedupe] = hits
    _drop_nested(out, hits_of)
    _drop_fragments(out, hits_of, trans)
    # adjectives of a person's name ("Crocean", "Gramscian"): terms, flagged ``eponym``
    surnames = ctx.people | {k[-1] for (k, acr), c in out.items() if c.kind == "person" and not acr}
    for key, surface, a, hits in deferred:
        if len(hits) >= 2 and _eponym(key[0], surnames) and _cap_ratio(surface, hits, trans) >= 0.8:
            a.signals.add("eponym")
            out[(key, False)] = _candidate(surface, "term", a, hits, trans, False)
    for dedupe, cand in list(out.items()):
        if cand.kind == "term" and len(dedupe[0]) == 1 and "eponym" not in cand.signals \
                and _is_cap(cand.source) and _eponym(dedupe[0][0], surnames):
            out[dedupe] = cand.model_copy(update={"signals": tuple(sorted({*cand.signals, "eponym"}))})
    # a plural form duplicates its singular (frequency already counts both)
    for (key, acronym) in list(out):
        singular = (key[:-1] + (key[-1][:-1],), False)
        if not acronym and key[-1].endswith("s") and singular in out \
                and out[singular].kind == out[(key, acronym)].kind:
            del out[(key, acronym)]
    return _merge_same_source(sorted(out.values(), key=lambda c: (c.kind, -c.frequency, c.source.casefold(),
                                                                  c.source)))


def _merge_same_source(candidates: Sequence[TermCandidate]) -> list[TermCandidate]:
    """One candidate per source string (BookTermbase keys entries by ``source_key``).

    The same surface can come out both as an acronym and as a named entity ("USSR"), or in two
    letter cases. Keep the acronym reading (it carries the expansion), otherwise the more frequent
    one; merge signals and keep the highest frequency. Order of first appearance is preserved."""
    merged: dict[str, TermCandidate] = {}
    for cand in candidates:
        key = source_key(cand.source)
        kept = merged.get(key)
        if kept is None:
            merged[key] = cand
            continue
        winner, other = ((cand, kept) if (cand.kind == "acronym") != (kept.kind == "acronym")
                         and cand.kind == "acronym" else
                         (kept, cand) if kept.kind == "acronym" or kept.frequency >= cand.frequency
                         else (cand, kept))
        merged[key] = winner.model_copy(update={
            "frequency": max(winner.frequency, other.frequency),
            "sections": max(winner.sections, other.sections),
            "signals": tuple(sorted({*winner.signals, *other.signals})),
            "expansion": winner.expansion or other.expansion,
            "definition_context": winner.definition_context or other.definition_context})
    return list(merged.values())


def _candidate(surface: str, kind: str, a: _Agg, hits: list[int], trans, acronym: bool) -> TermCandidate:
    sections = len({tuple(trans[h].section_path) for h in hits})
    context = a.definition or _context_of(trans[hits[0]].source_text, surface)
    score = round(len(hits) * max(1.0, math.log2(1 + sections)), 3)
    return TermCandidate(
        source=surface, kind=kind, frequency=len(hits), sections=sections,
        unit_ids=tuple(trans[h].unit_id for h in hits[:_MAX_SAMPLE]),
        definition_context=context, expansion=a.expansion if acronym else None,
        signals=tuple(sorted(a.signals)), score=score)


def _drop_nested(out: dict, hits_of: dict, share: float = 0.75) -> None:
    """Drop a single word found only by keyness, and the possessor of an accepted possessive
    name ("Devil" of "Devil's Advocacy"), when >= ``share`` of its units are covered by a
    longer accepted candidate containing it (the word rarely stands alone)."""
    longer: dict[str, set[int]] = defaultdict(set)
    possessors: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for (key, acronym), cand in out.items():
        if acronym or len(key) < 2:
            continue
        for w in set(key):
            longer[w] |= set(hits_of[(key, acronym)])
        if "possessive_join" in cand.signals:
            for n in range(1, len(key)):
                possessors[key[:n]] |= set(hits_of[(key, acronym)])
    for (key, acronym), cand in list(out.items()):
        if acronym:
            continue
        own = set(hits_of[(key, acronym)])
        covered = possessors.get(key, set())
        if len(key) == 1 and "keyness" in cand.signals and set(cand.signals) <= {"keyness", "capitalized"}:
            w = key[0]
            covered = covered | longer.get(w, set()) | longer.get(w[:-1] if w.endswith("s") else w + "s", set())
        if covered and len(own & covered) >= share * len(own):
            del out[(key, acronym)]


# Capitalised candidates found only by these signals may be fragments of a longer name (v3).
_WEAK_SIGNALS = frozenset({"capitalized", "heading", "kind_uncertain"})
# A fragment is cut from its longer name at a function word or a slash ("Program Evaluation |
# and Review Technique", "A | Tradecraft Primer", "High Impact | /Low Probability Analysis");
# a modifier before a head noun ("Red Hat" Analysis) or after a title ("Psychologist" Abraham
# Maslow) is an entry of its own.
_CUT_WORDS = frozenset({"and", "or", "of", "for", "the", "a", "an", "to", "in", "on"})


def _cut_from(key: tuple[str, ...], other: tuple[str, ...], other_surface: str) -> bool:
    n, m = len(key), len(other)
    for i in range(m - n + 1):
        if other[i:i + n] != key:
            continue
        if (i > 0 and other[i - 1] in _CUT_WORDS) or (i + n < m and other[i + n] in _CUT_WORDS):
            return True
    words = other_surface.casefold().split("/")
    return len(words) > 1 and any(_key(part)[:n] == key or _key(part)[-n:] == key for part in words)


def _phrase_pattern(key: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + r"[\W_]+".join(re.escape(w) for w in key) + r"(?:s|es)?(?![\w])", re.I)


def _drop_fragments(out: dict, hits_of: dict, trans, share: float = NESTED_SHARE,
                    max_alone: int = MAX_ALONE) -> None:
    """Drop a candidate found only by weak signals when ONE longer kept candidate containing
    it covers at least ``share`` of its occurrences and it stands alone (outside every longer
    candidate) at most ``max_alone`` times ("Review Technique" inside "Program Evaluation and
    Review Technique"). "Labour Party" survives "British Labour Party" (no single longer name
    covers half of its mentions); people are never fragments (a surname is its own entry)."""
    keys = [(k, acr) for (k, acr) in out if not acr]
    containers: dict[tuple[str, ...], list[tuple[str, ...]]] = defaultdict(list)
    for key, _ in keys:
        n = len(key)
        for other, _ in keys:
            m = len(other)
            if m > n and any(other[i:i + n] == key for i in range(m - n + 1)):
                containers[key].append(other)
    patterns: dict[tuple[str, ...], re.Pattern[str]] = {}

    def spans(key: tuple[str, ...], unit: int) -> list[tuple[int, int]]:
        pattern = patterns.setdefault(key, _phrase_pattern(key))
        return [m.span() for m in pattern.finditer(trans[unit].source_text)]

    for key, longer in containers.items():
        cand = out.get((key, False))
        if cand is None or cand.kind == "person" or not set(cand.signals) <= _WEAK_SIGNALS:
            continue
        own = {h: spans(key, h) for h in hits_of[(key, False)]}
        total = sum(len(s) for s in own.values())
        live = [o for o in longer if (o, False) in out and _cut_from(key, o, out[(o, False)].source)]
        if not total or not live:
            continue
        covered: dict[tuple[str, ...], set[tuple[int, int, int]]] = {}
        for other in live:
            covered[other] = {(h, a, b) for h, mine in own.items() for a, b in mine
                              if any(x <= a and b <= y for x, y in spans(other, h))}
        alone = total - len(set().union(*covered.values()))
        if alone <= max_alone and max(len(c) for c in covered.values()) >= share * total:
            del out[(key, False)]


_EPONYM_SUFFIX = re.compile(r"(?:ians?|eans?|ists?|isms?|ites?)$")


def _eponym(word: str, surnames: set[str]) -> bool:
    """``word`` = stem + -ian/-ean/-ist/-ism/-ite where a known surname starts with the stem
    and is at most two letters longer ("crocean" <- "croce", "gramscian" <- "gramsci")."""
    m = _EPONYM_SUFFIX.search(word)
    if not m:
        return False
    stem = word[:m.start()]
    if len(stem) < 3:
        return False
    return any(p.startswith(stem) and len(p) - len(stem) <= 2 and len(p) >= 4 for p in surnames)


def _index_person(key: tuple[str, ...], surface: str, ctx: _Ctx) -> bool:
    words = surface.split()
    if not all(_is_cap(w) for w in words) or len(words) > 4:
        return False
    if len(key) > 1 and not _name_like(key[0], ctx):
        return False    # "Young Gramsci", "Postcolonial Gramsci": a title, not a person
    return key[-1] in ctx.people


def _name_like(word: str, ctx: _Ctx) -> bool:
    """A given name is capitalised mid-sentence more often than not, and is not a connector
    or a derived adjective ("Leninist")."""
    return (word not in _CONNECTORS and not _EPONYM_SUFFIX.search(word) and not _TECHNICAL_SUFFIX.search(word)
            and not word.endswith("ing") and ctx.stats.lower[word] <= ctx.stats.cap_mid[word])


def _reported_surname(key: tuple[str, ...], surface: str, ctx: _Ctx, aggs) -> bool:
    """A 2-3 word Title-Case name ("Amos Tversky") whose last word, used alone, is followed by
    reporting verbs / possessives ("Tversky argued") and is not a common English word."""
    words = surface.split()
    if not 2 <= len(words) <= 3 or len(key) != len(words) \
            or not all(_is_cap(w) and w.replace("-", "").isalpha() for w in words):
        return False
    if any(w.casefold() in _CONNECTORS or w.casefold() in STOPWORDS or w in _ORG_HEADS or w in _PLACE_HEADS
           or w in _WORK_HEADS for w in words):
        return False
    single = aggs.get(key[-1:])
    return bool(single and single.reported and _name_like(key[0], ctx)
                and (single.reported + single.possessive) / max(1, single.mentions) >= 0.05
                and _zipf(ctx.ref(key[-1])) < MAX_REFERENCE_ZIPF)


def _kind(key, surface: str, a: _Agg, ctx: _Ctx, aggs) -> str:
    kind = _surface_kind(key, surface, a, ctx, aggs)
    if kind == "person" and _not_people(key):
        return "term"   # v3: "September", "Polish", "South" are never people
    return kind


def _not_people(key: tuple[str, ...]) -> bool:
    return bool(key) and all(w in NOT_PEOPLE for w in key)


def _surface_kind(key, surface: str, a: _Agg, ctx: _Ctx, aggs) -> str:
    if a.name_pattern:
        return "person"
    if a.org_head:
        return "organization"
    if a.place_head:
        return "place"
    if a.work_head or (a.quoted + a.work_cue and (a.quoted + a.work_cue) / max(1, a.mentions) >= 0.3):
        return "work"
    if "possessive_join" in a.signals:
        return "term"
    if _index_person(key, surface, ctx) and not _TECHNICAL_SUFFIX.search(surface) \
            and not ("keyness" in a.signals and len(key) == 1):
        return "person"
    if a.reported and len(key) <= 3 and not _TECHNICAL_SUFFIX.search(surface) \
            and (a.reported + a.possessive) / max(1, a.mentions) >= 0.05:
        return "person"
    if _reported_surname(key, surface, ctx, aggs):
        return "person"
    return "term"


def _keep(kind, key, surface, a: _Agg, hits, trans, freq, min_freq, min_sections, ctx: _Ctx) -> bool:
    signals = a.signals
    n_words = len(key)
    capitalised = bool(signals & {"capitalized", "heading"})
    if kind != "term":
        if a.name_pattern or (kind == "person" and "index" in signals and n_words > 1):
            return True
        if _cap_ratio(surface, hits, trans) < 0.8:
            return False
        return n_words > 1 or freq >= 2 or a.place_head > 0
    if "possessive_join" in signals and a.join_possessor:
        # the possessor must (almost) never stand alone: capitalised non-possessive mentions
        possessor = ctx.aggs.get(a.join_possessor)
        alone = possessor.mentions - possessor.possessive if possessor else 0
        return freq >= 2 and alone <= 0.25 * freq and _cap_ratio(surface, hits, trans) >= 0.8
    if "defined" in signals or ("acronym_expansion" in signals and n_words >= 2):
        return True
    if "c_value" in signals and freq >= min_freq and \
            len({tuple(trans[h].section_path) for h in hits}) >= min_sections:
        return True
    if "index" in signals and (n_words > 1 or _TECHNICAL_SUFFIX.search(surface)):
        return True
    if "index" in signals and n_words == 1 and freq >= 2 and _zipf(ctx.ref(key[0])) < MAX_INDEX_ZIPF \
            and ctx.stats.lower_share(key[0]) >= MIN_LOWER_SHARE and ctx.stats.noun_like(key[0], ctx.ref) \
            and ctx.stats.keyness(key[0], ctx.ref) >= MIN_INDEX_KEYNESS:
        return True     # a lowercase concept the indexer chose
    if "keyness" in signals:
        return True     # filters applied in ``_key_words``
    if capitalised:
        if _cap_ratio(surface, hits, trans) < 0.8 or freq < 2:
            return False
        if n_words == 1 and not _TECHNICAL_SUFFIX.search(surface):
            return False
        if n_words == 1 and _zipf(ctx.ref(key[0])) >= MAX_REFERENCE_ZIPF:
            return False   # common nationality / general adjectives ("Italian", "European")
        return any(trans[h].kind != "heading" for h in hits)
    return False


def _proper_noun(key, surface, a: _Agg, hits, trans) -> bool:
    """A single word written with a capital in (nearly) every mid-sentence mention, at
    least twice: a proper noun whose type the surface rules could not decide."""
    return (len(key) == 1 and a.mentions >= 2 and len(surface) >= 3 and surface[0].isupper()
            and not surface.isupper() and not _TECHNICAL_SUFFIX.search(surface)
            and not surface.endswith(("ean", "ian", "ist", "ism"))
            and _cap_ratio(surface, hits, trans, min_mid=2) >= 0.95)


def _cap_ratio(surface: str, hits: list[int], trans, *, min_mid: int = 0) -> float:
    """Share of mid-sentence occurrences written with a capital (Mikheev-style evidence)."""
    pattern = re.compile(r"(?<![\w])" + r"[\s-]+".join(re.escape(w) for w in surface.split()) + r"(?![\w])",
                         re.IGNORECASE)
    cap = low = 0
    for h in hits:
        unit = trans[h]
        if unit.kind == "heading" or _mostly_upper(unit.source_text):
            continue
        for m in pattern.finditer(unit.source_text):
            found = m.group(0)
            if found[:1].isupper():
                prefix = unit.source_text[:m.start()].rstrip()
                if not prefix or prefix[-1] in ".!?:\"“'‘(":
                    continue  # sentence-initial: no evidence either way
                cap += 1
            else:
                low += 1
    if cap + low == 0 or cap < min_mid:
        return 1.0 if min_mid == 0 else 0.0
    return cap / (cap + low)
