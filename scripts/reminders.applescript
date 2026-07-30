-- reminders.applescript
-- Handles all Apple Reminders actions for the apple-mcp server.
--
-- Called by tools/reminders.py via:
--   osascript reminders.applescript <action> [args...]
--
-- Actions:
--   list_lists                                         -> name|count\n...
--   get_reminders  <list> <count> <offset> <include_completed>
--                                                      -> total\nid|title|due_date|notes|is_completed|list\n...
--   search         <query> <include_completed> <search_notes>
--                                                      -> id|title|due_date|notes|is_completed|list\n...
--   build_index                                        -> id|title|list\n...   (lightweight, all lists)
--   hydrate        <id> [<id> ...]                     -> id|due_date|notes|is_completed\n...
--   create         <title> <list> <due_date> <notes>   -> reminder_id
--   complete       <reminder_id>                       -> (no output)
--   update         <reminder_id> <title> <due_date> <notes> <list>
--                                                      -> (no output)
--   delete         <reminder_id>                       -> (no output)

-- Shared handlers (sanitise_field, format_date), loaded once per invocation.
property util : missing value

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
        return search_reminders(searchQuery, includeCompleted, searchNotes)
    else if action is "build_index" then
        return build_index()
    else if action is "hydrate" then
        if (count of argv) < 2 then return ""
        return hydrate_reminders(items 2 thru -1 of argv)
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


-- Utilities
-- ------------------------------------------------------------

-- Format a reminder record as a pipe-delimited line.
-- Output: id|title|due_date|notes|is_completed|list
-- `props` is a local properties record (already fetched in bulk by the
-- caller), NOT a live reminder reference. Reading its labelled fields
-- needs the Reminders terminology, hence the tell block, but because the
-- record is local it costs no Apple-event round-trip.
on format_reminder(props, listName)
    tell application "Reminders"
        set remId to id of props
        set remName to name of props
        set remBody to body of props
        set remDue to due date of props
        set remDone to completed of props
    end tell

    set remTitle to util's sanitise_field(remName)

    if remBody is missing value then
        set remNotes to ""
    else
        set remNotes to util's sanitise_field(remBody)
    end if

    set remDueDate to ""
    if remDue is not missing value then set remDueDate to util's format_date(remDue)

    if remDone then
        set remCompletedStr to "true"
    else
        set remCompletedStr to "false"
    end if

    return (remId as text) & "|" & remTitle & "|" & remDueDate & "|" & remNotes & "|" & remCompletedStr & "|" & listName & linefeed
end format_reminder


-- Load the shared handler library (sanitise_field, format_date) that sits
-- alongside this script. Resolved relative to this file's own path so it
-- works regardless of the caller's working directory.
on load_utilities()
    set myPosix to POSIX path of (path to me)
    set AppleScript's text item delimiters to "/"
    set dirParts to items 1 thru -2 of (text items of myPosix)
    set utilPath to (dirParts as text) & "/utilities.applescript"
    set AppleScript's text item delimiters to ""
    return (run script (read POSIX file utilPath as «class utf8»))
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
-- which timed out on large accounts. `get` forces resolution so an unknown
-- id raises here instead of surfacing a cryptic error at the mutation site.
on find_reminder(reminderId)
    tell application "Reminders"
        try
            set rem to reminder id reminderId
            get name of rem
            return rem
        on error
            error "Reminder not found: " & reminderId
        end try
    end tell
end find_reminder




-- Public handlers
-- ------------------------------------------------------------

