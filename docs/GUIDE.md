# Sthanik guide

[← Back to the README](../README.md)

This is the full reference: every page, tool and setting. If you only want to try Sthanik, the README has what you need.

Sthanik (स्थानिक, "local") is a local AI studio built around Liquid AI's **LFM3** models: chat, vision/video, voice,
omni-modal conversation, long-document writing, diagrams, document editing, scheduled tasks and image generation.
Everything runs on your own GPU through [llama.cpp](https://github.com/ggml-org/llama.cpp) (and
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) for pictures).

## Install

Download the file for your system from the **Releases** page:

| System | File | Then |
|---|---|---|
| Windows 10/11 (64-bit) | `Sthanik-<version>-windows-x64-setup.exe` (installer) or `…-windows-x64.zip` (unzip and run `Sthanik.exe`) | Start it from the Start menu. A console window stays open while it runs; closing it quits. |
| macOS (Apple silicon, or Intel) | `Sthanik-<version>-macos-arm64.dmg` / `…-macos-x86_64.dmg` | Drag *Sthanik* to Applications. The app is not signed by Apple, so the first time use **right-click → Open** (or *System Settings → Privacy & Security → Open Anyway*). |
| Linux (x86-64 or arm64) | `Sthanik-<version>-linux-<arch>.tar.gz` | `tar xzf` it, run `./install.sh` (adds it to your applications menu, no root needed), or run `./sthanik/sthanik` directly. |

Every release lists a SHA-256 for each file (`SHA256SUMS`). The Windows and macOS builds are not code-signed, so SmartScreen / Gatekeeper warn on first run.

**First run sets everything up.** Sthanik opens in your browser at `http://127.0.0.1:7860` and, until it has an engine and a chat model, shows **Setup**:

1. It looks at the computer: operating system, processor, memory, graphics card(s) and free disk.
2. It picks the **llama.cpp engine** build that suits it: the CUDA build for an NVIDIA card whose driver supports it, Vulkan for AMD, Intel and older NVIDIA cards, Metal on Apple silicon, the plain CPU build otherwise. Each build is checked against the SHA-256 recorded for the pinned llama.cpp release, then asked which devices it can use (`llama-server --list-devices`); if a GPU build finds no usable GPU (a missing driver, say) the next one is tried, ending on the CPU build, so a wrong guess costs a download, not a broken install.
3. It picks the **precision** of the model files from the memory they will run in: BF16 (what Liquid ships) where a 3.5 GB model plus its working memory fits comfortably, 8-bit (`Q8_0`) where it only just fits, 4-bit (`Q4_K_M`) on small machines. You can choose another, and tick which parts to install: **Chat** (always), **Vision**, **Omni**, **Voice** and, optionally, **Pictures**. Files come from the LiquidAI repositories on Hugging Face and are checked against the SHA-256 Hugging Face publishes; downloads resume where they stopped after a dropped connection.
4. When a model loads, the GPU gets all its layers if the model and its working memory fit in the card's memory (measured by the engine, not guessed), none if there is no GPU, and otherwise llama.cpp fits as many as the free memory allows. Override with `LFM3_NGL` (for example `LFM3_NGL=0` to force the CPU).

The same thing works without a browser: `sthanik --setup --parts text,vl --quant Q8_0` (add `--pictures` for picture drawing). Reopen Setup any time from **Models & System → Hardware & setup…** to add parts or repair.

Only github.com (the engine) and huggingface.co (the models) are contacted during setup, through `LFM3_PROXY` if you set it. Nothing else is downloaded, and nothing is sent anywhere.

**Where things are kept.** Chats, documents, pictures, memory, settings, the engine and the models live in one folder: `%LOCALAPPDATA%\Sthanik` on Windows, `~/Library/Application Support/Sthanik` on macOS, `~/.local/share/sthanik` on Linux. Change it with `--data-dir DIR` or `STHANIK_HOME`; put a file named `portable.txt` next to the program to keep everything beside it. Uninstalling the program never deletes that folder. The models need about 3.5 GB (Chat, BF16) up to 16 GB (everything, BF16) of disk; 4-bit is a third of that.

**Not tested here.** The Linux x86-64 build was run on the machine this was developed on (RTX 3060 6 GB, CUDA 13.4). Windows, macOS and Linux arm64 builds are built and self-tested by GitHub's runners on every release (the program starts, serves its pages, makes Word/PowerPoint/Excel/HTML files and reads the hardware), but not run on a real GPU there; choosing the engine for AMD, Intel, Apple and older NVIDIA hardware follows the rules above and the device check, which has not been seen with that hardware.

