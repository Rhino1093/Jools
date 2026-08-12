# LightsToCeiling Accuracy + Color Override Plan

## Goals
- Improve ceiling hit accuracy for selected elements (including linked ceilings).
- Detect and flag elements that do not snap to a valid ceiling target.
- Apply color overrides to flagged elements for rapid visual QA.
- Keep the workflow fast and predictable for large selections.

## Non-Goals (initial scope)
- No auto-hosting of elements to ceilings.
- No permanent parameter changes beyond the selected target parameter.
- No changes to linked model geometry.

## Current Behavior Snapshot
- Uses `ReferenceIntersector` to find nearest ceiling above/below.
- Chooses closest hit and optionally adjusts for thickness.
- Updates a target parameter (default `Offset from Host`).
- Reports adjustments in the pyRevit output panel.

## Problems to Address
- Incorrect ceiling hit (wrong ceiling in stacked conditions or nearby sloped ceilings).
- Missing hit due to view settings, phase filters, or linked model issues.
- Ambiguous hit direction (up vs down) for elements near mid-ceiling.
- Lack of visual feedback when an element could not be snapped.

## Plan Overview
1) Add robust hit selection rules.
2) Add snapping validation checks.
3) Add color overrides for failed or uncertain snaps.
4) Add reporting and optional export for QA.
5) Add simple configuration options for users.

## 1) Robust Hit Selection Rules
- Prefer hits within a user-defined max snap distance (default: 5'-0").
- Use a small vertical tolerance band above and below the element to avoid far hits.
- If both up/down hits are valid, apply deterministic tie-breakers:
  - Prefer down hit if element is below a ceiling plane.
  - Prefer up hit if element is above the ceiling plane.
  - If uncertain, pick the closest hit but mark as "uncertain" for QA.
- For sloped ceilings, consider using the hit face normal to validate direction.
- For linked ceilings, track link instance transform for precise world coordinates.

## 2) Snapping Validation Checks
- Check hit distance against max snap distance.
- Validate that the hit face is a ceiling face (not underside of a roof or floor).
- Validate target parameter is writable and has expected storage type.
- Validate the new value is within a reasonable range (min/max).
- Record a status per element:
  - `snapped_ok`
  - `snapped_uncertain`
  - `no_hit`
  - `invalid_param`
  - `out_of_range`
 - "Uncertain" snaps should only flag the element (no parameter change).

## 3) Color Override Strategy
- Apply temporary graphic overrides in the active view only.
- Use a single override color for all flagged elements: red (RGB 255,0,0).
- Flagged statuses include `snapped_uncertain`, `no_hit`, `invalid_param`, and `out_of_range`.
- Include a "Clear Overrides" option to reset all touched elements.
- Optionally include a "Preview Only" mode (no parameter changes, only coloring).

## 4) Reporting and QA Output
- Extend the output table to include:
  - Snap status
  - Hit direction (up/down)
  - Hit distance
  - Ceiling element id (and link name if applicable)
- Optionally write a CSV report to the user's Downloads folder.
- Summarize counts by status for quick review.

## 5) UI/Config Changes
- New settings in the dialog:
  - Max snap distance (feet).
  - Min/Max acceptable parameter delta (feet).
  - Target category selection via checkboxes (floors, ceilings, roofs, etc.).
  - Enable "Preview Only".
  - Enable "Apply Color Overrides".
  - Enable "Clear Overrides".
- Persist last-used settings (pyRevit config or simple json cache).

## Edge Cases to Consider
- Elements without `Location.Point` (e.g., curve-based families).
- Elements in groups or pinned elements.
- Elements whose host is in a different phase.
- Ceilings in linked models with different unit settings.
- Active view not 3D or not including linked model visibility.

## Implementation Notes (High Level)
- Collect elements and pre-filter by category and location type.
- Build a per-element evaluation record for status and diagnostics.
- Apply parameter changes within a single transaction.
- Apply view overrides in a separate transaction for clarity.
- Keep performance in mind: avoid expensive per-element collectors.

## Open Questions (Answered)
- "Uncertain" snaps should only flag the element (no parameter change).
- Target category should be user-selectable via checkboxes (floors, ceilings, roofs, etc.).
- Use red (RGB 255,0,0) for overrides.
- Store the CSV report in the user's Downloads folder for now.

## Success Criteria
- 95%+ of tested elements snap to the correct ceiling in typical projects.
- All failed snaps are visually obvious in the view.
- Users can quickly diagnose issues without manual selection.