-- Return all reminder lists with their reminder counts.
-- Output is pipe-delimited: name|count
on list_lists()
    tell application "Reminders"
        set output to ""
        repeat with lst in every list
            set lstName to name of lst
            set lstCount to count of reminders of lst
            set output to output & lstName & "|" & (lstCount as text) & linefeed
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
    -- per-reminder property reads both cost one round-trip per reminder,
    -- which times out on large lists (e.g. a 1000+ item grocery list).
    tell application "Reminders"
        set lstName to name of targetList
        set allProps to properties of (reminders of targetList)
    end tell

    if includeCompleted is "true" then
        set wanted to allProps
    else
        set wanted to {}
        tell application "Reminders"
            repeat with p in allProps
                if not (completed of p) then set end of wanted to contents of p
            end repeat
        end tell
    end if

    set totalCount to count of wanted
    set output to (totalCount as text) & linefeed
    if totalCount is 0 then return output

    -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
    set startIdx to batchOffset + 1
    if startIdx > totalCount then return output

    set endIdx to batchOffset + batchCount
    if endIdx > totalCount then set endIdx to totalCount

    repeat with i from startIdx to endIdx
        set output to output & (my format_reminder(item i of wanted, lstName))
    end repeat

    return output
end get_reminders
        

-- Search for reminders by text across all lists.
-- Output is pipe-delimited: id|title|due_date|notes|is_completed|list
--
-- Fetching full `properties of (reminders of lst)` for every list serialises
-- 15+ fields per reminder and times out on large lists. Reading only the fields
-- needed to MATCH in bulk per list, then the heavy output fields for the few
-- matches, is much cheaper (matches addressed positionally, since bulk field
-- order aligns with `reminder i of`).
--
-- The dominant cost is `body of reminders of lst`: on a 1200-reminder account a
-- name+body read costs ~64s (over the client timeout) versus ~24s for names
-- alone. So notes matching is OPT-IN (searchNotes is "true"); by default we read
-- names only. Either way the matched output line still includes the notes field,
-- read per-match, which is cheap because matches are few.
on search_reminders(searchQuery, includeCompleted, searchNotes)
    tell application "Reminders"
        set allLists to every list
    end tell

    set output to ""
    repeat with lst in allLists
        tell application "Reminders"
            set lstName to name of lst
            set nameList to name of reminders of lst
            if searchNotes is "true" then
                set bodyList to body of reminders of lst
            else
                set bodyList to {}
            end if
        end tell

        set n to count of nameList
        repeat with i from 1 to n
            set remName to item i of nameList

            set isMatch to false
            ignoring case
                if remName contains searchQuery then
                    set isMatch to true
                else if searchNotes is "true" then
                    set b to item i of bodyList
                    if (b is not missing value) and (b contains searchQuery) then set isMatch to true
                end if
            end ignoring

            if isMatch then
                -- Read the remaining fields (including notes) for this match only.
                tell application "Reminders"
                    set rem to reminder i of lst
                    set remId to id of rem
                    set remDone to completed of rem
                    set remDue to due date of rem
                    set remBody to body of rem
                end tell
                if remBody is missing value then set remBody to ""

                if (includeCompleted is "true") or (not remDone) then
                    set dueStr to ""
                    if remDue is not missing value then set dueStr to util's format_date(remDue)
                    if remDone then
                        set doneStr to "true"
                    else
                        set doneStr to "false"
                    end if
                    set output to output & (remId as text) & "|" & (util's sanitise_field(remName)) & "|" & dueStr & "|" & (util's sanitise_field(remBody)) & "|" & doneStr & "|" & lstName & linefeed
                end if
            end if
        end repeat
    end repeat
    return output
end search_reminders


