# MediaAI Renamer M3 User Manual

Version: v0.3.0
Stage: M3 Safe Rename
Date: 2026-06-26

## 1. What M3 Supports

M3 adds the first safe rename workflow on top of naming previews.

Before changing real files, the system runs a dry-run conflict check. Only ready items can be executed. Files are renamed in their original folders only; M3 does not move folders or overwrite existing files.

## 2. Before You Start

Complete these steps first:

1. Add a media source.
2. Run a scan task.
3. Generate naming previews.
4. Review or edit target file names.

## 3. Execute Rename

On the Naming Preview page:

1. Select a media source and scan task.
2. Click Query to load previews for the task.
3. Use one of the rename entry points:
   - Select rows and click Execute Rename to process selected items.
   - Click Rename All to process all renameable items in the current query result.
   - Click the row action icon to process a single item.

The system checks empty target names before the dry-run. If empty target names exist, a dialog lists those rows. You can return to edit them, or click Remove Empty-Name Items and Continue to exclude them from the current operation.

After that, the system runs the dry-run conflict check and opens the confirmation dialog. The dialog shows total, ready, conflict, renamed, and failed counts.

## 4. Conflict Reasons

Common conflict reasons include:

- Source file is missing.
- Target file name is empty.
- Target file name contains a path separator.
- Target file already exists.
- Multiple selected rows have the same target path.
- Preview status is not allowed for rename.

Conflict items are not executed. Fix the target name or rescan the source files before retrying.

## 5. Confirm Rename

If the dry-run result contains ready items, click Confirm Rename.

After execution:

- Successful items become Renamed.
- Media file path and file name are updated.
- Failed items keep their failure reason.
- The naming preview list is refreshed.

## 6. Notes

- M3 does not support undo.
- M3 does not overwrite existing files.
- M3 does not move files to another directory.
- Files locked by another program may fail to rename.
- Files moved or deleted after dry-run may fail during execution.

## 7. Current Limits

This version does not support:

- Directory moves.
- Rename rollback.
- Approval workflow.
- AI metadata matching.
- Dedicated rename history page.
