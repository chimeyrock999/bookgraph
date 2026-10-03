# Daily reading memory for AI agents

## Problem

Long books and technical documents do not fit in one useful agent context. A generic
retrieval flow can answer from a few matching chunks, but it does not know what the
agent has actually read, what remains unread, which concepts were verified during a
reading session, or whether a translation/export is complete.

A daily reading agent needs durable state:

- where it stopped;
- what sections it has read;
- what it understood and summarized;
- which concepts were verified by reading versus merely found by search;
- where a concept appears in unread sections;
- which sections have fresh translations;
- which figures/tables/assets were inspected;
- what can be exported as a reader-facing edition.

Without that state, the agent either reloads too much source text, forgets previous
sessions, or gives overconfident answers about parts of the book it has not read.

## Idea

BookGraph should be a **daily reading memory system for AI agents**.

The workflow:

1. Prepare a book once: parse, segment, index, and create a reading plan.
2. Each day, the agent reads the next section batch with the user.
3. The agent enriches the workspace with summaries, verified concepts, notes, asset
   inspection status, and optional translations.
4. Later, when the user asks about a concept, the agent answers from accumulated
   source-grounded memory and points to unread leads when the book has not been fully
   covered.
5. The same workspace can render durable outputs: concept pages, a linked wiki,
   translated sections, bilingual PDFs, EPUBs, and export reports.

Whole-book indexing gives the agent a map. Daily reading turns that map into verified
memory.

## Product story

BookGraph lets an AI agent read a long book with you over time:

- **Today:** "Read the next part with me."
- **During reading:** the agent summarizes sections, records concepts, answers local
  questions, checks figures/tables, and optionally translates the section.
- **Tomorrow:** the agent resumes from the exact next unread section.
- **Later:** when asked about a concept, it synthesizes notes from the sections already
  read, cites source sections/pages, and lists unread sections where the concept likely
  appears.
- **When ready:** the workspace exports a translated or bilingual reading edition.

This is different from a one-shot PDF chat tool. The goal is not to pretend the whole
book is in context. The goal is to build a durable memory of what the agent has read
and improve that memory every day.

## Core distinction: map vs memory vs answer

| Layer | Source | Purpose | Trust level |
| --- | --- | --- | --- |
| Map | parsed sections, outline, FTS index, deterministic concepts | Find where things may be | Candidate evidence |
| Memory | agent summaries, verified concepts, annotations, translations | Remember what was actually read | Verified by reading |
| Answer | MCP retrieval + memory synthesis | Explain concepts and cite sources | Must label coverage |

Important rule: candidate hits from unread sections should not be mixed silently with
verified reading memory. They are leads, not proof that the agent has understood that
part of the book.

## Target user workflows

### 1. Prepare a reading workspace

```bash
bookgraph init .bookgraph
bookgraph parse book.md -o .bookgraph
bookgraph segment .bookgraph book
bookgraph index build .bookgraph book
bookgraph reading-plan create .bookgraph book --plan-id main
bookgraph mcp .bookgraph
```

Future convenience wrapper:

```bash
bookgraph prepare .bookgraph book.md --plan-id main
```

### 2. Daily co-reading loop

MCP flow:

```text
get_today(plan_id)
get_next_section(plan_id)
get_context(doc_id, section_id)
annotate_section(doc_id, section_id, summary=..., concepts=[...])
write_section_translation(...)       # optional
validate_reading_batch(plan_id, ...)
complete_reading_batch(plan_id, ...)
```

The agent should end each session with durable writes, not just a chat summary.

### 3. Concept question after multiple days

User asks:

```text
What does the book mean by "log-structured merge tree"?
```

The agent should:

1. call concept recall;
2. prefer verified mentions from read sections;
3. synthesize summaries/glosses across sections;
4. cite sections/pages;
5. report unread candidate mentions separately;
6. offer to read the most relevant unread section next.

Desired answer shape:

```text
From the sections read so far, LSM trees are...

Evidence:
- Chapter 3 / section ...: ...
- Chapter 4 / section ...: ...

Coverage:
- 3 verified read mentions
- 1 read but unverified mention
- 5 unread candidate mentions in later chapters

I can read the strongest unread lead next if you want the full-book view.
```

### 4. Translation and export

The agent can translate as it reads, cache translations by section, and later export:

