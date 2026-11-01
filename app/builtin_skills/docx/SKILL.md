---
name: docx
description: Write an editable Word document (proposal, contract draft, cover letter, meeting minutes, SOP) with the create_document tool. Use when the user wants a .docx or a document they will keep editing.
metadata:
  title: Word documents
  icon: file
  version: 7
  triggers: [word document, docx, proposal, cover letter, minutes, contract, sop, letter]
  tools: [skills, documents]
  example: Create a Word file containing a proposal for a 3-month website redesign project, with scope, a timeline table and pricing.
  style:
    accent: "#14633a"
    office_font: "Calibri"
    body_pt: 11
---

# Creating a Word document

**Always create the file with the `create_document` tool (`format: "docx"`). Never paste the whole document into the chat.** After the tool succeeds, reply with one or two sentences and the file name.

Word files are meant to be edited, so keep the structure clean and conventional: real headings, real lists, real tables. Do not simulate layout with spaces or repeated characters.

## Layouts

**Proposal**: `# Title`, `## Background`, `## Scope` (bullets of deliverables), `## Timeline` (table: Phase | Duration | Output), `## Pricing` (table with a Total row), `## Assumptions`, `## Next steps`.

**Letter**: sender block, date, recipient block, "Dear ...", three short paragraphs (purpose, evidence, request), closing, name. Use square-bracket placeholders only for facts you truly do not have, e.g. [Your name].

**Meeting minutes**: `# Meeting title`, bold **Date / Attendees / Location**, `## Agenda`, `## Decisions` (numbered), `## Action items` (table: Action | Owner | Due).

**SOP / how-to**: `# Title`, `## Purpose`, `## Before you start` (checklist), `## Steps` (numbered, one action per step), `## Troubleshooting`.

## Rules
- Use heading levels in order (`##` before `###`). One `#` title.
- Bullets for parallel items, numbers for sequences. Nest at most two levels.
- Tables need a header row; keep cells short.
- Plain, direct language; define abbreviations on first use.
- Diagrams go in ```mermaid blocks (see the Diagrams skill). No raw HTML or pictures from the web. Links appear as text followed by the address.
- After creating the file, tell the user its name and that it opens in Word, LibreOffice or Google Docs.

## Changing a file you already made
For a correction or addition, **edit the existing file instead of creating a new one**: call `edit_document` once with ALL the changes in one instruction (for example "make the introduction shorter and add a section about cost savings after Benefits"). It carries out every change and reports what it did. To add pictures call `illustrate_document` with the number wanted. Never say something was changed unless the tool reported it. The person can also press **Edit** on the file.

## Look and feel
The palette, fonts and sizes of the finished file come from the `style` block in this file's header, not from the content. To change how these files look, edit that block (colours as `#rrggbb`; leave a line out to use the built-in value). Never put colours, fonts or sizes in the content you write.
