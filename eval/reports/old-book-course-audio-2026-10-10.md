# Old book course audio — 2026-10-10

B2C only. Source identity book-aaea5c4b55e6a7519bdea580 (PDF SHA aaea5c4b55e6a7519bdea580fcab7075b0778da690bbe3534e97279a5c447e01).

The five immutable artifacts were independently read and every named file content and aggregate SHA verified: parsed source e40a641e / SHA719c0009, QA approval8f187c78 / SHA7834da22, QA38d9a570 / SHAf626ae40, appendix translationd6bff832 / SHA4add240b, body translationf6527650 / SHA2a448c5f. Outline JSON in approval8f187 and8a4a is identical. Translation-source refs match original source exactly.

Request selects75 lessons from author Preface m07-pr-ef-a-c-e.l01 onward. Scope audit finds62 final lesson QA scopes and13 pending/partial lesson attachments. All selected final items carry final status and all automated checks true. Their translated question/answer IDs are present and Arabic;888 translation units overall means444 questions and444 answers. Original QA remains failed, and is not promoted to final.

Audio order is original translated lesson text, then its Arabic questions/answers, then next lesson. No summaries or group/course extras. Missing/partial attachments are explicitly pending in manifest and incremental progress, never empty tracks or claimed complete. Each complete MP3 is stored immediately through immutable per-track cache; repeat invocation reuses it. Attachment cache keys distinguish inclusion of summaries.

Legacy QA has no inline source hashes. The gate accepts it only through the migration registry with exact QA ArtifactRef, approval ArtifactRef, parsed SHA, PDF SHA, and migration identity. Audio workflow and activity use isolated audio queue. Generation workers are unchanged.

Focused checks:7 orchestration audio tests passed (partial acceptance explicit, ordering, pending coverage, no summary, cache reuse, exact legacy source record rejection, language gate).3 identity/progress API tests passed in joint run before final legacy-specific test.

Runtime rollout and first actual output audit: pending completion below.


Runtime rollout: API image sha256:c955f4b3edf0b647ed81277db73c2981a94a9e074d9ed04b4e1b7d6e201d424e and audio-worker image rebuilt. Only API/audio-worker recreated. Fasih was stopped and renderer previously had no reachable Arabic backend despite renderer health ok. Started Fasih from existing mounted weights, waited until actual provider health returned ok/Fasih-TTS-V1/cpu, then revalidated lineage in newly deployed code (LINEAGE_PASS) and queued HTTP202.

New execution b2c-mp3-57f9ded6d7b8eea8843b56866c13639d157b3bedcbb885e4301f3503e2f188a3 is RUNNING on isolated audio queue, planned137 tracks (75 body+62 final QA). Live old-book stable progress contains this audio workflow and exact parsed SHA719c/PDFaaea. Historical full-book execution7cf28 was already TERMINATED at2026-10-09 23:58:27UTC; this task sent no termination/cancel to it or any other workflow.

First body is author Preface pages30–41,10433characters split into17renderer pieces. Initial two temporary WAVs are headings “مقدمة” and “الأصل والغرض”; they are preliminary pieces and are not claimed as complete lesson outputs. First final MP3 pair audit is active through scripts/monitor-course-audio-first-pair.py with exact source-text comparison, source identity validation and output hashes. Decoded audio/language audit awaits final tracks. Current pending execution is not a claim of completed audio production.


Preliminary actual speech: renderer completed the first substantial Preface paragraph (713characters), WAV80.557333seconds/3,866,796bytes. ffprobe validates its format and duration. Cached local faster-whisper-tiny auto-detects Arabic with probability0.995695 and transcribes the same opening subject as the translated source (“لجان التحقيق التي تلت الهجمات الإرهابية…”). Expected text, actual WAV and ASR JSON saved under eval/reports/old-book-audio-audit-2026-10-10. Tiny ASR is used for language/content-contamination screening, not a calibrated TTS fidelity score. Complete first-lesson MP3 and QA attachment remain pending; no claim that those files have completed yet.


