# Course start and question scope for the next book

Owner request recorded 2026-10-09. Apply this only to a new course after the current book's translation and audio run has finished. Do not mutate the active `b2c-demo` outline, QA artifact, translation workflows, or running worker.

## Required behavior

- Start the course at the book's actual instructional introduction or first substantive chapter. Do not make publisher/editor forewords, publisher notes, copyright pages, contents, acknowledgments, dedication, author/publisher pages, title pages, or other clearly non-instructional front matter into lessons.
- Treat headings as outline/navigation metadata. A heading may label a lesson, but a title-only or heading-only span is never a lesson; its substantive body must define the lesson content.
- Keep the original parsed PDF and provenance unchanged. Record any excluded leading blocks by source block ID and the rule that excluded them; exclusions are non-destructive course-assembly decisions.
- Generate questions from the lesson's instructional body and learning concepts. Reject questions asking for a book, chapter, section, module, or lesson title/name/heading. Questions about the underlying topic remain valid even when the topic is also named by a heading.
- Apply the same rules to English and Arabic, and keep a conservative fallback: ambiguous front matter is not silently discarded; flag it for outline review.

## Implementation sequence

1. Add a versioned front-matter classifier for explicit headings in English and Arabic; identify only a contiguous leading prefix and preserve all source IDs.
2. Build the outline from the first substantive introduction/chapter, merge heading-only fragments into adjacent substantive spans, and update coverage/system review so only explicitly excluded prefix blocks are omitted from course coverage.
3. Strengthen question-generation prompts and deterministic validation for title/meta questions. Keep evidence quotes attached to actual lesson body chunks; a lesson title is context, never evidence.
4. Add regressions for publisher foreword followed by a real Introduction, heading-only fragments, Arabic front matter, legitimate topic questions, and rejected title questions.
5. Validate against the next book's parsed block order and preview the outline before approval. Deploy only after the current book's active work is complete, then use this policy for the next book.

## Activation gate

Source changes and tests may be prepared now. Do not rebuild/restart the active translation/generation worker or regenerate the current book's outline/QA. Activate the new outline/question policy only after current translation and audio finish and before processing the next book.