-- Build a lightweight index of every reminder across all lists, for the
-- Python-side search cache. Output is pipe-delimited: id|title|list
--
-- Only two bulk field reads per list (id, name) keep this well under the client
-- timeout even on large accounts. The heavier per-reminder fields (due date,
-- notes, completed) are fetched on demand by `hydrate` for matches only, so
-- they never have to be serialised for the whole account here.
on build_index()
    tell application "Reminders"
        set allLists to every list
    end tell

    set output to ""
    repeat with lst in allLists
        tell application "Reminders"
            set lstName to name of lst
            set idList to id of reminders of lst
            set nameList to name of reminders of lst
        end tell
        repeat with i from 1 to count of idList
            set output to output & (item i of idList) & "|" & (util's sanitise_field(item i of nameList)) & "|" & lstName & linefeed
        end repeat
    end repeat
    return output
end build_index


-- Resolve the heavy fields for a specific set of reminder ids (the matches from
-- a cached search). Output is pipe-delimited: id|due_date|notes|is_completed
--
-- `reminder id` is an O(1) direct reference, so hydrating a handful of matches
-- is cheap even though a full scan of the same fields would not be. Unknown ids
-- are skipped silently (e.g. a reminder deleted since the index was built).
on hydrate_reminders(idList)
    set output to ""
    repeat with rid in idList
        set ridStr to rid as text
        set gotIt to false
        tell application "Reminders"
            try
                set rem to reminder id ridStr
                set remDue to due date of rem
                set remBody to body of rem
                set remDone to completed of rem
                set gotIt to true
            end try
        end tell
        if gotIt then
            set dueStr to ""
            if remDue is not missing value then set dueStr to util's format_date(remDue)
            if remBody is missing value then set remBody to ""
            if remDone then
                set doneStr to "true"
            else
                set doneStr to "false"
            end if
            set output to output & ridStr & "|" & dueStr & "|" & (util's sanitise_field(remBody)) & "|" & doneStr & linefeed
        end if
    end repeat
    return output
end hydrate_reminders


-- Create a new reminder in the named list.
-- Returns the id of the created reminder.
-- Pass empty string for due_date or notes to omit them.
on create_reminder(remTitle, listName, dueDateStr, notes)
    set targetList to resolve_list(listName)

    -- Parse the ISO due date BEFORE the tell block. AppleScript's `date "..."`
    -- coercion mangles ISO strings, so use the shared component-based parser.
    set dueProvided to (dueDateStr is not "")
    if dueProvided then set parsedDue to util's parse_iso_date(dueDateStr)

    tell application "Reminders"
        set newReminder to make new reminder at end of targetList with properties {name:remTitle}
        if dueProvided then set due date of newReminder to parsedDue
        if notes is not "" then set body of newReminder to notes
        return id of newReminder
    end tell
end create_reminder


-- Mark a reminder as complete.
on complete_reminder(reminderId)
    set rem to find_reminder(reminderId)
    tell application "Reminders"
        set completion date of rem to current date
    end tell
end complete_reminder


-- Update one or more fields of an existing reminder.
-- Field arguments are "" to leave a field unchanged. The due date argument
-- uses a sentinel: "__KEEP__" leaves it unchanged (the Python layer sends this
-- when the caller omits due_date); any other value is parsed and applied.
--
-- Note: Apple Reminders' AppleScript interface cannot clear an existing due
-- date (setting it to missing value, deleting it, and clearing remind-me date
-- all error), so an explicit "" is rejected rather than silently ignored.
on update_reminder(reminderId, newTitle, newDueDate, newNotes, newListName)
    -- Parse/validate the due date BEFORE mutating anything, so a bad or
    -- unsupported value cannot leave a half-applied record.
    set applyDue to false
    if newDueDate is "__KEEP__" then
        set applyDue to false
    else if newDueDate is "" then
        error "Apple Reminders cannot clear a due date via AppleScript; omit due_date to leave it unchanged."
    else
        set parsedDue to util's parse_iso_date(newDueDate)
        set applyDue to true
    end if

    set rem to find_reminder(reminderId)
    tell application "Reminders"
        if newTitle is not "" then set name of rem to newTitle
        if applyDue then set due date of rem to parsedDue
        if newNotes is not "" then set body of rem to newNotes
    end tell
    if newListName is not "" then
        set targetList to resolve_list(newListName)
        tell application "Reminders"
            move rem to targetList
        end tell
    end if
end update_reminder


-- Permanently delete a reminder.
on delete_reminder(reminderId)
    set rem to find_reminder(reminderId)
    tell application "Reminders"
        delete rem
    end tell
end delete_reminder
