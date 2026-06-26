# MediaAI Renamer M4 User Manual

Version: v0.4.0 preview
Stage: M4 TMDB metadata scraping and file operations
Date: 2026-06-26

## 1. What M4 Supports

M4 adds TMDB metadata matching and a pending file list.

You can configure TMDB in System Settings, then run metadata matching from the Naming Preview page. High-confidence results are applied automatically. Low-confidence results open a candidate dialog for manual selection.

M4 also supports a minimum file size threshold. Video files below the threshold are not added to the normal rename queue. They are routed to the pending file list.

## 2. Configure TMDB

Open System Settings:

1. Select the TMDB metadata category.
2. Enable TMDB.
3. Enter a TMDB API Key.
4. Adjust language, region, and timeout if needed.
5. Save the settings.

Settings take effect without restarting the service. The API key is masked after saving.

If TMDB is disabled or no API key is configured, the system falls back to local parsing.

## 3. Configure Minimum File Size

In System Settings, set Minimum Scan File Size.

- `0`: all recognized video files are accepted.
- Greater than `0`: smaller video files are routed to the pending list.

The value is applied on the next scan.

## 4. Run TMDB Matching

On the Naming Preview page:

1. Select a media source and scan task.
2. Click Query.
3. Click the TMDB Match icon in a row.

Result types:

- High confidence: target file name is updated automatically.
- Needs confirmation: candidate dialog opens for manual selection.
- Failed: local preview is kept.

## 5. Review Match Information

The preview table shows:

- Metadata match status.
- Match percentage.
- Source and score in the detail dialog.

You can still manually edit the target file name. Manual edits are used for final rename.

## 6. Pending File List

The pending file list is shown under the naming preview table.

It displays files routed out of the normal queue, including:

- Reason tag.
- File name.
- Source path.
- File size.
- Scan time.

## 7. Pending File Actions

Supported actions:

1. Remove one task: removes the pending task record only.
2. Clear list: clears matching pending task records only.
3. Move selected files: moves selected real files to a target folder.

Before moving files, make sure the target folder exists and no same-name file already exists there.

## 8. Notes

- TMDB is optional. Local parsing still works when it is disabled.
- Network errors, missing API key, or TMDB timeout do not block existing workflows.
- Remove and Clear do not delete real files.
- Move Selected Files moves real files.
- M4 does not provide an undo action for moved files.