## Run from source

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh            # then open http://127.0.0.1:7860 (first run opens Setup)
./run.sh --setup    # or install the engine and models from the terminal
.venv/bin/python -I tests/test_document_harness.py && .venv/bin/python -I tests/test_setup.py
```

Using a model loads it automatically (about 4 s) and unloads the previous one. A source checkout keeps its data in the checkout (`data/`, `models/`, `bin/`, `config/`).
Desktop launcher for a checkout: `sed "s|@DIR@|$PWD|g" packaging/linux/sthanik-dev.desktop.in > ~/.local/share/applications/sthanik.desktop`.

## Pages

| Page | What it does |
|---|---|
| **Chat** | Beside Search sit **Simple / Complex** and **Create**. *Simple* answers quickly. *Complex* makes its own to-do list for the request and works through it by itself (no Plan switch: a request that makes something or is long enough triggers it; greetings and one-liners are answered at once): each item is researched in rounds, written, reviewed against its purpose and rewritten until it passes, and the whole result is reviewed once against your request; web search is always on. The **Create** menu holds the files the chat can make (Word document, PowerPoint slides, Excel spreadsheet, PDF document, Slides as PDF, Web page) and the tool switches. Pick a file kind, describe it, and that one message ends in a downloadable file (the chip beside Create shows the kind; click it to cancel). While a private session is on the whole app turns a muted violet and the top-right control says Private with a lock. Pick the model at the top: **Text** (LFM3-1.7B) or **Omni** (LFM3-Omni-2B: images, video with sound and audio in, speech out). Features the chosen model lacks are greyed out and offer to switch (click the paperclip in Text and it moves you to Omni). The microphone works in both: with Omni the audio goes straight to the model; with Text it is transcribed behind the scenes by the audio model and sent as text. |
| **Vision** | LFM3-VL-2B: look at images and video and ask about them. It is deliberately plain: no web search, tools, Create menu or pictures fetched from the web. |
| **Voice** | *Conversation*: a live spoken conversation with Audio-2B or Omni-2B (a large mic, plain-language status, transcript above, reply format and system prompt under Settings); *Transcribe* and *Read aloud*. Live mode streams audio both ways and can look things up: web search is on by default (switch it off under Settings), using the realtime endpoint's function calling. While it looks something up it says so: the model usually announces it itself ("I'll look that up"); when it stays silent before a search, a short spoken line ("Give me a moment, I'm searching for that") made once by the loaded speech model is played instead. A reply that claims it cannot look things up is cut off and the model is asked once to search. A 2B model decides *itself* whether a question needs a search: prices, weather and news reliably do; a well-known-sounding fact ("who is the prime minister of…") may still be answered from its memory, so say "search for…" when it matters. |
| **Library** | *Documents* (search, type filter, sort, grouped by day, paged; **Edit** or **Chat** with any of them), *Pictures* (search, grouped by day, paged; add one to a document) and *Memory*. **Select** on any of the three tabs picks several items (or all that match the current search) and deletes them together. |
| **Tools & Skills** | Collapsible cards for each tool group and MCP server (add one by URL or by command; local ones are added switched off and ask before they start); the playbook skills, which are always on. |
| **Schedules** | Run prompts on a schedule. |
| **Models & System** | What is on disk and loaded, change the weights behind a slot, download more, server log. |

**Private session** (the control in the top-right corner of every page, and a switch on Models & System; it toggles at once, with no confirmation; off by default). Off: everything is saved as usual and web, news and image search use the engines' strict adult-content filters (Bing's news results cannot be filtered). On: nothing the session makes is written to disk (below) and that search filter is switched off. The same models run in both modes. Switching reloads the page and unloads the resident model; it is refused while a reply is streaming.

## Private session

Nothing the session produces is written to disk. Chats, documents, pictures (found or generated) and every temp file (PDF export, picture generation) go to a folder on RAM-backed
tmpfs, `/dev/shm/sthanik-<uid>-<pid>-…` (mode 0700). **On Windows and macOS there is no RAM disk without extra drivers, so there the folder is made in the system temp directory instead** (the screen says so): it is still zero-filled and removed the same way, but it did touch the disk. It is overwritten with zeros and removed when you end the private session, when Sthanik stops, and, after a crash, the next time it starts
(a folder whose owner is still running is never touched). While it is active:

- your normal chats, documents and pictures are hidden (they are untouched on disk) and the Library only shows what this session made;
- memory cannot be written, schedules are paused (their prompts and results would be saved), and the image log and the server log are not written;
- the switch itself is never saved: every start of Sthanik begins with the private session off, and the browser forgets which chat was open.

Limits you should know: tmpfs pages can be swapped out under memory pressure (this machine swaps to compressed RAM first and only then to the 16 GB swap file; encrypting swap closes that),
model files you downloaded stay in `models/`, files a model writes with the *files* tools go where that tool is pointed, and your browser's own history keeps the page addresses (`localhost`).

## Privacy and security

- Only these things leave the machine, and only when you or the model asks: web/news/image searches (their query text goes to Yahoo and Bing over HTTPS, with no tracking cookies, a fixed browser identity and no referrer),
  pages and pictures the model fetches (that site sees the address and your IP), skill installs (GitHub) and model downloads (Hugging Face). Chats, documents, pictures and memory are never sent anywhere.
  Set `LFM3_PROXY=http://host:port` to send all of that through an HTTP proxy (for example Privoxy in front of Tor) so sites see its address instead of yours.
