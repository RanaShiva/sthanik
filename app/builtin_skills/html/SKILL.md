---
name: html
description: "Make a web page / HTML file (article, report, handout, one-page summary, simple landing page) with the create_document tool. Use when the user asks for HTML, a web page or a page they can open in a browser."
metadata:
  title: HTML pages
  icon: globe
  version: 2
  triggers: [html, html page, html file, web page, webpage, landing page, web document]
  tools: [skills, documents]
  example: Make an HTML page about the history of Dwarka with sections, a short timeline table and two pictures.
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

# Creating an HTML page

**Always create the file with the `create_document` tool (`format: "html"`). Never paste the page into the chat.** After the tool succeeds, reply with one or two sentences and the file name.

You write Markdown and the app turns it into one clean, self-contained, styled HTML file (it opens in any browser, works offline, prints well). So write real structure, not HTML tags: raw HTML is not allowed.

## Layout
- `# Title` once, then a short opening paragraph that says what the page is.
- `##` sections with a clear name each; `###` for sub-parts. Keep sections short (2-4 short paragraphs or a list).
- A table for comparisons or timelines (header row, short cells). Bullets for parallel items, numbers for steps.
- Pictures: `![caption](img:NAME)` from `find_image` (real photos, credited) or `generate_image` (illustrations). One per main section at most.
- Diagrams go in ```mermaid blocks (see the Diagrams skill).
- Links are written as text followed by the address.

## Rules
- Specific, concrete content; no filler sections such as "Introduction to the topic".
- Do not invent facts, dates or numbers. When you rely on web results, say where they came from.
- End with a short "Sources" or "Next steps" section only when it has real content.

## Changing a file you already made
Edit it with `edit_document` (one call, all changes in one instruction) instead of creating a new file. Never say something was changed unless the tool reported it.

## Look and feel
The palette, fonts and sizes of the finished file come from the `style` block in this file's header, not from the content. To change how these files look, edit that block (colours as `#rrggbb`; leave a line out to use the built-in value). Never put colours, fonts or sizes in the content you write.
