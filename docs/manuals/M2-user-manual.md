# MediaAI Renamer M2 User Manual

Version: v0.2.0 Preview
Stage: M2 Standard Naming Preview
Date: 2026-06-24

## 1. What You Can Do Now

M2 generates standard naming previews from scanned media files.

You can view the original file name, parsed result, suggested target file name, and preview status. You can also manually edit the target file name.

M2 does not rename real files.

## 2. Opening Naming Preview

Click "Naming Preview" in the left sidebar.

The page includes:

- Generate Preview button
- Refresh button
- Media source filter
- Scan job filter
- Status filter
- Type filter
- Keyword search
- Preview table
- Edit dialog

## 3. Generating Previews

Steps:

1. Scan a media source first.
2. Open the "Naming Preview" page.
3. Optionally select a media source or scan job filter.
4. Click "Generate Preview".
5. Wait for the list to refresh.

If no filter is selected, the system generates previews for all scanned media files in the database.

## 4. Preview Status

Statuses:

- Available: the system identified the item as a movie or episode and generated a target file name.
- Needs Review: the system could not fully determine the type, year, or episode information.
- Edited: the user manually edited the target file name.

## 5. Naming Rules

Movie format:

```text
Title.Year.extension
```

Example:

```text
The.Matrix.1999.mkv
```

Episode format:

```text
Title.SxxEyy.extension
```

Example:

```text
Show.Name.S02E03.mp4
```

## 6. Editing Target File Names

Click "Edit" in the preview table.

Enter the new target file name and click "Save".

Notes:

- Only file names are allowed. Directory paths are not allowed.
- If no extension is provided, the original extension is added automatically.
- Editing only updates the preview record. It does not modify the real file.

## 7. Current Limitations

M2 does not support:

- Actual file renaming
- Rename conflict detection
- Target directory planning
- External metadata matching
- AI parsing
- Batch confirmation and execution

These capabilities are planned for later milestones.
