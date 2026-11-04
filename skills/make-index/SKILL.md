---
name: make-index
description: Index files the student added to their class library, so later documents can find things without rereading them. Use when a request names library files to index.
---

# Indexing library files

The tools are `mcp__math-worksheet__library_*`. Load them with ToolSearch if they aren't listed. The student never sees this index. It exists so that later work costs fewer tokens, so keep it short and spend as little as you can making it.

Each file's text is already saved and searchable, so the index only has to tell a future you where to look.

## For each file

1. Call `library_pending` with the file ids to see each file's size and any pictures to read.
2. Call `library_read` with `outline: true`. This gives the first line of every page, which on slides is usually the title, and it's often enough on its own.
3. Read only what you need to name the topics correctly. Use `library_read` on the table of contents, the first few pages and any pages the outline leaves unclear. Don't read everything, and for a long file such as a textbook, rely on the outline and a few samples.
4. Call `library_save_index` with the following:
   - `summary`: one or two sentences on what the file is and what it covers.
   - `topics`: `{"topic": "...", "pages": "12-18"}` entries, using the terms the class uses. Prefer about 5 to 25 for a typical file, with enough detail to find things.
   - `descriptions`: see below.
   - `request_id`, if the request names one.

## Pages that are pictures

`pictures_to_read` lists pages whose words couldn't be extracted, like scans, handwriting or image-only slides. Each has to be read once, and then never again:

- Open the page with the Read tool at `path_for_read_tool`, using `pages` for a PDF.
- Pass `{"page": 7, "text": "..."}` in `descriptions`. Write out any handwriting and equations word for word in LaTeX where you can, and describe any figures. Mark anything you can't read as `[unclear]` rather than guessing, since later documents will rely on this.

These pages cost far more tokens than text. **If a file has more than 30 such pages, ask the student first**, and tell them how many there are. Pass `descriptions` in batches of up to 20 pages, calling `library_save_index` after each batch, so an interruption doesn't lose work. If a page is just a photo or video still with nothing to learn from, say that in a few words.

## Finish

Tell the student in a line or two what was indexed, how many picture pages were read, and anything you couldn't read.

## Never

Never commit or push with git for the student.