- The page may talk only to this server: a Content-Security-Policy refuses every remote image, script, stylesheet, font, frame and connection, so nothing in a model's answer, a search result or a
  document can make the browser contact another host. API responses are `no-store`; `nosniff`, `no-referrer`, frame and cross-origin protections are on.
- The server listens on 127.0.0.1 only and rejects foreign `Host`/`Origin` headers; the model server is started with `--offline`, a per-run API key, and gets no network of its own.
- `data/` and `config/` are made readable by your user only (0700/0600), and everything the app creates uses that umask.
- Checked: starting the app, loading all pages, idling and running the model make no outbound connections; a search connects only to `search.yahoo.com` and `www.bing.com`.
- Search: every web search asks **Yahoo and Bing at the same time** and merges the answers (a page both return ranks first). Bing alone returned unrelated pages (a dictionary entry, a login page, support articles) for about a third of everyday queries, differently on every call, so each engine's answer is checked on its own: if its results do not mention the query's words between them it is left out and the other carries the search. Measured on 22 everyday questions in safe mode: 22 on-topic, none empty, about 2.3 s each. Further rounds (other Bing market settings, a quoted phrase, a simplified query) run only if neither engine was on topic; if nothing relevant comes back the tool says so and tells the model to use fewer, simpler words. Identical repeated searches are cached for five minutes (in memory only) and refused if the model repeats one within a turn. When the model runs out of tool rounds, or ends a turn with no text, it is asked once more to answer from what it has (as the last message, with hidden reasoning off); if that still produces nothing, the chat shows the pages it found instead of a blank reply.

## The document you are working on

A conversation knows which document it is working on. It appears above the message box (*Working on this · Edit · Change · ✕*, or a quiet *Work on a document…* link when there is none); it is set when
the chat creates or edits a file, when you press **Edit** on a file card or **Chat** in the Library, or from the **document picker**: search by title, filter by type, the documents of this conversation first then recent ones grouped by day, ↑ ↓ and Enter to choose, and it pages, so hundreds of documents stay usable. The model is
told that document's text, the editing tools are switched on for it even if the Documents group is off, and "make the intro shorter" can
only mean that document. Without a chosen document, the model still sees a list of your recent documents and pictures, so you can say
"add a conclusion to the cycling report" in a brand-new chat.

Small models are unreliable at chaining tool calls (they do the first half of a request and report the whole job done), so the model
gets *whole-job* tools and the work happens in code:
- `edit_document(instruction)`: the request is split into its parts, each part is matched against the document's real headings with a
  schema-constrained answer, placement ("after Benefits", "at the top", else the end) is read from your wording, and each section is
  rewritten or written as plain text.
- `illustrate_document(count)`: pictures are placed by plan (one in each section that has none first), all prompts are written in one
  call and the pictures are drawn one after another.

**Checks after every turn.** When a turn worked on a document, a *Checked* card verifies each part of your request: word counts for
"shorter/longer", headings for "add/remove a section", and a judge reading the actual diff for anything else; counts such as "10 images"
or "5 sections" are compared with the document. Whatever was not done is done by Sthanik itself (missing edits are applied, missing
pictures are made) and the card says which. The card is saved with the conversation.

## Memory

**Library → Memory** holds short facts (one sentence each, up to 100) that are shown to the model in every conversation: preferences,
names, standing instructions. The model saves facts itself (`remember`, `recall`, `forget`), and "remember that I prefer short answers"
is saved by the app directly. Edit, pin, delete or switch memory off on the page.

## To-do mode (Complex): long documents on a small context window

Choose **Complex** under the message box (Text chat) and describe the job ("write a 10-section report on…", "a 7-slide deck on…", or
any multi-part question). Sthanik first asks the model for a **plan** (a JSON list of steps, constrained by a schema) that you can read
and change: rename steps, rewrite what each should cover, reorder, add or remove, set the number of **pictures**, and choose the output
(Document as PDF/Word/HTML/Markdown, Slides as PowerPoint/PDF, or an answer in chat). If you name a number ("10-section", "7 slides") the
plan has exactly that many steps; if you name pictures ("with 4 images") that is the default picture count.

