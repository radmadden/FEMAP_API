# FEMAP_API

FEMAP API macros (WinWrap Basic `.bas`, run in FEMAP via File > Programming > API
Programming), plus some Python tooling (`npz_to_3D.py`). The API reference is `api.pdf`
(FEMAP API Guide 2025).

## Workflow for every new macro: research → plan → implement

Follow these steps in order, even when the macro looks small. Don't write code until the
user approves the plan.

1. **Research:** write `research-femap-<topic>.md`.
   - State the goal in a few lines.
   - Under **Key API findings (confirmed in api.pdf)**, give every call, constant and enum
     you'll rely on, with a line reference into the extracted guide text (`api ~61119`).
     Don't use an API call you haven't found in the guide.
   - Add **Engineering notes**: solver behavior, limits, and the options with their
     trade-offs.
   - End with **Open design questions (for the plan)**, a numbered list.
   - Then stop, so the user can answer.
2. **User answers:** the user replies inline in the research doc, after each question, as
   `>> answer`. Reread the doc when they say they've commented, and treat `>>` lines as
   decisions.
3. **Plan:** write `plan-femap-<topic>.md`.
   - Open with **Decisions locked from the research review** (one bullet per answer).
   - Then cover: **What the user supplies at run time**, **Algorithm** (numbered steps),
     **Output**, **Helper routines**, **Assumptions / limits**.
   - End with **Status**, including a **first-run checklist**: concrete checks to run in
     FEMAP, such as comparing against the Nastran .f06.
   - Flag any choice you had to make that the user didn't answer as **a decision to
     confirm**.
   - Then stop and ask for approval.
4. **Implement:** write the `.bas` only after the plan is approved. If the plan changes
   while you're implementing, update the plan doc to match. Set Status to "implemented, not
   yet run in FEMAP" until the user reports a test run.

## Reading api.pdf

The PDF is large, so extract it once to text in the scratchpad and grep that:
`pdftotext -layout api.pdf <scratchpad>/api.txt`. It prints a harmless `Unknown filter
'Crypt'` error. The line refs in the research docs point into this text.

## .bas conventions (match the existing macros)

- A header block between `'====` rules, with these sections: **PURPOSE**, **HOW IT
  WORKS**, **ASSUMES**, **HOW TO RUN**, **API CALLS USED (Femap API Guide 2025)**.
- Tunable values go in a **USER SETTINGS** `Const` block at the top. Prompts use
  `InputBox`/`MsgBox`/`Begin Dialog`, and entity picks use `feSet.Select`.
- `Sub Main` is split by `' STEP n:` comment banners. Helpers go below it, each with a
  `'---` comment header.
- **Comment verbosely.** The user reads these macros to learn the FEMAP API, so follow the
  density of `write_group_includes.bas`:
  - Every STEP banner says what the step does **and why**.
  - Every non-obvious API call gets a comment on what it returns or how it behaves (return
    codes, 0- vs 1-based indexes, what a Set holds afterwards, which `api ~line` it came
    from when that helps).
  - Every helper's header explains its inputs, outputs and edge cases.
  - Workarounds for WinWrap limits say what they replace and why.
  - Explain the reasoning, not just a restatement of the code line.
- Look up output vector IDs with `feResultsIDQuery`, never hard-code them. Read results with
  the Results Browsing Object (`feResults`).
- Never change the model silently. Restore any settings you change temporarily, and report
  warnings and counts through `feAppMessage`/`MsgBox`.
- **FEMAP's WinWrap Basic is not VBA.** `Collection` and `Scripting.Dictionary` don't exist.
  FEMAP reports "Expecting a valid data type" on `As Collection`. For an ID → value lookup,
  use a plain array indexed by ID and sized with `feSet.Last()` (the highest ID in a set), or
  a `femap.Set` with `IsAdded`. When in doubt about a language feature, copy a pattern from
  a macro that has actually been run.
- Nothing can run FEMAP from here, so code is verified against api.pdf only. Always say it's
  untested in FEMAP.

## Git

- Commit only when the user asks. Stage only the files for the current macro. The working
  tree often has unrelated work in progress (thermal/point-cloud files, `.pptx`/`.docx`,
  `model1_*` data); leave it alone.
- One commit per macro, holding the `.bas` and its research and plan docs. Write a short
  summary line: `add <name>.bas: <what it does>`.
- Commit **directly to `main`** (no feature branches), and push when the user asks.
