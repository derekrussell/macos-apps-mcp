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
--   search         <query> <include_completed>         -> id|title|due_date|notes|is_completed|list\n...
--   create         <title> <list> <due_date> <notes>   -> reminder_id
--   complete       <reminder_id>                       -> (no output)
--   update         <reminder_id> <title> <due_date> <notes> <list>
--                                                      -> (no output)
--   delete         <reminder_id>                       -> (no output)

on run argv
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
        return search_reminders(searchQuery, includeCompleted)
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

    set remTitle to my sanitise_field(remName)

    if remBody is missing value then
        set remNotes to ""
    else
        set remNotes to my sanitise_field(remBody)
    end if

    set remDueDate to ""
    if remDue is not missing value then set remDueDate to my format_date(remDue)

    if remDone then
        set remCompletedStr to "true"
    else
        set remCompletedStr to "false"
    end if

    return (remId as text) & "|" & remTitle & "|" & remDueDate & "|" & remNotes & "|" & remCompletedStr & "|" & listName & linefeed
end format_reminder


-- Strip pipe and newline characters from a string field.
-- Must be called with "my" from inside tell blocks so it runs in script scope,
-- ensuring text item delimiters resolve as an AppleScript language construct.
on sanitise_field(str)
    -- Replace each delimiter/newline character with a space. The split
    -- (text items) and join (as text) must use DIFFERENT delimiters:
    -- splitting and joining on the same delimiter is a no-op.
    set str to str as text
    set AppleScript's text item delimiters to "|"
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to linefeed
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to return
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to ""
    return str
end sanitise_field


-- Format a date as ISO 8601 (YYYY-MM-DDTHH:MM:SS) without shell invocation.
on format_date(theDate)
    set y to year of theDate as text
    set mo to month of theDate as integer
    set d to day of theDate as integer
    set h to hours of theDate
    set mi to minutes of theDate
    set s to seconds of theDate
    if mo < 10 then set mo to "0" & mo
    if d < 10 then set d to "0" & d
    if h < 10 then set h to "0" & h
    if mi < 10 then set mi to "0" & mi
    if s < 10 then set s to "0" & s
    return y & "-" & mo & "-" & d & "T" & h & ":" & mi & ":" & s
end format_date


-- Find a reminder list by name, or return the default list.
on resolve_list(listName)
    tell application "Reminders"
        if listName is "default" then return default list
        return first list whose name is listName
    end tell
end resolve_list


-- Find a reminder by its internal id across all lists
on find_reminder(reminderId)
    tell application "Reminders"
        repeat with lst in every list
            set matches to (reminders of lst whose id is reminderId)
            if (count of matches) > 0 then return item 1 of matches
        end repeat
        error "Reminder not found: " & reminderId
    end tell
end find_reminder


-- Return true if a reminder matches the search query and filter.
-- `props` is a local properties record (fetched in bulk by the caller).
on reminder_matches(props, searchQuery, includeCompleted)
    tell application "Reminders"
        set remDone to completed of props
        set remName to name of props
        set remBody to body of props
    end tell

    -- Guard: skip completed reminders if not including them.
    if includeCompleted is "false" and remDone then return false

    if remBody is missing value then set remBody to ""
    ignoring case
        return remName contains searchQuery or remBody contains searchQuery
    end ignoring
end reminder_matches


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
on search_reminders(searchQuery, includeCompleted)
    tell application "Reminders"
        set allLists to every list
    end tell

    set output to ""
    repeat with lst in allLists
        -- One bulk Apple event per list, then match/format locally.
        tell application "Reminders"
            set lstName to name of lst
            set allProps to properties of (reminders of lst)
        end tell
        repeat with p in allProps
            set pc to contents of p
            if my reminder_matches(pc, searchQuery, includeCompleted) then
                set output to output & (my format_reminder(pc, lstName))
            end if
        end repeat
    end repeat
    return output
end search_reminders


-- Create a new reminder in the named list.
-- Returns the id of the created reminder.
-- Pass empty string for due_date or notes to omit them.
on create_reminder(remTitle, listName, dueDateStr, notes)
    set targetList to resolve_list(listName)
    tell application "Reminders"
        set newReminder to make new reminder at end of targetList with properties {name: remTitle}
        if dueDateStr is not "" then
            set due date of newReminder to date dueDateStr
        end if
        if notes is not "" then
            set body of newReminder to notes
        end if
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
-- Pass empty string for any field that should not be changed.
-- Pass empty string for due date to clear it.
on update_reminder(reminderId, newTitle, newDueDate, newNotes, newListName)
    set rem to find_reminder(reminderId)
    tell application "Reminders"
        if newTitle is not "" then set name of rem to newTitle
        if newDueDate is "" then
            set due date of rem to missing value
        else
            set due date of rem to date newDueDate
        end if
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