Then it works through the plan **one step at a time**. Every step is a separate small model call whose prompt holds only the outline,
a one-line summary of each finished step and the tail of the previous one, so the prompt does not grow with the document. Stop pauses
the run; *Resume* continues from the first unfinished step (plans are saved with the conversation). At the end the file is created,
pictures are placed with the same routine as `illustrate_document`, the file becomes the conversation's working document and a *Checked*
card confirms the section and picture counts. Per step: it must write exactly one section, a diagram that does not draw gets one repair
attempt and is then left out, stray tool-call text is stripped, and a step that only talks about the work (an "outline", "plan the
structure", "generate all the content") or answers with an apology is never kept as content: the plan drops such steps up front, and an
apology gets one retry, then stops the run so you can edit the step and resume.

**Long requests.** A plan has at most 14 steps, so for "a 200-slide deck" (more slides or sections than that) Sthanik first has the model
list every slide title (rejecting repeats and notes-to-self), then gives each step a batch of titles to write; a missing one is asked
for again. The step shows its titles one per line, so you can edit them before you run it. "2 slides per topic" is a rate, not the total.
Plan exports allow up to 40 drawn diagrams (more are left out with a message), and a grouped step keeps at most one diagram per four slides.

**Web search in plans.** When the web tools are on, a document or deck plan searches the web itself before writing each step (a small model
cannot be trusted to decide when to) and gives the results to the writer as untrusted facts; the pages used are listed in a final
*Sources* section. Skipped for creative or personal writing (letters, poems, speeches…) and when web search is off.

## Diagrams (Mermaid)

Ask for a flowchart, sequence diagram, Gantt chart, mind map and so on. The model writes a fenced `mermaid` block and Chat draws it
(theme-aware, with *Copy source* and *Download SVG*; half-written diagrams while a reply streams keep showing their source).
Inside documents and slides, mermaid blocks become pictures in the PDF, Word and PowerPoint files (drawn by headless Chrome in
Mermaid's strict mode; the diagram text is never executed as HTML or script). A built-in **Diagrams (Mermaid)** skill teaches
small models the syntax; flowchart labels with punctuation are quoted automatically.

## Editing documents

Every PDF, DOCX, HTML, PPTX and XLSX the app creates keeps its Markdown (or CSV) source next to it
(`data/documents/NAME.ext.src.json`), so it can be changed and rebuilt in place. Press **Edit** on a file card in Chat or on the
Documents page:
- edit the text with a live preview (diagrams and pictures included);
- **ask the model** to change the whole text, rewrite one section, or add a section after another (optionally letting it **search the
  web** first for facts the change needs). The answer lands in the editor as an unsaved draft you can discard or keep;
- **Add pictures** places a number of AI pictures in the saved document;
- **Save** rebuilds the same file; **Undo last save** restores the previous version (the last twelve are kept); **Save a copy** makes
  another format without touching the original.

Files made before this feature existed cannot be edited because their source was not stored.

## Images

The **Library → Pictures** page, the `generate_image` and `illustrate_document` tools, the editor's *Add pictures* and plan mode use **Z-Image-Turbo** (Q4_K GGUF, 8 steps) with a Qwen3-4B text encoder through
stable-diffusion.cpp's Vulkan build, with the weights offloaded to RAM and flash attention so it fits a 6 GB card. The tool asks
before it runs (it needs the whole GPU), and the model can place the result in a document or slide with
`![caption](img:NAME)`. Pictures are stored in `data/images/`; each run's full output is kept in `data/sd.log`; only names that exist there are ever shown or embedded (remote image
URLs in model text are never loaded).

Files expected (the Images page tells you what is missing):

```
bin/sd/sd-cli                                   stable-diffusion.cpp release, Vulkan build (Linux x86_64)
models/image/z_image_turbo-Q4_K.gguf            huggingface.co/leejet/Z-Image-Turbo-GGUF
models/image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF
models/image/ae.safetensors                     huggingface.co/Comfy-Org/z_image_turbo (split_files/vae)
```

The binary comes from the project's GitHub releases (`master-1024-77b72a2`, `…-Linux-Ubuntu-24.04-x86_64-vulkan.zip`); verify the
archive against the digest GitHub shows for the release before extracting it into `bin/sd/`. Set `LFM3_SD_DEVICE` to a Vulkan
device index if the wrong GPU is picked (by default the discrete GPU is chosen).

## Pictures from the web, or drawn

When an answer needs a picture, the model chooses: `find_image` searches the web for a **real** photo (a landmark, animal, map, person, object) and
`generate_image` draws an **imagined** or stylised one. Either way the picture appears in the chat and can be reused. In documents,
`illustrate_document` decides **per section** (real place → web, abstract idea → generated; you can force *From the web* or *Generated*
in the editor, in the plan card or in the tool call), falls back to drawing when the web has nothing usable, and puts web pictures in with a
`(source: site)` caption. **Library → Pictures → Find a picture on the web** searches by hand, previews and saves; web pictures carry a *Web*
badge and a link to the source page.

Downloads are careful: public addresses only (re-checked after every redirect), at most 10 MB, SVG refused, and the file must really decode as
a picture (it is re-encoded, so only pixels are kept). Web pictures may be copyrighted: the app records and credits the source, but checking the
licence before you publish is up to you.

## Keeping documents correct (the harness)

A small model is unreliable at long chains of tool calls, so the app does the dependable parts itself and checks its own work.

- **Whole-job tools.** `edit_document` (one clause at a time), `illustrate_document` (a picture per section, from the web or drawn; `replace` swaps the old ones),
  `remove_pictures_from_document`, `add_picture_to_document`, `check_document`, `tidy_document`, `undo_document_change`.
  Repeating an identical editing call in one turn is ignored, and a request that names sections ("the picture in Place 3", "Place 4 and Place 6")
  stays inside them.
- **Cleanup that never rewrites content** (`docaudit.py`): closes a diagram/code fence that was never closed (otherwise everything after it renders as code),
  removes leftover "New step"/"Image" sections, copies of the title, placeholders, leftover commentary, repeated sections/paragraphs/pictures and broken picture
  references. Numbered sections with gaps ("Place 1, 5 missing") are only *reported*: they need new text, so the app asks instead of inventing them.
- **Edits keep what they should.** A rewrite keeps the section's heading, pictures and diagrams; a "shorter" rewrite that drops most of the text is refused;
  sections the request names exactly ("Place 5: Rameswaram") get that exact title and go in numeric order.
- **Verification after every document turn** (the "Checked" card): tidy, measurable checks of each clause, old pictures gone for "replace"/"remove",
  the number of pictures/sections asked for, every section covered, and unresolved tool failures. It finishes the job when something is missing.
- **Looking at the pictures.** `review_document_pictures` (also the **Review pictures** button in the document editor) loads the vision model, judges the real
  pixels of every picture against its section, fetches several web candidates for each weak one, has the vision model score those too, and swaps in the best,
  only when clearly better. It uses a lighter vision configuration (short context, projector on the CPU) so it fits next to the desktop on a 6 GB card, then
  brings the previous model back.
- **Skills drive the file formats.** In chat the matching skill (pdf, docx, pptx, xlsx, html, mermaid) is put in front of the model; in plan mode each step also
  receives that format's style rules (`/api/skills/writing`; only the rules, never the whole-file layouts, which a one-section writer would copy), plus the diagram rules when a step draws one.
- **One undo per request.** All the changes one chat request made are undone together (the last 12 versions are kept).
- Regression tests for the deterministic parts: `.venv/bin/python -I tests/test_document_harness.py`.

## Changing models

On **Models & System**, *Change…* on a model card points that slot at other GGUF files from the `models/` folder (name, model file,
projector for vision/audio, context size, source). Settings live in `config/models.json`; *Reset to default* restores the built-in
weights. Pick the same kind of model a slot is for (a vision model for Vision). *Get more models* downloads one GGUF from Hugging
Face into `models/custom/` (resumable, cancellable, checked for the GGUF header and free disk space; only huggingface.co).
Check a model's licence and size first: the GPU holds about 4 GB at a time.

**Using a download:** *Downloaded files* lists everything in `models/custom/` with its size and precision. *Use for…* opens the same *Change…* dialog for the slot you pick with that file selected (the projector sitting in the same folder is chosen for you); *Delete* removes a file no slot points at. Saving refuses anything that is not a real GGUF file inside `models/`. Downloads refuse a file that already exists or is already downloading, explain gated repositories (this app has no Hugging Face login: download in a browser and copy the `.gguf` into `models/`), and leave no empty folder behind when they fail.## Tools & connectors

Chat can call tools. Turn them on from the **Search** / **Tools** buttons under the message box, or manage everything on the
**Tools & Connectors** page.

| Group | Tools | Notes |
|---|---|---|
| Web | `web_search`, `news_search`, `fetch_url`, `find_image` | Bing (through ddgs), no API key. Queries leave your machine. `find_image` saves a real photo to your library and shows it. |
| Utilities | `current_time`, `calculator` | Calculator is a safe expression parser, not `eval`. |
| Local files | `list_files`, `read_file` | Read-only, one folder you choose, no hidden files. Asks before running. |
| Documents | `create_document`, `create_slides`, `create_spreadsheet`, `list_documents`, `read_document`, `edit_document` | Create and edit PDF, Word, HTML, PowerPoint and Excel files. |
| Images | `generate_image`, `illustrate_document`, `list_images` | Local text-to-image; pictures for documents. Asks before running. |
| Memory | `remember`, `recall`, `forget` | On by default; see Memory above. |
| Skills | `use_skill`, `read_skill_file` | Expert playbooks. |
| MCP servers | whatever the server offers | Local (stdio) and remote (streamable HTTP). Asks before running. |

Each group can run automatically or ask first (Allow / Always allow / Deny). Answers that used the web show source chips.

**Adding MCP servers**
- Remote (HTTP, no auth): Tools page → *Add a remote server*.
- Local (runs a command as you): edit `config/mcp.json`, then press *Reload*. The format matches other MCP clients
  (`servers` or `mcpServers`). Add servers on the Tools page: a remote one by URL, or a local one by command (saved switched off; it starts only after you turn it on and confirm).

**Safety model**
- Tool output is treated as untrusted text and the model is told never to follow instructions inside it. A tool call the model writes as plain text is understood and run, and when tools run out it is told to answer from what it has, so a long lookup ends in an answer.
- `fetch_url` refuses loopback, LAN, link-local and cloud-metadata addresses, including after redirects.
- Local MCP servers can only be added by editing the file, never from the browser.
- The backend rejects requests whose `Host`/`Origin` isn't localhost (blocks DNS rebinding and cross-site calls), the internal
  llama-server requires a per-run API key, and the page's policy blocks remote images and framing.
- Model-chosen files stay inside their folders: model weights must be `.gguf` files under `models/`, images are served only from
  `data/images/`, documents only from `data/documents/`.
- Don't enable *Local files* and *Web* together on documents you don't trust: injected text in a file could try to leak it through a
  search query. Keeping Local files on "ask first" (the default) lets you see every call.

## Questions before a big request

When you ask for a report, slides, a spreadsheet or a plan and the request leaves out what it is about ("analyse how **the movie** was received", "make a presentation"), Sthanik asks first instead of researching blind: a few short questions with answer choices and a field for "none of these". Your answers are added to the request, so the plan, its web searches and the file all use them. *Skip questions* carries on as written, the cross cancels and keeps your message in the box, and *Ask questions first* in Chat settings turns it off. Plan mode is for requests that make something: a remark such as "sorry, I meant the 2026 film" right after a plan is merged into that plan's request (and planned again) instead of becoming a new plan, and other chatty messages are sent as normal chat.

Research in plan mode is a loop: each stage searches, then checks whether the notes really answer what that stage needs (concrete names, figures, dates about your subject, not generic background), and if not searches again with sharper queries it proposes (up to 3 rounds and 6 searches per stage; a stage that lists several items starts with one search per item). The stage shows what it is doing ("Searching the web (2): …", "Checking whether the notes answer …") and afterwards how often it searched and what is still thin. Plan mode's web search is also tied to what you named: the searches include it, and results that never mention it are ignored. A part written without usable sources is told to stay general (no invented scores, dates, events or quotes) and is marked in the plan. A note a rewrite leaves about its own edit ("*Note: the document … has been updated*") is removed from the text instead of being saved into the document.

## Conversations

Chat and Vision save every conversation automatically to `data/chats/`, including attachments, tool calls, sources,
plans and the file cards the model produced. The **history panel** (clock icon, top right of the chat) lists them by day with
search, rename and delete; the last conversation reopens by itself. Live voice sessions are not saved.

**A chat is saved the moment you press send**, not when the reply ends: it appears in the history panel at once, and if you reload the page (or the browser dies) while a reply is being written, the chat is still there and the unfinished reply says so and offers **Try again**. Saves are queued, so an older save can never overwrite a newer one.

**Projects** are folders for conversations. The folder-plus button next to *New chat* makes one (type the name, press Enter; an empty project stays). Drag a chat onto a project to put it in, drag it back onto the list to take it out, or use the folder button on a chat for a menu (the keyboard and touch route). Projects collapse, rename in place, and removing one keeps its chats.

Chat stays alive while you browse: switch pages or models mid-reply and it keeps streaming (a pulsing dot marks the page in the
sidebar). If another page needs a different model, it waits for the running reply to finish. The hamburger button collapses the
sidebar, and the sun/moon button switches theme (it follows your system until you choose).

**Long conversations (compaction).** The model's window is finite (32,768 tokens by default, and the sidebar meter shows how full it is). Before every model call,
including between tool rounds, the chat estimates the request (characters, scaled by the real token counts the server reports) and, once it would fill
about 85% of the usable window, makes room in this order: (1) shorten old tool results, drop old hidden reasoning and old attachments from the request (free, no model call);
(2) have the model **summarize the older turns** into a short summary (a word budget of about 40% of the input, at most 280 words, with the reply pre-filled inside `<summary>` tags
because this model otherwise drafts out loud; if the result is not clearly smaller, or drafts, a plain digest is used instead), always keeping the newest two turns word for word and never separating a tool call
from its results; (3) squeeze harder. If the server still refuses a request as too big, the real size from its message corrects the estimate and the request is retried once. Later compactions fold the earlier summary
in, so facts survive repeated compaction. Nothing is deleted: the full transcript stays on screen and in `data/chats/`; the summary is saved with the conversation, a divider shows where it begins (click *See the summary*), and the
**Compact** button in the chat toolbar summarizes everything but the latest turn on demand. Regenerating or retrying past the summarized point drops the summary. The 85% trigger point is a setting (*Chat settings → Summarize older messages when the model's memory is this full*, 40–95%): lower it to compact earlier.
Plan mode needs none (each step's prompt is the plan plus the tail of the previous step, so it never grows with the document), and voice sessions are not compacted. **Scheduled tasks** make room the same free way, since a run is one prompt plus tool results that pile up:
before each round, once the request would fill 85% of the window, old tool results are cut to 500 characters (the two newest stay whole), then to 200 (only the newest stays, capped at 3,000); a request the server still refuses as too big is retried once after that.

## Look and feel lives in the skills

The palette, fonts and sizes of every file come from the `style` block in the header of that kind of file's skill: `docx` (Word), `pdf` and `html` (PDF and web page), `pptx` (PowerPoint and slides PDF), `xlsx` (Excel). Edit the block on the Skills page and the next file you make looks different; leave a line out and the built-in value is used.

```yaml
metadata:
  style:
    accent: "#14633a"      # rule above titles, bullets, links, table accent, spreadsheet header
    accent2: "#3ddc84"     # the bright accent on slides
    ink: "#15201a"         # body text            heading: "#0f3d24"   dark: "#07100b" (title slide, code)
    paper: "#f5f8f6"       # slide background     tint: "#e6f0ea" (table header)
    font: "Noto Sans"      # PDF / web page / slides PDF       office_font: "Calibri" (Word, PowerPoint)
    body_pt: 11            # h1_pt: 25   h2_pt: 15.5   slide_title_pt: 36
```

Colours must be `#rrggbb`, fonts plain names (they must be installed on the computer that opens or renders the file), sizes within sane ranges; anything invalid is ignored. The pale tints (borders, stripes, quote boxes) are worked out from `accent`. The built-in skills spell out today's values, so a skill that is refreshed by an app update goes back to them.

## Skills

A skill is a short playbook (an [Agent Skills](https://agentskills.io) `SKILL.md`) that the model reads before a job. Six ship
with the app:

| Skill | For |
|---|---|
| `pdf` | reports, briefs, memos, one-pagers as PDF |
| `docx` | proposals, letters, minutes, SOPs as editable Word files |
| `pptx` | slide decks (storyline, slide titles, bullet limits, tables) |
| `xlsx` | clean spreadsheets (header rules, bare numbers, ISO dates) |
| `research-brief` | web research that ends in a sourced document |
| `mermaid` | flowcharts and other diagrams |

Skills are always on. Small models rarely load a playbook unprompted, so each skill has **trigger words**
(for example "pdf", "slides", "flowchart"): when one appears in the conversation the playbook is put in front of the model
automatically, and the reply shows a `skill: pdf` note. *Always on* pins a skill into every chat (costs tokens each turn). On the
**Skills** page you can try, read, turn off, remove (*Restore built-ins* brings them back) or write skills, or install public
GitHub skills repositories (text files only; nothing in a skill is ever executed).

## Documents

Switch on **Documents** under *Tools* and the model can create files itself. Every reply also has a download button (*Save as…*)
that turns it into a PDF, Word file, slides, HTML or Markdown without the model's help. Files land in `data/documents/`. PDFs
are rendered by headless Chrome/Chromium/Brave (full Unicode, including Indic scripts) with a no-network policy; model text is
rendered with raw HTML disabled. Spreadsheet cells can never become live formulas.

## Scheduled tasks

**Schedules** page: run a prompt every N minutes/hours, daily, on chosen weekdays, by cron expression, or once. Each task picks a
model (Text/Vision/Omni), the tool groups it may use, whether it may use skills, and an output (keep the text, or also save a
PDF/DOCX/slides/HTML/Markdown).
- Runs only while the app is open; missed occurrences are skipped. Shortest interval: 5 minutes.
- One run at a time. Scheduled runs wait until the model has been idle ~1 minute (and no picture is being made); *Run now* doesn't wait.
- Unattended means no one can click *Allow*: tools from groups that normally ask are **skipped** unless the task explicitly ticks
  *Run without asking* for that group.

## Layout

```
run.sh                  start the UI + backend from a source checkout (app/launcher.py)
app/launcher.py         entry point of the packaged program: start, --setup, --selftest, --data-dir, Quit
app/paths.py            where the program's files and the user's data live
app/hardware.py         detect the computer; choose the engine build, the precision, the GPU layers
app/installer.py        first-run setup: engine and model downloads (resumable, SHA-256 checked), setup job
app/runtime_manifest.json  pinned llama.cpp / stable-diffusion.cpp builds with their SHA-256 (packaging/make_manifest.py)
app/server.py           backend: llama-server lifecycle, status (GPU/CPU/context), API + WebSocket proxy
app/tools.py            tool hub: built-in tools, files connector, MCP client
app/documents.py        Markdown/CSV -> PDF / DOCX / PPTX / XLSX / HTML, sources, editing helpers
app/docagent.py         edit_document / illustrate_document / edit verification
app/memory.py           long-term memory
app/diagrams.py         Mermaid -> PNG (headless Chrome)         app/chrome.py   shared headless-Chrome runner
app/images.py           stored pictures                          app/imagegen.py  Z-Image-Turbo via sd-cli
app/registry.py         model slots, overrides and downloads     app/sysmetrics.py  CPU / RAM readings
app/skills.py           skills store and installer               app/builtin_skills/  bundled skills
app/chats.py            saved conversations                      app/scheduler.py     scheduled tasks
app/static/             the UI (plain HTML/CSS/ES modules, no build step; libraries vendored under vendor/)
config/mcp.json         MCP servers   config/models.json  model overrides   config/tools.json  connected folder
skills/  data/          installed skills; chats, documents, images, schedules, run history
bin/llama/              the llama.cpp engine the installer chose (b11979; LFM3 needs b11960+)
bin/sd/                 stable-diffusion.cpp (Vulkan)             models/<name>/  GGUF weights, models/image/
downloads/              archives and logs (safe to delete)
.venv/                  Python env from requirements.txt
packaging/              PyInstaller spec, per-system packaging (Windows installer, Linux install.sh, icons), manifest tool
.github/workflows/      CI (tests + self-test) and the release build for every system
```

## Notes

- Microphone features need `localhost` (or HTTPS); the UI binds to `127.0.0.1` only.
- Sthanik's own code is released under the MIT License (see `LICENSE`). The models it runs are separate works with their own licences.
- LFM3 weights are under the LFM Open License v1.0; check it before commercial use. Z-Image-Turbo and Qwen3 have their own licences.
- **Releasing:** change `app/version.py`, commit, then `git tag v2.0.1 && git push --tags`. The *Build and release* workflow builds the five packages (Linux x86-64/arm64, Windows x64, macOS arm64/x86-64), runs each one's own `--selftest`, and publishes them with `SHA256SUMS`. To move to a newer llama.cpp, run `python packaging/make_manifest.py --llama <tag>` and review the diff. Details: `packaging/README.md`.
- Built-in per-model context sizes and flags are in `MODELS` in `app/server.py`; use *Change…* to override the context size.
- Text-to-speech has no voice cloning; voices are `female` / `male`.
- The UI bundles marked, DOMPurify, highlight.js, KaTeX, Mermaid and Inter under `app/static/vendor/` (licences alongside), so it works offline.
- The project is a git repository; `models/`, `bin/`, `data/`, `skills/` and local config are not tracked.

## Verification, voice dictation and projects

- **Every part is checked.** In Complex mode each part is reviewed against what it is *for*: is it on the actual subject, backed by the search notes, and more than filler? What fails is rewritten with the reviewer's problems (up to three tries) and the plan card shows the outcome ("Checked, good after 1 fix" or "still weak: …"). When all parts are done the whole result is reviewed once against your request and weak parts are rewritten. A part with no sources is not failed for lacking figures, only for being off-topic or empty.
- **The mic writes, it does not send.** In Chat (Text and Omni) the recording is transcribed into the message box so you can edit it or say more, then press send.
- **Projects.** The folder-plus button beside *New chat* makes a project; drag a conversation onto it, or use the folder button on a conversation for a menu. Projects are collapsible sections at the top of the history, with + (new chat in the project), rename and remove. A project is a folder you create (it stays when empty); drag chats in and out. In a private session the collapsed state is not remembered.

### How little it is allowed to make up (Complex mode)

Facts come from the web, and the model only writes them up. Each part is researched with search queries the model writes for that part (naming your subject), the best result page is opened and only the lines that bear on the part are kept, and a part is written from those notes alone. Afterwards a check that does **not** rely on a small model's judgement compares what was written with the notes: every number and every multi-word name must appear in the sources or in your own request (never in the plan's step text, which the small planner can also invent), and a number must sit next to the name it is claimed for (not, say, another country's row). A part that fails is rewritten with the specific problems, up to three times; anything still unsupported is cut, so the result has gaps instead of invented facts ("not listed in the provided excerpts"). What this does **not** catch: a claim that is wrong but present in a source (for example a mid-event snapshot quoted as final), and sentences that are vague or inconsistent without containing a number or a name. Simple mode answers are not run through this check.
