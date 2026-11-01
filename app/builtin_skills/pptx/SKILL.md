---
name: pptx
description: Design and export a slide deck (PowerPoint .pptx or slides PDF) with the create_slides tool. Use for presentations, pitches, lessons, status updates and training decks.
metadata:
  title: Slide decks
  icon: presentation
  version: 7
  triggers: [pptx, powerpoint, slides, slide deck, deck, presentation, pitch]
  tools: [skills, documents]
  example: Make a 7-slide deck introducing local AI to a non-technical team, covering why it matters, benefits, risks, a comparison table and next steps.
  style:
    accent: "#14633a"
    accent2: "#3ddc84"
    ink: "#15201a"
    dark: "#07100b"
    paper: "#f5f8f6"
    tint: "#e6f0ea"
    font: "Noto Sans"
    office_font: "Calibri"
    slide_title_pt: 36
---

# Creating a slide deck

**Always create the deck with the `create_slides` tool. Never paste the slides into the chat.** Call it with `title`, `content` and `format` (`pptx` by default, `pdf` for a shareable copy). The first slide (title + subtitle) is generated for you.

## Content format (exact)
```
# Deck title
One-line subtitle

## Slide title
- Short point
- Another point
  - Sub-point (indent two spaces, one level only)
Notes: what to say out loud (optional)

## Comparison
| Option | Cost | Speed |
|---|---|---|
| A | Low | Fast |
```
Each `##` heading starts a new slide. A slide may hold bullets, one table, or one fenced code block.

## Storyline (6-10 slides unless asked otherwise)
1. Context or problem: why this matters now
2. 2-4 slides of substance, one idea each
3. Evidence: a comparison table or key numbers
4. Risks or open questions
5. Next steps: concrete actions with owners or dates

## Rules
- **One idea per slide.** The title states the takeaway ("Local AI keeps data private"), not a topic ("Privacy").
- 3-5 bullets per slide, each under 12 words. Fragments, not sentences. No paragraphs on slides.
- Tables: at most 5 rows and 4 columns.
- Put detail in `Notes:` lines, not on the slide.
- A process, structure or timeline belongs on its own slide as a ```mermaid diagram (see the Diagrams skill). No emoji walls or raw HTML.
- After creating the file, give the slide count and file name in one sentence.

## Changing a file you already made
For a correction or addition, **edit the existing file instead of creating a new one**: call `edit_document` once with ALL the changes in one instruction (for example "make the introduction shorter and add a section about cost savings after Benefits"). It carries out every change and reports what it did. To add pictures call `illustrate_document` with the number wanted. Never say something was changed unless the tool reported it. The person can also press **Edit** on the file.

## Look and feel
The palette, fonts and sizes of the finished file come from the `style` block in this file's header, not from the content. To change how these files look, edit that block (colours as `#rrggbb`; leave a line out to use the built-in value). Never put colours, fonts or sizes in the content you write.
