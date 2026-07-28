-- notes.applescript
-- Handles all Apple Notes actions for the apple-mcp server.
--
-- Called by tools/notes.py via:
--   osascript notes.applescript <action> [args...]
--
-- Actions:
--   list_folders                               -> name|count\n...
--   get_notes   <folder> <count> <offset>      -> total\nid|title|folder|modified_date\n...
--   search      <query>                        -> id|title|folder|modified_date\n...
--   create      <title> <body> <folder>        -> note_id
--   delete      <note_id>                      -> (no output)
--   update      <note_id> <title> <body>       -> (no output)
--   append      <note_id> <text>               -> (no output)

on run argv
    set action to item 1 of argv

    if action is "list_folders" then
        return list_folders()
    else if action is "get_notes" then
        set folderName to item 2 of argv
        set batchCount to (item 3 of argv) as integer
        set batchOffset to (item 4 of argv) as integer
        return get_notes(folderName, batchCount, batchOffset)
    else if action is "search" then
        return search_notes(item 2 of argv)
    else if action is "create" then
        return create_note(item 2 of argv, item 3 of argv, item 4 of argv)
    else if action is "delete" then
        delete_note(item 2 of argv)
    else if action is "update" then
        update_note(item 2 of argv, item 3 of argv, item 4 of argv)
    else if action is "append" then
        append_to_note(item 2 of argv, item 3 of argv)
    else
        error "Unknown action: " & action
    end if
end run

-- Utilities
-- ------------------------------------------------------------

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


-- Format a note as a pipe-delimited line from already-fetched primitive
-- values (id, name, modification date). Callers bulk-fetch these fields
-- so this handler performs no Apple-event round-trips.
-- Output: id|title|folder|modified_date (date is ISO 8601)
on format_note_line(noteId, noteName, noteDate, folderName)
    set noteDateStr to ""
    if noteDate is not missing value then set noteDateStr to my format_date(noteDate)
    return (noteId as text) & "|" & (my sanitise_field(noteName)) & "|" & folderName & "|" & noteDateStr & linefeed
end format_note_line


-- Find a Notes folder by name, or return the default folder.
on resolve_folder(folderName)
    tell application "Notes"
        if folderName is "default" then return folder 1 of default account
        return first folder whose name is folderName
    end tell
end resolve_folder


-- Find a note by its internal id across all folders.
on find_note(noteId)
    tell application "Notes"
        repeat with theFolder in every folder
            set matches to (notes of theFolder whose id is noteId)
            if (count of matches) > 0 then return item 1 of matches
        end repeat
        error "Note not found: " & noteId
    end tell
end find_note


-- Public handlers
-- ------------------------------------------------------------

-- Return all folders with their note counts.
-- Output is pipe-delimited: name|count
on list_folders()
    tell application "Notes"
        set output to ""
        repeat with theFolder in every folder
            set folderName to name of theFolder
            set folderCount to count of notes of theFolder
            set output to output & folderName & "|" & (folderCount as text) & linefeed
        end repeat
        return output
    end tell
end list_folders


-- Return a paginated batch of notes from the named folder.
-- First line is the total count: subsequent lines are pipe-delimited.
on get_notes(folderName, batchCount, batchOffset)
    set targetFolder to resolve_folder(folderName)
    tell application "Notes"
        set fName to name of targetFolder
        set totalCount to count of notes of targetFolder
    end tell

    set output to (totalCount as text) & linefeed
    if totalCount is 0 then return output

    -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
    set startIdx to batchOffset + 1
    if startIdx > totalCount then return output

    set endIdx to batchOffset + batchCount
    if endIdx > totalCount then set endIdx to totalCount

    -- Fetch only the paginated slice's fields, each as a single bulk Apple
    -- event, instead of reading id/name/date per note. This keeps the whole
    -- call to a handful of round-trips regardless of folder size.
    tell application "Notes"
        set noteIds to id of (notes startIdx thru endIdx of targetFolder)
        set noteNames to name of (notes startIdx thru endIdx of targetFolder)
        set noteDates to modification date of (notes startIdx thru endIdx of targetFolder)
    end tell

    repeat with i from 1 to count of noteIds
        set output to output & (my format_note_line(item i of noteIds, item i of noteNames, item i of noteDates, fName))
    end repeat

    return output
end get_notes


-- Search for notes by text across all folders (title and body).
-- Output is pipe-delimited: id|title|folder|modified_date
--
-- Matching is pushed into Notes via a `whose` clause (case-insensitive),
-- so the app returns only the matches. "a reference to" keeps the result a
-- specifier rather than a list, which lets us bulk-fetch each field in one
-- Apple event instead of reading id/name/date per note.
on search_notes(searchQuery)
    tell application "Notes"
        set output to ""
        repeat with theFolder in every folder
            set fName to name of theFolder
            set matched to a reference to (notes of theFolder whose name contains searchQuery or body contains searchQuery)
            set noteIds to id of matched
            set noteNames to name of matched
            set noteDates to modification date of matched
            repeat with i from 1 to count of noteIds
                set output to output & (my format_note_line(item i of noteIds, item i of noteNames, item i of noteDates, fName))
            end repeat
        end repeat
        return output
    end tell
end search_notes


-- Create a new note in the named folder.
-- Returns the id of the created note.
on create_note(noteTitle, noteBody, folderName)
    set targetFolder to resolve_folder(folderName)
    tell application "Notes"
        set newNote to make new note at targetFolder with properties {name: noteTitle}
        if noteBody is not "" then
            set body of newNote to noteBody
        end if
        return id of newNote
    end tell
end create_note


-- Update the title and/or body of an existing note.
-- Pass empty string for any field that should not be changed.
on update_note(noteId, newTitle, newBody)
    set theNote to find_note(noteId)
    tell application "Notes"
        if newTitle is not "" then set name of theNote to newTitle
        if newBody is not "" then set body of theNote to newBody
    end tell
end update_note


-- Append plain text to an existing note's body.
-- Inserts a new <div> before </body> to preserve valid HTML structure.
on append_to_note(noteId, newText)
    set theNote to find_note(noteId)
    tell application "Notes"
        set existingBody to body of theNote
        set bodyClose to "</body>"
        set closePos to offset of bodyClose in existingBody
        if closePos > 0 then
            set newBody to (text 1 thru (closePos - 1) of existingBody) & "<div>" & newText & "</div>" & bodyClose & "</html>"
        else
            set newBody to existingBody & "<div>" & newText & "</div>"
        end if
        set body of theNote to newBody
    end tell
end append_to_note


-- Permanently delete a note.
on delete_note(noteId)
    set theNote to find_note(noteId)
    tell application "Notes"
        delete theNote
    end tell
end delete_note