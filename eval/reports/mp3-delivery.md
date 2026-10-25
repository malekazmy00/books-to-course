# مخرجات الصوت النهائية للمتعلم: MP3 وID3v2.3 وقص الصمت وحد المسار والنسخ إلى الكرت (2026-10-25)

- **القرار:** D-042 في `docs/00-DECISIONS.md`.
- **المراجع:**
  - `docs/proposals/2026-10-25-learning-companion.md` §13–§15 (D-036 وD-037 وD-038).
  - `eval/reports/speech-readability-2026-10-25.md`، البندان المفتوحان 3 و5.
- **الحالة:** كود واختبارات وقياس محلي فقط.
  - لم تُشغَّل حاويات الكلام، ولم يُعَد بناء أي صورة.
  - لم يُلمس أي container، ولم يُكتب شيء في Postgres أو MinIO.
  - لم يُركَّب أي قرص.
- **العقود:** لا تغيير في `b2c_contracts`، فلم يُرفع `SCHEMA_VERSION`. الحقول المستعملة كلها موجودة: `pause_after_ms` و`boundary_after` و`break_after`.

## 1. ما نُفّذ

| البند | التنفيذ |
|---|---|
| ترميز MP3 | `b2c_speech/audio/mp3.py` (`encode_mp3` و`Mp3Settings` و`mp3_frame_probe`) و`b2c_speech/audio/resample.py` |
| وسوم ID3v2.3 | `write_id3v23` و`read_id3` و`Id3Tags` في الملف نفسه؛ و`episode_tags(..., artist=)` |
| حلقة كاملة | `b2c_speech/delivery.py`: ‏`encode_episode` (تطبيع ← فحص ← ترميز ← وسوم ← تقرير) |
| قص الصمت | `b2c_speech/audio/trim.py` (`trim_edges` و`assemble_with_pauses`)، وموصول في `speech_v2_assemble_track` |
| حد المسار | `b2c_speech/plan.py`: ‏`max_segments_per_track=None`، و`max_track_seconds=1800`، و`speech-plan-2` |
| ذاكرة محدودة | BS.1770 وtrue peak على كتل، وفحوص QA بلا نسخ كاملة، وكتابة WAV على كتل، وتطبيع في المكان أثناء التجميع |
| النسخ إلى الكرت | `scripts/copy-course-to-card.py` |

### 1.1 إعدادات الترميز ومصادرها (قاعدة 10، قُرئت في 2026-10-25)

