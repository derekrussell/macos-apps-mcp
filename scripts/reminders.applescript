-- reminders.applescript
-- Handles all Apple Reminders actions for the macos-apps-mcp server.
--
-- Called by tools/reminders.py via:
--   osascript reminders.applescript <action> [args...]
--
-- Actions:
--   list_lists                                         -> name|count\n...
--   get_reminders  <list> <count> <offset> <include_completed>
--                                                      -> total\nid|title|due_date|notes|is_completed|list\n...
--   search         <query> <include_completed> <search_notes> <time_budget>
--                                                      -> status\nid|title|due_date|notes|is_completed|list\n...
--                                                         (status = "ok" | "timeout"; time_budget in seconds)
--   build_index                                        -> id|title|list|due_date|is_completed\n...   (all lists)
--   create         <title> <list> <due_date> <notes>   -> reminder_id|resolved_list
--   complete       <reminder_id>                       -> (no output)
--   update         <reminder_id> <title> <due_date> <notes> <list>
--                                                      -> (no output)
--   delete         <reminder_id>                       -> (no output)

-- Shared handlers (sanitise_field, format_date, parse_iso_date), loaded once
-- per invocation.
property util : missing value

-- Sentinel the Python layer sends for update's due date when the caller omitted
-- it (meaning "leave unchanged"), distinct from an explicit new value. Must
-- match _KEEP_DUE_DATE in tools/reminders.py.
property keepDueDateSentinel : "__KEEP__"

on run argv
    set util to load_utilities()
    set action to item 1 of argv

    if action is "list_lists" then
        return list_lists()
    else if action is "get_reminders" then
        set listName to item 2 of argv
        set batchCount to (item 3 of argv) as integer
        set batchOffset to (item 4 of argv) as integer
        set includeCompleted to item 5 of argv
        return get_reminders(listName, batchCount, batchOffset, includeCompleted)
    else if action is "search" then
        set searchQuery to item 2 of argv
        set includeCompleted to item 3 of argv
        set searchNotes to item 4 of argv
        set timeBudget to (item 5 of argv) as integer
        return search_reminders(searchQuery, includeCompleted, searchNotes, timeBudget)
    else if action is "build_index" then
        return build_index()
    else if action is "create" then
        return create_reminder(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv)
    else if action is "complete" then
        complete_reminder(item 2 of argv)
    else if action is "update" then
        update_reminder(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv, item 6 of argv)
    else if action is "delete" then
        delete_reminder(item 2 of argv)
    else
        error "Unknown action: " & action
    end if
end run


-- Helpers
-- ------------------------------------------------------------

-- Load the shared handler library (sanitise_field, format_date, parse_iso_date)
-- that sits alongside this script. Resolved relative to this file's own path so
-- it works regardless of the caller's working directory.
on load_utilities()
    set myPosixPath to POSIX path of (path to me)
    set AppleScript's text item delimiters to "/"
    set directoryParts to items 1 thru -2 of (text items of myPosixPath)
    set utilitiesPath to (directoryParts as text) & "/utilities.applescript"
    set AppleScript's text item delimiters to ""
    return (run script (read POSIX file utilitiesPath as «class utf8»))
end load_utilities


-- Find a reminder list by name, or return the default list.
on resolve_list(listName)
    tell application "Reminders"
        if listName is "default" then return default list
        return first list whose name is listName
    end tell
end resolve_list


-- Find a reminder by its internal id. Uses a direct `reminder id` reference
-- (O(1)) rather than scanning every list with a slow `whose id is` clause,
-- which timed out on large accounts. `get` forces resolution so an unknown id
-- raises here instead of surfacing a cryptic error at the mutation site.
on find_reminder(reminderId)
    tell application "Reminders"
        try
            set targetReminder to reminder id reminderId
            get name of targetReminder
            return targetReminder
        on error
            error "Reminder not found: " & reminderId
        end try
    end tell
end find_reminder


