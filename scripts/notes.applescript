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


-- Format a note record as a pipe-delimited line.
-- Output: id|title|folder|modified_date
on format_note(theNote, folderName)
    tell application "Notes"
        set noteId to id of theNote
        set noteTitle to name of theNote
        set noteDate to ""
        if modification date of theNote is not missing value then
            set noteDate to (modification date of theNote) as text
        end if
        return noteId & "|" & (my sanitise_field(noteTitle)) & "|" & folderName & "|" & noteDate & linefeed
    end tell
end format_note


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


-- Return true if a note's title or body contains the search query.
on note_matches(theNote, searchQuery)
    tell application "Notes"
        ignoring case
            return (name of theNote) contains searchQuery or (body of theNote) contains searchQuery
        end ignoring
    end tell
end note_matches


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
        set allNotes to notes of targetFolder

        set totalCount to count of allNotes
        set output to (totalCount as text) & linefeed

        if totalCount is 0 then return output

        -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
        set startIdx to batchOffset + 1
        if startIdx > totalCount then return output

        set endIdx to batchOffset + batchCount
        if endIdx > totalCount then set endIdx to totalCount

        set batchNotes to items startIdx thru endIdx of allNotes

        repeat with theNote in batchNotes
            set output to output & (my format_note(theNote, fName))
        end repeat

        return output
    end tell
end get_notes


-- Search for notes by test across all folders (title and body).
-- Output is pipe-delimited: id|title|folder|modified_date
on search_notes(searchQuery)
    tell application "Notes"
        set output to ""
        repeat with theFolder in every folder
            set fName to name of theFolder
            repeat with theNote in notes of theFolder
                if my note_matches(theNote, searchQuery) then
                    set output to output & (my format_note(theNote, fName))
                end if
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