---
name: xlsx
description: Build a clean Excel spreadsheet from tabular data (budgets, trackers, comparisons, schedules, datasets) with the create_spreadsheet tool. Use when the user wants a spreadsheet, table of records or .xlsx file.
metadata:
  title: Spreadsheets
  icon: table
  version: 5
  triggers: [xlsx, excel, spreadsheet, csv, budget, tracker, worksheet]
  tools: [skills, documents]
  example: Create a spreadsheet of a monthly household budget with categories and planned versus actual amounts for six months.
  style:
    accent: "#14633a"
    office_font: "Calibri"
---

# Creating a spreadsheet

**Always create the file with the `create_spreadsheet` tool. Never paste the table into the chat.** Call it with a `title` and the data as **CSV text** in `csv`. The tool styles the header row, sizes the columns, freezes the top row and stores numbers as numbers.

## CSV rules
- **First row = headers**: short, unique, plain words. Put units in the header ("Revenue (USD)", "Weight (kg)").
- **One record per row**, the same columns in every row, no blank rows, no merged cells, no sub-tables.
- **Numbers are bare**: `1234.5`, not `$1,234.50` or `1.234,5`. No thousands separators, no currency symbols, no percent signs (use a column "Rate (%)" with `12.5`).
- **Dates** as `YYYY-MM-DD`. Times as `HH:MM`.
- Quote any field that contains a comma with double quotes: `"Smith, Jane"`.
- **Formulas are not evaluated.** Compute totals yourself (use the `calculator` tool for sums) and add a `Total` row only when the user asks for one. A cell that starts with `=` is stored as plain text.
- Keep it to what was asked; for large requests give a representative sample and say so.

## Example
```
Category,Planned (USD),Actual (USD)
Rent,1200,1200
Groceries,400,435.5
Transport,120,98
Total,1720,1733.5
```

## After creating
Tell the user the file name, the number of rows and columns, and any assumptions you made (for example invented sample values).

## Look and feel
The palette, fonts and sizes of the finished file come from the `style` block in this file's header, not from the content. To change how these files look, edit that block (colours as `#rrggbb`; leave a line out to use the built-in value). Never put colours, fonts or sizes in the content you write.
