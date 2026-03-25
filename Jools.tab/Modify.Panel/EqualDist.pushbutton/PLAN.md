# Implementation Plan - Equal Dist Detail Lines

## Objective
Create a pyRevit script to equally distribute parallel detail lines.

## Proposed Logic

### 1. Selection & Validation
- **Case A: 3+ Detail Lines Selected**
    - Verify all lines are parallel.
    - Identify the "boundary" lines (the two furthest apart).
    - Calculate the equal spacing based on the distance between boundaries and the total count.
    - Move intermediate lines to their calculated positions.
- **Case B: 2 Detail Lines Selected**
    - Verify they are parallel.
    - Prompt user via `FlexForm` for the number of lines to add **between** them.
    - Create new detail lines (matching the style of the first selected line) at equal intervals.
- **Case C: 0 Detail Lines Selected**
    - Prompt user to pick two points (Start Point and End Point).
    - Prompt user for the **total number of lines** to be placed (including start and end).
    - Create lines at these points and equally spaced between them.
    - *Question:* What should be the orientation/length of these lines? (Likely perpendicular to the path between points).

### 2. UI/UX Suggestions
- Use `rpw.ui.forms.FlexForm` for a clean, Revit-native input experience.
- Provide clear status messages in the Revit Status Bar (bottom left) during point picking.
- Use `TransactionGroup` to ensure all additions/moves are bundled as one "Undo" action.

## Implementation Details
- **Parallel Check**: Compare the direction vectors of the lines.
- **Sorting**: To distribute correctly, lines must be sorted based on their projection onto the normal vector of their direction.
- **Movement**: Use `ElementTransformUtils.MoveElement` or update the line's `LocationCurve`.

## Questions for Clarification
1. **Line Orientation (0-selection case)**: When picking two points for start/end, should the new lines be created perpendicular to the vector between those points? (e.g. pick top and bottom of a hallway to place horizontal lines).
    Answer: yes, the new lines should be created perpendicular to the vector between those points.
2. **Line Length (0-selection case)**: How long should the new lines be? A default length (e.g. 4') or should we prompt for a third point?
    Answer: Default 4'
3. **Parallel Tolerance**: Should the script allow for a tiny margin of error if lines are not *perfectly* parallel?
    Answer: no, revit is strict on this so we'll be strict on this as well
4. **Line Style**: Should newly created lines always match the current active line style, or inherit from selected lines when available?
    Answer: inherit the selected lines when available, otherwise use the `<Hidden>` detail line style.