---
name: document-editing
description: "Change a document that already exists: edit text, clean it up, add, replace or remove pictures, find real photos online. Use for any request to modify, fix, tidy, translate, illustrate or put images into an existing file."
metadata:
  title: Editing documents
  icon: pencil
  version: 1
  triggers: [edit, change, update, shorten, rewrite, translate, tidy, clean up, uniform, consistent, replace, remove, delete, images, pictures, photos, illustrate, from online, find images]
  tools: [documents, images, web]
  example: Remove the current pictures from my document and put real photos from the web in each section, then clean up any leftover placeholder text.
---

# Changing an existing document

**Never create a new file when a document is open.** Change that one. Pick the tool by the job:

| The request | Call |
|---|---|
| change wording, add / remove / reorder sections, translate, shorten | `edit_document` ONCE with every change in one instruction |
| "clean up", "make it uniform", "remove the weird text / placeholders / leftover prompts" | `check_document`, then `tidy_document` |
| add pictures to the whole document | `illustrate_document` with no count (every section gets one) |
| **replace** the pictures / "remove these and put real ones" | `illustrate_document` with `replace: true` (and `source: "web"` for real photos) |
| remove pictures only (all, or one section with `section`) | `remove_pictures_from_document` |
| **one** section's picture ("replace the picture in Place 3") | `illustrate_document` with `replace: true`: it stays inside the sections the user named |
| one specific picture into one section | `find_image` or `generate_image`, then `add_picture_to_document` |
| "the pictures are not good", "check / analyse the images", "find better ones" | `review_document_pictures`: the vision model looks at each picture and swaps weak ones for better web photos. Never judge pictures from their file names or captions |
| "undo that", "go back" | `undo_document_change` |
| what the document contains | `read_document` |

## Real photo or drawn picture?
- **Real, named things** (a place, landmark, building, person, animal, plant, object, map, flag, event) → a **web photo**: `source: "web"` or `find_image`. If the user says "online", "real", "actual" or "photo", always use the web.
- **Ideas, concepts, processes, moods, imagined scenes, one consistent style** → **generated**: `source: "generate"` or `generate_image`.
- Not sure → `source: "auto"`: it chooses per section and falls back to drawing if the web has nothing usable.
- Web pictures keep a "(source: site)" caption. Say where a photo came from.

## Stay inside what was asked
- "The picture in Place 3" or "Place 4 and Place 6" means ONLY those sections. Never remove or redo the other sections' pictures.
- "Remove the images" means remove, nothing else: do not add new ones afterwards.
- Shorten / rewrite requests keep pictures, diagrams and headings. Say if a section could not be changed.

## Do the whole job
- "Add images for each place" means **every** section. Use the whole-document tools above, not one picture at a time (that runs out of steps).
- Read the tool result. If it says something was **NOT done**, a count is short, or a tool failed, fix it or tell the user plainly. Never say "done" for work a tool did not report.
- Numbered sections with gaps (Place 1, 2, 4 …) mean content is missing. **Tell the user and ask what they should contain. Never invent them.** Add them only when the user names them.
- After `tidy_document`, mention what it removed. It never rewrites real content, and the user can undo it from the editor.