-- Return "true"/"false" for an AppleScript boolean (the wire format's booleans).
on boolean_to_text(flag)
    if flag then
        return "true"
    else
        return "false"
    end if
end boolean_to_text


-- Format a due date as ISO 8601, or "" when there is no due date.
on format_due_date(dueDateValue)
    if dueDateValue is missing value then return ""
    return util's format_date(dueDateValue)
end format_due_date


-- Format a reminder record as a pipe-delimited line.
-- Output: id|title|due_date|notes|is_completed|list
-- `reminderProperties` is a local properties record (already fetched in bulk by
-- the caller), NOT a live reminder reference. Reading its labelled fields needs
-- the Reminders terminology, hence the tell block, but because the record is
-- local it costs no Apple-event round-trip.
on format_reminder(reminderProperties, listName)
    tell application "Reminders"
        set reminderId to id of reminderProperties
        set reminderName to name of reminderProperties
        set reminderBody to body of reminderProperties
        set reminderDueDate to due date of reminderProperties
        set reminderIsCompleted to completed of reminderProperties
    end tell

    set titleField to util's sanitise_field(reminderName)

    if reminderBody is missing value then
        set notesField to ""
    else
        set notesField to util's sanitise_field(reminderBody)
    end if

    set dueDateField to my format_due_date(reminderDueDate)
    set completedField to my boolean_to_text(reminderIsCompleted)

    return (reminderId as text) & "|" & titleField & "|" & dueDateField & "|" & notesField & "|" & completedField & "|" & listName & linefeed
end format_reminder


-- Format a reminder record as an index line: id|title|list|due_date|is_completed
-- (no notes; the index does not carry them).
-- Every field is read from ONE local properties record, fetched in bulk by the
-- caller, so all fields are guaranteed to belong to the SAME reminder. This is
-- why build_index must not join parallel `due date of reminders` / `completed of
-- reminders` lists positionally: those are separate Apple events that Reminders
-- can return in different orders (completed items re-sort), which silently
-- misaligns due dates against ids. See issue #13.
on format_index_line(reminderProperties, listName)
    tell application "Reminders"
        set reminderId to id of reminderProperties
        set reminderName to name of reminderProperties
        set reminderDueDate to due date of reminderProperties
        set reminderIsCompleted to completed of reminderProperties
    end tell

    set titleField to util's sanitise_field(reminderName)
    set dueDateField to my format_due_date(reminderDueDate)
    set completedField to my boolean_to_text(reminderIsCompleted)

    return (reminderId as text) & "|" & titleField & "|" & listName & "|" & dueDateField & "|" & completedField & linefeed
end format_index_line


-- Public handlers
-- ------------------------------------------------------------

-- Return all reminder lists with their reminder counts.
-- Output is pipe-delimited: name|count
on list_lists()
    tell application "Reminders"
        set output to ""
        repeat with currentList in every list
            set currentListName to name of currentList
            set reminderCount to count of reminders of currentList
            set output to output & currentListName & "|" & (reminderCount as text) & linefeed
        end repeat
        return output
    end tell
end list_lists


-- Return a paginated batch of reminders from the named list.
-- First line of output is the total matching count.
-- Subsequent lines are pipe-delimited: id|title|due_date|notes|is_completed|list
on get_reminders(listName, batchCount, batchOffset, includeCompleted)
    set targetList to resolve_list(listName)

    -- Fetch every reminder's full properties in a SINGLE Apple event, then
    -- filter and paginate locally. The `whose completed is false` clause and
    -- per-reminder property reads both cost one round-trip per reminder, which
    -- times out on large lists (e.g. a 1000+ item grocery list).
    tell application "Reminders"
        set resolvedListName to name of targetList
        set allReminderProperties to properties of (reminders of targetList)
    end tell

    if includeCompleted is "true" then
        set wantedReminders to allReminderProperties
    else
        set wantedReminders to {}
        tell application "Reminders"
            repeat with reminderProperties in allReminderProperties
                if not (completed of reminderProperties) then
                    set end of wantedReminders to contents of reminderProperties
                end if
            end repeat
        end tell
    end if

    set totalCount to count of wantedReminders
    set output to (totalCount as text) & linefeed
    if totalCount is 0 then return output

    -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
    set startIndex to batchOffset + 1
    if startIndex > totalCount then return output

    set endIndex to batchOffset + batchCount
    if endIndex > totalCount then set endIndex to totalCount

    repeat with recordIndex from startIndex to endIndex
        set output to output & (my format_reminder(item recordIndex of wantedReminders, resolvedListName))
    end repeat

    return output
end get_reminders


-- Number of reminders whose properties are read in one Apple event by
-- search_reminders. Small on purpose: it caps the size of any single
-- (uninterruptible) event and makes the time-budget check below fine-grained.
property searchChunkSize : 25


-- Search for reminders by text across all lists, bounded by a time budget.
-- Output: a status line ("ok" | "timeout") followed by matching records
--   id|title|due_date|notes|is_completed|list
--
-- This is the OPT-IN live full-text scan (search_notes). A full body scan of a
-- large account is expensive and, run to completion or hard-killed mid-event,
-- wedges EventKit so that even simple reads stall for minutes (see CLAUDE.md and
-- issue #17). A hard timeout can't help: an osascript blocked inside an Apple
-- event ignores SIGKILL until that event returns. So this scan bounds ITSELF:
--
--   * It reads `properties of (reminders i thru j of list)` in small CHUNKS.
--     One atomic record per reminder means every output field is read from the
--     same record (no parallel-array positional join, which is what misaligned
--     due dates in issue #13), and a small chunk keeps each Apple event short.
--   * Between chunks it checks an elapsed-time budget and, once exceeded, stops
--     and returns a "timeout" status with whatever it found so far — a
--     cooperative abort at a statement boundary, never a mid-event kill.
--
-- The caller sets the osascript timeout ABOVE this budget so this abort fires
-- first. `body` is read whether or not searchNotes is "true" (it rides along in
-- the properties record for free); searchNotes only controls whether body TEXT
-- is consulted for matching.
on search_reminders(searchQuery, includeCompleted, searchNotes, timeBudget)
    tell application "Reminders"
        set allLists to every list
    end tell

    set scanStart to current date
    set truncated to false
    set output to ""

    repeat with currentList in allLists
        tell application "Reminders"
            set currentListName to name of currentList
            set listReminderCount to count of reminders of currentList
        end tell

        set chunkStart to 1
        repeat while chunkStart ≤ listReminderCount
            if ((current date) - scanStart) ≥ timeBudget then
                set truncated to true
                exit repeat
            end if

            set chunkEnd to chunkStart + searchChunkSize - 1
            if chunkEnd > listReminderCount then set chunkEnd to listReminderCount

            tell application "Reminders"
                set chunkProperties to properties of (reminders chunkStart thru chunkEnd of currentList)
            end tell

            repeat with reminderProperties in chunkProperties
                set output to output & my match_and_format(contents of reminderProperties, currentListName, searchQuery, includeCompleted, searchNotes)
            end repeat

            set chunkStart to chunkEnd + 1
        end repeat

        if truncated then exit repeat
    end repeat

    if truncated then
        return "timeout" & linefeed & output
    else
        return "ok" & linefeed & output
    end if
end search_reminders


-- Test one reminder record against the query and, if it matches (and passes the
-- completed filter), return its formatted line; otherwise return "".
-- Reads every field from the single local properties record, so the emitted
-- fields all belong to the same reminder. Title is always matched; body text is
-- matched only when searchNotes is "true".
on match_and_format(reminderProperties, listName, searchQuery, includeCompleted, searchNotes)
    tell application "Reminders"
        set reminderName to name of reminderProperties
        set reminderBody to body of reminderProperties
        set reminderIsCompleted to completed of reminderProperties
        set reminderId to id of reminderProperties
        set reminderDueDate to due date of reminderProperties
    end tell

    if (includeCompleted is not "true") and reminderIsCompleted then return ""
    if reminderBody is missing value then set reminderBody to ""

    set isMatch to false
    ignoring case
        if reminderName contains searchQuery then
            set isMatch to true
        else if (searchNotes is "true") and (reminderBody contains searchQuery) then
            set isMatch to true
        end if
    end ignoring
    if not isMatch then return ""

    set dueDateField to my format_due_date(reminderDueDate)
    set completedField to my boolean_to_text(reminderIsCompleted)
    return (reminderId as text) & "|" & (util's sanitise_field(reminderName)) & "|" & dueDateField & "|" & (util's sanitise_field(reminderBody)) & "|" & completedField & "|" & listName & linefeed
end match_and_format


-- Build the search index of every reminder across all lists, for the Python
-- side to hold in memory. Output is pipe-delimited:
--   id|title|list|due_date|is_completed
--
-- Fetches `properties of (reminders of currentList)` ONCE per list (a single
-- Apple event) and reads every field from each atomic per-reminder record via
-- format_index_line - the same approach get_reminders uses. This is deliberate:
-- reading id/name/due date/completed as four SEPARATE bulk `... of reminders`
-- events and joining them positionally silently misaligned due dates against ids
-- (Reminders can return those events in different orders once completed items
-- re-sort), which is the issue #13 regression. An atomic record per reminder
-- cannot misalign. Notes ARE included in the record but simply not emitted; this
-- runs in the background where cost affects only freshness, not a user timeout.
on build_index()
    tell application "Reminders"
        set allLists to every list
    end tell

    set output to ""
    repeat with currentList in allLists
        tell application "Reminders"
            set currentListName to name of currentList
            set allReminderProperties to properties of (reminders of currentList)
        end tell

        repeat with reminderProperties in allReminderProperties
            set output to output & (my format_index_line(contents of reminderProperties, currentListName))
        end repeat
    end repeat
    return output
end build_index


-- Create a new reminder in the named list.
-- Returns "id|list" - the created reminder's id and the RESOLVED list name (so
-- "default" becomes e.g. "Reminders"), which lets the caller seed its search
-- index with the real list name rather than the raw argument.
-- Pass empty string for due_date or notes to omit them.
on create_reminder(reminderTitle, listName, dueDateString, notes)
    set targetList to resolve_list(listName)

    -- Parse the ISO due date BEFORE the tell block. AppleScript's `date "..."`
    -- coercion mangles ISO strings, so use the shared component-based parser.
    set dueDateProvided to (dueDateString is not "")
    if dueDateProvided then set parsedDueDate to util's parse_iso_date(dueDateString)

    tell application "Reminders"
        set newReminder to make new reminder at end of targetList with properties {name:reminderTitle}
        if dueDateProvided then set due date of newReminder to parsedDueDate
        if notes is not "" then set body of newReminder to notes
        return (id of newReminder) & "|" & (name of targetList)
    end tell
end create_reminder


-- Mark a reminder as complete.
on complete_reminder(reminderId)
    set targetReminder to find_reminder(reminderId)
    tell application "Reminders"
        set completion date of targetReminder to current date
    end tell
end complete_reminder


-- Update one or more fields of an existing reminder.
-- Field arguments are "" to leave a field unchanged. The due date argument uses
-- a sentinel: keepDueDateSentinel leaves it unchanged (the Python layer sends
-- this when the caller omits due_date); any other value is parsed and applied.
--
-- Note: Apple Reminders' AppleScript interface cannot clear an existing due date
-- (setting it to missing value, deleting it, and clearing remind-me date all
-- error), so an explicit "" is rejected rather than silently ignored.
on update_reminder(reminderId, newTitle, newDueDate, newNotes, newListName)
    -- Parse/validate the due date BEFORE mutating anything, so a bad or
    -- unsupported value cannot leave a half-applied record.
    set applyDueDate to false
    if newDueDate is keepDueDateSentinel then
        set applyDueDate to false
    else if newDueDate is "" then
        error "Apple Reminders cannot clear a due date via AppleScript; omit due_date to leave it unchanged."
    else
        set parsedDueDate to util's parse_iso_date(newDueDate)
        set applyDueDate to true
    end if

    set targetReminder to find_reminder(reminderId)
    tell application "Reminders"
        if newTitle is not "" then set name of targetReminder to newTitle
        if applyDueDate then set due date of targetReminder to parsedDueDate
        if newNotes is not "" then set body of targetReminder to newNotes
    end tell

    if newListName is not "" then
        set targetList to resolve_list(newListName)
        tell application "Reminders"
            move targetReminder to targetList
        end tell
    end if
end update_reminder


-- Permanently delete a reminder.
on delete_reminder(reminderId)
    set targetReminder to find_reminder(reminderId)
    tell application "Reminders"
        delete targetReminder
    end tell
end delete_reminder
