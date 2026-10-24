# Audio monitor evidence 2026-10-10

Workflow b2c-mp3-7cf28e504c8f0b593327dedc1b90d33a66e3a78e779ca09219cba34f7e90628e is RUNNING. Source Arabic translation SHA b058582310584fb588fa6beb50de6cc64fbffbe7826ddd1e568b0283276e6c76 has 776 segments, 639097 characters. Full_book receives 1125 pieces and has no durable per-piece checkpoint. It is not lesson/appendix output.

12 temporary WAVs total 128.816 seconds at audit. First title: الطبعة الثالثة. PCM16 mono 24kHz, 1.802667s, 86572 bytes; ffmpeg decode passes. SHA 336c4794087f49eb6fe4177fb674c08e1c6ebc9b7dfc3dead746f087e734f5aa. Tiny ASR gives الطبعة الساريسة, CER 4/13=30.77%; not a calibrated TTS quality verdict.

Reference has 7 English-dominant complete segments and 19 long English-dominant lines, 15145 characters=2.37%. Most are instructional paragraphs rather than proper names. Complete segments: book-main:166:0,168:0,226:0,227:0,228:0,229:0,591:1. Additional mixed segments:3:0,170:0,235:0,241:0,289:1,365:0,555:0,556:0,561:0,636:0,643:0.

Piece11 (575 English characters from book-main:3:0) generated a44.757333s WAV. Auto-detected ASR en probability0.9552 transcribes English. Therefore audio is genuinely mixed-language despite Arabic target metadata.

WAV artifacts retained in eval/reports/audio-audit-2026-10-10/. No workflow or unrelated containers modified. Final MP3 and complete-book alignment pending.
