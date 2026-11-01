---
name: pdf
description: Write and export a professional PDF (report, brief, memo, one-pager, letter, handout) with the create_document tool. Use whenever the user asks for a PDF or a printable document.
metadata:
  title: PDF documents
  icon: filetext
  version: 7
  triggers: [pdf, one-pager, handout, printable, report, memo, brief]
  tools: [skills, documents]
  example: Create a one-page PDF brief titled "Why run AI locally" with a short summary, three key benefits and a next-steps section.
  style:
    accent: "#14633a"
    heading: "#0f3d24"
    ink: "#15201a"
    tint: "#e6f0ea"
    dark: "#07100b"
    font: "Noto Sans"
    body_pt: 11
    h1_pt: 25
    h2_pt: 15.5
---

# Creating a PDF

**Always create the file with the `create_document` tool. Never paste the whole document into the chat.**

1. **Decide** the document type and reader (executive, customer, team). Pick the matching layout below.
2. **Outline** the sections in your head, then write the full text as Markdown.
3. **Call `create_document`** with `title`, `content` (the Markdown) and `format: "pdf"`.
4. **Reply** with one or two sentences: what the file contains and its file name. Do not paste the document again.

## Layouts

**Report / brief**: `# Title`, a one-line subtitle, `## Summary` (3 bullets with the conclusions), 2-4 content sections (`##`), a table for any comparison or numbers, `## Next steps` (numbered, each with an owner or date if known), `## Sources` when you used the web.

**Memo**: `# Memo`, then bold labels on separate lines: **To:**, **From:**, **Date:**, **Subject:**, a horizontal rule `---`, then the body in short paragraphs, ending with the decision or action requested.

**One-pager**: title, a 2-sentence purpose, three short `##` sections with 3-4 bullets each, one highlighted number or quote (`> ...`).

## Supported Markdown
Headings `#` to `####`, paragraphs, `-` bullets, `1.` lists (nested with two spaces), pipe tables, **bold**, *italic*, `inline code`, fenced code blocks, `>` quotes, `---` rules, links. Non-Latin scripts (Hindi, Tamil, Arabic, Chinese...) render correctly.
**Diagrams:** a flowchart or other diagram goes in a ```mermaid block (see the Diagrams skill) and is drawn into the file. **Not supported:** raw HTML, footnotes, math, pictures from the web.

## Quality rules
- One `#` heading only (the title). Sections use `##`.
- Paragraphs of at most four sentences; prefer bullets for lists of facts.
- Tables: header row, at most six columns, units in the header ("Cost (USD)").
- Dates as "29 October 2026". No placeholder text such as "Lorem ipsum" or "TBD" unless the user asked for a template.
- Never wrap the content in a code fence and never put the file extension in the title.

## Changing a file you already made
For a correction or addition, **edit the existing file instead of creating a new one**: call `edit_document` once with ALL the changes in one instruction (for example "make the introduction shorter and add a section about cost savings after Benefits"). It carries out every change and reports what it did. To add pictures call `illustrate_document` with the number wanted. Never say something was changed unless the tool reported it. The person can also press **Edit** on the file.

## Look and feel
The palette, fonts and sizes of the finished file come from the `style` block in this file's header, not from the content. To change how these files look, edit that block (colours as `#rrggbb`; leave a line out to use the built-in value). Never put colours, fonts or sizes in the content you write.