```bash
bookgraph export translated-pdf .bookgraph book --lang vi
bookgraph export translated-pdf .bookgraph book --lang vi --mode bilingual
bookgraph export translated-pdf .bookgraph book --lang vi --out exports/book.vi.epub
```

Export should use freshness/coverage reports so the user knows which sections are
missing, stale, or missing assets.

## Current strengths

BookGraph already has several right primitives:

- workspace artifacts instead of prompt-only state;
- parser and segmenter stages;
- reading plans;
- MCP reading/query tools;
- annotation files;
- deterministic concept extraction and concept pages;
- SQLite search/graph index;
- translation cache and structure checks;
- bilingual/translated export pipeline;
- asset warnings and repair paths;
- optional heavy integrations behind adapters.

These pieces support the idea. The missing work is mostly product spine and memory
semantics.

## Gaps

### 1. Memory is not first-class enough

Reading memory is currently distributed across reading plans, annotations, indexes,
translations, and exports. Users and agents need a single status surface.

Needed:

```text
get_memory_status(doc_id or plan_id)
bookgraph memory status <workspace> <doc_id>
```

Should report:

- read sections vs total;
- annotated sections vs total;
- stale/missing annotations;
- verified concepts count;
- candidate concepts count;
- translation coverage by language;
- stale translations;
- export readiness.

### 2. Concept recall needs verified/read/unread split

The concept surface should distinguish:

- `verified_mentions`: agent annotated the concept after reading;
- `read_candidates`: section was read but concept was only found by index/search;
- `unread_candidates`: section is not read yet but likely contains the concept;
- `coverage`: counts and recommended next sections.

This is the most important feature for honest concept answers.

### 3. Daily loop needs a product-level tool

Current tools expose pieces. Add a daily entry point:

```text
get_today(plan_id)
bookgraph today <workspace> <plan_id>
```

Should return:

- next section batch;
- current chapter progress;
- boundary info;
- warnings/assets to inspect;
- stale memory/translation issues;
- suggested concepts to watch;
- export/translation reminders if configured.

### 4. Onboarding is too staged for first use

Staged commands are good for correctness, but adoption needs a short demo path.

Needed:

```bash
bookgraph demo .demo
```

Demo should use a bundled Markdown/public-domain sample, no MinerU required. It should
produce a workspace, sections, index, reading plan, and next-step instructions for MCP.

### 5. Product docs need an example transcript

README should show the loop, not only list capabilities:

- Day 1: read section batch;
- Day 3: ask concept question;
- Day 5: translate sections;
- Later: export bilingual edition.

A transcript makes the product story concrete.

## Proposed roadmap

### Phase 1: make the story visible

- Add `docs/design/daily-reading-memory.md`.
- Add README example transcript for daily reading and concept recall.
- Add `examples/mini-book.md`.
- Add `bookgraph demo <workspace>`.
- Add MCP client snippets for common clients.

### Phase 2: make memory explicit

- Add `get_memory_status` MCP tool.
- Add `bookgraph memory status` CLI command.
- Add read/verified/unread split to concept recall.
- Add coverage fields to concept responses.
- Update `bookgraph-reader` skill to prefer verified memory and label unread leads.

### Phase 3: make daily reading smooth

- Add `get_today(plan_id)` MCP tool.
- Add `bookgraph today <workspace> <plan_id>`.
- Add `bookgraph prepare` convenience command for parse → segment → index → plan.
- Add friendlier wrapper around batch validation/completion for daily reading.

### Phase 4: make outputs feel real

- Add export readiness summary to memory status.
- Add translation progress by language.
- Add example bilingual/translated export output.
- Add a small terminal screenshot/GIF or copied transcript.

## Non-goals

- Do not become a generic vector database.
- Do not pretend unread sections are understood.
- Do not hide provenance behind generated prose.
- Do not make heavy PDF/OCR tools mandatory for the core demo.
- Do not replace staged commands; add friendly wrappers on top.

## Success criteria

A new user should understand this in under one minute:

```text
My agent can read a long book over days.
BookGraph remembers what it actually read.
When I ask about a concept, it answers from verified sections and shows unread leads.
If I translate while reading, BookGraph can export a translated or bilingual edition.
```

A working demo should prove it in under five minutes without requiring MinerU.