- **أحادي، MPEG-1 Layer III، ‏44.1kHz، ‏CBR ‏96kbps افتراضيًا، و64 مسموح:**
  - صفحة Apple ‏[Audio requirements](https://podcasters.apple.com/support/audio-requirements): الـ MP3 الأحادي حده الأدنى 32kbps، والموصى به **96–128kbps**، والمعدل 44.1/48kHz. ولخلاصات RSS المدى الأحادي 64–128kbps.
  - لماذا 44.1kHz لا 24kHz: ‏22.05 و24kHz هما MPEG-2 LSF، وليس مؤكدًا أن كل مشغل قديم يفكهما. فيُعاد أخذ العينات (polyphase من scipy) على كتل.
- **الجهارة:**
  - Apple: ‏«‎-16 dB LKFS, with a +/- 1 dB tolerance»، وtrue peak «doesn't exceed -1 dB FS»، والقياس بـ BS.1770، والتطبيع «before the encoding process».
  - طُبّق ذلك بملف `app-speech-16` الموجود، مع هامش 0.5 dB إضافي لتجاوز المرمّز.
  - **تحفظ موثَّق:**
    - [AES TD1008](https://productionadvice.co.uk/td1008/) يجعل ‎-18 LUFS مرجع الكلام.
    - [Podnews](https://podnews.net/article/lufs-lkfs-for-podcasters) يقول إن الملف الأحادي عند ‎-19 LUFS يُسمع كالستيريو عند ‎-16.
    - القياس هنا BS.1770 على الإشارة الأحادية (وزن القناة 1). الاختيار النهائي يبقى قرار المالك المعلّق Q2.
- **ID3v2.3 لا 2.4:**
  - [Philips](https://www.usa.philips.com/c-f/XC000010888/what-versions-of-id3-are-supported-by-my-philips-player): ‏«ID3 version 2 tags with unsynchronization scheme or UTF8 encoding will not be supported».
  - منتدى Mp3tag: كثير من المشغلات لا يفهم 2.4.
- **الترميز النصي:**
  - [id3.org/id3v2.3.0](https://id3.org/id3v2.3.0) §3.3: ‏«$01» = Unicode، و«Unicode strings must begin with the Unicode BOM».
  - TRCK: ‏«…may be extended with a "/" character and … the total number of tracks» (مثال «4/9»).
- **المكتبات (قاعدة 9):**
  - `lameenc==1.8.4` (LAME مضمّن، رُفع 2026-06-27). استُبعد 1.8.5 و1.9.0 لأن عمرهما أقل من أسبوعين.
  - `mutagen==1.48.1` (رُفع 2026-06-25).
  - كلاهما مسجّل في `docs/04-OPERATIONS.md` §5 وفي uv.lock (بقي `revision = 3`).
  - لم يُستعمل ffmpeg: صورة العامل لا تحويه، وإضافته تستلزم إعادة بناء.

## 2. الأدلة (أوامر فعلية)

### 2.1 حلقة تجريبية ← MP3 ← قراءة (نغمات + صمت حواف 0.3/0.5 ث)
`uv run python scratchpad/demo_course.py …/demo/course` (سكربت في الـ scratchpad). سطر واحد من ثلاثة:

```
{"file": "01 - الفصل الأول كيف نفكر.mp3", "tags": {"version": "2.3.0", "TIT2": "01 - الفصل الأول: كيف نفكر؟",
 "TALB": "سيكولوجية تحليل الاستخبارات", "TRCK": "2/3", "TPE1": "ريتشاردز هوير", "APIC": null}, "header": "4944330300",
 "mpeg": {"rate": 44100, "channels": 1, "bitrate": 96000, "length_s": 7.76},
 "frames": {"frames": 297, "trailing_bytes": 0, "bitrates_kbps": [96], "sample_rates": [44100],
            "channel_modes": ["mono"], "versions": ["MPEG-1"], "layers": [3]},
 "report": {"integrated_lufs": -16.0, "true_peak_dbtp": -8.38, "gain_db": 17.635, "passed": true}, "gaps_ms": [450, 1200]}
```

- الرأس `49 44 33 03 00` = ‏ID3v2.3.0.
- الوقفات المسموعة (450 و1200ms) = المخططة.
- الـ CBR مُثبت على رأس كل frame. السبب: lameenc لا يكتب frame ‏Info/Xing، فيقرأ mutagen وضع المعدل `UNKNOWN`.

### 2.2 النسخ إلى صورة FAT (mtools، بلا تركيب)

```
$ python scripts/copy-course-to-card.py …/demo/course --image …/demo/card.img
[1/3] 00 - مقدمة الكورس.mp3
[2/3] 01 - الفصل الأول كيف نفكر.mp3
[3/3] 02 - الفصل الثاني.mp3
OK: 3 files to …/card.img (FAT image, mtools) (fat); entry order verified
$ mdir -i …/card.img ::/
00-___~1 MP3     93290 2026-10-25  20:19  00 - مقدمة الكورس.mp3
01-___~1 MP3     93310 2026-10-25  20:19  01 - الفصل الأول كيف نفكر.mp3
02-___~1 MP3     93290 2026-10-25  20:19  02 - الفصل الثاني.mp3
```

- `--ascii-only` أنتج `::/00.mp3` و`::/01.mp3` و`::/02.mp3` بالترتيب.
- النسخ إلى مجلد على tmpfs طبع: «WARNING: destination file system is tmpfs, not FAT/exFAT … not checked»، ثم «OK … entry order not checked».
- الاختبار `test_verification_detects_a_card_written_out_of_order` يكتب الملفات بترتيب معكوس، و`verify_order` يكشف ذلك عند الموضع 0.

**ملاحظة:** الاسم القصير 8.3 للأسماء العربية يصير `00-___~1.MP3`، والرقم في أوله محفوظ. المشغل الذي لا يعرض إلا الأسماء القصيرة سيعرض هذا الاسم، فيكون `--ascii-only` أوضح له.

### 2.3 قياس الذاكرة لتحديد حد المسار (البند 5)

- **الأداة:** `scratchpad/mem_assemble.py N`، وهي محاكاة لنشاط التجميع بمقاطع 6 ث وحواف صامتة.
- **ما تشمله:** مقاطع المصدر تبقى حية لأن النشاط يحتفظ بها لـ ASR. ثم القص والوقفات، وBS.1770، وtrue peak، وQA، وكتابة WAV.
- **التشغيل:** ‏`nice -n 15`.

| مدة المسار | ذروة RSS قبل تحسين الذاكرة | بعده |
|---:|---:|---:|
| 15 د | 1127 MiB | — |
| 30 د | — | **1160 MiB** |
| 45 د | — | **1657 MiB** |
| 60 د | 4094 MiB | **2157 MiB** |
| 60 د + ترميز MP3 للحلقة | 4754 MiB | 2816 MiB (224 ث، 43,234,086 بايت ≈ 96.07kbps) |

**الاستنتاج:**
- حد `mem_limit` لعامل الصوت 2g، فمسار الساعة (الافتراضي القديم 3600 ث) لا يتسع له.
- قبل التحسين كانت x8 oversampling لـ true peak وحدها تحتاج نحو 5.5 GB لساعة.
- الحد الجديد: **مدة فقط، 1800 ث افتراضيًا** (≈ 1.16 GiB، مع هامش لنموذج ASR). لا يُرفع إلا مع رفع الذاكرة.
- عدد المقاطع لم يعد حدًا. السبب: 400 مقطع بالتقطيع الجديد (53 حرفًا وسيطًا، نحو 6.4 ث مع الوقفة) قطعت المسار عند نحو 43 دقيقة.
- الاختبار يثبت الحالتين: بحد ساعة صريح يصل كل مسار غير أخير إلى أكثر من 3585 ث، وبالحد القديم (400) لا يتجاوز أي مسار 0.75 ساعة.

## 3. الاختبارات (كُتبت أولًا وفشلت)

- **commit الاختبارات أولًا:** `tests: learner MP3 delivery … (failing first)`.
  - فشل الجمع بـ `ModuleNotFoundError: b2c_speech.audio.trim`.
  - وفي `test_speech_v2` رُفض المسار ذو الحواف الصامتة (`rejected`)، لأن الصمت الداخلي = ذيل + وقفة + بداية > 1.5 ث. هذا بالضبط ما يصلحه القص.
- **المعرّفات:** في `docs/03-TESTING.md`، وهي T-AUD-07 (محدَّث) وT-AUD-08..13.

| الملف | اختبارات |
|---|---:|
| `tests/unit/speech/test_audio_trim.py` | 7 |
| `tests/unit/speech/test_mp3_delivery.py` | 21 |
| `tests/unit/speech/test_episode_mp3_e2e.py` | 6 |
| `tests/unit/speech/test_copy_course_to_card.py` | 18 (4 منها على صورة FAT، وتُتخطى بلا mtools) |
| `tests/unit/speech/test_speech_plan.py` | +3 |
| `tests/unit/speech/test_audio_qa.py` | +16 (تكافؤ BS.1770 المتدفق، 5 معدلات × 3 أحجام كتل + إشارة قصيرة) |
| `tests/unit/orchestration/test_speech_v2.py` | +1 |
| **المجموع الجديد** | **72** |

- **الحزمة المعنية:** `pytest tests/unit/speech tests/unit/orchestration/test_speech_v2.py` = **345 passed، 0 skipped** (مع `B2C_MTOOLS_BIN`).
- **`make check`:** في §5.

## 4. ما لم يُنفذ أو بقي مفتوحًا (UNVERIFIED)

1. **لا صوت حقيقي ولا مشغل حقيقي:**
   - لم يُسمع ملف، ولم يُجرَّب على مشغل MP3 رخيص.
   - قراءة الوسوم العربية UTF-16 على تلك الأجهزة مبنية على المواصفة وعلى المصادر أعلاه، لا على تجربة.
   - لا تُكتب وسوم ID3v1، لأنها لا تحمل العربية.
2. **الجهارة بعد الترميز لم تُقس:** لا يوجد مفكّك MP3 مثبت. القياس قبل الترميز، وهامش 0.5 dB لتجاوز المرمّز مبني على تقدير.
3. **‎-16 مقابل ‎-19 LUFS للأحادي:** قرار المالك Q2.
4. **ترميز الحلقة غير موصول بـ workflow:**
   - `encode_episode` يأخذ PCM واحدًا، والحلقة (الدرس) قد تمتد على عدة مسارات.
   - ترميز حلقة ساعة قيس بذروة 2.75 GiB.
   - يلزم قبل ربطه بعامل الصوت:
     - تجميع متدفق من مسارات WAV المقبولة؛
     - artifact ‏`episode-mp3` ببوابة؛
     - وصل مع حدود الدروس.
5. **صور الحاويات لم يُعد بناؤها:** العامل الجاري لا يحمل lameenc وmutagen، ولا كود `trim` الجديد. `SPEECH_TRACK_VERSION=2` و`speech-plan-2` يعنيان أن المسارات والخطط السابقة لن تُستعمل من الـ cache. لا توجد مسارات v2 منتجة حتى الآن (التقرير السابق: «لا صوت»).
6. **توزيع الوقفات على صوت XTTS/Fasih حقيقي:**
   - القص مُختبر على إشارات مولّدة، وحارس 40ms مأخوذ من المقترح.
   - يبقى قياسه من `segments.json` (الحقول `removed_*_ms` و`kept_*_ms` و`pause_inserted_ms`) بعد أول تشغيل صوتي.
7. **mtools غير مثبت على المضيف:**
   - نُزّل `mtools_4.0.49-1` وفُك في الـ scratchpad فقط، بلا root وبلا تثبيت.
   - في CI تُتخطى 4 اختبارات صورة FAT ما لم يُضبط `B2C_MTOOLS_BIN`.
   - `fatsort` لم يُختبر، والسكربت يوصي به فقط.
8. **الكروت الحقيقية:**
   - التحقق من الترتيب على كرت مركّب يعتمد على أن `readdir` على vfat يعيد ترتيب المدخلات الفعلي. هذا سلوك نواة Linux المعروف، ولم يُجرَّب هنا على قرص.
   - على WSL، الكرت المركّب عبر drvfs يظهر `9p`، فيحذّر السكربت ولا يدّعي التحقق.
9. **رقم القرار:** ‏D-040 مستعمل على hardening، وD-041 على فرع آخر لم يُدمج، فاستُعمل D-042.

## 5. نتيجة `make check`

الأمر: `B2C_MTOOLS_BIN=… nice -n 15 make check` (exit 0، بعد الـ rebase على hardening ‏805f31f):
- ruff: ‏`All checks passed!`
- import-linter: ‏`Contracts: 9 kept, 0 broken.`
- contract: ‏**133 passed**.
- unit: ‏**2300 passed**، و0 failed، و0 skipped، في 272.5 ث. التحذير الوحيد `StarletteDeprecationWarning` من fastapi، ولا علاقة له بهذا العمل.