## Renderer and Fasih runtime repairs (17:11 UTC)
- Actual attempt 1 failed at CATT positional encoding: normalized input 1631 character tokens exceeded model limit 1024.
- Renderer now normalizes/expands numbers before Arabic segmentation at 500 characters, then joins WAV pieces in order. Persistent immutable WAV cache avoids redoing successful subsegments.
- Focused renderer + Fasih serialization checks: 4 passed. An isolated fake-provider smoke exercised actual CATT tokenizer and real ffmpeg: 10433-char lesson became 22 pieces, maximum494 tokens, HTTP200, valid MP3 (smoke only, not course output).
- py-spy stack dump proved two simultaneous inference threads: old XTTS decoding plus current CATT. Only one renderer TCP connection remained. Old disconnected HTTP synthesis continued running; XTTS mutates shared prefix state.
- Added model serialization lock to batch and streaming inference. Rebuilt Fasih server layer only and recreated Fasih alone; API, audio-worker and QA workers were untouched.
- Owner/parent approved 4 CPU threads based on existing paired benchmark (37.681s at4 vs104.288s at28 for same audio11.648s).
- Two real beginning WAV pieces were saved persistently before Fasih restart (111148,86572 bytes); no final lesson MP3 had been saved yet. They will be reused, not regenerated.
- This is runtime progress, not evidence of a completed lesson MP3 or completed course.


## Sentence-sized CATT inputs (17:18 UTC)
- Changed Arabic segments to <=160 normalized/number-expanded characters, preferring sentence punctuation or newlines and retaining every character in original order.
- Five focused tests passed, including sentence-boundary preference, exact reassembly, number expansion and immutable WAV reuse.
- Renderer alone recreated; Fasih and audio-worker left running.
- Cache key version remains compatible for identical text. The two short heading WAVs are reused; previous500-character pieces do not match the new160-character texts and are not falsely reused.
- Actual endpoint encoding smoke used only the two previously generated heading WAVs (no new synthesis): HTTP200, MP3 bytes50156, duration4.176s, ffmpeg full decode exit0. Saved real-cached-heading-smoke.mp3 is explicitly not a lesson or final course output.
- The prior500-character call continued after renderer restart. Serialization prevents overlap; profiler confirmed it subsequently moved from CATT to XTTS generation.


## Live attempt11 evidence (17:28 UTC)
- Second Fasih-only restart17:20UTC was explicitly authorized to remove the old500-character request after renderer segmentation changed. No third restart or any worker restart occurred.
- Fasih healthy again17:22UTC; same workflow attempt11 began17:22:11UTC. Profiler showed one active XTTS inference thread, no stale lock owner.
- Actual first lesson contains83 renderer segments. At17:28UTC six WAV files were cached, including four substantive/new pieces. No per-lesson MP3 saved yet.
- New first substantive160-character WAV:818860 bytes, duration17.0586875s. Offline local ASR-tiny detected Arabic probability0.99724966 and recovered the same intelligence-analysis opening content. ASR is a language/content smoke, not calibrated fidelity or pronunciation acceptance.
- Matching text/hash evidence and actual WAV are saved as first-new-arabic-segment.txt/.wav and first-new-arabic-segment-asr.json.
- Observed CPU wallclock around90 seconds/piece implies first10.4k-character lesson could take1.5–2hours; no truncation or lesson-order changes are made to manufacture earlier completion.


## Contention check17:34UTC (no live settings changed)
- Host56logical CPUs /28physical cores; LLM~18.4 CPUs, Fasih~3.9CPUs.
- CPU pressure avg60~14%; Fasih cgroup nr_throttled=0; availableRAM60GiB,swap0;memoryPSI0,IOPSI nearly0.
- LLM log confirms task2130 finished and task2630 progressed prompt2048tokens at36.6tok/s. Fasih cache increased6→10WAV.
- Both jobs progress. Shared CPU/cache contention may contribute, but no paired idle-vs-concurrent measurement proves its magnitude. Keep both live jobs running; no caps/restarts made for this assessment.
